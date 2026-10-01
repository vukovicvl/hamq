"""Tests for hamq.core.stats (task M4-02)."""

from __future__ import annotations

import ast
import copy
import dataclasses
import enum
import math
import random
import sqlite3
import time
import types
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path

import pytest

from hamq.core import stats
from hamq.core.stats import CONTINENTS, LongestQso, QsoStats, compute_stats

UTC = timezone.utc

# QSO_FIELDS names from the "core/qso.py" section of docs/ARCHITECTURE.md, in order, no fid.
FIELD_NAMES = (
    "call",
    "qso_datetime",
    "band",
    "mode",
    "submode",
    "freq_mhz",
    "rst_sent",
    "rst_rcvd",
    "gridsquare",
    "my_gridsquare",
    "dxcc",
    "country",
    "cont",
    "cq_zone",
    "itu_zone",
    "distance_km",
    "bearing_deg",
    "loc_source",
    "source",
    "dedup_key",
    "adif_extra",
)

EMPTY = QsoStats(
    total=0,
    dxcc_count=0,
    unique_calls=0,
    grid_count=0,
    by_continent={},
    by_band={},
    by_mode={},
    longest=None,
    first_qso=None,
    last_qso=None,
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def row(**values: object) -> dict[str, object]:
    """A full row as qgis_io.gpkg.read_qso_rows returns it: every QSO_FIELDS name."""
    unknown = set(values) - set(FIELD_NAMES)
    assert not unknown, unknown
    result: dict[str, object] = dict.fromkeys(FIELD_NAMES)
    result.update(values)
    return result


def first_qso_of(value: object) -> datetime | None:
    return compute_stats([{"qso_datetime": value}]).first_qso


class NullLike:
    """Stand-in for QGIS 3's NULL (a null QVariant): falsy, equal to None, not str or number."""

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return other is None or isinstance(other, NullLike)

    def __hash__(self) -> int:
        return 2178309

    def __repr__(self) -> str:
        return "NULL"


NULL = NullLike()


class TimeSpec(enum.Enum):
    """Qt.TimeSpec as PyQt6 returns it (PyQt5 returns int subclasses)."""

    LocalTime = 0
    UTC = 1
    OffsetFromUTC = 2
    TimeZone = 3


LOCAL, UTC_SPEC, OFFSET, TIME_ZONE = 0, 1, 2, 3


class FakeQDateTime:
    """Duck-typed QDateTime, which QGIS returns for GeoPackage datetime fields.

    Modelled on PyQt5 and PyQt6 (checked on QGIS 3.44 and 4.2): toPyDateTime()
    returns the naive wall-clock fields in the value's own time spec; toUTC() converts
    (a LocalTime value by the computer's zone, UTC+2 here; an OffsetFromUTC or TimeZone
    value by ``offset_hours``); an invalid (null) value raises ValueError in
    toPyDateTime(). timeSpec() gives an int like PyQt5, or an enum member like PyQt6
    with ``enum_spec=True``.
    """

    COMPUTER_ZONE = timedelta(hours=2)

    def __init__(
        self,
        wall: datetime | None,
        spec: int = UTC_SPEC,
        offset_hours: float = 0.0,
        enum_spec: bool = False,
    ) -> None:
        self.wall = wall
        self.spec = spec
        self.offset = timedelta(hours=offset_hours)
        self.enum_spec = enum_spec

    def isValid(self) -> bool:
        return self.wall is not None

    def timeSpec(self) -> object:
        return TimeSpec(self.spec) if self.enum_spec else self.spec

    def toUTC(self) -> FakeQDateTime:
        if self.wall is None:
            return self
        shift = {LOCAL: self.COMPUTER_ZONE, UTC_SPEC: timedelta(0)}.get(self.spec, self.offset)
        return type(self)(self.wall - shift, UTC_SPEC, enum_spec=self.enum_spec)

    def toPyDateTime(self) -> datetime:
        if self.wall is None:
            raise ValueError("year 0 is out of range")
        return self.wall


class SpeclessFakeQDateTime(FakeQDateTime):
    """A binding without timeSpec(): toUTC() is trusted."""

    timeSpec = None  # type: ignore[assignment]


class AwareFakeQDateTime(FakeQDateTime):
    """A binding that returns aware datetimes from toPyDateTime()."""

    def toUTC(self) -> AwareFakeQDateTime:
        return self

    def toPyDateTime(self) -> datetime:
        assert self.wall is not None
        return self.wall.replace(tzinfo=timezone(self.offset))


class NoOffset(tzinfo):
    """A tzinfo whose utcoffset() is None: such datetimes count as naive."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


class PlainMapping(Mapping):
    """A read-only Mapping that is not a dict."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


# --- a realistic small log ------------------------------------------------------------------
# Station YU1XX in Belgrade (KN04ft). Distances are great-circle km between grid centres
# (hamq.core.geo.distance_km of the maidenhead.to_latlon centres, rounded to 0.1 km).
# DXCC codes are ADIF entity codes; the JA1XYZ row came live from WSJT-X before cty.csv was
# downloaded, so it has a country name but no code.
LOG = [
    row(
        call="DL1ABC",
        qso_datetime="2026-09-15T18:45:00Z",
        band="20m",
        mode="FT8",
        freq_mhz=14.074,
        rst_sent="-10",
        rst_rcvd="-12",
        gridsquare="JO62qm",
        my_gridsquare="KN04ft",
        dxcc=230,
        country="Fed. Rep. of Germany",
        cont="EU",
        cq_zone=14,
        itu_zone=28,
        distance_km=1001.5,
        bearing_deg=316.5,
        loc_source="grid",
        source="adif:wsjtx_log.adi",
        dedup_key="DL1ABC|202609151845|20m|FT8",
        adif_extra="{}",
    ),
    row(
        call="W1AW",
        qso_datetime=utc(2026, 9, 16, 21, 3, 15),
        band="20m",
        mode="FT8",
        freq_mhz=14.075,
        gridsquare="FN31pr",
        my_gridsquare="KN04ft",
        dxcc=291,
        country="United States of America",
        cont="NA",
        distance_km=7105.9,
        source="adif:wsjtx_log.adi",
    ),
    row(
        call="VK2XYZ",
        qso_datetime="2026-09-18T07:12:30+00:00",
        band="20m",
        mode="MFSK",
        submode="FT4",
        freq_mhz=14.08,
        gridsquare="QF56od",
        my_gridsquare="KN04ft",
        dxcc=150,
        country="Australia",
        cont="OC",
        distance_km=15679.0,
        source="adif:wsjtx_log.adi",
    ),
    row(
        call="JA1ABC",
        qso_datetime="2026-09-19 20:30:00",
        band="40m",
        mode="FT8",
        freq_mhz=7.074,
        gridsquare="PM95tq",
        dxcc=339,
        country="Japan",
        cont="AS",
        distance_km=9174.8,
    ),
    row(
        call="PY2ABC",
        qso_datetime=datetime(2026, 9, 20, 16, 5),
        band="15m",
        mode="SSB",
        freq_mhz=21.25,
        gridsquare="GG66qk",
        dxcc=108,
        country="Brazil",
        cont="SA",
        distance_km=10189.3,
    ),
    row(
        call="ZS6ABC",
        qso_datetime="2026-09-21T17:40Z",
        band="15m",
        mode="CW",
        freq_mhz=21.03,
        gridsquare="KG43at",
        dxcc=462,
        country="South Africa",
        cont="AF",
        distance_km=7932.3,
    ),
    row(
        call="9A1ABC",
        qso_datetime="2026-09-22T19:00:00.000Z",
        band="40m",
        mode="CW",
        freq_mhz=7.02,
        gridsquare="JN75xt",
        dxcc=497,
        country="Croatia",
        cont="EU",
        distance_km=369.0,
    ),
    row(
        call="YU7ABC",
        qso_datetime="2026-09-23T18:00:00Z",
        band="2m",
        mode="FM",
        freq_mhz=145.5,
        gridsquare="JN95wg",
        dxcc=296,
        country="Serbia",
        cont="EU",
        distance_km=68.5,
    ),
    row(
        call="DL1ABC",
        qso_datetime="2026-09-24T06:15:00Z",
        band="40m",
        mode="CW",
        freq_mhz=7.025,
        gridsquare="JO62",
        dxcc=230,
        country="Fed. Rep. of Germany",
        cont="EU",
        distance_km=1014.1,  # centre of the square JO62, not of JO62qm
    ),
    row(
        call="JA1XYZ",
        qso_datetime="2026-09-25T11:22:33Z",
        band="20m",
        mode="FT8",
        freq_mhz=14.074,
        gridsquare="PM95",
        country="Japan",
        cont="AS",
        distance_km=9155.6,  # centre of the square PM95, not of PM95tq
        source="wsjtx",
    ),
    row(
        call="HA1ABC",
        qso_datetime="2026-09-26T19:45:00Z",
        band="80m",
        mode="SSB",
        freq_mhz=3.75,
        dxcc=239,
        country="Hungary",
        cont="EU",
        loc_source="cty",
    ),
]


# --- module and contract ----------------------------------------------------------------------


def test_module_is_pure_python():
    tree = ast.parse(Path(stats.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"qgis", "PyQt5", "PyQt6", "sip"}


def test_contract_names_and_fields():
    assert CONTINENTS == ("EU", "AS", "AF", "NA", "SA", "OC", "AN")
    assert [f.name for f in dataclasses.fields(LongestQso)] == [
        "call",
        "distance_km",
        "country",
        "band",
        "mode",
        "qso_datetime",
    ]
    assert [f.name for f in dataclasses.fields(QsoStats)] == [
        "total",
        "dxcc_count",
        "unique_calls",
        "grid_count",
        "by_continent",
        "by_band",
        "by_mode",
        "longest",
        "first_qso",
        "last_qso",
    ]
    assert set(stats.__all__) == {"CONTINENTS", "LongestQso", "QsoStats", "compute_stats"}


# --- empty input and the realistic log -----------------------------------------------------------


@pytest.mark.parametrize("rows", [[], (), iter([]), (r for r in []), None])
def test_empty_input(rows):
    assert compute_stats(rows) == EMPTY


@pytest.mark.parametrize(
    "empty_row",
    [
        {},
        dict.fromkeys(FIELD_NAMES),
        dict.fromkeys(FIELD_NAMES, ""),
        dict.fromkeys(FIELD_NAMES, "   "),
        dict.fromkeys(FIELD_NAMES, NULL),
    ],
    ids=["no-keys", "all-none", "all-empty", "all-blank", "all-qgis-null"],
)
def test_row_without_data_counts_only_as_unknown(empty_row):
    result = compute_stats([empty_row])
    assert result == QsoStats(
        total=1,
        dxcc_count=0,
        unique_calls=0,
        grid_count=0,
        by_continent={"?": 1},
        by_band={"?": 1},
        by_mode={"?": 1},
        longest=None,
        first_qso=None,
        last_qso=None,
    )


def test_realistic_log():
    result = compute_stats(LOG)
    assert result.total == 11
    # 9 codes; the code-less "Japan" row is the same entity as the JA1ABC row (code 339)
    assert result.dxcc_count == 9
    assert result.unique_calls == 10  # DL1ABC worked twice
    assert result.grid_count == 8  # JO62 and PM95 appear twice
    assert list(result.by_continent.items()) == [
        ("EU", 5),
        ("AS", 2),
        ("AF", 1),
        ("NA", 1),
        ("SA", 1),
        ("OC", 1),
    ]
    assert list(result.by_band.items()) == [
        ("80m", 1),
        ("40m", 3),
        ("20m", 4),
        ("15m", 2),
        ("2m", 1),
    ]
    assert list(result.by_mode.items()) == [
        ("FT8", 4),
        ("CW", 3),
        ("SSB", 2),
        ("FM", 1),
        ("FT4", 1),
    ]
    assert result.longest == LongestQso(
        call="VK2XYZ",
        distance_km=15679.0,
        country="Australia",
        band="20m",
        mode="FT4",
        qso_datetime=utc(2026, 9, 18, 7, 12, 30),
    )
    assert result.first_qso == utc(2026, 9, 15, 18, 45)
    assert result.last_qso == utc(2026, 9, 26, 19, 45)
    for value in (result.first_qso, result.last_qso, result.longest.qso_datetime):
        assert value.tzinfo is UTC


def test_result_is_independent_of_row_order():
    expected = compute_stats(LOG)
    shuffled = LOG[:]
    random.Random(7).shuffle(shuffled)
    assert compute_stats(shuffled) == expected
    assert compute_stats(reversed(LOG)) == expected


def test_result_uses_plain_dicts_and_input_is_not_modified():
    before = copy.deepcopy(LOG)
    result = compute_stats(LOG)
    assert LOG == before
    for mapping in (result.by_continent, result.by_band, result.by_mode):
        assert type(mapping) is dict
    assert compute_stats(LOG) == result  # no state kept between calls


# --- dxcc_count ------------------------------------------------------------------------------------


def dxcc_count(*pairs: tuple[object, object]) -> int:
    return compute_stats([{"dxcc": code, "country": country} for code, country in pairs]).dxcc_count


@pytest.mark.parametrize("code", [296, "296", " 296 ", "0296", "296.0", 296.0, Decimal("296")])
def test_dxcc_code_forms_are_the_same_entity(code):
    assert dxcc_count((296, None), (code, None)) == 1
    assert dxcc_count((code, None)) == 1


def test_dxcc_distinct_codes():
    assert dxcc_count((296, None), (230, None), (291, None), (296, None)) == 3


def test_dxcc_code_wins_over_country_name():
    # same code, different or missing names: one entity
    assert dxcc_count((230, "Fed. Rep. of Germany"), (230, "Germany"), (230, None)) == 1
    # different codes that share a (wrong) name still count separately
    assert dxcc_count((54, "Russia"), (15, "Russia")) == 2


def test_dxcc_rows_without_code_count_by_normalized_country_name():
    assert dxcc_count((None, "Serbia"), ("", " serbia "), (NULL, "SERBIA"), (None, "Serbia\t")) == 1
    assert dxcc_count((None, "United  States"), (None, "united states")) == 1
    assert dxcc_count((None, "Serbia"), (None, "Montenegro")) == 2
    # casefold, not only lower(): "STRASSE" and "straße" are one name
    assert dxcc_count((None, "STRASSE"), (None, "straße")) == 1


def test_dxcc_rows_with_neither_code_nor_name_do_not_count():
    assert dxcc_count((None, None), ("", ""), ("  ", "  "), (NULL, NULL), ("abc", None)) == 0
    assert dxcc_count((None, None), (296, None)) == 1


@pytest.mark.parametrize(
    "bad_code", ["abc", "12a", "", "   ", -5, "-5", 296.5, "296.5", True, False, math.nan, []]
)
def test_dxcc_unusable_code_falls_back_to_country_name(bad_code):
    assert dxcc_count((bad_code, None)) == 0
    assert dxcc_count((bad_code, "Canada")) == 1
    # bool is not a number: True must not turn into code 1 (Canada)
    assert dxcc_count((bad_code, "Canada"), (1, None)) == 2


@pytest.mark.parametrize("zero", [0, "0", 0.0, " 0 "])
def test_dxcc_code_zero_means_not_a_dxcc_entity(zero):
    # ADIF DXCC 0: "the contacted station is known to not be within a DXCC entity" (/MM, /AM)
    assert dxcc_count((zero, None)) == 0
    assert dxcc_count((zero, "Maritime Mobile")) == 0
    # the log labels that name as no entity, so code-less rows with it do not count either,
    # like a name seen next to a code > 0 (see the next test)
    assert dxcc_count((zero, "Maritime Mobile"), (None, " maritime  MOBILE ")) == 0
    assert dxcc_count((None, "Maritime Mobile"), (zero, "Maritime Mobile")) == 0  # any order
    # other names and codes still count
    assert dxcc_count((zero, "Maritime Mobile"), (None, "Montenegro"), (296, "Serbia")) == 2


def test_dxcc_name_seen_with_a_code_is_not_counted_again():
    # a log mixing ADIF rows (code + name) and code-less rows with the same name
    assert dxcc_count((296, "Serbia"), (None, "SERBIA"), (None, " serbia ")) == 1
    assert dxcc_count((None, "Serbia"), (296, "Serbia")) == 1  # order does not matter
    # the documented caveat: a name never seen next to a code cannot be matched to one
    assert dxcc_count((230, None), (None, "Fed. Rep. of Germany")) == 2
    assert dxcc_count((230, "Fed. Rep. of Germany"), (None, "Germany")) == 2


# --- unique_calls ------------------------------------------------------------------------------------


def unique_calls(*calls: object) -> int:
    return compute_stats([{"call": call} for call in calls]).unique_calls


def test_unique_calls_case_insensitive_and_stripped():
    assert unique_calls("YU1AB", "yu1ab", " YU1AB ", "Yu1Ab\n") == 1
    assert unique_calls("YU1AB", "YU1AB/P", "YU1CD") == 3  # calls are compared as logged


def test_unique_calls_ignores_missing_calls():
    assert unique_calls(None, "", "   ", NULL, 1234, b"YU1AB") == 0
    assert unique_calls(None, "YU1AB", "") == 1


# --- grid_count ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gridsquare", "counted"),
    [
        ("KN04", True),
        ("kn04", True),
        ("KN04ft", True),
        ("kn04FT", True),
        ("KN04ft55", True),
        (" KN04ft \n", True),
        ("KN04ft55ab", True),
        ("KN04zz", True),  # only the first four characters are checked
        ("AA00", True),
        ("RR99xx", True),
        ("rr99", True),
        ("KN", False),  # a field, no square
        ("KN0", False),
        ("K", False),
        ("", False),
        ("   ", False),
        (None, False),
        (NULL, False),
        (1234, False),
        ("SN04", False),  # S is beyond R
        ("KS04", False),
        ("04KN", False),
        ("KNO4", False),  # letter O instead of zero
        ("KN 04", False),
        ("K N04", False),
        ("KN04", False),  # KELVIN SIGN looks like K
        ("ＫＮ04", False),  # fullwidth KN
        ("KN٠٤", False),  # Arabic-Indic digits
    ],
)
def test_grid_count_valid_looking_squares(gridsquare, counted):
    assert compute_stats([{"gridsquare": gridsquare}]).grid_count == int(counted)


def test_grid_count_distinct_four_character_squares():
    grids = ["KN04ft", "kn04aa", "KN04", " kn04XX55 ", "JN95wg", "jn95", "KN", None, "ZZ00"]
    assert compute_stats([{"gridsquare": g} for g in grids]).grid_count == 2


def test_grid_count_ignores_my_gridsquare():
    result = compute_stats([{"my_gridsquare": "KN04ft"}, {"my_gridsquare": "JN95wg"}])
    assert result.grid_count == 0


# --- by_continent ----------------------------------------------------------------------------------


def by_continent(*conts: object) -> list[tuple[str, int]]:
    return list(compute_stats([{"cont": cont} for cont in conts]).by_continent.items())


def test_by_continent_follows_continents_order():
    assert by_continent(*reversed(CONTINENTS)) == [(c, 1) for c in CONTINENTS]


def test_by_continent_others_alphabetical_then_unknown_last():
    conts = ["XX", None, "EU", "AA", "", "  ", "af", "ZZ", NULL, "?", "EU", "!"]
    assert by_continent(*conts) == [
        ("EU", 2),
        ("AF", 1),
        ("!", 1),
        ("AA", 1),
        ("XX", 1),
        ("ZZ", 1),
        ("?", 5),
    ]


def test_by_continent_normalizes_case_and_whitespace():
    assert by_continent(" eu ", "Eu", "EU", "oc\n") == [("EU", 3), ("OC", 1)]


def test_by_continent_omits_zero_counts():
    assert by_continent("EU", "EU") == [("EU", 2)]
    assert by_continent(None) == [("?", 1)]
    assert "AN" not in compute_stats(LOG).by_continent


# --- by_band -----------------------------------------------------------------------------------------


def by_band(*rows: dict[str, object]) -> list[tuple[str, int]]:
    return list(compute_stats(rows).by_band.items())


def test_by_band_frequency_order():
    bands = ["70cm", "2m", "20m", "160m", "40m", "6m", "10m", "2190m", "23cm"]
    assert by_band(*({"band": b} for b in bands)) == [
        ("2190m", 1),
        ("160m", 1),
        ("40m", 1),
        ("20m", 1),
        ("10m", 1),
        ("6m", 1),
        ("2m", 1),
        ("70cm", 1),
        ("23cm", 1),
    ]


def test_by_band_normalizes_band_names():
    assert by_band({"band": "20M"}, {"band": " 20m "}, {"band": "20m"}) == [("20m", 3)]


def test_by_band_unknown_bands_after_known_then_missing_last():
    rows = [{"band": b} for b in ["11m", "zz", None, "20m", "abc", "", NULL, "?"]]
    # "?" sorts before letters as text, but missing bands always come last
    assert by_band(*rows) == [("20m", 1), ("11m", 1), ("abc", 1), ("zz", 1), ("?", 4)]


def test_by_band_derived_from_frequency_when_band_is_missing():
    rows = [
        {"band": None, "freq_mhz": 14.074},
        {"band": "", "freq_mhz": "7.074"},
        {"freq_mhz": 7},
        {"band": "40m", "freq_mhz": 14.074},  # the logged band wins
        {"band": None, "freq_mhz": 99.0},  # outside every band
        {"band": None, "freq_mhz": "abc"},
        {"band": None, "freq_mhz": math.nan},
    ]
    assert by_band(*rows) == [("40m", 3), ("20m", 1), ("?", 3)]


# --- by_mode -----------------------------------------------------------------------------------------


def by_mode(*pairs: tuple[object, object]) -> list[tuple[str, int]]:
    rows = [{"mode": mode, "submode": submode} for mode, submode in pairs]
    return list(compute_stats(rows).by_mode.items())


def test_by_mode_uses_display_mode():
    assert by_mode(("MFSK", "FT4"), ("FT4", None), ("ft4", ""), (None, "ft4")) == [("FT4", 4)]
    assert by_mode(("ssb", None), ("SSB", "  ")) == [("SSB", 2)]


def test_by_mode_missing_is_question_mark():
    assert by_mode((None, None), ("", ""), ("  ", None), (NULL, NULL), (5, None)) == [("?", 5)]
    assert by_mode((5, "FT4"), ("CW", 5)) == [("CW", 1), ("FT4", 1)]


def test_by_mode_sorted_by_count_then_name():
    pairs = [("FT8", None)] * 3 + [("SSB", None)] * 2 + [("CW", None)] * 2
    pairs += [("MFSK", "FT4"), (None, None)]
    assert by_mode(*pairs) == [("FT8", 3), ("CW", 2), ("SSB", 2), ("?", 1), ("FT4", 1)]


def test_by_mode_unknown_can_come_first():
    assert by_mode((None, None), (None, None), ("CW", None)) == [("?", 2), ("CW", 1)]


# --- longest ---------------------------------------------------------------------------------------


def test_longest_picks_the_largest_distance():
    rows = [
        {"call": "YU7ABC", "distance_km": 68.5},
        {"call": "VK2XYZ", "distance_km": 15679.0},
        {"call": "JA1ABC", "distance_km": 9174.8},
    ]
    assert compute_stats(rows).longest.call == "VK2XYZ"


@pytest.mark.parametrize(
    "bad_distance",
    [None, "", "  ", "abc", math.nan, "nan", math.inf, "inf", -math.inf, -1.0, "-5", -1e-9]
    + [True, False, NULL, [100.0], {"km": 1}, b"100", 10**400],
)
def test_longest_ignores_unusable_distances(bad_distance):
    assert compute_stats([{"call": "YU1AB", "distance_km": bad_distance}]).longest is None
    rows = [{"call": "YU1AB", "distance_km": 12.5}, {"call": "YU1CD", "distance_km": bad_distance}]
    longest = compute_stats(rows).longest
    assert longest.call == "YU1AB"
    assert longest.distance_km == 12.5


def test_longest_accepts_numeric_strings_and_ints():
    rows = [
        {"call": "A", "distance_km": 12000.0},
        {"call": "B", "distance_km": " 12345.6 "},
        {"call": "C", "distance_km": 12345},
        {"call": "D", "distance_km": Decimal("100.5")},
    ]
    longest = compute_stats(rows).longest
    assert (longest.call, longest.distance_km) == ("B", 12345.6)
    assert type(longest.distance_km) is float
    only_int = compute_stats([{"call": "C", "distance_km": 500}]).longest
    assert only_int.distance_km == 500.0
    assert type(only_int.distance_km) is float


def test_longest_zero_distance_is_valid():
    longest = compute_stats([{"call": "YU1AB", "distance_km": -0.0}]).longest
    assert longest.distance_km == 0.0
    assert math.copysign(1.0, longest.distance_km) == 1.0  # no "-0.0 km"


def test_longest_tie_goes_to_the_earliest_qso():
    rows = [
        {"call": "LATE", "distance_km": 5000.0, "qso_datetime": "2026-09-16T10:00:00Z"},
        {"call": "EARLY", "distance_km": 5000.0, "qso_datetime": utc(2026, 9, 15, 10, 0)},
        {"call": "LATER", "distance_km": "5000", "qso_datetime": "2026-09-17T10:00:00+00:00"},
        {"call": "SHORT", "distance_km": 4999.9, "qso_datetime": "2020-01-01T00:00:00Z"},
    ]
    assert compute_stats(rows).longest.call == "EARLY"
    assert compute_stats(reversed(rows)).longest.call == "EARLY"


def test_longest_tie_compares_instants_not_text():
    rows = [
        {"call": "UTC", "distance_km": 100.0, "qso_datetime": "2026-09-15T17:30:00Z"},
        {"call": "OFFSET", "distance_km": 100.0, "qso_datetime": "2026-09-15T19:00:00+02:00"},
    ]
    assert compute_stats(rows).longest.call == "OFFSET"  # 17:00 UTC


def test_longest_tie_prefers_a_row_with_a_time():
    rows = [
        {"call": "NOTIME", "distance_km": 100.0},
        {"call": "BADTIME", "distance_km": 100.0, "qso_datetime": "garbage"},
        {"call": "TIMED", "distance_km": 100.0, "qso_datetime": "2026-09-15T18:45:00Z"},
    ]
    assert compute_stats(rows).longest.call == "TIMED"
    assert compute_stats(reversed(rows)).longest.call == "TIMED"


def test_longest_tie_without_times_keeps_the_first_row():
    rows = [{"call": "FIRST", "distance_km": 100.0}, {"call": "SECOND", "distance_km": 100.0}]
    assert compute_stats(rows).longest.call == "FIRST"


def test_longest_tie_at_the_same_time_keeps_the_first_row():
    rows = [
        {"call": "FIRST", "distance_km": 100.0, "qso_datetime": "2026-09-15T20:45:00+02:00"},
        {"call": "SECOND", "distance_km": 100.0, "qso_datetime": utc(2026, 9, 15, 18, 45)},
    ]
    assert compute_stats(rows).longest.call == "FIRST"


def test_longest_fields_are_normalized():
    rows = [
        {
            "call": " vk2xyz ",
            "distance_km": "15679.0",
            "country": "  Australia ",
            "band": "20M",
            "mode": "MFSK",
            "submode": "ft4",
            "qso_datetime": "2026-09-18T09:12:30+02:00",
        }
    ]
    assert compute_stats(rows).longest == LongestQso(
        call="VK2XYZ",
        distance_km=15679.0,
        country="Australia",
        band="20m",
        mode="FT4",
        qso_datetime=utc(2026, 9, 18, 7, 12, 30),
    )


def test_longest_missing_fields_are_empty():
    longest = compute_stats([{"distance_km": 100.0, "country": "  ", "mode": ""}]).longest
    assert longest == LongestQso(
        call="", distance_km=100.0, country=None, band=None, mode=None, qso_datetime=None
    )
    derived = compute_stats([{"call": "YU1AB", "distance_km": 1.0, "freq_mhz": 7.074}]).longest
    assert derived.band == "40m"


# --- first_qso / last_qso -------------------------------------------------------------------------

AT = utc(2026, 9, 15, 18, 45)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-15T18:45:00Z", AT),  # Python 3.9 fromisoformat() rejects "Z"
        ("2026-09-15T18:45:00+00:00", AT),
        ("2026-09-15T18:45:00-00:00", AT),
        ("2026-09-15T18:45:00", AT),  # naive: UTC
        ("2026-09-15T18:45Z", AT),
        ("2026-09-15T18:45", AT),
        ("2026-09-15T18:45+00:00", AT),
        ("2026-09-15 18:45:00Z", AT),
        ("2026-09-15 18:45:00", AT),
        ("2026-09-15 18:45", AT),
        ("2026-09-15t18:45:00z", AT),
        ("  2026-09-15T18:45:00Z\n", AT),
        ("2026-09-15T18:45:07Z", utc(2026, 9, 15, 18, 45, 7)),
        ("2026-09-15T18:45:07.123Z", utc(2026, 9, 15, 18, 45, 7, 123000)),
        ("2026-09-15T18:45:07.123+00:00", utc(2026, 9, 15, 18, 45, 7, 123000)),
        ("2026-09-15 18:45:07.123", utc(2026, 9, 15, 18, 45, 7, 123000)),
        ("2026-09-15T18:45:07.123456Z", utc(2026, 9, 15, 18, 45, 7, 123456)),
        ("2026-09-15T18:45:07.1Z", utc(2026, 9, 15, 18, 45, 7, 100000)),
        ("2026-09-15T18:45:07.12345678Z", utc(2026, 9, 15, 18, 45, 7, 123456)),  # truncated
        ("2026-09-15T18:45:07,5Z", utc(2026, 9, 15, 18, 45, 7, 500000)),
        ("2026-09-15T20:45:00+02:00", AT),
        ("2026-09-15T20:45+0200", AT),
        ("2026-09-15T20:45:00+02", AT),
        ("2026-09-15T13:45:00-05:00", AT),
        ("2026-09-16T00:15:00+05:30", AT),  # previous day in UTC
        ("2026-09-14T22:45:00-20:00", AT),  # next day in UTC
        ("2027-01-01T00:30:00+01:00", utc(2026, 12, 31, 23, 30)),
        ("20260915T184500Z", AT),  # ISO 8601 basic format
        ("20260915T1845", AT),
        ("20260915 184500", AT),
        ("20260915T204507.25+0200", utc(2026, 9, 15, 18, 45, 7, 250000)),
        # the date, the time and the offset may each use either format (not mixed within)
        ("2026-09-15T1845", AT),
        ("20260915T18:45:00Z", AT),
        ("20260915T204500+02:00", AT),
        ("2026-09-15", utc(2026, 9, 15)),  # date only: midnight UTC
        ("20260915", utc(2026, 9, 15)),
        ("2024-02-29T12:00:00Z", utc(2024, 2, 29, 12)),
        ("2026-12-31T23:59:59.999Z", utc(2026, 12, 31, 23, 59, 59, 999000)),
    ],
)
def test_iso_text_is_parsed_to_utc(text, expected):
    result = first_qso_of(text)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "garbage",
        "None",
        "NULL",
        "2026-13-01T00:00:00Z",
        "2026-00-10T00:00:00Z",
        "2026-02-29T10:00:00Z",  # 2026 is not a leap year
        "2026-09-31T10:00Z",
        "2026-09-15T24:00:00Z",
        "2026-09-15T18:60Z",
        "2026-09-15T18:45:60Z",
        "2026-09-15T18:45:00+24:00",
        "2026-09-15T18:45:00+02:60",
        "2026-09-15T18:45:00+2:00",
        "2026-9-15T18:45Z",
        "26-09-15T18:45Z",
        "2026-09-15T8:45Z",
        "2026-0915T18:45Z",  # mixed extended and basic date
        "202609-15T18:45Z",
        "2026-09-15T1845:00Z",  # mixed extended and basic time
        "2026-09-15T18:4500Z",
        "2026-09-15T18",  # hours without minutes
        "2026-09-15T18Z",
        "2026-09-15T",
        "2026-09-15Z",
        "2026-09-15T18:45:00.Z",
        "2026-09-15T18:45:00ZZ",
        "2026-09-15T18:45:00Z trailing",
        "x2026-09-15T18:45:00Z",
        "2026-09-15T18:45:00 UTC",
        "2026-09-15T18:45:00 +02:00",
        "2026/09/15 18:45:00",
        "15.09.2026 18:45",
        "٢٠٢٦-09-15T18:45Z",  # Arabic-Indic digits
        "２０２６-09-15T18:45Z",  # fullwidth digits
        "0000-01-01T00:00:00Z",
        "0001-01-01T00:30:00+01:00",  # before datetime.min in UTC
        "9999-12-31T23:30:00-01:00",  # after datetime.max in UTC
    ],
)
def test_unparsable_text_is_ignored(text):
    assert first_qso_of(text) is None
    rows = [{"qso_datetime": text}, {"qso_datetime": "2026-09-15T18:45:00Z"}]
    result = compute_stats(rows)
    assert (result.first_qso, result.last_qso) == (AT, AT)


@pytest.mark.parametrize(
    "value", [20260915, 1.5, True, b"2026-09-15T18:45:00Z", ["2026-09-15"], {}, object(), NULL]
)
def test_other_types_are_ignored(value):
    assert first_qso_of(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (AT, AT),
        (datetime(2026, 9, 15, 18, 45), AT),  # naive: UTC
        (datetime(2026, 9, 15, 20, 45, tzinfo=timezone(timedelta(hours=2))), AT),
        (datetime(2026, 9, 15, 13, 45, tzinfo=timezone(timedelta(hours=-5))), AT),
        (datetime(2026, 9, 15, 18, 45, tzinfo=NoOffset()), AT),
        (date(2026, 9, 15), utc(2026, 9, 15)),
    ],
)
def test_datetime_values_are_converted_to_utc(value, expected):
    result = first_qso_of(value)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize("enum_spec", [False, True], ids=["pyqt5-int-spec", "pyqt6-enum-spec"])
@pytest.mark.parametrize(
    ("wall", "spec", "offset_hours"),
    [
        (datetime(2026, 9, 15, 18, 45), UTC_SPEC, 0),
        (datetime(2026, 9, 15, 20, 45), OFFSET, 2),
        (datetime(2026, 9, 15, 13, 45), OFFSET, -5),
        # a time zone (Asia/Tokyo, UTC+9): 03:45 the next day there is 18:45 UTC
        # (checked with a real QTimeZone on QGIS 3.44, 4.0 and 4.2)
        (datetime(2026, 9, 16, 3, 45), TIME_ZONE, 9),
        # stored without a zone: the fields are UTC, not shifted by the computer's zone
        (datetime(2026, 9, 15, 18, 45), LOCAL, 0),
    ],
)
def test_qdatetime_values_are_converted_to_utc(wall, spec, offset_hours, enum_spec):
    value = FakeQDateTime(wall, spec, offset_hours, enum_spec=enum_spec)
    result = first_qso_of(value)
    assert result == AT
    assert result.tzinfo is UTC


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset() is Unix only")
def test_naive_times_are_utc_whatever_the_computer_zone(monkeypatch):
    monkeypatch.setenv("TZ", "JST-9")  # POSIX rule for UTC+9, needs no tz database
    time.tzset()
    try:
        assert datetime(2026, 9, 15, 18, 45).astimezone(UTC) != AT  # the zone is active
        rows = [
            {"qso_datetime": datetime(2026, 9, 15, 18, 45)},
            {"qso_datetime": "2026-09-15T18:45:00"},
            {"qso_datetime": "2026-09-15 18:45"},
        ]
        result = compute_stats(rows)
        assert (result.first_qso, result.last_qso) == (AT, AT)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_qdatetime_without_time_spec_and_aware_bindings():
    assert first_qso_of(SpeclessFakeQDateTime(datetime(2026, 9, 15, 20, 45), OFFSET, 2)) == AT
    aware = first_qso_of(AwareFakeQDateTime(datetime(2026, 9, 15, 20, 45), OFFSET, 2))
    assert aware == AT
    assert aware.tzinfo is UTC


@pytest.mark.parametrize("enum_spec", [False, True])
def test_invalid_qdatetime_is_ignored(enum_spec):
    assert first_qso_of(FakeQDateTime(None, enum_spec=enum_spec)) is None


def test_invalid_qdatetime_is_ignored_even_if_it_converts():
    class Lenient(FakeQDateTime):
        """isValid() is False, yet toPyDateTime() returns a value."""

        def isValid(self) -> bool:
            return False

    assert first_qso_of(Lenient(datetime(2026, 9, 15, 18, 45))) is None


@pytest.mark.parametrize(
    "value",
    [
        datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-1))),
    ],
)
def test_datetime_out_of_range_in_utc_is_ignored(value):
    assert first_qso_of(value) is None


def test_datetime_with_a_broken_tzinfo_is_ignored():
    class Broken(tzinfo):
        def utcoffset(self, dt: datetime | None) -> int:  # must be a timedelta
            return 3600

    assert first_qso_of(datetime(2026, 9, 15, 18, 45, tzinfo=Broken())) is None


def test_first_and_last_across_mixed_inputs():
    rows = [
        {"qso_datetime": "2026-09-15T20:00:00+02:00"},  # 18:00 UTC
        {"qso_datetime": datetime(2026, 9, 15, 18, 30)},  # naive: 18:30 UTC
        {"qso_datetime": "2026-09-15 17:59:59.999"},
        {"qso_datetime": FakeQDateTime(datetime(2026, 9, 15, 21, 0), OFFSET, 2)},  # 19:00 UTC
        {"qso_datetime": "garbage"},
        {"qso_datetime": None},
        {},
    ]
    result = compute_stats(rows)
    assert result.first_qso == utc(2026, 9, 15, 17, 59, 59, 999000)
    assert result.last_qso == utc(2026, 9, 15, 19, 0)
    assert result.total == 7


ISO_SEEDS = [
    "2026-09-15T18:45:00Z",
    "2026-09-15T18:45:00.123Z",
    "2026-09-15T18:45:00.123456Z",
    "2026-09-15 18:45:00",
    "2026-09-15T18:45",
    "2026-09-15T18:45Z",
    "2026-09-15T18:45:00+02:00",
    "2026-09-15T18:45:00.123-05:30",
    "2026-09-15T18:45+23:59",
    "2026-09-15 00:00:00.000000-23:59",
    "2024-02-29T23:59:59.999Z",
    "2026-02-28T12:00:00Z",
    "0001-01-01T00:00:00Z",
    "0001-01-01T00:30+01:00",
    "9999-12-31T23:59:59.999999Z",
    "9999-12-31T23:30-01:00",
    "2026-09-15T24:00:00Z",
    "20260915T184500Z",
]


def mutate(text: str, rng: random.Random) -> str:
    """One or two random edits; digit swaps keep the layout (month 13, hour 24, ...)."""
    chars = list(text)
    for _ in range(rng.randint(1, 2)):
        position = rng.randrange(len(chars))
        operation = rng.randrange(5)
        if operation <= 1 and chars[position].isdigit():
            chars[position] = rng.choice("0123456789")
        elif operation == 2:
            chars[position] = rng.choice("0123456789-: T.+Z")
        elif operation == 3:
            del chars[position]
        else:
            chars.insert(position, rng.choice("0123456789-: T.+Z"))
    return "".join(chars)


def test_fast_iso_path_agrees_with_the_general_parser():
    # _parse_iso reads common layouts with datetime.fromisoformat (whose syntax differs
    # between Python versions); _parse_iso_general defines the accepted syntax.
    rng = random.Random(99)
    texts = list(ISO_SEEDS)
    texts += [mutate(seed, rng) for seed in ISO_SEEDS for _ in range(600)]
    fast = fast_rejected = parsed = 0
    for text in texts:
        result = stats._parse_iso(text)
        assert result == stats._parse_iso_general(text), text
        if result is not None:
            parsed += 1
            assert result.tzinfo is UTC, text
        if stats._FAST_ISO_RE.fullmatch(text) is not None:
            fast += 1
            fast_rejected += result is None
    # the fuzz reaches the fast path, including impossible dates it must reject
    assert fast > 2000
    assert fast_rejected > 400
    assert parsed > 2000


def test_single_qso_is_first_and_last():
    result = compute_stats([{"qso_datetime": "2026-09-15T18:45:00Z"}])
    assert result.first_qso == result.last_qso == AT


# --- robustness ------------------------------------------------------------------------------------


def test_missing_keys_and_numeric_strings():
    rows = [
        {"call": "YU1AB"},
        {"dxcc": "296", "distance_km": "68.5", "freq_mhz": "145.5"},
        {"gridsquare": "KN04"},
    ]
    result = compute_stats(rows)
    assert result.total == 3
    assert result.unique_calls == 1
    assert result.dxcc_count == 1
    assert result.grid_count == 1
    assert list(result.by_band.items()) == [("2m", 1), ("?", 2)]
    assert result.longest.distance_km == 68.5
    assert result.longest.band == "2m"


def test_non_mapping_rows_are_skipped():
    rows = [None, "YU1AB", 42, ["call"], [("call", "YU1AB")], {"call": "YU1CD"}]
    result = compute_stats(rows)
    assert result.total == 1
    assert result.unique_calls == 1


def test_any_mapping_is_accepted():
    data = {"call": "YU1AB", "band": "20m", "distance_km": 12.5}
    for mapping in (PlainMapping(data), types.MappingProxyType(data)):
        result = compute_stats([mapping])
        assert result.total == 1
        assert result.unique_calls == 1
        assert list(result.by_band.items()) == [("20m", 1)]
        assert result.longest.distance_km == 12.5


def test_sqlite_rows_are_accepted():
    # a GeoPackage is SQLite: a reader may hand over sqlite3.Row objects with ISO text times
    con = sqlite3.connect(":memory:")
    try:
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE qso (call TEXT, qso_datetime TEXT, band TEXT, dxcc INTEGER,"
            " distance_km REAL)"
        )
        con.executemany(
            "INSERT INTO qso VALUES (?, ?, ?, ?, ?)",
            [
                ("YU7ABC", "2026-09-15T18:45:00.000Z", "2m", 296, 68.5),
                ("DL1ABC", "2026-09-16T10:00:00.000Z", "20m", 230, 1001.5),
                ("W1AW", None, None, None, None),
            ],
        )
        rows = con.execute("SELECT * FROM qso").fetchall()
        result = compute_stats(rows)
    finally:
        con.close()
    assert result.total == 3
    assert result.dxcc_count == 2
    assert result.unique_calls == 3
    assert list(result.by_band.items()) == [("20m", 1), ("2m", 1), ("?", 1)]
    assert result.longest.call == "DL1ABC"
    assert (result.first_qso, result.last_qso) == (AT, utc(2026, 9, 16, 10))


def test_generator_input_is_consumed_once():
    consumed = []

    def rows():
        for item in LOG:
            consumed.append(item)
            yield item

    assert compute_stats(rows()) == compute_stats(LOG)
    assert len(consumed) == len(LOG)


def test_random_garbage_never_raises():
    rng = random.Random(1234)
    junk = [
        None,
        NULL,
        "",
        "  ",
        "?",
        "abc",
        "20m",
        "KN04ft",
        "2026-09-15T18:45:00Z",
        "2026-02-30T00:00Z",
        "296",
        "-1",
        "nan",
        "1e999",
        0,
        -1,
        1,
        296,
        10**400,
        0.5,
        -0.0,
        math.nan,
        math.inf,
        True,
        b"bytes",
        [],
        {},
        (1, 2),
        object(),
        date(2026, 9, 15),
        datetime(2026, 9, 15, 18, 45),
        datetime.max.replace(tzinfo=timezone(timedelta(hours=-1))),
        FakeQDateTime(None),
        Decimal("NaN"),
    ]
    rows = [{name: rng.choice(junk) for name in FIELD_NAMES} for _ in range(3000)]
    result = compute_stats(rows)
    assert result.total == 3000
    assert sum(result.by_band.values()) == 3000
    assert sum(result.by_mode.values()) == 3000
    assert sum(result.by_continent.values()) == 3000
    assert 0 < result.grid_count <= 1
    assert result.first_qso is not None
    assert result.first_qso.tzinfo is UTC


# --- performance ---------------------------------------------------------------------------------


def big_log(count: int) -> list[dict[str, object]]:
    """Rows as read_qso_rows returns them, most times as GeoPackage ISO text (the slow path)."""
    rng = random.Random(20260915)
    bands = [("160m", 1.84), ("80m", 3.573), ("40m", 7.074), ("20m", 14.074), ("15m", 21.074)]
    bands += [("10m", 28.074), ("6m", 50.313), ("2m", 144.174)]
    modes = [("FT8", None), ("MFSK", "FT4"), ("CW", None), ("SSB", None), ("FM", None)]
    start = datetime(2015, 1, 1)
    result = []
    for i in range(count):
        band, freq = rng.choice(bands)
        mode, submode = rng.choice(modes)
        when = start + timedelta(seconds=i * 3571 + rng.randrange(60))
        kind = i % 4
        if kind == 0:
            stamp: object = when.replace(tzinfo=UTC)
        elif kind == 1:
            stamp = (when + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+02:00")
        else:
            stamp = when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{rng.randrange(1000):03d}Z"
        code = rng.randrange(1, 500)
        grid = (
            rng.choice("ABCDEFGHIJKLMNOPQR")
            + rng.choice("ABCDEFGHIJKLMNOPQR")
            + f"{rng.randrange(100):02d}"
            + rng.choice("abcdefghijklmnopqrstuvwx")
            + rng.choice("abcdefghijklmnopqrstuvwx")
        )
        result.append(
            row(
                call=f"YU{rng.randrange(10)}{rng.choice('ABCDEFGH')}{i % 5000:04d}",
                qso_datetime=stamp,
                band=band,
                mode=mode,
                submode=submode,
                freq_mhz=freq,
                rst_sent="-10",
                rst_rcvd="-12",
                gridsquare=grid if i % 10 else None,
                my_gridsquare="KN04ft",
                dxcc=code if i % 7 else None,
                country=f"Country {code}",
                cont=rng.choice(CONTINENTS) if i % 13 else None,
                cq_zone=15,
                itu_zone=28,
                distance_km=rng.uniform(0.0, 20000.0) if i % 9 else None,
                bearing_deg=rng.uniform(0.0, 360.0),
                loc_source="grid",
                source="adif:big.adi",
                dedup_key=f"KEY{i}",
                adif_extra="{}",
            )
        )
    return result


@pytest.mark.slow
def test_50000_rows_under_half_a_second():
    rows = big_log(50_000)
    timings = []
    for _ in range(3):  # best of three: measures the code, not a busy machine
        start = time.perf_counter()
        result = compute_stats(rows)
        timings.append(time.perf_counter() - start)
    assert result.total == 50_000
    assert result.first_qso == rows[0]["qso_datetime"]  # times increase with the row index
    assert result.last_qso is not None
    assert result.dxcc_count == 499  # codes 1..499; every code-less name also has a code
    assert sum(result.by_band.values()) == 50_000
    assert min(timings) < 0.5, timings
