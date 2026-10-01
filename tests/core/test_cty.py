"""Tests for hamq.core.cty: AD1C cty.dat / cty.csv parsing and callsign -> DXCC entity lookup.

The fixture ``tests/fixtures/cty/cty_excerpt.dat`` / ``.csv`` is a 20-entity excerpt of the
real AD1C "Big CTY" files of 15 September 2026 (see the README there). Expected zones, codes
and coordinates below were checked against the full real files.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import time
from pathlib import Path

import pytest

from hamq.core import cty
from hamq.core.cty import CtyDatabase, CtyMatch, Entity, clean_call

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "cty"
DAT = FIXTURES / "cty_excerpt.dat"
CSV = FIXTURES / "cty_excerpt.csv"
CATALOG = ROOT / "hamq" / "i18n" / "sr_Latn" / "core_cty.json"
SOURCE = ROOT / "hamq" / "core" / "cty.py"

EXCERPT_NAMES = [
    "Montenegro",
    "Vienna Intl Ctr",
    "Croatia",
    "Fed. Rep. of Germany",
    "Bosnia-Herzegovina",
    "Scotland",
    "Shetland Islands",
    "Italy",
    "African Italy",
    "Sicily",
    "United States",
    "Guantanamo Bay",
    "Hawaii",
    "Alaska",
    "Austria",
    "Asiatic Turkey",
    "European Turkey",
    "Canada",
    "Australia",
    "Serbia",
]

# Real header lines (cty.dat of 15 September 2026) for small synthetic databases.
ENGLAND = "England:                  14:  27:  EU:   52.77:     1.47:     0.0:  G:"
NORWAY = "Norway:                   14:  18:  EU:   61.00:    -9.00:    -1.0:  LA:"
GERMANY = "Fed. Rep. of Germany:     14:  28:  EU:   51.00:   -10.00:    -1.0:  DL:"
SERBIA = "Serbia:                   15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:"
FRANCE = "France:                   14:  27:  EU:   46.00:    -2.00:    -1.0:  F:"
ARGENTINA = "Argentina:                13:  14:  SA:  -32.50:    62.13:     3.0:  LU:"
LATVIA = "Latvia:                   15:  29:  EU:   57.03:   -24.65:    -2.0:  YL:"
EASTER_ISLAND = "Easter Island:            12:  63:  SA:  -27.10:   109.37:     6.0:  CE0Y:"
GUANTANAMO = "Guantanamo Bay:           08:  11:  NA:   20.00:    75.00:     5.0:  KG4:"
USA = "United States:            05:  08:  NA:   37.60:    91.87:     5.0:  K:"
SVALBARD = "Svalbard:                 40:  18:  EU:   78.00:   -16.00:    -1.0:  JW:"
BEAR_ISLAND = "Bear Island:              40:  18:  EU:   74.43:   -19.08:    -1.0:  *JW/b:"
ANTARCTICA = "Antarctica:               13:  74:  SA:  -90.00:     0.00:     0.0:  CE9:"
EUROPEAN_RUSSIA = "European Russia:          16:  29:  EU:   53.65:   -41.37:    -4.0:  UA:"


@pytest.fixture(scope="module")
def db() -> CtyDatabase:
    return CtyDatabase.from_files(DAT, CSV)


@pytest.fixture(scope="module")
def db_dat_only() -> CtyDatabase:
    return CtyDatabase.from_files(str(DAT))


@pytest.fixture(scope="module")
def dat_text() -> str:
    return DAT.read_text(encoding="ascii")


def entity(db: CtyDatabase, name: str) -> Entity:
    found = [e for e in db.entities if e.name == name]
    assert len(found) == 1, name
    return found[0]


def mentions_number(message: str, number: int) -> bool:
    """Language-independent check that a warning names a line number."""
    return re.search(rf"(?<![\d.]){number}(?![\d.])", message) is not None


def resolve(db: CtyDatabase, call: str) -> CtyMatch:
    match = db.lookup(call)
    assert match is not None, call
    return match


# --- download URLs -----------------------------------------------------------------------


def test_default_urls_point_to_ad1c_big_cty():
    assert cty.DEFAULT_CTY_URL == "https://www.country-files.com/bigcty/cty.dat"
    assert cty.DEFAULT_CTY_CSV_URL == "https://www.country-files.com/bigcty/cty.csv"


# --- loading ---------------------------------------------------------------------------


def test_fixture_is_ad1c_excerpt_with_crlf_and_comment_header():
    raw = DAT.read_bytes()
    assert raw.startswith(b"# ")
    assert b"AD1C" in raw[:600] and b"country-files.com" in raw[:600]
    assert b"\r\n" in raw  # line endings as in the real file
    assert CSV.read_bytes().startswith(b"# ")


def test_entity_count_and_file_order(db):
    assert len(db) == 20
    assert [e.name for e in db.entities] == EXCERPT_NAMES
    assert db.warnings == []


def test_entity_fields_from_header(db):
    serbia = entity(db, "Serbia")
    assert serbia == Entity(
        name="Serbia",
        cq_zone=15,
        itu_zone=28,
        continent="EU",
        lat=44.0,
        lon=21.0,
        utc_offset=-1.0,
        primary_prefix="YU",
        wae_only=False,
        dxcc=296,
        dxcc_name="Serbia",
    )


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [
        ("Serbia", 44.0, 21.0),
        ("United States", 37.6, -91.87),
        ("Hawaii", 21.12, -157.48),
        ("Australia", -23.7, 132.33),
        ("Scotland", 56.82, -4.18),
        ("Shetland Islands", 60.5, -1.5),
    ],
)
def test_longitude_is_east_positive(db, name, lat, lon):
    e = entity(db, name)
    assert e.lat == pytest.approx(lat)
    assert e.lon == pytest.approx(lon)


def test_match_coordinates_are_east_positive(db):
    match = resolve(db, "YU1AB")
    assert (match.lat, match.lon) == (pytest.approx(44.0), pytest.approx(21.0))


def test_utc_offset_keeps_cty_dat_sign(db):
    # cty.dat convention: local time = UTC - utc_offset (Serbia is UTC+1 -> -1.0)
    assert entity(db, "Serbia").utc_offset == -1.0
    assert entity(db, "United States").utc_offset == 5.0
    assert resolve(db, "VK2XYZ").utc_offset == -10.0


def test_zero_longitude_is_not_negative_zero():
    db = CtyDatabase.from_text(
        "Nullland:  14:  27:  EU:   51.50:     0.00:     0.0:  ZZ:\n    ZZ;\n"
    )
    lon = db.entities[0].lon
    assert lon == 0.0 and str(lon) == "0.0"


def test_entities_are_frozen(db):
    with pytest.raises(dataclasses.FrozenInstanceError):
        db.entities[0].name = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolve(db, "YU1AB").cq_zone = 1  # type: ignore[misc]


def test_lf_and_crlf_give_the_same_database(dat_text):
    lf = CtyDatabase.from_text(dat_text.replace("\r\n", "\n"))
    crlf = CtyDatabase.from_text(dat_text.replace("\r\n", "\n").replace("\n", "\r\n"))
    assert lf.entities == crlf.entities
    for call in ("YU1AB", "AD1C", "W1AW/KH6", "IT9ABC", "VE3ABC"):
        assert lf.lookup(call) == crlf.lookup(call)


def test_from_text_equals_from_files(db, dat_text):
    other = CtyDatabase.from_text(dat_text, CSV.read_text(encoding="ascii"))
    assert other.entities == db.entities


# --- the skill's test table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "name", "continent", "cq", "itu"),
    [
        ("YU1AB", "Serbia", "EU", 15, 28),
        ("YT0A", "Serbia", "EU", 15, 28),
        ("9A1GS", "Croatia", "EU", 15, 28),
        ("W1AW", "United States", "NA", 5, 8),
        ("VK2XYZ", "Australia", "OC", 30, 59),
        ("DL1ABC/P", "Fed. Rep. of Germany", "EU", 14, 28),
        ("YU/DL1ABC", "Serbia", "EU", 15, 28),
        ("DL1ABC/YU", "Serbia", "EU", 15, 28),
        ("KH6XX", "Hawaii", "OC", 31, 61),
        ("KL7ABC", "Alaska", "NA", 1, 1),
        ("VE1ABC", "Canada", "NA", 5, 9),
        ("OE1ABC", "Austria", "EU", 15, 28),
        ("GM3ABC", "Scotland", "EU", 14, 27),
        ("I1ABC", "Italy", "EU", 15, 28),
        ("4O1ABC", "Montenegro", "EU", 15, 28),
        ("E71ABC", "Bosnia-Herzegovina", "EU", 15, 28),
    ],
)
def test_skill_table(db, call, name, continent, cq, itu):
    match = resolve(db, call)
    assert match.entity.name == name
    assert (match.continent, match.cq_zone, match.itu_zone) == (continent, cq, itu)


@pytest.mark.parametrize("call", ["DL1ABC/MM", "DL1ABC/AM", "W1AW/MM", "YU1AB/MM/P", "DL1ABC/P/AM"])
def test_maritime_and_aeronautical_mobile_have_no_entity(db, call):
    assert db.lookup(call) is None


def test_prefix_match_reports_matched_prefix(db):
    match = resolve(db, "YU1AB")
    assert (match.matched, match.exact) == ("YU", False)
    assert resolve(db, "YT0A").matched == "YT"


# --- overrides ---------------------------------------------------------------------------


def test_exact_call_with_zone_overrides(db):
    # =AD1C(4)[7] in the United States list: CQ 4, ITU 7 instead of the entity's 5 / 8
    match = resolve(db, "AD1C")
    assert match.exact is True
    assert match.matched == "AD1C"
    assert match.entity.name == "United States"
    assert (match.cq_zone, match.itu_zone) == (4, 7)
    assert (match.entity.cq_zone, match.entity.itu_zone) == (5, 8)
    assert (match.continent, match.lat, match.lon) == ("NA", 37.6, -91.87)


def test_exact_call_with_itu_override_only(db):
    match = resolve(db, "VE2FK")  # =VE2FK[9]
    assert (match.exact, match.cq_zone, match.itu_zone) == (True, 5, 9)


@pytest.mark.parametrize(
    ("call", "matched", "cq", "itu"),
    [
        ("VE3ABC", "VE3", 4, 4),  # VE3(4)[4]
        ("VA2XYZ", "VA2", 5, 4),  # VA2[4]
        ("VK6ABC", "VK6", 29, 58),  # VK6(29)[58]
        ("VK4ABC", "VK4", 30, 55),  # VK4[55]
        ("W6ABC", "W6", 3, 6),  # W6(3)[6]
        ("W8ABC", "W8", 4, 8),  # W8(4): CQ override only
        ("K0ABC", "K0", 4, 7),
    ],
)
def test_prefix_entries_with_overrides(db, call, matched, cq, itu):
    match = resolve(db, call)
    assert (match.matched, match.exact) == (matched, False)
    assert (match.cq_zone, match.itu_zone) == (cq, itu)


def test_five_character_prefix_beats_shorter_ones():
    # RI1AN(29)[69], Russian stations in Antarctica, is one of the real 5-character prefixes
    db = CtyDatabase.from_text(f"{EUROPEAN_RUSSIA}\n    R;\n{ANTARCTICA}\n    RI1AN(29)[69];\n")
    match = resolve(db, "RI1ANX")
    assert (match.entity.name, match.matched, match.cq_zone, match.itu_zone) == (
        "Antarctica",
        "RI1AN",
        29,
        69,
    )
    assert resolve(db, "RI1AB").entity.name == "European Russia"


def test_all_override_kinds_in_any_order():
    text = (
        "Testland:                 15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:\n"
        "    YU,YU8(16)[29]{AS}<42.50/-21.00>~-1.5~,\n"
        "    =YU1XYZ~-2.0~{AF}<-10.00/-20.00>[53](38),=YU2ABC[30]{eu};\n"
    )
    db = CtyDatabase.from_text(text)
    assert db.warnings == []
    m = resolve(db, "YU8ABC")
    assert (m.cq_zone, m.itu_zone, m.continent) == (16, 29, "AS")
    assert (m.lat, m.lon, m.utc_offset) == (42.5, 21.0, -1.5)
    m = resolve(db, "YU1XYZ")
    assert m.exact
    assert (m.cq_zone, m.itu_zone, m.continent) == (38, 53, "AF")
    assert (m.lat, m.lon, m.utc_offset) == (-10.0, 20.0, -2.0)
    m = resolve(db, "YU2ABC")
    assert (m.cq_zone, m.itu_zone, m.continent, m.utc_offset) == (15, 30, "EU", -1.0)
    assert (m.lat, m.lon) == (44.0, 21.0)


def test_win_test_style_comments_and_coordinate_overrides():
    # layout of the Win-Test cty_wt_mod.dat: '#' lines inside the entity, <lat/lon> overrides
    text = (
        "# ADIF 4\n"
        "Agalega & St. Brandon:    39:  53:  AF:  -10.45:   -56.67:    -4.0:  3B6:\n"
        "# Agalega Islands\n"
        "    3B6<-10.42/-56.58>,\n"
        "# St. Brandon Islands\n"
        "    3B7<-16.58/-59.62>;\n"
    )
    db = CtyDatabase.from_text(text)
    assert db.warnings == []
    assert (resolve(db, "3B6AB").lat, resolve(db, "3B6AB").lon) == (-10.42, 56.58)
    assert (resolve(db, "3B7C").lat, resolve(db, "3B7C").lon) == (-16.58, 59.62)
    assert db.entities[0].lon == 56.67


# --- exact calls and their priority ---------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "name"),
    [
        ("YU4WU", "Bosnia-Herzegovina"),  # =YU4WU beats the YU prefix
        ("4O0A", "Serbia"),  # =4O0A in Serbia beats the 4O prefix (Montenegro)
        ("MR6TMS", "Scotland"),  # README check of Big CTY: Scotland, not England
        ("KH6XX/0", "United States"),  # =KH6XX/0(4)[7], while KH6XX is Hawaii
        ("VER20260915", "Canada"),  # version marker of the excerpt's release
        ("YU/IZ1VUC/LH", "Serbia"),
        ("VE2/G3ZAY/P", "Canada"),
    ],
)
def test_exact_calls_beat_prefixes(db, call, name):
    match = resolve(db, call)
    assert match.exact is True
    assert match.entity.name == name


def test_exact_maritime_mobile_call_listed_in_file(db):
    # =II0PN/MM(40) is listed in Italy: exact match wins over the /MM rule
    match = resolve(db, "II0PN/MM")
    assert (match.entity.name, match.cq_zone, match.exact) == ("Italy", 40, True)
    assert resolve(db, "NQ4I/AM").entity.name == "United States"


@pytest.mark.parametrize("call", ["II0PN/MM/P", "II0PN/P/MM", "II0PN/QRP/MM/P"])
def test_exact_maritime_mobile_call_with_other_modifiers(db, call):
    # the /MM rule applies only after every exact candidate was tried
    match = resolve(db, call)
    assert (match.entity.name, match.matched, match.cq_zone, match.exact) == (
        "Italy",
        "II0PN/MM",
        40,
        True,
    )


@pytest.mark.parametrize(
    ("call", "matched", "name"),
    [
        ("4U1VIC/P", "4U1VIC", "Vienna Intl Ctr"),
        ("AD1C/QRP", "AD1C", "United States"),
        ("VE2FK/M", "VE2FK", "Canada"),
        ("IT9ACJ/I/BN/P", "IT9ACJ/I/BN", "Sicily"),  # progressive removal from the end
        ("IT9ACJ/I/BN/P/QRP", "IT9ACJ/I/BN", "Sicily"),  # more than one suffix removed
        ("YU/IZ1VUC/LH/P", "YU/IZ1VUC/LH", "Serbia"),
        ("4U/DA1KY/P", "4U/DA1KY", "Serbia"),
        # =9A70DP/KA (Croatian county suffix): a modifier in the middle is dropped too;
        # without the exact entry KA would be a location (K -> United States)
        ("9A70DP/KA/P", "9A70DP/KA", "Croatia"),
        ("9A70DP/P/KA", "9A70DP/KA", "Croatia"),
    ],
)
def test_exact_after_dropping_non_location_suffixes(db, call, matched, name):
    match = resolve(db, call)
    assert (match.exact, match.matched, match.entity.name) == (True, matched, name)


def test_location_after_a_middle_modifier(db):
    match = resolve(db, "DL1ABC/P/OE")  # not listed: portable, operating from Austria
    assert (match.entity.name, match.matched, match.exact) == ("Austria", "OE", False)


def test_exact_overrides_kept_after_dropping_suffix(db):
    match = resolve(db, "AD1C/P")
    assert (match.cq_zone, match.itu_zone) == (4, 7)


def test_first_occurrence_wins_for_duplicate_exact_calls(db):
    # =GB2ELH is listed in Scotland and again in Shetland Islands; the file is read top down
    assert resolve(db, "GB2ELH").entity.name == "Scotland"
    assert resolve(db, "G0FBJ").entity.name == "Scotland"
    assert resolve(db, "2M0BDR").entity.name == "Shetland Islands"


def test_first_occurrence_wins_for_duplicate_prefixes():
    text = f"{SERBIA}\n    YU,ZZ;\n{GERMANY}\n    DL,ZZ;\n"
    db = CtyDatabase.from_text(text)
    assert resolve(db, "ZZ1A").entity.name == "Serbia"


def test_first_csv_row_wins_for_a_duplicated_primary_prefix():
    csv_text = (
        "YU,Serbia,296,EU,15,28,44.00,-21.00,-1.0,YU;\n"
        "YU,Serbia,999,EU,15,28,44.00,-21.00,-1.0,YU;\n"
    )
    db = CtyDatabase.from_text(f"{SERBIA}\n    YU;\n", csv_text)
    assert entity(db, "Serbia").dxcc == 296
    assert db.warnings == []


# --- portable designators ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "name", "matched"),
    [
        ("KH6/W1AW", "Hawaii", "KH6"),
        ("W1AW/KH6", "Hawaii", "KH6"),
        ("YU/DL1ABC/P", "Serbia", "YU"),
        ("DL1ABC/YU/P", "Serbia", "YU"),
        ("OE/YU1AB", "Austria", "OE"),
        ("YU1AB/OE", "Austria", "OE"),
        ("4O/YU1AB", "Montenegro", "4O"),
        ("YU1AB/E7", "Bosnia-Herzegovina", "E7"),
        ("I/DL1ABC", "Italy", "I"),
        ("W1AW/VE3", "Canada", "VE3"),
        ("VE3ABC/W6", "United States", "W6"),
        ("KH6XX/W1", "United States", "W"),
        ("MM/DL1ABC", "Scotland", "MM"),  # MM in front is a Scottish prefix, not maritime
    ],
)
def test_prefix_and_suffix_locations(db, call, name, matched):
    match = resolve(db, call)
    assert (match.entity.name, match.matched, match.exact) == (name, matched, False)


def test_location_prefix_overrides_apply(db):
    match = resolve(db, "W1AW/VE3")
    assert (match.cq_zone, match.itu_zone) == (4, 4)
    match = resolve(db, "VE3ABC/W6")
    assert (match.cq_zone, match.itu_zone) == (3, 6)


def test_home_call_exact_entry_is_not_used_for_other_locations(db):
    # AD1C is an exact entry (CQ 4); AD1C/KH6 operates from Hawaii
    match = resolve(db, "AD1C/KH6")
    assert (match.entity.name, match.cq_zone, match.exact) == ("Hawaii", 31, False)


@pytest.mark.parametrize(
    ("call", "name", "matched"),
    [
        # parts of equal length: the part with the longer prefix match is the location,
        # whichever order the call is written in
        ("K1A/KL7", "Alaska", "KL"),
        ("KL7/K1A", "Alaska", "KL"),
        ("W1A/VE1", "Canada", "VE"),
        ("VE1/W1A", "Canada", "VE"),
        ("K1A/VE3", "Canada", "VE3"),
        ("VE3/K1A", "Canada", "VE3"),
        # equally long prefix matches: the first part, as in WSJT-X
        ("VE3/KH6", "Canada", "VE3"),
        ("KH6/VE3", "Hawaii", "KH6"),
    ],
)
def test_equal_length_parts_location_is_the_more_specific_prefix(db, call, name, matched):
    match = resolve(db, call)
    assert (match.entity.name, match.matched, match.exact) == (name, matched, False)


@pytest.mark.parametrize("call", ["YT1A/CE0Y", "CE0Y/YT1A", "W1AW/CE0Y", "CE0Y/W1AW"])
def test_equal_length_location_that_is_not_a_listed_prefix(call):
    # CE0Y is Easter Island's primary prefix, but only CE0 is listed (real prefix list)
    db = CtyDatabase.from_text(
        f"{EASTER_ISLAND}\n    3G0,CA0,CB0,CC0,CD0,CE0,XQ0,XR0;\n"
        f"{SERBIA}\n    YT,YU;\n{USA}\n    K,N,W;\n"
    )
    match = resolve(db, call)
    assert (match.entity.name, match.matched) == ("Easter Island", "CE0")


@pytest.mark.parametrize(
    "suffix",
    [
        "P",
        "M",
        "A",
        "B",
        "R",
        "QRP",
        "QRPP",
        "LH",
        "LS",
        "LT",
        "FF",
        "FD",
        "YL",
        "LGT",
        "BCN",
        "JOTA",
        "1",
        "9",
    ],
)
def test_non_location_suffixes_are_ignored(db, suffix):
    match = resolve(db, f"DL1ABC/{suffix}")
    assert (match.entity.name, match.matched) == ("Fed. Rep. of Germany", "DL")


def test_digit_suffix_is_dropped_not_a_call_area_change(db):
    match = resolve(db, "W1AW/7")  # per the skill: /7 is not a location
    assert (match.entity.name, match.matched, match.cq_zone, match.itu_zone) == (
        "United States",
        "W",
        5,
        8,
    )


def test_single_letter_suffix_is_not_a_country():
    # a single letter is taken as an operating condition (portable, mobile, rover, ...), not
    # a country; the Big CTY lists e.g. CALL/D 1049 and CALL/F 119 times under the home entity
    db = CtyDatabase.from_text(f"{ENGLAND}\n    G,M;\n{GERMANY}\n    DL;\n")
    assert resolve(db, "DL1ABC/M").entity.name == "Fed. Rep. of Germany"
    assert resolve(db, "DL1ABC/G").entity.name == "Fed. Rep. of Germany"
    assert resolve(db, "G/DL1ABC").entity.name == "England"
    assert resolve(db, "M/DL1ABC").entity.name == "England"


def test_lighthouse_suffix_is_not_norway():
    db = CtyDatabase.from_text(f"{NORWAY}\n    LA,LB,LH;\n{GERMANY}\n    DL;\n")
    assert resolve(db, "DL1ABC/LH").entity.name == "Fed. Rep. of Germany"
    assert resolve(db, "LH1AB").entity.name == "Norway"
    assert resolve(db, "DL1ABC/LA").entity.name == "Norway"


@pytest.fixture(scope="module")
def activity_db() -> CtyDatabase:
    # real header lines and prefix lists of the countries these suffixes look like
    return CtyDatabase.from_text(
        f"{FRANCE}\n    F,HW,HX,HY,TH,TM,TO,TP,TQ,TV,TX;\n"
        f"{ARGENTINA}\n    AY,AZ,L1,L2,L3,L4,L5,L6,L7,L8,L9,LO,LP,LQ,LR,LS,LT,LU,LV,LW;\n"
        f"{LATVIA}\n    YL;\n{NORWAY}\n    LA,LB,LH;\n{GERMANY}\n    DL;\n{SERBIA}\n    YT,YU;\n"
    )


@pytest.mark.parametrize(
    "call",
    [
        "DL1ABC/FF",  # WWFF (flora and fauna), not France
        "DL1ABC/FD",  # field day, not France
        "DL1ABC/LT",  # lighthouse activation, not Argentina
        "DL1ABC/LS",  # lightship activation, not Argentina
        "DL1ABC/YL",  # YL operator, not Latvia
        "DL1ABC/LH",  # lighthouse, not Norway
        "DL1ABC/FF/P",
        "DL1ABC/P/FF",
    ],
)
def test_activity_suffixes_are_not_locations(activity_db, call):
    match = resolve(activity_db, call)
    assert (match.entity.name, match.matched) == ("Fed. Rep. of Germany", "DL")


@pytest.mark.parametrize(
    ("call", "name"),
    [
        ("F/DL1ABC", "France"),  # in front they are location prefixes
        ("YL/DL1ABC", "Latvia"),
        ("LT/DL1ABC", "Argentina"),
        ("YL2ABC", "Latvia"),  # and real calls stay where they are
        ("LT1F", "Argentina"),
        ("DL1ABC/LU", "Argentina"),  # other two-letter suffixes are still locations
        ("DL1ABC/LA", "Norway"),
        ("DL1ABC/YU", "Serbia"),
    ],
)
def test_activity_suffix_letters_elsewhere_are_locations(activity_db, call, name):
    assert resolve(activity_db, call).entity.name == name


def test_unknown_location_falls_back_to_home_call(db):
    assert resolve(db, "DL1ABC/XX").entity.name == "Fed. Rep. of Germany"


@pytest.mark.parametrize(
    ("call", "home", "name", "cq", "itu"),
    [
        # XX is no prefix, so the home call decides, and its exact entry beats its prefix
        ("4U1VIC/XX", "4U1VIC", "Vienna Intl Ctr", 15, 28),  # prefix 4U alone: Italy
        ("XX/4U1VIC", "4U1VIC", "Vienna Intl Ctr", 15, 28),
        ("AD1C/XX", "AD1C", "United States", 4, 7),  # =AD1C(4)[7]; prefix AD: CQ 5, ITU 8
        ("KG4CAN/XX", "KG4CAN", "Hawaii", 31, 61),  # =KG4CAN; the KG4 rule: United States
        ("4O0A/XX", "4O0A", "Serbia", 15, 28),  # =4O0A; prefix 4O alone: Montenegro
        ("4O0A/XX/P", "4O0A", "Serbia", 15, 28),
    ],
)
def test_unknown_location_falls_back_to_the_exact_home_call(db, call, home, name, cq, itu):
    match = resolve(db, call)
    assert (match.entity.name, match.matched, match.exact) == (name, home, True)
    assert (match.cq_zone, match.itu_zone) == (cq, itu)


# --- KG4: Guantanamo Bay or United States ----------------------------------------------------


@pytest.mark.parametrize(
    ("call", "name", "dxcc", "matched", "exact"),
    [
        # cty.dat maps the whole KG4 prefix to Guantanamo Bay. Only KG4 plus two letters is
        # Guantanamo Bay; KG4 plus one or three letters is a US call (WSJT-X has the same rule)
        ("KG4ABC", "United States", 291, "K", False),
        ("KG4A", "United States", 291, "K", False),
        ("KG4ZZZ", "United States", 291, "K", False),
        ("KG4ABC/P", "United States", 291, "K", False),
        ("KG4ABC/M", "United States", 291, "K", False),
        ("KG4ABC/4", "United States", 291, "K", False),
        ("KG4A/QRP", "United States", 291, "K", False),
        ("KG4AB", "Guantanamo Bay", 105, "KG4", False),
        ("KG4ZZ", "Guantanamo Bay", 105, "KG4", False),
        ("KG4AB/P", "Guantanamo Bay", 105, "KG4", False),
        ("KG44XX", "Guantanamo Bay", 105, "KG4", False),  # not a US call format
        ("KG4", "Guantanamo Bay", 105, "KG4", False),
        # KG4 as a location part is Guantanamo Bay
        ("KG4/W1AW", "Guantanamo Bay", 105, "KG4", False),
        ("K1ABC/KG4", "Guantanamo Bay", 105, "KG4", False),
        ("KG4ABC/KG4", "Guantanamo Bay", 105, "KG4", False),
        ("W1AW/KG4", "Guantanamo Bay", 105, "W1AW/KG4", True),
        # exact entries win over the rule
        ("KG4AC", "Guantanamo Bay", 105, "KG4AC", True),
        ("KG4NE/P", "Guantanamo Bay", 105, "KG4NE", True),
        ("KG44WW", "Guantanamo Bay", 105, "KG44WW", True),
        ("KG4CAN", "Hawaii", 110, "KG4CAN", True),
        ("KG4DFX", "United States", 291, "KG4DFX", True),
        # and so does a location part
        ("KG4ABC/KH6", "Hawaii", 110, "KH6", False),
    ],
)
def test_kg4_calls_guantanamo_bay_or_united_states(db, call, name, dxcc, matched, exact):
    match = resolve(db, call)
    assert (match.entity.name, match.dxcc, match.matched, match.exact) == (
        name,
        dxcc,
        matched,
        exact,
    )


def test_kg4_us_call_has_united_states_values(db):
    match = resolve(db, "KG4ABC")
    assert match.entity == entity(db, "United States")
    assert (match.cq_zone, match.itu_zone, match.continent) == (5, 8, "NA")
    assert (match.lat, match.lon, match.utc_offset) == (37.6, -91.87, 5.0)
    assert match.dxcc_name == "United States"
    gitmo = resolve(db, "KG4AB")
    assert (gitmo.cq_zone, gitmo.itu_zone, gitmo.continent) == (8, 11, "NA")
    # an exact entry keeps its overrides: =KG4V(4) in the United States list
    match = resolve(db, "KG4V")
    assert (match.entity.name, match.exact, match.cq_zone, match.itu_zone) == (
        "United States",
        True,
        4,
        8,
    )


def test_kg4_rule_without_united_states_entity_keeps_the_file_entity():
    db = CtyDatabase.from_text(f"{GUANTANAMO}\n    KG4,=KG4AC;\n{SERBIA}\n    YU;\n")
    assert resolve(db, "KG4ABC").entity.name == "Guantanamo Bay"


# --- WAE-only entities and DXCC identity ------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "name", "wae_only", "prefix", "dxcc", "dxcc_name", "continent"),
    [
        ("IT9ABC", "Sicily", True, "IT9", 248, "Italy", "EU"),
        ("IG9ABC", "African Italy", True, "IG9", 248, "Italy", "AF"),
        ("I1ABC", "Italy", False, "I", 248, "Italy", "EU"),
        ("TA1ABC", "European Turkey", True, "TA1", 390, "Turkey", "EU"),
        ("TA2ABC", "Asiatic Turkey", False, "TA", 390, "Turkey", "AS"),
        ("4U1VIC", "Vienna Intl Ctr", True, "4U1V", 206, "Austria", "EU"),
        ("OE1ABC", "Austria", False, "OE", 206, "Austria", "EU"),
        ("2M0BDR", "Shetland Islands", True, "GM/s", 279, "Scotland", "EU"),
        ("GM3ABC", "Scotland", False, "GM", 279, "Scotland", "EU"),
        ("YU1AB", "Serbia", False, "YU", 296, "Serbia", "EU"),
    ],
)
def test_wae_entities_and_dxcc_identity(
    db, call, name, wae_only, prefix, dxcc, dxcc_name, continent
):
    match = resolve(db, call)
    assert match.entity.name == name
    assert match.entity.wae_only is wae_only
    assert match.entity.primary_prefix == prefix
    assert match.continent == continent
    assert match.dxcc == dxcc
    assert match.dxcc_name == dxcc_name


def test_african_italy_zones(db):
    match = resolve(db, "IG9ABC")
    assert (match.cq_zone, match.itu_zone) == (33, 37)


@pytest.mark.parametrize(
    ("call", "dxcc_name"),
    [
        ("IT9ABC", "Italy"),
        ("IG9ABC", "Italy"),
        ("TA1ABC", "Turkey"),
        ("TA2ABC", "Turkey"),
        ("4U1VIC", "Austria"),
        ("2M0BDR", "Scotland"),
        ("YU1AB", "Serbia"),
    ],
)
def test_wae_parent_without_csv_uses_documented_table(db_dat_only, call, dxcc_name):
    match = resolve(db_dat_only, call)
    assert match.dxcc_name == dxcc_name
    assert match.dxcc is None


def test_without_csv_no_entity_has_a_dxcc_code(db_dat_only):
    assert all(e.dxcc is None for e in db_dat_only.entities)
    assert len(db_dat_only) == 20
    assert db_dat_only.warnings == []  # no cty.csv given: nothing to report


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Serbia", 296),
        ("Fed. Rep. of Germany", 230),
        ("United States", 291),
        ("Italy", 248),
        ("Croatia", 497),
        ("Montenegro", 514),
        ("Bosnia-Herzegovina", 501),
        ("Canada", 1),
        ("Australia", 150),
        ("Hawaii", 110),
        ("Alaska", 6),
        ("Austria", 206),
        ("Scotland", 279),
        ("Asiatic Turkey", 390),
        ("Guantanamo Bay", 105),
        # WAE-only entities carry the code of the DXCC entity they count for
        ("Sicily", 248),
        ("African Italy", 248),
        ("European Turkey", 390),
        ("Vienna Intl Ctr", 206),
        ("Shetland Islands", 279),
    ],
)
def test_adif_dxcc_codes_from_csv(db, name, code):
    assert entity(db, name).dxcc == code


def test_wae_parent_derived_from_csv_code():
    # a WAE-only entity unknown to the built-in table gets its parent from the cty.csv code
    dat = (
        f"{SERBIA}\n    YU;\n"
        "Test Island:              15:  28:  EU:   44.00:   -21.00:    -1.0:  *YU/t:\n    =YU9T;\n"
    )
    csv_text = (
        "YU,Serbia,296,EU,15,28,44.00,-21.00,-1.0,YU;\n"
        "*YU/t,Test Island,296,EU,15,28,44.00,-21.00,-1.0,=YU9T;\n"
    )
    match = resolve(CtyDatabase.from_text(dat, csv_text), "YU9T")
    assert (match.entity.name, match.entity.wae_only, match.dxcc, match.dxcc_name) == (
        "Test Island",
        True,
        296,
        "Serbia",
    )
    alone = resolve(CtyDatabase.from_text(dat), "YU9T")
    assert (alone.dxcc, alone.dxcc_name) == (None, "Test Island")


def test_bear_island_counts_for_svalbard_without_csv():
    # the sixth WAE-only entity of the real file (real headers and exact calls); Norway's LA
    # would be the wrong parent
    db = CtyDatabase.from_text(
        f"{NORWAY}\n    LA,LB;\n{SVALBARD}\n    JW;\n{BEAR_ISLAND}\n    =JW0BEA,=JW1I;\n"
    )
    match = resolve(db, "JW0BEA")
    assert (match.entity.name, match.entity.wae_only, match.dxcc, match.dxcc_name) == (
        "Bear Island",
        True,
        None,
        "Svalbard",
    )
    assert resolve(db, "JW5ABC").dxcc_name == "Svalbard"


def test_wae_entity_without_its_own_csv_row_gets_the_parent_code(dat_text):
    # a cty.csv without the Sicily row: Sicily still counts for Italy, nothing is reported
    csv_text = "\n".join(
        line
        for line in CSV.read_text(encoding="ascii").splitlines()
        if not line.startswith("*IT9,")
    )
    db = CtyDatabase.from_text(dat_text, csv_text)
    assert (entity(db, "Sicily").dxcc, entity(db, "Sicily").dxcc_name) == (248, "Italy")
    assert db.warnings == []


def test_ambiguous_csv_code_does_not_choose_the_wae_parent(dat_text):
    # a broken cty.csv gives Montenegro Italy's code 248: Sicily's parent then comes from the
    # built-in table, not from the first entity that has code 248
    csv_text = CSV.read_text(encoding="ascii").replace("4O,Montenegro,514,", "4O,Montenegro,248,")
    db = CtyDatabase.from_text(dat_text, csv_text)
    assert resolve(db, "IT9ABC").dxcc_name == "Italy"


def test_match_properties_for_hand_built_entity():
    e = Entity("Serbia", 15, 28, "EU", 44.0, 21.0, -1.0, "YU", False)
    match = CtyMatch(e, 15, 28, "EU", 44.0, 21.0, -1.0, "YU", False)
    assert (match.dxcc, match.dxcc_name) == (None, "Serbia")
    e = Entity("Serbia", 15, 28, "EU", 44.0, 21.0, -1.0, "YU", False, dxcc=296)
    assert CtyMatch(e, 15, 28, "EU", 44.0, 21.0, -1.0, "YU", False).dxcc == 296


# --- input cleanup and garbage ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (" yu1ab ", "YU1AB"),
        ("yu1ab / p", "YU1AB/P"),
        ("\tDl1Abc\n", "DL1ABC"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_clean_call(raw, clean):
    assert clean_call(raw) == clean


def test_clean_call_tolerates_non_strings():
    assert clean_call(None) == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "name"), [(" yu1ab ", "Serbia"), ("dl1abc / p", "Fed. Rep. of Germany")]
)
def test_lookup_lowercase_and_spaces(db, raw, name):
    assert resolve(db, raw).entity.name == name


@pytest.mark.parametrize(
    "call",
    ["", "   ", "/", "//", "???", "YU1AB-7", "12345", "YUGO", "N/A", "DL/YU", "YU/", "ЈУ1АБ"],
)
def test_garbage_gives_none(db, call):
    assert db.lookup(call) is None


def test_non_string_input_gives_none(db):
    assert db.lookup(None) is None  # type: ignore[arg-type]
    assert db.lookup(42) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("call", ["F1ABC", "ZS6ABC", "XX9ABC"])
def test_unknown_prefix_gives_none(db, call):
    assert db.lookup(call) is None


def test_leading_and_trailing_slashes_are_ignored(db):
    assert resolve(db, "/YU1AB/").entity.name == "Serbia"


def test_text_longer_than_64_characters_is_not_a_callsign(db):
    at_limit = "YU1AB/" + "P" * 58
    assert len(at_limit) == 64
    assert resolve(db, at_limit).entity.name == "Serbia"
    assert db.lookup(at_limit + "P") is None
    # the limit applies after whitespace is removed
    assert resolve(db, " " * 100 + "YU1AB/P" + " " * 100).entity.name == "Serbia"


@pytest.mark.parametrize(
    "call",
    [
        "YU1AB" + "/P" * 50_000,  # 100 KB: ADIF and WSJT-X UDP do not limit the length
        "YU1AB/" * 20_000,
        "/" * 100_000 + "YU1AB",
        "YU1AB/P/" + "QRP/LH/" * 15_000,
    ],
    ids=["slash-p", "repeated-call", "leading-slashes", "modifiers"],
)
def test_huge_input_is_rejected_quickly(db, call):
    start = time.perf_counter()
    assert db.lookup(call) is None
    assert time.perf_counter() - start < 1.0


# --- robustness ------------------------------------------------------------------------------


def croatia_header_line(dat_text: str) -> int:
    lines = dat_text.splitlines()
    return next(i for i, line in enumerate(lines, 1) if line.startswith("Croatia:"))


def test_corrupted_entity_is_skipped_rest_loads(dat_text):
    line = croatia_header_line(dat_text)
    broken = dat_text.replace("Croatia:                  15:", "Croatia:                  1X:")
    db = CtyDatabase.from_text(broken)
    assert len(db) == 19
    assert "Croatia" not in [e.name for e in db.entities]
    assert db.lookup("9A1GS") is None
    assert resolve(db, "YU1AB").entity.name == "Serbia"
    assert resolve(db, "OE1ABC").entity.name == "Austria"  # entity after the broken one
    assert len(db.warnings) == 1
    assert mentions_number(db.warnings[0], line)


@pytest.mark.parametrize(
    "bad_header",
    [
        "Croatia:                  15:  28:  EU:   45.18:",  # too few fields
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:    -1.0:  9A",  # no last ':'
        "Croatia:                  15:  28:  XX:   45.18:   -15.30:    -1.0:  9A:",  # continent
        "Croatia:                  00:  28:  EU:   45.18:   -15.30:    -1.0:  9A:",  # CQ zone
        "Croatia:                  41:  28:  EU:   45.18:   -15.30:    -1.0:  9A:",
        "Croatia:                  15:  00:  EU:   45.18:   -15.30:    -1.0:  9A:",  # ITU zone
        "Croatia:                  15:  91:  EU:   45.18:   -15.30:    -1.0:  9A:",
        "Croatia:                  15:  99:  EU:   45.18:   -15.30:    -1.0:  9A:",
        "Croatia:                  15:  28:  EU:   nan:   -15.30:    -1.0:  9A:",  # latitude
        "Croatia:                  15:  28:  EU:   95.00:   -15.30:    -1.0:  9A:",
        "Croatia:                  15:  28:  EU:  -95.00:   -15.30:    -1.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:   200.00:    -1.0:  9A:",  # longitude
        "Croatia:                  15:  28:  EU:   45.18:  -200.00:    -1.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:     inf:    -1.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:    30.0:  9A:",  # UTC offset
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:   -30.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:    24.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:   -24.0:  9A:",
        "Croatia:                  15:  28:  EU:   45.18:   -15.30:    -1.0:    :",  # prefix
        ":                         15:  28:  EU:   45.18:   -15.30:    -1.0:  9A:",  # name
    ],
)
def test_invalid_headers_skip_only_that_entity(dat_text, bad_header):
    good = next(line for line in dat_text.splitlines() if line.startswith("Croatia:"))
    db = CtyDatabase.from_text(dat_text.replace(good, bad_header))
    assert len(db) == 19
    assert db.lookup("9A1GS") is None
    assert resolve(db, "DL1ABC").entity.name == "Fed. Rep. of Germany"
    assert len(db.warnings) == 1
    assert mentions_number(db.warnings[0], croatia_header_line(dat_text))


@pytest.mark.parametrize(
    ("header", "values"),
    [
        # the extremes of the real file: Antarctica -90.00, Eastern Kiribati UTC offset -14.0,
        # Baker & Howland Islands 12.0; the zone limits CQ 1..40 and ITU 1..90
        (
            "Edge:  01:  01:  AN:  -90.00:   180.00:   -14.0:  ZZ:",
            (1, 1, "AN", -90.0, -180.0, -14.0),
        ),
        (
            "Edge:  40:  90:  AN:   90.00:  -180.00:    12.0:  ZZ:",
            (40, 90, "AN", 90.0, 180.0, 12.0),
        ),
    ],
)
def test_header_values_at_the_limits_are_accepted(header, values):
    db = CtyDatabase.from_text(f"{header}\n    ZZ;\n")
    assert db.warnings == []
    e = db.entities[0]
    assert (e.cq_zone, e.itu_zone, e.continent, e.lat, e.lon, e.utc_offset) == values


def test_lowercase_continent_in_header_is_accepted():
    db = CtyDatabase.from_text(SERBIA.replace("EU:", "eu:") + "\n    YU;\n")
    assert db.warnings == []
    assert db.entities[0].continent == "EU"
    assert resolve(db, "YU1AB").continent == "EU"


def test_bad_entry_is_skipped_rest_of_entity_loads():
    text = f"{SERBIA}\n    YT,Y#U,=YU1(AB,YU(99),YU;\n{GERMANY}\n    DL;\n"
    db = CtyDatabase.from_text(text)
    assert len(db) == 2
    assert resolve(db, "YT1A").entity.name == "Serbia"
    assert resolve(db, "YU1A").cq_zone == 15  # YU(99) was rejected, plain YU kept
    assert len(db.warnings) == 3
    assert all(mentions_number(w, 2) for w in db.warnings)


@pytest.mark.parametrize(
    "entry",
    [
        "YU(0)",
        "YU(41)",
        "YU[0]",
        "YU[91]",
        "YU{XX}",
        "YU{E}",
        "YU<95.00/0.00>",
        "YU<-95.00/0.00>",
        "YU<45.00/181.00>",
        "YU<45.00/-181.00>",
        "YU<45.00>",
        "YU<a/b>",
        "YU~30~",
        "YU~24~",
        "YU~-24~",
        "YU~x~",
        "YU(\u0661\u0665)",  # Arabic-Indic digits are not a zone number
    ],
)
def test_invalid_override_skips_the_entry(entry):
    db = CtyDatabase.from_text(f"{SERBIA}\n    YT,{entry};\n")
    assert resolve(db, "YT1A").entity.name == "Serbia"
    assert db.lookup("YU1A") is None  # skipped, not loaded with the entity defaults
    assert len(db.warnings) == 1
    assert mentions_number(db.warnings[0], 2)


def test_override_values_at_the_limits_are_accepted():
    db = CtyDatabase.from_text(
        f"{SERBIA}\n    YU<-90.00/-180.00>~12.0~(40)[90],YT<90.00/180.00>~-14.0~(1)[1];\n"
    )
    assert db.warnings == []
    m = resolve(db, "YU1A")
    assert (m.lat, m.lon, m.utc_offset, m.cq_zone, m.itu_zone) == (-90.0, 180.0, 12.0, 40, 90)
    m = resolve(db, "YT1A")
    assert (m.lat, m.lon, m.utc_offset, m.cq_zone, m.itu_zone) == (90.0, -180.0, -14.0, 1, 1)


def test_missing_semicolon_keeps_both_entities():
    text = f"{SERBIA}\n    YT,YU\n{GERMANY}\n    DL;\n"
    db = CtyDatabase.from_text(text)
    assert [e.name for e in db.entities] == ["Serbia", "Fed. Rep. of Germany"]
    assert resolve(db, "YU1AB").entity.name == "Serbia"
    assert resolve(db, "DL1ABC").entity.name == "Fed. Rep. of Germany"
    assert len(db.warnings) == 1


def test_missing_semicolon_at_end_of_file():
    db = CtyDatabase.from_text(f"{SERBIA}\n    YT,YU")
    assert resolve(db, "YU1AB").entity.name == "Serbia"
    assert len(db.warnings) == 1


def test_blank_and_comment_lines_are_skipped():
    text = f"\n# comment\n{SERBIA}\n\n    YT,\n  # inside the list\n    YU;\n\n"
    db = CtyDatabase.from_text(text)
    assert db.warnings == []
    assert resolve(db, "YT1A").entity.name == "Serbia"
    assert resolve(db, "YU1A").entity.name == "Serbia"


def test_continuation_line_without_trailing_comma():
    db = CtyDatabase.from_text(f"{SERBIA}\n    YT\n    YU;\n")
    assert resolve(db, "YT1A").matched == "YT"
    assert resolve(db, "YU1A").matched == "YU"


def test_compact_header_without_padding():
    db = CtyDatabase.from_text("Serbia:15:28:EU:44.00:-21.00:-1.0:YU:\n    YU;\n")
    assert db.entities[0].lon == 21.0
    assert resolve(db, "YU1AB").cq_zone == 15


def test_text_outside_entities_is_reported_once():
    db = CtyDatabase.from_text(f"stray text\nmore stray,\n{SERBIA}\n    YU;\n")
    assert len(db) == 1
    assert len(db.warnings) == 1


def test_empty_text_gives_empty_database():
    db = CtyDatabase.from_text("")
    assert len(db) == 0
    assert db.entities == []
    assert db.lookup("YU1AB") is None
    assert len(db.warnings) == 1  # "no entities"


def test_html_page_instead_of_cty_dat():
    html = "<!DOCTYPE html>\n<html><head><title>404: Not Found</title></head>\n" + (
        "<p>style: color: red; margin: 0;</p>\n" * 200
    )
    db = CtyDatabase.from_text(html)
    assert len(db) == 0
    assert db.lookup("YU1AB") is None
    # "no entities" first, then at most 50 details and one line saying how many were not listed
    assert 0 < len(db.warnings) <= 52
    assert mentions_number(db.warnings[-1], len(html.splitlines()) - 50)


def test_from_files_decodes_non_utf8_bytes(tmp_path):
    path = tmp_path / "cty.dat"
    path.write_bytes(b"Serbia\xe9:  15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:\n    YU;\n")
    db = CtyDatabase.from_files(path)
    assert resolve(db, "YU1AB").entity.name == "Serbia\u00e9"


def test_from_files_ignores_a_utf8_bom(tmp_path):
    # the fixture starts with a '#' line: after a BOM it must still be read as a comment
    dat_path = tmp_path / "cty.dat"
    dat_path.write_bytes(b"\xef\xbb\xbf" + DAT.read_bytes())
    csv_path = tmp_path / "cty.csv"
    csv_path.write_bytes(b"\xef\xbb\xbf" + CSV.read_bytes())
    db = CtyDatabase.from_files(dat_path, csv_path)
    assert db.warnings == []
    assert db.entities == CtyDatabase.from_files(DAT, CSV).entities


@pytest.mark.parametrize("first_line", ["", "# comment\n"])
def test_from_text_ignores_a_leading_bom(first_line):
    db = CtyDatabase.from_text(
        f"\ufeff{first_line}{SERBIA}\n    YU;\n",
        "\ufeffYU,Serbia,296,EU,15,28,44.00,-21.00,-1.0,YU;\n",
    )
    assert db.warnings == []
    assert (db.entities[0].name, db.entities[0].dxcc) == ("Serbia", 296)


def test_from_files_missing_csv_loads_without_codes(tmp_path):
    db = CtyDatabase.from_files(DAT, tmp_path / "missing.csv")
    assert len(db) == 20
    assert resolve(db, "YU1AB").dxcc is None
    assert len(db.warnings) == 1


def test_from_files_missing_dat_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        CtyDatabase.from_files(tmp_path / "missing.dat")


def test_bad_csv_rows_are_skipped():
    dat = f"{SERBIA}\n    YU;\n{GERMANY}\n    DL;\n"
    csv_text = (
        "# comment\n\nYU,Serbia,296,EU,15,28,44.00,-21.00,-1.0,YU;\n"
        "DL,Fed. Rep. of Germany,x230,EU,14,28,51.00,-10.00,-1.0,DL;\nshort,row\n"
    )
    db = CtyDatabase.from_text(dat, csv_text)
    assert entity(db, "Serbia").dxcc == 296
    assert entity(db, "Fed. Rep. of Germany").dxcc is None
    # first how many entities have no code (Germany), then the rows
    assert len(db.warnings) == 3
    assert mentions_number(db.warnings[0], 1)
    assert mentions_number(db.warnings[1], 4)
    assert mentions_number(db.warnings[2], 5)


@pytest.mark.parametrize(
    "csv_text",
    [
        "",  # empty download
        "\r\n",
        "# comment only\r\n",
        "ZZ,Nowhere,999,EU,15,28,44.00,-21.00,-1.0,ZZ;\r\n",  # rows of another file
    ],
    ids=["empty", "blank-line", "comment-only", "other-file"],
)
def test_csv_without_any_matching_row_is_reported(dat_text, csv_text):
    db = CtyDatabase.from_text(dat_text, csv_text)
    assert all(e.dxcc is None for e in db.entities)
    assert len(db.warnings) == 1
    assert mentions_number(db.warnings[0], 20)


def test_truncated_csv_is_reported(dat_text):
    lines = CSV.read_text(encoding="ascii").splitlines()
    assert lines[9].startswith("GM,Scotland,")
    cut = "\r\n".join(lines[:9]) + "\r\nGM,Scotl"  # download cut off in line 10
    db = CtyDatabase.from_text(dat_text, cut)
    assert [e.name for e in db.entities if e.dxcc is not None] == EXCERPT_NAMES[:5]
    assert len(db.warnings) == 2
    assert mentions_number(db.warnings[0], 15)  # entities without a code
    assert mentions_number(db.warnings[1], 10)  # the cut row


def test_csv_summary_is_kept_when_row_warnings_are_capped(dat_text):
    db = CtyDatabase.from_text(dat_text, "not,a code\n" * 60)
    assert all(e.dxcc is None for e in db.entities)
    # the summary, 50 row warnings, then how many were not listed
    assert len(db.warnings) == 52
    assert mentions_number(db.warnings[0], 20)
    assert mentions_number(db.warnings[-1], 10)


def test_complete_csv_gives_no_warning(dat_text):
    db = CtyDatabase.from_text(dat_text, CSV.read_text(encoding="ascii"))
    assert all(e.dxcc is not None for e in db.entities)
    assert db.warnings == []


@pytest.mark.parametrize(
    ("code", "dxcc"),
    [
        ("1", 1),
        ("522", 522),
        ("999", 999),
        ("0", None),
        ("1000", None),
        ("-296", None),
        ("296.0", None),
        ("\uff12\uff19\uff16", None),  # full-width digits
    ],
)
def test_csv_dxcc_code_range(code, dxcc):
    db = CtyDatabase.from_text(
        f"{SERBIA}\n    YU;\n", f"YU,Serbia,{code},EU,15,28,44.00,-21.00,-1.0,YU;\n"
    )
    assert entity(db, "Serbia").dxcc == dxcc
    # a rejected code: the summary (1 entity without a code) and the row (line 1)
    assert len(db.warnings) == (0 if dxcc else 2)


def test_empty_database_object():
    db = CtyDatabase()
    assert len(db) == 0
    assert db.lookup("YU1AB") is None


# --- translations ----------------------------------------------------------------------------


def translated_literals() -> list[str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("tr", "tr_noop")
        ):
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
            assert isinstance(node.args[0].value, str)
            found.append(node.args[0].value)
    return found


def test_catalog_has_every_message_and_is_formatted():
    raw = CATALOG.read_text(encoding="utf-8")
    catalog = json.loads(raw)
    literals = translated_literals()
    assert literals
    assert set(literals) == set(catalog)
    assert raw == json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for english, serbian in catalog.items():
        assert serbian and serbian != english
        assert sorted(re.findall(r"\{(\w*)\}", serbian)) == sorted(
            re.findall(r"\{(\w*)\}", english)
        )
