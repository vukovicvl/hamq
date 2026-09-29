"""Tests for hamq.core.adif (task M2-01)."""

from __future__ import annotations

import ast
import json
import math
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hamq.core import adif
from hamq.core.adif import (
    AdifDocument,
    decode_bytes,
    dedup_key,
    format_document,
    format_record,
    parse_adi,
    parse_document,
    parse_freq,
    parse_latlon,
    parse_qso_datetime,
    read_adi,
)
from hamq.core.modes import display_mode

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "adif"
CATALOG = ROOT / "hamq" / "i18n" / "sr_Latn" / "core_adif.json"
SOURCE = ROOT / "hamq" / "core" / "adif.py"

SKILL_EXAMPLE = (
    "optional free text header\n"
    "<ADIF_VER:5>3.1.4\n"
    "<PROGRAMID:6>WSJT-X\n"
    "<EOH>\n"
    "<CALL:5>YU1AB <QSO_DATE:8>20260915 <TIME_ON:6>184500 <BAND:3>20m\n"
    "<MODE:3>FT8 <FREQ:9>14.075123 <GRIDSQUARE:4>KN04 <RST_SENT:3>-10\n"
    "<RST_RCVD:3>-12 <EOR>\n"
)

SERBIAN_VALUES = ["Đorđe", "Đorđe Petrović", "Miloš Šćekić", "Čačak", "Хвала на вези"]


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Warnings are compared in English, independent of the global plugin language."""
    monkeypatch.setattr(adif, "tr", lambda text: text)


def one(text: str) -> dict[str, str]:
    doc = parse_document(text)
    assert len(doc.records) == 1, doc
    return doc.records[0]


def joined(doc: AdifDocument) -> str:
    return "\n".join(doc.warnings)


# --- decode_bytes -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"<CALL:5>YU1AB <EOR>", "<CALL:5>YU1AB <EOR>"),
        (b"\xef\xbb\xbf<CALL:5>YU1AB <EOR>", "<CALL:5>YU1AB <EOR>"),
        ("<NAME:5>Đorđe".encode(), "<NAME:5>Đorđe"),
        ("<NAME:6>Jürgen".encode("latin-1"), "<NAME:6>Jürgen"),
        (b"abc\x80\x81\xfe", "abc\x80\x81\xfe"),
        (b"a\r\nb\rc\n", "a\r\nb\rc\n"),
        (b"", ""),
        (b"\xef\xbb\xbf", ""),
        ("<CALL:5>YU1AB <EOR>".encode("utf-16"), "<CALL:5>YU1AB <EOR>"),
        (b"\xfe\xff" + "<NAME:5>Đorđe".encode("utf-16-be"), "<NAME:5>Đorđe"),
    ],
)
def test_decode_bytes(data, expected):
    assert decode_bytes(data) == expected


def test_decode_bytes_accepts_bytes_like_and_never_raises():
    assert decode_bytes(bytearray(b"<EOR>")) == "<EOR>"
    assert decode_bytes(memoryview(b"<EOR>")) == "<EOR>"
    assert decode_bytes(None) == ""
    assert decode_bytes("﻿<EOR>") == "<EOR>"
    rng = random.Random(7)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
        assert isinstance(decode_bytes(data), str)


# --- structure: header, records, tags -----------------------------------------------------


def test_skill_example():
    doc = parse_document(SKILL_EXAMPLE)
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "WSJT-X"}
    assert doc.records == [
        {
            "CALL": "YU1AB",
            "QSO_DATE": "20260915",
            "TIME_ON": "184500",
            "BAND": "20m",
            "MODE": "FT8",
            "FREQ": "14.075123",
            "GRIDSQUARE": "KN04",
            "RST_SENT": "-10",
            "RST_RCVD": "-12",
        }
    ]
    assert doc.warnings == []


def test_parse_adi_returns_records_and_warnings():
    doc = parse_document(SKILL_EXAMPLE + "<CALL:5>YU2AB")
    assert parse_adi(SKILL_EXAMPLE + "<CALL:5>YU2AB") == (doc.records, doc.warnings)
    assert len(doc.records) == 2 and len(doc.warnings) == 1


def test_document_defaults():
    doc = AdifDocument()
    assert (doc.header, doc.records, doc.warnings) == ({}, [], [])


def test_tags_case_insensitive_and_names_uppercased():
    doc = parse_document("<adif_ver:5>3.1.4 <eoh>\n<call:5>YU1AB <Qso_Date:8>20260915 <eor>")
    assert doc.header == {"ADIF_VER": "3.1.4"}
    assert doc.records == [{"CALL": "YU1AB", "QSO_DATE": "20260915"}]
    assert doc.warnings == []


def test_type_indicator_is_ignored():
    text = "<FREQ:9:N>14.075123 <CALL:5:s>YU1AB <QSO_DATE:8:D>20260915 <X:1:>y <Y:2:Number>42 <EOR>"
    rec = one(text)
    assert rec == {
        "FREQ": "14.075123",
        "CALL": "YU1AB",
        "QSO_DATE": "20260915",
        "X": "y",
        "Y": "42",
    }


def test_leading_zeros_in_lengths_are_accepted():
    zeros = "0" * 30
    rec = one(f"<COMMENT:000> <NOTES:0008>TEMP 24C <CALL:{zeros}5>YU1AB <EOR>")
    assert rec == {"COMMENT": "", "NOTES": "TEMP 24C", "CALL": "YU1AB"}


def test_application_and_user_field_names():
    rec = one("<APP_N1MM_ID:3>abc <APP_WSJT-X_X:1>1 <USERDEF1:2>ok <EOR>")
    assert rec == {"APP_N1MM_ID": "abc", "APP_WSJT-X_X": "1", "USERDEF1": "ok"}


def test_values_are_stripped_and_keep_inner_whitespace():
    rec = one("<NAME:11>  Marko  M. <COMMENT:6>\tok\r\n <EOR>")
    assert rec == {"NAME": "Marko  M.", "COMMENT": "ok"}


def test_zero_length_fields_are_kept_empty():
    doc = parse_document("<CALL:5>YU1AB <GRIDSQUARE:0> <NAME:0><EOR>")
    assert doc.records == [{"CALL": "YU1AB", "GRIDSQUARE": "", "NAME": ""}]
    assert doc.warnings == []


@pytest.mark.parametrize(
    "value", ["a<b", "<", ">", "1 < 2 > 0", "<b>", "<EOR>", "<CALL:5>YU1AB", "x <eoh> y"]
)
def test_lt_inside_value_of_correct_length_is_kept(value):
    text = f"<EOH><COMMENT:{len(value)}>{value} <CALL:5>YU1AB <EOR><CALL:5>YU2AB <EOR>"
    doc = parse_document(text)
    assert doc.records == [{"COMMENT": value, "CALL": "YU1AB"}, {"CALL": "YU2AB"}]
    assert doc.warnings == []


def test_text_between_records_is_ignored():
    text = "junk <CALL:5>YU1AB <EOR> comment: next QSO\n\n<CALL:5>YU2AB <EOR> trailing text"
    doc = parse_document(text)
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB"}]
    assert doc.warnings == []


def test_text_right_after_a_value_is_ignored_with_a_warning():
    doc = parse_document("<CALL:5>YU1AB this is not ADIF <BAND:3>20m <EOR>")
    assert doc.records == [{"CALL": "YU1AB", "BAND": "20m"}]
    assert doc.warnings == [
        "Record 1: length of field CALL does not match its data, "
        "this or the next field may be wrong"
    ]


def test_lotw_comment_after_a_value_is_not_a_length_problem():
    text = (
        "<MY_STATE:2>CO // Colorado\n<CALL:5>KD4LV  // LotW ADIF includes comments\n"
        "<QSLRDATE:8>20150602 // A comment ending a record\n<eor>\n"
    )
    doc = parse_document(text)
    assert doc.records == [{"MY_STATE": "CO", "CALL": "KD4LV", "QSLRDATE": "20150602"}]
    assert doc.warnings == []


def test_slashes_right_after_a_value_still_warn():
    doc = parse_document("<COMMENT:5>http://example.com <EOR>")
    assert doc.records == [{"COMMENT": "http:"}]
    assert len(doc.warnings) == 1 and "COMMENT" in doc.warnings[0]


def test_application_marker_without_length_is_ignored():
    doc = parse_document("<CALL:5>YU1AB\n<eor>\n\n<APP_LoTW_EOF>\n")
    assert doc.records == [{"CALL": "YU1AB"}]
    assert doc.warnings == []


def test_crlf_between_tags_and_inside_values():
    text = "Log\r\n<ADIF_VER:5>3.1.4\r\n<EOH>\r\n<CALL:5>YU1AB\r\n<NOTES:6>ab\r\ncd\r\n<EOR>\r\n"
    doc = parse_document(text)
    assert doc.header == {"ADIF_VER": "3.1.4"}
    assert doc.records == [{"CALL": "YU1AB", "NOTES": "ab\r\ncd"}]
    assert doc.warnings == []


def test_missing_eoh_means_the_whole_file_is_records():
    doc = parse_document("<CALL:5>YU1AB <EOR>\n<CALL:5>YU2AB <EOR>\n")
    assert doc.header == {}
    assert [r["CALL"] for r in doc.records] == ["YU1AB", "YU2AB"]
    assert doc.warnings == []


@pytest.mark.parametrize(
    "text",
    [
        "<EOH><CALL:5>YU1AB <EOR>",
        "WSJT-X ADIF Export<eoh>\n<call:5>YU1AB <eor>",
        "Created by <b>me</b> & 1 < 2 > 0 <http://x.y>\n<EOH>\n<CALL:5>YU1AB <EOR>",
        "﻿Header text\n<EOH:0>\n<CALL:5>YU1AB <EOR>",
    ],
)
def test_header_without_fields_and_free_text(text):
    doc = parse_document(text)
    assert doc.header == {}
    assert doc.records == [{"CALL": "YU1AB"}]
    assert doc.warnings == []


def test_header_fields_after_free_text_with_lt():
    text = (
        "Log <created by> HamQ < 5 >\n<ADIF_VER:5>3.1.4 <PROGRAMID:4>HamQ "
        "<CREATED_TIMESTAMP:15>20260929 101500\n<EOH>\n<CALL:5>YU1AB <EOR>"
    )
    doc = parse_document(text)
    assert doc.header == {
        "ADIF_VER": "3.1.4",
        "PROGRAMID": "HamQ",
        "CREATED_TIMESTAMP": "20260929 101500",
    }
    assert doc.warnings == []


def test_last_record_without_eor_is_kept_with_a_warning():
    doc = parse_document("<EOH><CALL:5>YU1AB <EOR>\n<CALL:5>YU2AB <BAND:3>20m\n")
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB", "BAND": "20m"}]
    assert doc.warnings == ["Record 2: missing <EOR> at the end of the file, record kept"]


def test_empty_records_are_skipped():
    doc = parse_document("<EOR> <EOR><CALL:5>YU1AB <EOR><eor>")
    assert doc.records == [{"CALL": "YU1AB"}]
    assert doc.warnings == []


@pytest.mark.parametrize(
    "text", ["", "   \n", "no tags at all", "<<<>>>", "<EOH>", "<EOR>", "<", "<CALL:", "<CALL:5"]
)
def test_empty_or_garbage_input(text):
    doc = parse_document(text)
    assert doc.records == []
    assert doc.header == {}


def test_parse_document_accepts_bytes_and_none():
    assert parse_document(b"\xef\xbb\xbf<CALL:5>YU1AB <EOR>").records == [{"CALL": "YU1AB"}]
    assert parse_document(None) == AdifDocument()


def test_duplicate_field_last_wins_with_warning():
    doc = parse_document("<CALL:5>YU1AB <BAND:3>20m <call:5>YU2AB <EOR>")
    assert doc.records == [{"CALL": "YU2AB", "BAND": "20m"}]
    assert doc.warnings == ["Record 1: duplicate field CALL, the last value is used"]


def test_duplicate_header_field_last_wins_with_warning():
    doc = parse_document("<PROGRAMID:1>A <PROGRAMID:1>B <EOH><CALL:5>YU1AB <EOR>")
    assert doc.header == {"PROGRAMID": "B"}
    assert doc.warnings == ["Header: duplicate field PROGRAMID, the last value is used"]


def test_tag_without_length_is_skipped_with_a_warning():
    doc = parse_document("<CALL:5>YU1AB <NAME>Marko <EOR>")
    assert doc.records == [{"CALL": "YU1AB"}]
    assert doc.warnings == ["Record 1: field NAME has no length, skipped"]


@pytest.mark.parametrize("tag", ["<NAME:x>", "<NAME:-5>", "<NAME:5x>", "<NAME: 5>"])
def test_invalid_tag_is_skipped_with_a_warning(tag):
    doc = parse_document(f"<CALL:5>YU1AB <EOR>\n<CALL:5>YU2AB {tag}Marko <EOR>")
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB"}]
    assert doc.warnings == [f"Record 2: invalid tag {tag} skipped"]


def test_joined_files_second_header_goes_to_the_header():
    first = format_document([{"CALL": "YU1AB"}], header={"PROGRAMID": "First"})
    second = format_document(
        [{"CALL": "YU2AB"}], header={"PROGRAMID": "Second", "PROGRAMVERSION": "2"}
    )
    doc = parse_document(first + second)
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB"}]
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "First", "PROGRAMVERSION": "2"}
    assert doc.warnings == [
        "Record 2: another <EOH> found (joined files?), the fields before it were treated as header"
    ]


def test_records_before_the_first_eoh_are_not_lost():
    second = format_document([{"CALL": "YU2AB"}], header={"PROGRAMID": "Second"})
    doc = parse_document("<CALL:5>YU1AB <EOR>\n" + second)
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB"}]
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "Second"}
    assert len(doc.warnings) == 1 and "<EOH>" in doc.warnings[0]


def test_warnings_are_translated(monkeypatch):
    monkeypatch.setattr(adif, "tr", lambda text: "T:" + text)
    doc = parse_document("<PROGRAMID:9>A<EOH><CALL:5>YU1AB <CALL:5>YU2AB <X>")
    assert len(doc.warnings) == 4
    assert all(w.startswith("T:") for w in doc.warnings)


def test_warnings_are_capped():
    doc = parse_document("<CALL:1>A <CALL:1>B <EOR>\n" * 150)
    assert len(doc.records) == 150
    assert len(doc.warnings) == 101
    assert doc.warnings[99] == "Record 100: duplicate field CALL, the last value is used"
    assert doc.warnings[-1] == "Further warnings not listed: 50"


# --- wrong lengths ------------------------------------------------------------------------

MISMATCH = "length of field {field} does not match its data, this or the next field may be wrong"
BYTES = "length of field {field} is given in bytes instead of characters, value corrected"


def test_too_short_length_warns_and_later_fields_parse():
    doc = parse_document("<CALL:5>YU1AB <NAME:4>Aleksandar <QTH:7>Beograd <EOR><CALL:5>YU2AB <EOR>")
    assert doc.records == [{"CALL": "YU1AB", "NAME": "Alek", "QTH": "Beograd"}, {"CALL": "YU2AB"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_too_long_length_warns_and_later_records_parse():
    text = "<CALL:5>YU1AB <NAME:10>Ivan <QTH:7>Beograd <EOR>\n<CALL:5>YU2AB <QTH:3>Niš <EOR>"
    doc = parse_document(text)
    assert doc.records == [{"CALL": "YU1AB", "NAME": "Ivan <QTH:"}, {"CALL": "YU2AB", "QTH": "Niš"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_length_past_the_end_takes_what_is_there():
    doc = parse_document("<EOH><CALL:5>YU1AB <EOR><CALL:5>YU2AB <COMMENT:50>truncated\n")
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB", "COMMENT": "truncated"}]
    assert doc.warnings == [
        "Record 2: field COMMENT runs past the end of the file, value may be incomplete",
        "Record 2: missing <EOR> at the end of the file, record kept",
    ]


@pytest.mark.parametrize("digits", ["99999999999999999999", "9" * 5000])
def test_huge_length_does_not_crash(digits):
    doc = parse_document(f"<CALL:5>YU1AB <EOR><COMMENT:{digits}>x <EOR>")
    assert doc.records == [{"CALL": "YU1AB"}, {"COMMENT": "x <EOR>"}]
    assert len(doc.warnings) == 2


def test_header_value_running_into_eoh():
    doc = parse_document("<ADIF_VER:5>3.1.4 <PROGRAMID:10>WSJT-X<EOH>\n<CALL:5>YU1AB <EOR>")
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "WSJT-X"}
    assert doc.records == [{"CALL": "YU1AB"}]
    assert doc.warnings == [
        "Header: field PROGRAMID runs past the end of the header, value may be incomplete"
    ]


def test_header_length_mismatch_and_byte_count():
    doc = parse_document("<PROGRAMID:2>WSJT-X <MY_NAME:7>Đorđe <EOH><CALL:5>YU1AB <EOR>")
    assert doc.header == {"PROGRAMID": "WS", "MY_NAME": "Đorđe"}
    assert doc.warnings == [
        "Header: length of field PROGRAMID does not match its data, the value may be wrong",
        "Header: " + BYTES.format(field="MY_NAME"),
    ]


def test_random_garbage_never_raises():
    rng = random.Random(2026)
    alphabet = "<>:EORHCAL0123456789 \n\r\tĐđš<:>_x"
    pieces = ["<EOR>", "<EOH>", "<CALL:5>", "<NAME:7>", "<X:0>", "<A:99>", "Đorđe", "YU1AB "]
    for _ in range(400):
        parts = []
        for _ in range(rng.randrange(30)):
            if rng.random() < 0.4:
                parts.append(rng.choice(pieces))
            else:
                parts.append("".join(rng.choice(alphabet) for _ in range(rng.randrange(8))))
        doc = parse_document("".join(parts))
        assert isinstance(doc.records, list) and isinstance(doc.warnings, list)
        for record in doc.records:
            assert record and all(k == k.upper() and v == v.strip() for k, v in record.items())


# --- narrow byte-count tolerance extension ------------------------------------------------


@pytest.mark.parametrize("sep", [" ", "", "\n", "\r\n", "  "])
@pytest.mark.parametrize("value", SERBIAN_VALUES)
def test_serbian_value_counted_in_characters(value, sep):
    text = f"<NAME:{len(value)}>{value}{sep}<QTH:7>Beograd{sep}<EOR>"
    doc = parse_document(text)
    assert doc.records == [{"NAME": value, "QTH": "Beograd"}]
    assert doc.warnings == []


@pytest.mark.parametrize("sep", [" ", "", "\n", "\r\n", "  "])
@pytest.mark.parametrize("value", SERBIAN_VALUES)
def test_serbian_value_counted_in_bytes(value, sep):
    size = len(value.encode("utf-8"))
    doc = parse_document(f"<NAME:{size}>{value}{sep}<QTH:7>Beograd{sep}<EOR>")
    assert doc.records == [{"NAME": value, "QTH": "Beograd"}]
    extra = size - len(value)
    if extra > len(sep):  # the character reading swallows the next tag
        assert doc.warnings == ["Record 1: " + BYTES.format(field="NAME")]
    else:  # the extra bytes only covered whitespace: value is right, nothing to report
        assert doc.warnings == []


@pytest.mark.parametrize("value", SERBIAN_VALUES)
def test_byte_counted_last_field_before_eor_and_at_end_of_file(value):
    size = len(value.encode("utf-8"))
    doc = parse_document(f"<CALL:5>YU1AB <NAME:{size}>{value}<EOR><CALL:5>YU2AB <EOR>")
    assert doc.records == [{"CALL": "YU1AB", "NAME": value}, {"CALL": "YU2AB"}]
    assert doc.warnings == ["Record 1: " + BYTES.format(field="NAME")]
    doc = parse_document(f"<CALL:5>YU1AB <NAME:{size}>{value}")
    assert doc.records == [{"CALL": "YU1AB", "NAME": value}]
    assert doc.warnings[0] == "Record 1: " + BYTES.format(field="NAME")


def test_ascii_value_that_is_too_long_is_not_read_as_bytes():
    doc = parse_document("<NAME:6>Ivan<QTH:7>Beograd<EOR>")
    assert doc.records == [{"NAME": "Ivan<Q"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_byte_reading_that_splits_a_character_is_not_used():
    doc = parse_document("<NAME:3>ĐĐ<QTH:7>Beograd<EOR>")
    assert doc.records == [{"NAME": "ĐĐ<"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_byte_reading_must_end_at_a_tag():
    # 6 bytes = 'Đorđ' ends inside the word, so it is not a byte count either
    doc = parse_document("<NAME:6>Đorđe<QTH:7>Beograd<EOR>")
    assert doc.records == [{"NAME": "Đorđe<"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_byte_reading_that_still_swallows_a_tag_is_not_used():
    # declared 12: as characters 'Đorđe <QTH:7' and as bytes 'Đorđe <QTH' both swallow <QTH:7>
    doc = parse_document("<NAME:12>Đorđe <QTH:7>Beograd <EOR>")
    assert doc.records == [{"NAME": "Đorđe <QTH:7"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_byte_reading_containing_a_tag_is_not_used_even_if_it_ends_cleanly():
    # 13 bytes = 'Đorđe <X:0>' is followed by a tag, but it contains <X:0> itself
    doc = parse_document("<NAME:13>Đorđe <X:0> <QTH:7>Beograd <EOR>")
    assert doc.records == [{"NAME": "Đorđe <X:0> <"}]
    assert doc.warnings == ["Record 1: " + MISMATCH.format(field="NAME")]


def test_character_counted_value_with_tag_like_text_is_literal():
    value = "Đorđe says <CALL:5>YU1AB"
    doc = parse_document(f"<COMMENT:{len(value)}>{value} <CALL:5>YU9ZZ <EOR>")
    assert doc.records == [{"COMMENT": value, "CALL": "YU9ZZ"}]
    assert doc.warnings == []


def test_byte_and_character_counted_records_mix_in_one_file():
    text = (
        "<CALL:6>YU1XYZ <NAME:5>Đorđe <EOR>\n"
        "<CALL:6>YU2XYZ <NAME:16>Miloš Šćekić <EOR>\n"
        "<CALL:6>YU3XYZ <NAME:12>Miloš Šćekić <EOR>\n"
    )
    doc = parse_document(text)
    assert [r["NAME"] for r in doc.records] == ["Đorđe", "Miloš Šćekić", "Miloš Šćekić"]
    assert doc.warnings == ["Record 2: " + BYTES.format(field="NAME")]


# --- parse_latlon -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("N044 48.750", 44.8125),
        ("E020 27.672", 20.4612),
        ("S033 52.128", -33.8688),
        ("W077 02.196", -77.0366),
        ("E151 12.558", 151.2093),
        ("n044 48.750", 44.8125),
        ("s033 52.128", -33.8688),
        ("N44 48.750", 44.8125),
        ("N4 30.000", 4.5),
        ("w1 30", -1.5),
        (" N044 48.750 ", 44.8125),
        ("N 044 48.750", 44.8125),
        ("N044  48.750", 44.8125),
        ("N044 48,750", 44.8125),
        ("N044 48", 44.8),
        ("N044 48.", 44.8),
        ("N044 5.5", 44.091666666666667),
        ("N090 00.000", 90.0),
        ("S090 00.000", -90.0),
        ("E180 00.000", 180.0),
        ("W180 00.000", -180.0),
        ("N000 59.999", 0.9999833333333333),
    ],
)
def test_parse_latlon(value, expected):
    result = parse_latlon(value)
    assert result == pytest.approx(expected, abs=1e-9)
    assert round(result, 4) == round(expected, 4)


def test_parse_latlon_zero_is_positive():
    for value in ("S000 00.000", "W000 00.000", "N000 00.000"):
        result = parse_latlon(value)
        assert result == 0.0 and math.copysign(1.0, result) == 1.0


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "044 48.750",
        "X044 48.750",
        "N044",
        "N044 60.000",
        "N044 75.000",
        "N091 00.000",
        "N090 00.001",
        "S090 00.060",
        "E181 00.000",
        "E180 00.060",
        "W180 01.000",
        "N1044 00.000",
        "N044 48.750x",
        "N044 -48.750",
        "N-44 48.750",
        "44.8125",
        "N044.8125",
        "N04448.750",
        "N044 48.750 E",
        "N044 1e1",
        "N٠٤٤ 48.750",
        "garbage",
        44.8125,
    ],
)
def test_parse_latlon_invalid(value):
    assert parse_latlon(value) is None


# --- parse_freq ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14.074", 14.074),
        (" 14.07400 ", 14.074),
        ("14,074", 14.074),
        ("+14.074", 14.074),
        ("14.", 14.0),
        (".5", 0.5),
        ("7", 7.0),
        ("145.500", 145.5),
        ("0.1375", 0.1375),
        ("10368.100", 10368.1),
        (14.074, 14.074),
        (7, 7.0),
    ],
)
def test_parse_freq(value, expected):
    assert parse_freq(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "abc",
        "-14.074",
        "0",
        "0.000",
        "14.074.1",
        "14,074.5",
        "1e3",
        "inf",
        "nan",
        "14.074 MHz",
        "- 14",
        "١٤.٠٧٤",
        True,
        -7.0,
        float("nan"),
        float("inf"),
    ],
)
def test_parse_freq_invalid(value):
    assert parse_freq(value) is None


# --- parse_qso_datetime -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("qso_date", "time_on", "expected"),
    [
        ("20260915", "184500", datetime(2026, 9, 15, 18, 45, 0, tzinfo=timezone.utc)),
        ("20260915", "184512", datetime(2026, 9, 15, 18, 45, 12, tzinfo=timezone.utc)),
        ("20260915", "1845", datetime(2026, 9, 15, 18, 45, 0, tzinfo=timezone.utc)),
        (" 20260915 ", " 0005 ", datetime(2026, 9, 15, 0, 5, 0, tzinfo=timezone.utc)),
        ("20240229", "000000", datetime(2024, 2, 29, 0, 0, 0, tzinfo=timezone.utc)),
        ("19300101", "2359", datetime(1930, 1, 1, 23, 59, 0, tzinfo=timezone.utc)),
        ("20261231", "235959", datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
    ],
)
def test_parse_qso_datetime(qso_date, time_on, expected):
    result = parse_qso_datetime(qso_date, time_on)
    assert result == expected
    assert result.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("qso_date", "time_on"),
    [
        ("20260230", "1200"),
        ("20250229", "1200"),
        ("20261301", "1200"),
        ("20260900", "1200"),
        ("20260915", "2460"),
        ("20260915", "2400"),
        ("20260915", "1860"),
        ("20260915", "184560"),
        ("20260915", "18450"),
        ("20260915", "184"),
        ("2026091A", "1200"),
        ("20260915", "12a0"),
        ("2026-09-15", "1200"),
        ("20260915", "18:45"),
        ("202609150", "1200"),
        ("19291231", "1200"),
        ("00000000", "0000"),
        ("", "1200"),
        ("20260915", ""),
        (None, "1200"),
        ("20260915", None),
        (None, None),
    ],
)
def test_parse_qso_datetime_invalid(qso_date, time_on):
    assert parse_qso_datetime(qso_date, time_on) is None


# --- dedup_key ----------------------------------------------------------------------------


def test_dedup_key_format():
    assert dedup_key("YU1AB", "20260915", "184500", "20m", "FT8") == "YU1AB|202609151845|20m|FT8"


def test_dedup_key_normalizes_and_uses_minute_precision():
    a = dedup_key(" yu1ab ", " 20260915", "184512 ", " 20M ", " ft8")
    b = dedup_key("YU1AB", "20260915", "1845", "20m", "FT8")
    assert a == b == "YU1AB|202609151845|20m|FT8"
    assert dedup_key("YU1AB", "20260915", "1846", "20m", "FT8") != b


def test_dedup_key_tolerates_missing_parts():
    assert dedup_key("YU1AB", "20260915", "1845", None, None) == "YU1AB|202609151845||"
    assert dedup_key("YU1AB", "20260915", "1845", "", "") == "YU1AB|202609151845||"


# --- format_record / format_document ------------------------------------------------------


def test_format_record():
    assert format_record({"call": "YU1AB", "BAND": "20m"}) == "<CALL:5>YU1AB <BAND:3>20m <EOR>\n"


def test_format_record_counts_characters():
    assert format_record({"NAME": "Đorđe Petrović"}) == "<NAME:14>Đorđe Petrović <EOR>\n"


def test_format_record_values():
    fields = {"CALL": "YU1AB", "GRIDSQUARE": "", "FREQ": 14.074, "X": None, "DXCC": 296}
    assert (
        format_record(fields) == "<CALL:5>YU1AB <GRIDSQUARE:0> <FREQ:6>14.074 <DXCC:3>296 <EOR>\n"
    )
    assert format_record({}) == "<EOR>\n"


@pytest.mark.parametrize(
    "name", ["", " ", "CALL SIGN", "A:B", "<X>", "EOR", "eoh", "NAME,", "{X}", "-X", "Đ"]
)
def test_format_record_rejects_invalid_field_names(name):
    with pytest.raises(ValueError):
        format_record({name: "x"})


def test_format_document_default_header():
    text = format_document([{"CALL": "YU1AB"}, {"CALL": "YU2AB", "NAME": "Đorđe"}])
    assert not text.startswith("<")
    lines = text.splitlines()
    assert "<ADIF_VER:5>3.1.4" in lines and "<PROGRAMID:4>HamQ" in lines and "<EOH>" in lines
    assert lines[-2:] == ["<CALL:5>YU1AB <EOR>", "<CALL:5>YU2AB <NAME:5>Đorđe <EOR>"]
    doc = parse_document(text)
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "HamQ"}
    assert doc.records == [{"CALL": "YU1AB"}, {"CALL": "YU2AB", "NAME": "Đorđe"}]
    assert doc.warnings == []


def test_format_document_header_override_and_iterables():
    header = {"programid": "HamQ test", "PROGRAMVERSION": "0.1.0", "X": None}
    doc = parse_document(format_document(iter([{"CALL": "YU1AB"}]), header=header))
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "HamQ test", "PROGRAMVERSION": "0.1.0"}
    assert doc.records == [{"CALL": "YU1AB"}]


def test_format_document_without_records():
    doc = parse_document(format_document([]))
    assert doc == AdifDocument({"ADIF_VER": "3.1.4", "PROGRAMID": "HamQ"}, [], [])


NAME_POOL = [
    "CALL", "QSO_DATE", "TIME_ON", "BAND", "MODE", "SUBMODE", "FREQ", "GRIDSQUARE", "NAME",
    "QTH", "COMMENT", "NOTES", "RST_SENT", "APP_N1MM_ID", "APP_QRZLOG_LOGID", "MY_GRIDSQUARE",
    "LAT", "LON", "X-1", "USERDEF_9",
]  # fmt: skip
ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;/-+#!?()'\"&<>"
    "ČčĆćĐđŠšŽžЉљЊњЂђЋћЏџЖжШшäöüßéèñ€\n\t"
)
TRICKY = [
    "a<b", "<", ">", "<>", "1 < 2 > 0", "<b>", "<EOR>", "<eoh>", "<CALL:5>YU1AB", "Đorđe <EOR>",
    "Miloš Šćekić", "x\r\ny", "<NAME:3>abc and more", "Хвала на вези", "<<EOR>>",
]  # fmt: skip


def random_value(rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(30))).strip()


def test_roundtrip_random_records():
    rng = random.Random(20260929)
    for _ in range(400):
        records = []
        for _ in range(rng.randrange(1, 8)):
            names = rng.sample(NAME_POOL, rng.randrange(1, 10))
            records.append(
                {n: rng.choice(TRICKY) if rng.random() < 0.2 else random_value(rng) for n in names}
            )
        header = {"PROGRAMVERSION": random_value(rng)} if rng.random() < 0.5 else None
        doc = parse_document(format_document(records, header))
        assert doc.records == records
        assert doc.warnings == []
        assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "HamQ", **(header or {})}


# --- fixtures and read_adi ----------------------------------------------------------------


def load(name: str) -> AdifDocument:
    return read_adi(FIXTURES / name)


def calls(doc: AdifDocument) -> list[str | None]:
    return [record.get("CALL") for record in doc.records]


def test_fixture_wsjtx_log():
    doc = load("wsjtx_log.adi")
    assert doc.header == {}
    assert doc.warnings == []
    assert calls(doc) == ["IT9XYZ", "W1XYZ", "KH6XYZ", "9A2XYZ", "YU7XYZ", "OH2XYZ/MM"]
    ft4 = doc.records[3]
    assert (ft4["MODE"], ft4["SUBMODE"], ft4["BAND"], ft4["FREQ"]) == (
        "MFSK",
        "FT4",
        "40m",
        "7.048520",
    )
    assert "SUBMODE" not in doc.records[0] and doc.records[0]["MODE"] == "FT8"
    assert all(r["STATION_CALLSIGN"] == "YU1QQ" for r in doc.records)
    assert all(r["MY_GRIDSQUARE"] == "KN04fs" for r in doc.records)
    assert doc.records[5]["GRIDSQUARE"] == ""


def test_fixture_n1mm():
    doc = load("n1mm.adi")
    assert doc.header == {
        "ADIF_VER": "3.1.0",
        "PROGRAMID": "N1MM Logger+",
        "PROGRAMVERSION": "1.0.10412",
    }
    assert doc.warnings == []
    assert calls(doc) == ["9A5XYZ", "W3XYZ", "IT9XYZ", "YU7XYZ", "EA8XYZ"]
    first = doc.records[0]
    assert (first["BAND"], first["MODE"], first["CONTEST_ID"]) == ("20M", "CW", "CQ-WPX-CW")
    assert first["APP_N1MM_CONTINENT"] == "EU" and first["APP_N1MM_EXCHANGE1"] == ""
    assert all(len(r["APP_N1MM_ID"]) == 32 and "GRIDSQUARE" not in r for r in doc.records)


def test_fixture_log4om():
    doc = load("log4om.adi")
    assert doc.header["PROGRAMID"] == "LOG4OM"
    assert doc.header["CREATED_TIMESTAMP"] == "20260920 083015"
    assert doc.warnings == []
    assert calls(doc) == ["VK2XYZ", "9A3XYZ", "KH6XYZ", "YU7XYZ", "IT9XYZ"]
    vk = doc.records[0]
    assert (vk["DXCC"], vk["COUNTRY"], vk["CONT"], vk["CQZ"], vk["ITUZ"]) == (
        "150", "Australia", "OC", "30", "59",
    )  # fmt: skip
    assert round(parse_latlon(vk["LAT"]), 4) == -33.8688
    assert round(parse_latlon(vk["LON"]), 4) == 151.2093
    assert round(parse_latlon(vk["MY_LAT"]), 4) == 44.8125
    assert round(parse_latlon(vk["MY_LON"]), 4) == 20.4612
    assert doc.records[1]["GRIDSQUARE"] == "JN75xt74oj"
    assert all(r["MY_GRIDSQUARE"] == "KN04fs" for r in doc.records)


def test_fixture_qrz_export():
    doc = load("qrz_export.adi")
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "QRZLogbook", "PROGRAMVERSION": "2.0"}
    assert doc.warnings == []
    assert calls(doc) == ["VK3XYZ", "W5XYZ", "DL1XYZ"]
    vk = doc.records[0]
    assert vk["APP_QRZLOG_LOGID"] == "987654321" and vk["EMAIL"] == ""
    assert parse_qso_datetime(vk["QSO_DATE"], vk["TIME_ON"]) == datetime(
        2026, 9, 15, 6, 16, tzinfo=timezone.utc
    )
    assert doc.records[2]["GRIDSQUARE"] == "JO6"


def test_fixture_utf8_names_counted_in_characters_and_bytes():
    chars = load("utf8_name.adi")
    raw = load("utf8_bytes.adi")
    assert chars.warnings == []
    assert [r["NAME"] for r in chars.records] == ["Đorđe", "Đorđe Petrović", "Miloš Šćekić"]
    assert [r["QTH"] for r in chars.records] == ["Niš", "Čačak", "Užice"]
    assert chars.records[1]["COMMENT"] == "Хвала на вези"
    assert raw.records == chars.records
    assert raw.header == chars.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "TestLog"}
    assert raw.warnings == [
        "Record 1: " + BYTES.format(field="NAME"),
        "Record 2: " + BYTES.format(field="NAME"),
        "Record 2: " + BYTES.format(field="QTH"),
        "Record 2: " + BYTES.format(field="COMMENT"),
        "Record 3: " + BYTES.format(field="NAME"),
    ]


def test_fixture_lotw():
    doc = load("lotw.adi")
    assert doc.header == {
        "PROGRAMID": "LoTW",
        "APP_LOTW_LASTQSL": "2026-09-20 17:45:03",
        "APP_LOTW_NUMREC": "3",
    }
    assert doc.warnings == []
    assert calls(doc) == ["W1XYZ", "9A2XYZ", "VK2XYZ"]
    first = doc.records[0]
    assert (first["STATE"], first["ITUZ"], first["DXCC"]) == ("MA", "08", "291")
    assert first["APP_LOTW_RXQSL"] == "2026-09-20 17:45:03"


def test_fixture_lotw_confirms_wsjtx_qsos_with_the_same_dedup_key():
    def key(r):
        mode = display_mode(r.get("MODE"), r.get("SUBMODE"))
        return dedup_key(r["CALL"], r["QSO_DATE"], r["TIME_ON"], r["BAND"], mode)

    lotw = load("lotw.adi").records
    wsjtx = load("wsjtx_log.adi").records
    assert key(lotw[0]) == key(wsjtx[1]) == "W1XYZ|202609121801|20m|FT8"
    assert key(lotw[1]) == key(wsjtx[3]) == "9A2XYZ|202609131920|40m|FT4"


def test_fixture_no_eoh():
    doc = load("no_eoh.adi")
    assert doc.header == {} and doc.warnings == []
    assert calls(doc) == ["YU1XYZ", None, "YT1XYZ", "YU1XYZ", "YU1XYZ"]
    bad_date, bad_time = doc.records[2], doc.records[3]
    assert parse_qso_datetime(bad_date["QSO_DATE"], bad_date["TIME_ON"]) is None
    assert parse_qso_datetime(bad_time["QSO_DATE"], bad_time["TIME_ON"]) is None
    assert doc.records[4]["GRIDSQUARE"] == "ZZ00"


def test_fixture_missing_eor():
    doc = load("missing_eor.adi")
    assert doc.header == {"ADIF_VER": "3.1.4"}
    assert calls(doc) == ["YU1XYZ", "9A2XYZ", "W1XYZ"]
    assert doc.records[2]["MODE"] == "SSB"
    assert doc.warnings == ["Record 3: missing <EOR> at the end of the file, record kept"]


def test_fixture_wrong_length():
    doc = load("wrong_length.adi")
    assert calls(doc) == ["YU1XYZ", "YU2XYZ", "YU3XYZ", "9A4XYZ", "W2XYZ"]
    assert (doc.records[1]["NAME"], doc.records[1]["QTH"]) == ("Alek", "Kragujevac")
    assert doc.records[2]["NAME"] == "Ivan <QTH:" and "QTH" not in doc.records[2]
    assert doc.records[3] == {
        "CALL": "9A4XYZ",
        "QSO_DATE": "20260810",
        "TIME_ON": "0815",
        "BAND": "40m",
        "MODE": "CW",
        "NAME": "Tomislav",
    }
    assert doc.warnings == [
        "Record 2: " + MISMATCH.format(field="NAME"),
        "Record 3: " + MISMATCH.format(field="NAME"),
    ]


def test_fixture_latin1():
    raw = (FIXTURES / "latin1.adi").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    doc = load("latin1.adi")
    assert [r["NAME"] for r in doc.records] == ["Jürgen Müller", "José Muñoz", "François"]
    assert [r["QTH"] for r in doc.records] == ["München", "Alcalá de Henares", "Besançon"]
    assert doc.warnings == ["File is not valid UTF-8, it was read as Latin-1 (ISO 8859-1)"]


def test_fixture_bom_crlf():
    raw = (FIXTURES / "bom_crlf.adi").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\r\n") >= 6
    doc = load("bom_crlf.adi")
    assert doc.header == {"ADIF_VER": "3.1.4", "PROGRAMID": "WinLog"}
    assert doc.warnings == []
    assert calls(doc) == ["YU1XYZ", "9A1XYZ"]
    assert doc.records[0]["NAME"] == "Željko"
    assert doc.records[0]["NOTES"] == "Prva veza na 6m.\r\nQSL preko biroa."


@pytest.mark.parametrize("name", sorted(path.name for path in FIXTURES.glob("*.adi")))
def test_every_fixture_parses_and_is_documented(name):
    assert load(name).records
    assert f"`{name}`" in (FIXTURES / "README.md").read_text(encoding="utf-8")


def test_read_adi_accepts_str_and_path():
    assert read_adi(str(FIXTURES / "n1mm.adi")) == read_adi(FIXTURES / "n1mm.adi")


def test_read_adi_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        read_adi(tmp_path / "missing.adi")


def test_read_adi_roundtrip_through_a_file(tmp_path):
    records = [{"CALL": "YU1AB", "NAME": "Đorđe Petrović"}, {"CALL": "9A1AA", "COMMENT": "a<b"}]
    path = tmp_path / "out.adi"
    path.write_bytes(format_document(records).encode("utf-8"))
    doc = read_adi(path)
    assert doc.records == records and doc.warnings == []


# --- translations -------------------------------------------------------------------------


def translated_literals() -> list[str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("tr", "tr_noop"):
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
            assert isinstance(node.args[0].value, str)
            found.append(node.args[0].value)
    return found


def test_catalog_has_every_message_and_is_formatted():
    raw = CATALOG.read_text(encoding="utf-8")
    catalog = json.loads(raw)
    literals = translated_literals()
    assert literals and len(literals) == len(set(literals))
    assert set(literals) == set(catalog)
    assert raw == json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for english, serbian in catalog.items():
        assert serbian and serbian != english
        placeholders = sorted(re.findall(r"\{(\w*)\}", english))
        assert sorted(re.findall(r"\{(\w*)\}", serbian)) == placeholders
        assert re.findall(r"<\w+>", serbian) == re.findall(r"<\w+>", english)


def test_shared_messages_agree_with_other_catalogs():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for other in sorted(CATALOG.parent.glob("*.json")):
        if other != CATALOG:
            data = json.loads(other.read_text(encoding="utf-8"))
            for key in set(data) & set(catalog):
                assert data[key] == catalog[key], (other.name, key)


# --- performance --------------------------------------------------------------------------


@pytest.mark.slow
def test_parse_10000_records_is_fast():
    records = []
    for i in range(10000):
        records.append(
            {
                "CALL": f"YU{i % 10}X{i:05d}",
                "QSO_DATE": "20260915",
                "TIME_ON": f"{(i // 60) % 24:02d}{i % 60:02d}00",
                "BAND": "20m",
                "MODE": "MFSK",
                "SUBMODE": "FT4",
                "FREQ": "14.081500",
                "RST_SENT": "-10",
                "RST_RCVD": "-12",
                "GRIDSQUARE": "KN04fs",
                "MY_GRIDSQUARE": "JN95wg",
                "STATION_CALLSIGN": "YU1QQ",
                "NAME": "Đorđe Petrović" if i % 3 == 0 else "John",
                "COMMENT": "tnx 73 <3",
            }
        )
    text = format_document(records)
    start = time.perf_counter()
    doc = parse_document(text)
    elapsed = time.perf_counter() - start
    assert len(doc.records) == 10000 and doc.warnings == []
    assert doc.records[1234] == records[1234]
    assert elapsed < 2.0, f"parsing 10 000 records took {elapsed:.2f} s"
