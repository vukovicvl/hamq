"""Tests for hamq.core.hamlib: rigctld / rotctld extended protocol helpers."""

from __future__ import annotations

import ast
import json
import math
import random
import re
from pathlib import Path

import pytest

from hamq.core import hamlib
from hamq.core.hamlib import (
    MODES,
    RIG_DEFAULT_PORT,
    ROT_DEFAULT_PORT,
    HamlibResponse,
    ResponseParser,
    cmd_get_freq,
    cmd_get_mode,
    cmd_get_pos,
    cmd_set_freq,
    cmd_set_mode,
    cmd_set_pos,
    cmd_stop,
    error_message,
    expected_command,
    parse_freq,
    parse_mode,
    parse_pos,
    rotator_target,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "hamq" / "i18n" / "sr_Latn" / "core_hamlib.json"


def R(command: str, args: str, fields: dict, rprt: int) -> HamlibResponse:
    return HamlibResponse(command=command, args=args, fields=fields, rprt=rprt)


# Real replies listed in docs/ARCHITECTURE.md (captured from rigctld -m 1 / rotctld -m 1).
CAPTURED = [
    (
        "+f",
        "get_freq:\nFrequency: 14074000\nRPRT 0\n",
        [R("get_freq", "", {"Frequency": "14074000"}, 0)],
    ),
    (
        "+m",
        "get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n",
        [R("get_mode", "", {"Mode": "USB", "Passband": "2400"}, 0)],
    ),
    ("+F 14074000", "set_freq: 14074000\nRPRT 0\n", [R("set_freq", "14074000", {}, 0)]),
    ("+M USB 0", "set_mode: USB 0\nRPRT 0\n", [R("set_mode", "USB 0", {}, 0)]),
    ("+t", "get_ptt:\nRPRT -11\n", [R("get_ptt", "", {}, -11)]),
    (
        "+p",
        "get_pos:\nAzimuth: 123.00\nElevation: 0.00\nRPRT 0\n",
        [R("get_pos", "", {"Azimuth": "123.00", "Elevation": "0.00"}, 0)],
    ),
    ("+P 123.5 10", "set_pos: 123.5 10\nRPRT 0\n", [R("set_pos", "123.5 10", {}, 0)]),
    ("+S", "stop:\nRPRT 0\n", [R("stop", "", {}, 0)]),
    ("+X_bogus", "", []),
    (
        "+F abc",
        "set_split_mode: bogus +F\nRPRT -1\nRPRT -18\nRPRT -1\n",
        [
            R("set_split_mode", "bogus +F", {}, -1),
            R("", "", {}, -18),
            R("", "", {}, -1),
        ],
    ),
]

# More replies captured from the hamq/hamlib-dummy image (Hamlib 4.6.2) for this module.
CAPTURED_EXTRA = [
    (
        "get_mode:\nMode: FM-D\nPassband: 15000\nRPRT 0\n",
        [R("get_mode", "", {"Mode": "FM-D", "Passband": "15000"}, 0)],
    ),
    ("set_freq: abc\nRPRT -1\n", [R("set_freq", "abc", {}, -1)]),
    ("set_pos: 500 0\nRPRT -21\n", [R("set_pos", "500 0", {}, -21)]),
    # '+M ?' lists the modes on a line that is neither a field nor RPRT: ignored.
    (
        "set_mode: ?\nAM CW USB LSB RTTY FM WFM CWR RTTYR\nRPRT 0\n",
        [R("set_mode", "?", {}, 0)],
    ),
    (
        "get_pos:\nAzimuth: 6.01\nElevation: 5.00\nRPRT 0\n",
        [R("get_pos", "", {"Azimuth": "6.01", "Elevation": "5.00"}, 0)],
    ),
]

ALL_CASES = [(text, expected) for _, text, expected in CAPTURED] + CAPTURED_EXTRA
CASE_IDS = [repr(text[:24]) for text, _ in ALL_CASES]
# Captured replies to the commands HamQ itself builds (not the bogus / PTT examples).
BUILDER_CASES = [c for c in CAPTURED if c[0] not in ("+X_bogus", "+F abc", "+t")]


def feed_all(parser: ResponseParser, chunks) -> list[HamlibResponse]:
    out: list[HamlibResponse] = []
    for chunk in chunks:
        out.extend(parser.feed(chunk))
    return out


# --------------------------------------------------------------------------- parser


class TestResponseParserCaptured:
    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_whole_str(self, text, expected):
        assert ResponseParser().feed(text) == expected

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_whole_bytes(self, text, expected):
        assert ResponseParser().feed(text.encode("utf-8")) == expected

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_byte_by_byte(self, text, expected):
        data = text.encode("utf-8")
        chunks = [data[i : i + 1] for i in range(len(data))]
        assert feed_all(ResponseParser(), chunks) == expected

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_char_by_char_str(self, text, expected):
        assert feed_all(ResponseParser(), list(text)) == expected

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_every_split_point(self, text, expected):
        data = text.encode("utf-8")
        for i in range(len(data) + 1):
            assert feed_all(ResponseParser(), [data[:i], data[i:]]) == expected, i

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_crlf(self, text, expected):
        crlf = text.replace("\n", "\r\n").encode("utf-8")
        assert ResponseParser().feed(crlf) == expected
        chunks = [crlf[i : i + 1] for i in range(len(crlf))]
        assert feed_all(ResponseParser(), chunks) == expected

    @pytest.mark.parametrize(("text", "expected"), ALL_CASES, ids=CASE_IDS)
    def test_blank_lines_and_padding(self, text, expected):
        padded = "\n\n" + text.replace("\n", "  \n\n \t\n")
        assert ResponseParser().feed(padded) == expected

    def test_all_concatenated(self):
        text = "".join(t for t, _ in ALL_CASES)
        expected = [r for _, exp in ALL_CASES for r in exp]
        assert ResponseParser().feed(text) == expected
        data = text.encode("utf-8")
        chunks = [data[i : i + 1] for i in range(len(data))]
        assert feed_all(ResponseParser(), chunks) == expected

    def test_all_concatenated_random_chunks(self):
        text = "".join(t for t, _ in ALL_CASES).replace("\n", "\r\n")
        expected = [r for _, exp in ALL_CASES for r in exp]
        data = text.encode("utf-8")
        rng = random.Random(4532)
        for _ in range(50):
            chunks, pos = [], 0
            while pos < len(data):
                size = rng.randint(1, 40)
                chunks.append(data[pos : pos + size])
                pos += size
            assert feed_all(ResponseParser(), chunks) == expected

    def test_one_parser_reused_for_a_session(self):
        parser = ResponseParser()
        for _, text, expected in CAPTURED:
            assert parser.feed(text) == expected

    @pytest.mark.parametrize(
        ("cmd", "text", "expected"), BUILDER_CASES, ids=[c for c, _, _ in BUILDER_CASES]
    )
    def test_header_matches_expected_command(self, cmd, text, expected):
        (resp,) = ResponseParser().feed(text)
        assert resp.command == expected[0].command == expected_command(cmd + "\n")


class TestResponseParserStructure:
    def test_stray_rprt_without_header(self):
        assert ResponseParser().feed("RPRT -11\n") == [R("", "", {}, -11)]

    def test_fields_without_header(self):
        parser = ResponseParser()
        assert parser.feed("Frequency: 7074000\nRPRT 0\n") == [
            R("", "", {"Frequency": "7074000"}, 0)
        ]

    def test_header_without_rprt_is_dropped_by_next_header(self):
        # rigctld prints the header but no RPRT when the rig returns -RIG_EIO.
        text = "get_freq:\nget_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n"
        assert ResponseParser().feed(text) == [
            R("get_mode", "", {"Mode": "USB", "Passband": "2400"}, 0)
        ]

    def test_unfinished_fields_are_not_mixed_into_next_response(self):
        text = "get_freq:\nFrequency: 1\nget_pos:\nAzimuth: 10.00\nElevation: 0.00\nRPRT 0\n"
        (resp,) = ResponseParser().feed(text)
        assert resp == R("get_pos", "", {"Azimuth": "10.00", "Elevation": "0.00"}, 0)

    def test_mixed_sequence(self):
        text = (
            "RPRT -1\n"
            "get_freq:\nFrequency: 14074000\nRPRT 0\n"
            "garbage without colon\n"
            "14074000\n"
            "RPRT -18\n"
            "stop:\nRPRT 0\n"
            "set_pos: 30 5\nRPRT -21\n"
        )
        assert ResponseParser().feed(text) == [
            R("", "", {}, -1),
            R("get_freq", "", {"Frequency": "14074000"}, 0),
            R("", "", {}, -18),
            R("stop", "", {}, 0),
            R("set_pos", "30 5", {}, -21),
        ]

    def test_partial_state_kept_between_feeds(self):
        parser = ResponseParser()
        assert parser.feed("get_mode:\nMode: U") == []
        assert parser.feed("SB\nPass") == []
        assert parser.feed(b"band: 2400\nRPRT") == []
        assert parser.feed(" 0") == []
        assert parser.feed("\n") == [R("get_mode", "", {"Mode": "USB", "Passband": "2400"}, 0)]

    def test_empty_feed(self):
        parser = ResponseParser()
        assert parser.feed(b"") == []
        assert parser.feed("") == []

    def test_accepts_bytearray_and_memoryview(self):
        data = b"stop:\nRPRT 0\n"
        assert ResponseParser().feed(bytearray(data)) == [R("stop", "", {}, 0)]
        assert ResponseParser().feed(memoryview(data)) == [R("stop", "", {}, 0)]

    def test_rejects_non_text_types(self):
        with pytest.raises(TypeError):
            ResponseParser().feed(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ResponseParser().feed(5)  # type: ignore[arg-type]

    def test_field_value_keeps_inner_colons_and_empty_values(self):
        text = "get_info:\nInfo: Dummy rotator: v1.0\nMode:\nRPRT 0\n"
        (resp,) = ResponseParser().feed(text)
        assert resp.fields == {"Info": "Dummy rotator: v1.0", "Mode": ""}

    def test_field_keys_with_spaces_and_tabs(self):
        text = "dump_caps:\nCaps dump for model:\t1\nMin Azimuth:\t\t-180.00\nRPRT 0\n"
        (resp,) = ResponseParser().feed(text)
        assert resp.fields == {"Caps dump for model": "1", "Min Azimuth": "-180.00"}

    @pytest.mark.parametrize(
        ("line", "rprt"),
        [("RPRT 0", 0), ("RPRT -11", -11), ("  RPRT -1  ", -1), ("RPRT\t-21", -21), ("RPRT +0", 0)],
    )
    def test_rprt_variants(self, line, rprt):
        assert ResponseParser().feed(line + "\r\n") == [R("", "", {}, rprt)]

    @pytest.mark.parametrize(
        "line", ["RPRT", "RPRT abc", "RPRT 1 2", "RPRT0", "rprt 0", "RPRT 1.5"]
    )
    def test_malformed_rprt_is_ignored(self, line):
        assert ResponseParser().feed(line + "\n") == []

    def test_malformed_rprt_does_not_end_response(self):
        text = "get_freq:\nFrequency: 7\nRPRT x\nRPRT 0\n"
        assert ResponseParser().feed(text) == [R("get_freq", "", {"Frequency": "7"}, 0)]

    def test_responses_do_not_share_field_dicts(self):
        text = "get_freq:\nFrequency: 1\nRPRT 0\nget_freq:\nFrequency: 2\nRPRT 0\n"
        first, second = ResponseParser().feed(text)
        first.fields["Frequency"] = "changed"
        assert second.fields == {"Frequency": "2"}

    def test_ok_property(self):
        assert R("get_freq", "", {}, 0).ok is True
        assert R("get_ptt", "", {}, -11).ok is False
        assert R("", "", {}, 1).ok is False


class TestResponseParserRobustness:
    def test_utf8_split_across_feeds(self):
        data = "get_info:\nInfo: Đorđe ćirilica Ђ\nRPRT 0\n".encode()
        start = data.index("Đ".encode())
        chunks = [data[: start + 1], data[start + 1 :]]
        (resp,) = feed_all(ResponseParser(), chunks)
        assert resp.fields["Info"] == "Đorđe ćirilica Ђ"
        byte_chunks = [data[i : i + 1] for i in range(len(data))]
        assert feed_all(ResponseParser(), byte_chunks) == [resp]

    def test_invalid_utf8_is_replaced(self):
        data = b"get_info:\nInfo: \xff\xfe bad \xc4\nRPRT 0\n"
        (resp,) = ResponseParser().feed(data)
        assert resp.fields["Info"] == "\ufffd\ufffd bad \ufffd"

    def test_pending_bytes_then_str(self):
        parser = ResponseParser()
        assert parser.feed(b"get_info:\nInfo: \xc4") == []
        assert parser.feed("x\nRPRT 0\n") == [R("get_info", "", {"Info": "\ufffdx"}, 0)]

    def test_nul_and_control_bytes(self):
        parser = ResponseParser()
        assert parser.feed(b"\x00\x01\x02\x1b[0m\n\x00RPRT 0\x00\n") == []
        assert parser.feed(b"stop:\nRPRT 0\n") == [R("stop", "", {}, 0)]

    def test_garbage_never_raises_and_resyncs(self):
        rng = random.Random(20260929)
        tokens = [
            b"RPRT ",
            b"RPRT -1\n",
            b"-",
            b"0",
            b"7",
            b"\n",
            b"\r\n",
            b":",
            b": ",
            b" ",
            b"\t",
            b"get_freq",
            b"set_pos:",
            b"Frequency",
            b"Azimuth: ",
            b"\xff",
            b"\xc4",
            b"\xe2\x82",
            "é".encode(),
            b"\x00",
            b"+",
            b"\\",
        ]
        valid = b"get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n"
        for _ in range(300):
            parts = []
            for _ in range(rng.randint(0, 120)):
                if rng.random() < 0.7:
                    parts.append(rng.choice(tokens))
                else:
                    parts.append(bytes(rng.randrange(256) for _ in range(rng.randint(1, 8))))
            data = b"".join(parts)
            parser = ResponseParser()
            pos = 0
            while pos < len(data):
                size = rng.randint(1, 16)
                for resp in parser.feed(data[pos : pos + size]):
                    assert isinstance(resp, HamlibResponse)
                    assert isinstance(resp.command, str)
                    assert isinstance(resp.args, str)
                    assert isinstance(resp.fields, dict)
                    assert isinstance(resp.rprt, int)
                pos += size
            # After a line break the parser is back in sync.
            got = parser.feed(b"\n" + valid)
            assert got[-1] == R("get_mode", "", {"Mode": "USB", "Passband": "2400"}, 0)

    def test_endless_garbage_without_newline_is_bounded(self):
        parser = ResponseParser()
        chunk = b"x" * 65536
        for _ in range(64):  # 4 MiB, no newline
            assert parser.feed(chunk) == []
            assert parser._buffered_chars() <= hamlib._MAX_LINE
        # The overlong line is dropped as a whole, including its tail.
        assert parser.feed(b"RPRT 0\n") == []
        assert parser.feed(b"stop:\nRPRT 0\n") == [R("stop", "", {}, 0)]

    def test_overlong_line_in_one_chunk_is_dropped(self):
        parser = ResponseParser()
        text = "get_freq:\nFrequency: " + "9" * (hamlib._MAX_LINE + 10) + "\nRPRT 0\n"
        assert parser.feed(text) == [R("get_freq", "", {}, 0)]

    def test_long_but_allowed_line(self):
        value = "A" * (hamlib._MAX_LINE - 10)
        (resp,) = ResponseParser().feed(f"get_info:\nInfo: {value}\nRPRT 0\n")
        assert resp.fields["Info"] == value

    def test_endless_fields_are_bounded(self):
        parser = ResponseParser()
        parser.feed("get_freq:\n")
        for i in range(5000):
            assert parser.feed(f"Key{i}: value\n") == []
        (resp,) = parser.feed("Frequency: 7\nRPRT 0\n")
        assert resp.command == "get_freq"
        assert len(resp.fields) == hamlib._MAX_FIELDS
        assert resp.fields["Key0"] == "value"

    def test_repeated_key_overwrites_even_when_full(self):
        parser = ResponseParser()
        body = "".join(f"Key{i}: v\n" for i in range(hamlib._MAX_FIELDS))
        (resp,) = parser.feed("get_x:\n" + body + "Key0: new\nRPRT 0\n")
        assert resp.fields["Key0"] == "new"

    def test_reset_discards_partial_state(self):
        parser = ResponseParser()
        assert parser.feed(b"get_freq:\nFrequency: 1") == []
        parser.reset()
        # The rest of the old reply is now a headerless garbage line plus a stray RPRT.
        assert parser.feed("4\nRPRT 0\n") == [R("", "", {}, 0)]
        assert parser.feed("stop:\nRPRT 0\n") == [R("stop", "", {}, 0)]

    def test_reset_clears_decoder_and_discard_mode(self):
        parser = ResponseParser()
        parser.feed(b"\xc4")
        parser.reset()
        assert parser.feed("stop:\nRPRT 0\n") == [R("stop", "", {}, 0)]
        parser.feed(b"y" * (hamlib._MAX_LINE + 1))
        parser.reset()
        assert parser._buffered_chars() == 0
        assert parser.feed(b"stop:\nRPRT 0\n") == [R("stop", "", {}, 0)]


# --------------------------------------------------------------------------- builders


class TestBuilders:
    def test_get_commands(self):
        assert cmd_get_freq() == "+f\n"
        assert cmd_get_mode() == "+m\n"
        assert cmd_get_pos() == "+p\n"
        assert cmd_stop() == "+S\n"

    @pytest.mark.parametrize(
        ("hz", "expected"),
        [
            (14074000, "+F 14074000\n"),
            (1, "+F 1\n"),
            (145000000, "+F 145000000\n"),
            (14074000.4, "+F 14074000\n"),
            (14074000.5, "+F 14074001\n"),
            (14.074 * 1e6, "+F 14074000\n"),
            (0.6, "+F 1\n"),
        ],
    )
    def test_set_freq(self, hz, expected):
        assert cmd_set_freq(hz) == expected

    @pytest.mark.parametrize(
        "hz", [0, -1, -14074000, 0.4, float("nan"), float("inf"), float("-inf")]
    )
    def test_set_freq_value_errors(self, hz):
        with pytest.raises(ValueError):
            cmd_set_freq(hz)

    @pytest.mark.parametrize("hz", ["14074000", None, True, b"1"])
    def test_set_freq_type_errors(self, hz):
        with pytest.raises(TypeError):
            cmd_set_freq(hz)

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (("USB",), "+M USB 0\n"),
            (("USB", 0), "+M USB 0\n"),
            (("PKTUSB", 3000), "+M PKTUSB 3000\n"),
            (("CW", 500), "+M CW 500\n"),
            (("FM", -1), "+M FM -1\n"),
            (("D-STAR", 0), "+M D-STAR 0\n"),
            (("LSB", 2400.0), "+M LSB 2400\n"),
        ],
    )
    def test_set_mode(self, args, expected):
        assert cmd_set_mode(*args) == expected

    @pytest.mark.parametrize("mode", MODES)
    def test_set_mode_every_mode(self, mode):
        assert cmd_set_mode(mode) == f"+M {mode} 0\n"

    @pytest.mark.parametrize("mode", ["usb", " USB", "FT8", "", "FM-D", "USB-D", "None", "DD"])
    def test_set_mode_unknown_mode(self, mode):
        with pytest.raises(ValueError):
            cmd_set_mode(mode)

    @pytest.mark.parametrize("passband", [-2, -2400, float("nan"), float("inf")])
    def test_set_mode_bad_passband(self, passband):
        with pytest.raises(ValueError):
            cmd_set_mode("USB", passband)

    def test_set_mode_type_errors(self):
        with pytest.raises(TypeError):
            cmd_set_mode(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            cmd_set_mode("USB", "2400")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            cmd_set_mode("USB", True)

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            ((123.5,), "+P 123.5 0.0\n"),
            ((123.5, 10), "+P 123.5 10.0\n"),
            ((90,), "+P 90.0 0.0\n"),
            ((90.0, 0.0), "+P 90.0 0.0\n"),
            ((12.345,), "+P 12.35 0.0\n"),
            ((2.675, 45.125), "+P 2.68 45.13\n"),
            ((-12.345,), "+P -12.35 0.0\n"),
            ((-90.0,), "+P -90.0 0.0\n"),
            ((0,), "+P 0.0 0.0\n"),
            ((-0.0, -0.0), "+P 0.0 0.0\n"),
            ((-0.001,), "+P 0.0 0.0\n"),
            ((0.004,), "+P 0.0 0.0\n"),
            ((0.005,), "+P 0.01 0.0\n"),
            ((359.999,), "+P 360.0 0.0\n"),
            ((449.99,), "+P 449.99 0.0\n"),
            ((100.10,), "+P 100.1 0.0\n"),
            ((1e20,), "+P 100000000000000000000.0 0.0\n"),
            ((1e-7,), "+P 0.0 0.0\n"),
        ],
    )
    def test_set_pos(self, args, expected):
        assert cmd_set_pos(*args) == expected

    @pytest.mark.parametrize(
        "args",
        [
            (float("nan"),),
            (float("inf"),),
            (float("-inf"),),
            (10.0, float("nan")),
            (10.0, float("inf")),
            (10**400,),
        ],
    )
    def test_set_pos_value_errors(self, args):
        with pytest.raises(ValueError):
            cmd_set_pos(*args)

    def test_set_pos_type_errors(self):
        with pytest.raises(TypeError):
            cmd_set_pos("123")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            cmd_set_pos(10.0, None)  # type: ignore[arg-type]

    def test_every_command_is_one_line(self):
        commands = [
            cmd_get_freq(),
            cmd_set_freq(7074000),
            cmd_get_mode(),
            cmd_set_mode("USB", 2400),
            cmd_get_pos(),
            cmd_set_pos(1.0, 2.0),
            cmd_stop(),
        ]
        for cmd in commands:
            assert cmd.startswith("+")
            assert cmd.endswith("\n")
            assert cmd.count("\n") == 1
            assert cmd.isascii()


class TestExpectedCommand:
    @pytest.mark.parametrize(
        ("cmd", "name"),
        [
            (cmd_get_freq(), "get_freq"),
            (cmd_set_freq(14074000), "set_freq"),
            (cmd_get_mode(), "get_mode"),
            (cmd_set_mode("USB"), "set_mode"),
            (cmd_get_pos(), "get_pos"),
            (cmd_set_pos(123.5, 10), "set_pos"),
            (cmd_stop(), "stop"),
            ("+f\n", "get_freq"),
            ("+F 14074000\n", "set_freq"),
            ("+m", "get_mode"),
            ("+M USB 0", "set_mode"),
            ("+p", "get_pos"),
            ("+P 1.0 2.0\n", "set_pos"),
            ("+S", "stop"),
            ("+S\r\n", "stop"),
            ("+\\get_freq\n", "get_freq"),
            ("+\\set_freq 14074000\n", "set_freq"),
            ("+\\stop\n", "stop"),
        ],
    )
    def test_known(self, cmd, name):
        assert expected_command(cmd) == name

    @pytest.mark.parametrize(
        "cmd",
        ["", "\n", "+", "f\n", "F 14074000\n", "+fx\n", "+X_bogus\n", "+\\\n", "+q\n", ";f\n"],
    )
    def test_unknown(self, cmd):
        assert expected_command(cmd) == ""

    def test_matches_real_reply_headers(self):
        pairs = [
            (cmd_get_freq(), "get_freq:\nFrequency: 14074000\nRPRT 0\n"),
            (cmd_get_mode(), "get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n"),
            (cmd_set_freq(14074000), "set_freq: 14074000\nRPRT 0\n"),
            (cmd_set_mode("USB"), "set_mode: USB 0\nRPRT 0\n"),
            (cmd_get_pos(), "get_pos:\nAzimuth: 123.00\nElevation: 0.00\nRPRT 0\n"),
            (cmd_set_pos(123.5, 10), "set_pos: 123.5 10.0\nRPRT 0\n"),
            (cmd_stop(), "stop:\nRPRT 0\n"),
        ]
        for cmd, reply in pairs:
            (resp,) = ResponseParser().feed(reply)
            assert resp.command == expected_command(cmd)


# --------------------------------------------------------------------------- parse_*


def _resp(fields: dict, rprt: int = 0, command: str = "x") -> HamlibResponse:
    return R(command, "", fields, rprt)


class TestParseFreq:
    def test_captured(self):
        (resp,) = ResponseParser().feed("get_freq:\nFrequency: 14074000\nRPRT 0\n")
        assert parse_freq(resp) == 14074000

    @pytest.mark.parametrize(
        ("value", "hz"),
        [
            ("14074000", 14074000),
            ("14074000.000000", 14074000),
            ("14074000.6", 14074001),
            (" 7074000 ", 7074000),
            ("14074000,000000", 14074000),
            ("1.4074e7", 14074000),
            ("10368100000", 10368100000),
            ("+145000000", 145000000),
        ],
    )
    def test_values(self, value, hz):
        assert parse_freq(_resp({"Frequency": value})) == hz

    @pytest.mark.parametrize(
        "value",
        ["", "abc", "0", "-5", "0.2", "nan", "inf", "1e999", "14 074 000", "1_000", "1.2.3"],
    )
    def test_unparsable(self, value):
        assert parse_freq(_resp({"Frequency": value})) is None

    def test_error_rprt(self):
        assert parse_freq(_resp({"Frequency": "14074000"}, rprt=-11)) is None
        assert parse_freq(_resp({"Frequency": "14074000"}, rprt=1)) is None

    def test_missing_field(self):
        assert parse_freq(_resp({})) is None
        assert parse_freq(_resp({"Mode": "USB"})) is None

    def test_key_case_insensitive_fallback(self):
        assert parse_freq(_resp({"FREQUENCY": "7074000"})) == 7074000


class TestParseMode:
    def test_captured(self):
        (resp,) = ResponseParser().feed("get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n")
        assert parse_mode(resp) == ("USB", 2400)

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("USB", "USB"),
            ("PKTUSB", "PKTUSB"),
            ("FM-D", "PKTFM"),
            ("AM-D", "PKTAM"),
            ("USB-D", "PKTUSB"),
            ("LSB-D", "PKTLSB"),
            ("CW-R", "CWR"),
            ("RTTY-R", "RTTYR"),
            ("usb", "USB"),
            (" CW ", "CW"),
            ("USBD1", "USBD1"),
            ("WFM_ST", "WFM_ST"),
        ],
    )
    def test_mode_names(self, mode, expected):
        assert parse_mode(_resp({"Mode": mode, "Passband": "2400"})) == (expected, 2400)

    @pytest.mark.parametrize(
        ("passband", "expected"), [("0", 0), ("15000", 15000), ("2400.0", 2400)]
    )
    def test_passband(self, passband, expected):
        assert parse_mode(_resp({"Mode": "FM", "Passband": passband})) == ("FM", expected)

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"Mode": "USB"},
            {"Passband": "2400"},
            {"Mode": "", "Passband": "2400"},
            {"Mode": "USB", "Passband": "wide"},
            {"Mode": "USB", "Passband": ""},
            {"Mode": "USB", "Passband": "nan"},
        ],
    )
    def test_missing_or_unparsable(self, fields):
        assert parse_mode(_resp(fields)) is None

    def test_error_rprt(self):
        assert parse_mode(_resp({"Mode": "USB", "Passband": "2400"}, rprt=-1)) is None


class TestParsePos:
    def test_captured(self):
        text = "get_pos:\nAzimuth: 123.00\nElevation: 0.00\nRPRT 0\n"
        (resp,) = ResponseParser().feed(text)
        assert parse_pos(resp) == (123.0, 0.0)

    @pytest.mark.parametrize(
        ("az", "el", "expected"),
        [
            ("6.01", "5.00", (6.01, 5.0)),
            ("-90.00", "0.00", (-90.0, 0.0)),
            ("450.00", "90.00", (450.0, 90.0)),
            ("123,50", "10,25", (123.5, 10.25)),
            ("7", "0", (7.0, 0.0)),
        ],
    )
    def test_values(self, az, el, expected):
        assert parse_pos(_resp({"Azimuth": az, "Elevation": el})) == expected

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"Azimuth": "123.00"},
            {"Elevation": "0.00"},
            {"Azimuth": "abc", "Elevation": "0.00"},
            {"Azimuth": "nan", "Elevation": "0.00"},
            {"Azimuth": "10.00", "Elevation": "inf"},
            {"Azimuth": "", "Elevation": ""},
        ],
    )
    def test_missing_or_unparsable(self, fields):
        assert parse_pos(_resp(fields)) is None

    def test_error_rprt(self):
        assert parse_pos(_resp({"Azimuth": "1.00", "Elevation": "0.00"}, rprt=-21)) is None


# --------------------------------------------------------------------------- errors, modes


HAMLIB_ERROR_CODES = range(0, 23)  # enum rig_errcode_e: RIG_OK .. RIG_EACCESS (Hamlib 4.6.x)


@pytest.fixture
def english(monkeypatch):
    """Pin tr() to the identity so exact English texts do not depend on global language state."""
    monkeypatch.setattr(hamlib, "tr", lambda text: text)


@pytest.mark.usefixtures("english")
class TestErrorMessage:
    def test_every_hamlib_code_has_its_own_message(self):
        messages = [error_message(-code) for code in HAMLIB_ERROR_CODES]
        assert all(isinstance(m, str) and m for m in messages)
        assert len(set(messages)) == len(messages)
        unknown = error_message(-9999)
        assert unknown not in messages

    @pytest.mark.parametrize("code", HAMLIB_ERROR_CODES)
    def test_sign_does_not_matter(self, code):
        assert error_message(code) == error_message(-code)

    @pytest.mark.parametrize(
        ("code", "text"),
        [
            (0, "Command completed successfully"),
            (-1, "Invalid parameter"),
            (-5, "Communication timed out"),
            (-6, "Input/output error"),
            (-11, "Feature not available"),
            (-18, "Function deprecated"),
            (-21, "Limit exceeded"),
        ],
    )
    def test_known_texts(self, code, text):
        assert error_message(code) == text

    @pytest.mark.parametrize("code", [-23, -99, 23, 1000, -(2**31)])
    def test_unknown_codes_contain_the_code(self, code):
        message = error_message(code)
        assert str(code) in message
        assert "Hamlib" in message

    def test_goes_through_tr(self, monkeypatch):
        monkeypatch.setattr(hamlib, "tr", lambda text: f"<{text}>")
        assert error_message(-11) == "<Feature not available>"
        assert error_message(-99) == "<Unknown Hamlib error (code -99)>"


class TestModes:
    REQUIRED = "USB LSB CW CWR AM FM WFM RTTY RTTYR PKTUSB PKTLSB PKTFM AMS DSB FMN PKTAM".split()

    def test_required_modes_present(self):
        assert isinstance(MODES, tuple)
        for mode in self.REQUIRED:
            assert mode in MODES

    def test_unique_uppercase_canonical(self):
        assert len(set(MODES)) == len(MODES)
        for mode in MODES:
            assert mode == mode.strip().upper()
        # Aliases accepted by rig_parse_mode() are not listed twice.
        for alias in ("AM-D", "FM-D", "CW-R", "RTTY-R", "LSB-D", "USB-D", "None"):
            assert alias not in MODES

    def test_common_modes_first(self):
        assert MODES[:4] == ("USB", "LSB", "CW", "CWR")

    def test_parse_mode_round_trip(self):
        for mode in MODES:
            parsed = parse_mode(_resp({"Mode": mode, "Passband": "0"}))
            assert parsed == (mode, 0)
            assert cmd_set_mode(parsed[0]) == f"+M {mode} 0\n"

    def test_default_ports(self):
        assert (RIG_DEFAULT_PORT, ROT_DEFAULT_PORT) == (4532, 4533)


# --------------------------------------------------------------------------- rotator_target


ROTATOR_TABLE = [
    # (bearing, min_az, max_az, current_az, expected)
    # 0..360
    (0, 0, 360, None, 0.0),
    (360, 0, 360, None, 0.0),
    (720, 0, 360, None, 0.0),
    (90, 0, 360, None, 90.0),
    (-90, 0, 360, None, 270.0),
    (-450, 0, 360, None, 270.0),
    (359.9, 0, 360, None, 359.9),
    (180, 0, 360, 350, 180.0),
    (0, 0, 360, 350, 360.0),
    (0, 0, 360, 10, 0.0),
    (360, 0, 360, 359, 360.0),
    # 0..450
    (30, 0, 450, None, 30.0),
    (30, 0, 450, 400, 390.0),
    (30, 0, 450, 10, 30.0),
    (30, 0, 450, 210, 30.0),  # tie: prefer the unwound equivalent
    (30, 0, 450, 211, 390.0),
    (90, 0, 450, 440, 450.0),
    (90, 0, 450, 100, 90.0),
    (100, 0, 450, 440, 100.0),  # 460 is outside the range
    (0, 0, 450, 420, 360.0),
    (0, 0, 450, None, 0.0),
    (450, 0, 450, None, 90.0),
    (-270, 0, 450, 449, 450.0),
    # -180..180
    (270, -180, 180, None, -90.0),
    (90, -180, 180, None, 90.0),
    (0, -180, 180, None, 0.0),
    (359, -180, 180, None, -1.0),
    (180, -180, 180, None, 180.0),
    (180, -180, 180, 170, 180.0),
    (180, -180, 180, -170, -180.0),
    (-180, -180, 180, None, 180.0),
    (-90, -180, 180, 100, -90.0),
    # 180..540
    (30, 180, 540, None, 390.0),
    (200, 180, 540, None, 200.0),
    (0, 180, 540, None, 360.0),
    (360, 180, 540, 200, 360.0),
    (180, 180, 540, None, 180.0),
    (180, 180, 540, 500, 540.0),
    (179.99, 180, 540, None, 539.99),
    # 0..180: bearings west of south are unreachable
    (270, 0, 180, None, None),
    (270, 0, 180, 90, None),
    (180.5, 0, 180, None, None),
    (359.99, 0, 180, None, None),
    (-1, 0, 180, None, None),
    (180, 0, 180, None, 180.0),
    (-180, 0, 180, None, 180.0),
    (0, 0, 180, None, 0.0),
    (360, 0, 180, None, 0.0),
    (90, 0, 180, 170, 90.0),
    # wider than 720
    (0, -360, 720, None, 0.0),
    (0, -360, 720, 700, 720.0),
    (0, -360, 720, -300, -360.0),
    (10, -360, 720, 190, 10.0),  # tie between 10 and 370: prefer the unwound one
    # degenerate and empty ranges
    (90, 90, 90, None, 90.0),
    (450, 90, 90, 0, 90.0),
    (91, 90, 90, None, None),
    (10, 360, 0, None, None),
]


class TestRotatorTarget:
    @pytest.mark.parametrize(("bearing", "lo", "hi", "cur", "expected"), ROTATOR_TABLE)
    def test_table(self, bearing, lo, hi, cur, expected):
        result = rotator_target(bearing, lo, hi, cur)
        if expected is None:
            assert result is None
        else:
            assert isinstance(result, float)
            assert result == pytest.approx(expected, abs=1e-9)
            assert lo <= result <= hi

    def test_no_negative_zero(self):
        for bearing in (0.0, -0.0, 360.0, -360.0, 720.0):
            result = rotator_target(bearing, 0.0, 360.0)
            assert result == 0.0
            assert math.copysign(1.0, result) == 1.0

    def test_float_noise_at_range_edges(self):
        assert rotator_target(90.00000000000001, 0.0, 90.0) == 90.0
        assert rotator_target(450.0 - 1e-12, 0.0, 450.0, 449.0) == pytest.approx(450.0)
        tiny = -1e-15  # -1e-15 % 360 == 360.0 in floating point
        assert rotator_target(tiny, 0.0, 180.0) == pytest.approx(0.0, abs=1e-9)
        assert rotator_target(tiny, 0.0, 360.0) == pytest.approx(0.0, abs=1e-9)

    def test_huge_range_is_fast_and_correct(self):
        result = rotator_target(30.0, -1e9, 1e9, 1e6)
        assert abs(result - 1e6) <= 180.0
        assert (result - 30.0) % 360.0 == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize(
        "args",
        [
            (float("nan"), 0, 360),
            (float("inf"), 0, 360),
            (10, float("nan"), 360),
            (10, 0, float("inf")),
            (10, 0, 450, float("nan")),
            (10, 0, 450, float("-inf")),
        ],
    )
    def test_non_finite_is_a_programmer_error(self, args):
        with pytest.raises(ValueError):
            rotator_target(*args)

    def test_type_errors(self):
        with pytest.raises(TypeError):
            rotator_target("90", 0, 360)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            rotator_target(90, 0, 360, "10")  # type: ignore[arg-type]

    def test_matches_brute_force(self):
        rng = random.Random(4533)
        ranges = [(0, 360), (0, 450), (-180, 180), (180, 540), (0, 180), (-90, 450), (-360, 720)]
        for _ in range(3000):
            lo, hi = rng.choice(ranges)
            if rng.random() < 0.3:
                lo = lo + rng.randint(-40, 40) / 2
                hi = max(lo, hi + rng.randint(-40, 40) / 2)
            bearing = rng.randint(-1440, 1440) / 4
            cur = None if rng.random() < 0.3 else rng.randint(-2000, 2000) / 4
            assert rotator_target(bearing, lo, hi, cur) == _brute_force(bearing, lo, hi, cur)


def _brute_force(bearing, lo, hi, cur):
    canonical = bearing % 360.0
    candidates = [
        canonical + 360.0 * k for k in range(-20, 21) if lo <= canonical + 360.0 * k <= hi
    ]
    if not candidates:
        return None
    ref = canonical if cur is None else cur
    return min(candidates, key=lambda c: (abs(c - ref), abs(c - canonical), c))


# --------------------------------------------------------------------------- i18n, purity


def _module_tree() -> ast.Module:
    return ast.parse(Path(hamlib.__file__).read_text(encoding="utf-8"))


def _marked_strings() -> set[str]:
    found = set()
    for node in ast.walk(_module_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("tr", "tr_noop")
        ):
            assert len(node.args) == 1, ast.dump(node)
            arg = node.args[0]
            if node.func.id == "tr_noop" or isinstance(arg, ast.Constant):
                assert isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                found.add(arg.value)
            else:  # tr(name) only with a plain variable holding a tr_noop-marked value
                assert isinstance(arg, ast.Name), ast.dump(node)
    return found


class TestTranslations:
    def test_catalog_matches_module_strings(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        assert set(catalog) == _marked_strings()

    def test_catalog_format(self):
        raw = CATALOG.read_text(encoding="utf-8")
        catalog = json.loads(raw)
        assert list(catalog) == sorted(catalog)
        assert raw == json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def test_translations_are_serbian_latin_and_keep_placeholders(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        for source, target in catalog.items():
            assert target.strip() and target != source
            assert not re.search("[\u0400-\u04ff]", target), target
            assert set(re.findall(r"\{\w+\}", source)) == set(re.findall(r"\{\w+\}", target))

    @pytest.mark.usefixtures("english")
    def test_every_error_code_is_marked(self):
        marked = _marked_strings()
        for code in HAMLIB_ERROR_CODES:
            assert error_message(code) in marked

    def test_no_qt_or_qgis_imports(self):
        for node in ast.walk(_module_tree()):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.split(".")[0].lower().startswith(("qgis", "pyqt"))
