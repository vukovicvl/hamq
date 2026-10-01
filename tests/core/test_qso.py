"""Tests for hamq.core.qso (task M2-02): one ADIF record -> one row of the ``qso`` layer.

Most tests read the real ADIF fixtures (``tests/fixtures/adif``, see the README there)
with ``hamq.core.adif`` and convert them with my station in KN04ft and the 20-entity
excerpt of the AD1C country files (``tests/fixtures/cty``). Expected positions were
derived by hand from the locator rules and the cty.dat header lines; distances and
bearings are checked against the WGS84 values of the geodesy skill (independently
re-computed with Vincenty's formulae: Beograd-Sydney 15676.1 km, 91.0 degrees).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hamq.core import adif, geo, i18n, maidenhead, modes, qso
from hamq.core.cty import CtyDatabase
from hamq.core.qso import (
    PATH_FIELDS,
    QSO_FIELDS,
    Qso,
    Station,
    display_mode,
    record_to_qso,
    records_to_qsos,
)

ROOT = Path(__file__).resolve().parents[2]
ADIF_DIR = ROOT / "tests" / "fixtures" / "adif"
CTY_DIR = ROOT / "tests" / "fixtures" / "cty"
CATALOG = ROOT / "hamq" / "i18n" / "sr_Latn" / "core_qso.json"
SOURCE = ROOT / "hamq" / "core" / "qso.py"

UTC = timezone.utc
HOME = Station(call="YU1QQ", grid="KN04ft")
# Centres worked out by hand from the locator definition (field 20x10, square 2x1,
# subsquare 5'x2.5', extended 30"x15"); see test_locator_centres_by_hand.
KN04FT = (44.8125, 20.458333333333332)  # my station, Beograd
JN95WG = (45.270833333333336, 19.875)  # Novi Sad
JN75XT74 = (45.81041666666667, 15.979166666666666)  # Zagreb, from JN75xt74oj
JM77 = (37.5, 15.0)
FN42 = (42.5, -71.0)
QF56 = (-33.5, 151.0)
# MY_LAT / MY_LON and LAT / LON of the log4om.adi Sydney record
BEOGRAD = (44.8125, 20.4612)  # N044 48.750, E020 27.672
SYDNEY = (-33.8688, 151.2093)  # S033 52.128, E151 12.558
# cty.dat entity positions (east positive) of the fixture excerpt
CTY_SERBIA = (44.0, 21.0)
CTY_CROATIA = (45.18, 15.3)
CTY_GERMANY = (51.0, 10.0)
CTY_SICILY = (37.5, 14.0)
CTY_USA = (37.6, -91.87)

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
# ADIF fields stored in a dedicated column; every other field goes to adif_extra.
MAPPED = frozenset(
    {
        "CALL",
        "QSO_DATE",
        "TIME_ON",
        "BAND",
        "MODE",
        "SUBMODE",
        "FREQ",
        "RST_SENT",
        "RST_RCVD",
        "GRIDSQUARE",
        "MY_GRIDSQUARE",
        "DXCC",
        "COUNTRY",
        "CONT",
        "CQZ",
        "ITUZ",
    }
)

NO_POSITION_SUMMARY = "QSOs without a position, not shown on the map: {count}"
NO_QTH_SUMMARY = (
    "QSOs without distance and bearing, my QTH unknown (set your locator in the HamQ "
    "settings): {count}"
)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Warnings are compared in English, independent of the global plugin language."""
    monkeypatch.setattr(qso, "tr", lambda text: text)


@pytest.fixture(scope="module")
def cty_db() -> CtyDatabase:
    db = CtyDatabase.from_files(CTY_DIR / "cty_excerpt.dat", CTY_DIR / "cty_excerpt.csv")
    assert len(db) == 20 and db.warnings == []
    return db


def read_records(name: str) -> list[dict[str, str]]:
    return adif.read_adi(ADIF_DIR / name).records


def convert(name: str, cty_db: CtyDatabase | None, station: Station | None = HOME):
    return records_to_qsos(read_records(name), station=station, cty=cty_db, source=f"adif:{name}")


def one(record: dict[str, str], **kwargs):
    """Convert one synthetic record; QSO_DATE / TIME_ON default to a valid time."""
    full = {"QSO_DATE": "20260915", "TIME_ON": "1845"}
    full.update(record)
    kwargs.setdefault("station", HOME)
    return record_to_qso(full, **kwargs)


def latlon(text_lat: str, text_lon: str) -> tuple[float, float]:
    return adif.parse_latlon(text_lat), adif.parse_latlon(text_lon)


def assert_position(q: Qso, expected: tuple[float, float] | None, source: str | None):
    if expected is None:
        assert (q.lat, q.lon, q.loc_source) == (None, None, None)
    else:
        assert (q.lat, q.lon) == pytest.approx(expected, abs=1e-9)
        assert q.loc_source == source


def assert_origin(q: Qso, expected: tuple[float, float] | None):
    if expected is None:
        assert (q.my_lat, q.my_lon) == (None, None)
        assert (q.distance_km, q.bearing_deg) == (None, None)
    else:
        assert (q.my_lat, q.my_lon) == pytest.approx(expected, abs=1e-9)


# --- contract shape ---------------------------------------------------------------------


def test_qso_fields_follow_the_plan():
    assert QSO_FIELDS == (
        ("call", "text"),
        ("qso_datetime", "datetime"),
        ("band", "text"),
        ("mode", "text"),
        ("submode", "text"),
        ("freq_mhz", "real"),
        ("rst_sent", "text"),
        ("rst_rcvd", "text"),
        ("gridsquare", "text"),
        ("my_gridsquare", "text"),
        ("dxcc", "int"),
        ("country", "text"),
        ("cont", "text"),
        ("cq_zone", "int"),
        ("itu_zone", "int"),
        ("distance_km", "real"),
        ("bearing_deg", "real"),
        ("loc_source", "text"),
        ("source", "text"),
        ("dedup_key", "text"),
        ("adif_extra", "text"),
    )
    assert tuple(name for name, _ in QSO_FIELDS) == FIELD_NAMES
    assert "fid" not in dict(QSO_FIELDS)
    assert {kind for _, kind in QSO_FIELDS} == {"int", "real", "text", "datetime"}


def test_path_fields():
    assert PATH_FIELDS == (
        ("qso_fid", "int"),
        ("distance_km", "real"),
        ("bearing_deg", "real"),
        ("band", "text"),
        ("mode", "text"),
    )


def test_qso_dataclass_has_the_contract_fields_in_order():
    names = [f.name for f in dataclasses.fields(Qso)]
    assert names == [*FIELD_NAMES, "lat", "lon", "my_lat", "my_lon"]


def test_station_defaults():
    assert Station() == Station(call="", grid="")
    assert Station().latlon() is None
    assert [f.name for f in dataclasses.fields(Station)] == ["call", "grid"]


def test_display_mode_is_reexported():
    assert display_mode is modes.display_mode
    assert qso.display_mode is modes.display_mode


def test_public_names():
    assert set(qso.__all__) == {
        "PATH_FIELDS",
        "QSO_FIELDS",
        "Qso",
        "Station",
        "display_mode",
        "record_to_qso",
        "records_to_qsos",
    }


def test_locator_centres_by_hand():
    # the module under test uses maidenhead.to_latlon; these constants were derived by hand
    for locator, centre in [
        ("KN04ft", KN04FT),
        ("JN95wg", JN95WG),
        ("JN75xt74", JN75XT74),
        ("JM77", JM77),
        ("FN42", FN42),
        ("QF56", QF56),
    ]:
        assert maidenhead.to_latlon(locator) == pytest.approx(centre, abs=1e-12)


# --- Station ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("grid", "expected"),
    [
        ("KN04ft", KN04FT),
        ("kn04FT", KN04FT),
        ("  KN04ft\t", KN04FT),
        ("KN04", (44.5, 21.0)),
        ("KN", (45.0, 30.0)),
        # SW corner 44 + 19/24 + 5/240, 20 + 5/12 + 5/120; half a cell is 1/480 and 1/240
        ("KN04ft55", (44.814583333333333, 20.4625)),
        ("KN04ft55xx", (44.814583333333333, 20.4625)),  # 10 characters: cut to 8
        ("", None),
        ("   ", None),
        ("KN0", None),
        ("ZZ00", None),
        ("KN04ft5", None),
        ("KN04ft55zz", None),
    ],
)
def test_station_latlon(grid, expected):
    result = Station(call="YU1QQ", grid=grid).latlon()
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=1e-9)


def test_station_latlon_tolerates_non_text():
    assert Station(grid=None).latlon() is None  # type: ignore[arg-type]
    assert Station(grid=4).latlon() is None  # type: ignore[arg-type]


# --- real logs ----------------------------------------------------------------------------


def test_wsjtx_log(cty_db):
    qsos, warnings = convert("wsjtx_log.adi", cty_db)
    assert [q.call for q in qsos] == ["IT9XYZ", "W1XYZ", "KH6XYZ", "9A2XYZ", "YU7XYZ", "OH2XYZ/MM"]
    # only the /MM station has no position; every record has MY_GRIDSQUARE
    assert warnings == [NO_POSITION_SUMMARY.format(count=1)]

    sicily = qsos[0]
    assert sicily.attributes() == {
        "call": "IT9XYZ",
        "qso_datetime": datetime(2026, 9, 12, 17, 15, 30, tzinfo=UTC),
        "band": "20m",
        "mode": "FT8",
        "submode": None,
        "freq_mhz": 14.07574,
        "rst_sent": "-08",
        "rst_rcvd": "-13",
        "gridsquare": "JM77",
        "my_gridsquare": "KN04ft",
        "dxcc": 248,
        "country": "Italy",  # Sicily counts for DXCC Italy (WAE-only entity in cty.dat)
        "cont": "EU",
        "cq_zone": 15,
        "itu_zone": 28,
        "distance_km": pytest.approx(geo.distance_km(*KN04FT, *JM77)),
        "bearing_deg": pytest.approx(geo.bearing_deg(*KN04FT, *JM77)),
        "loc_source": "grid",
        "source": "adif:wsjtx_log.adi",
        "dedup_key": "IT9XYZ|202609121715|20m|FT8",
        "adif_extra": json.dumps(
            {"QSO_DATE_OFF": "20260912", "STATION_CALLSIGN": "YU1QQ", "TIME_OFF": "171642"},
            ensure_ascii=False,
            sort_keys=True,
        ),
    }
    assert_position(sicily, JM77, "grid")
    assert_origin(sicily, KN04FT)  # MY_GRIDSQUARE of the record
    assert sicily.distance_km == pytest.approx(932.19, abs=0.01)  # independent haversine

    ft4 = qsos[3]
    assert (ft4.mode, ft4.submode, ft4.display_mode) == ("MFSK", "FT4", "FT4")
    assert ft4.dedup_key == "9A2XYZ|202609131920|40m|FT4"


def test_maritime_mobile_without_locator_has_no_position(cty_db):
    qsos, _ = convert("wsjtx_log.adi", cty_db)
    mm = qsos[5]
    assert mm.call == "OH2XYZ/MM"
    assert mm.gridsquare is None  # zero-length GRIDSQUARE counts as missing
    assert_position(mm, None, None)
    assert (mm.distance_km, mm.bearing_deg) == (None, None)
    assert (mm.dxcc, mm.country, mm.cont, mm.cq_zone, mm.itu_zone) == (None,) * 5
    assert_origin(mm, KN04FT)  # my position is still known
    assert mm.band == "15m" and mm.freq_mhz == 21.07542


def test_log4om_beograd_sydney(cty_db):
    qsos, warnings = convert("log4om.adi", cty_db)
    assert len(qsos) == 5
    assert warnings == [
        "Record 2 (9A3XYZ, 20260918 071544): locator JN75xt74oj in GRIDSQUARE cut to JN75xt74"
    ]
    vk = qsos[0]
    assert vk.call == "VK2XYZ"
    assert vk.qso_datetime == datetime(2026, 9, 18, 6, 30, 12, tzinfo=UTC)
    assert (vk.band, vk.mode, vk.submode, vk.freq_mhz) == ("20m", "SSB", None, 14.195)
    assert (vk.rst_sent, vk.rst_rcvd) == ("57", "55")
    assert (vk.gridsquare, vk.my_gridsquare) == ("QF56od", "KN04ft")
    assert (vk.dxcc, vk.country, vk.cont, vk.cq_zone, vk.itu_zone) == (
        150,
        "Australia",
        "OC",
        30,
        59,
    )
    # LAT / LON beat the locator, MY_LAT / MY_LON beat MY_GRIDSQUARE
    assert_position(vk, SYDNEY, "latlon")
    assert_origin(vk, BEOGRAD)
    assert (vk.lat, vk.lon) == latlon("S033 52.128", "E151 12.558")
    # the geodesy skill: WGS84 15676.1 km and 91.0 degrees; the sphere is within 0.5 %
    assert abs(vk.distance_km - 15676.1) / 15676.1 < 0.005
    assert abs(vk.bearing_deg - 91.0) < 0.5
    assert vk.distance_km == pytest.approx(geo.distance_km(*BEOGRAD, *SYDNEY), rel=1e-12)
    assert vk.bearing_deg == pytest.approx(geo.bearing_deg(*BEOGRAD, *SYDNEY), rel=1e-12)
    assert vk.dedup_key == "VK2XYZ|202609180630|20m|SSB"
    assert json.loads(vk.adif_extra) == {
        "LAT": "S033 52.128",
        "LON": "E151 12.558",
        "LOTW_QSL_SENT": "Y",
        "MY_LAT": "N044 48.750",
        "MY_LON": "E020 27.672",
        "NAME": "John",
        "OPERATOR": "YU1QQ",
        "QSL_RCVD": "N",
        "QSL_SENT": "N",
        "QTH": "Sydney",
        "STATION_CALLSIGN": "YU1QQ",
        "TIME_OFF": "063012",
    }


def test_ten_character_locator_is_cut_to_eight(cty_db):
    qsos, _ = convert("log4om.adi", cty_db)
    zagreb = qsos[1]
    assert zagreb.call == "9A3XYZ"
    assert zagreb.gridsquare == "JN75xt74"
    assert_position(zagreb, JN75XT74, "grid")
    assert "GRIDSQUARE" not in json.loads(zagreb.adif_extra)


def test_record_values_win_over_cty(cty_db):
    qsos, _ = convert("log4om.adi", cty_db)
    sicily = qsos[4]
    assert sicily.call == "IT9XYZ"
    # log4om filled everything; the cty.dat match (Sicily) is not needed
    assert (sicily.dxcc, sicily.country, sicily.cont, sicily.cq_zone, sicily.itu_zone) == (
        248,
        "Italy",
        "EU",
        15,
        28,
    )
    record = {
        "CALL": "W1AW",
        "DXCC": "1",
        "COUNTRY": "Canada",
        "CONT": "eu",
        "CQZ": "14",
        "ITUZ": "27",
    }
    q, warnings = one(record, cty=cty_db)
    assert (q.dxcc, q.country, q.cont, q.cq_zone, q.itu_zone) == (1, "Canada", "EU", 14, 27)
    # the position still comes from cty.dat (no LAT / LON, no locator)
    assert_position(q, CTY_USA, "cty")
    # a differing DXCC code is reported, nothing else
    assert warnings == [
        "QSO W1AW, 20260915 1845: DXCC 1 in the log does not match cty.dat (United States, DXCC 291)"
    ]


def test_missing_dxcc_data_is_filled_from_cty(cty_db):
    qsos, warnings = convert("n1mm.adi", cty_db)
    assert [q.call for q in qsos] == ["9A5XYZ", "W3XYZ", "IT9XYZ", "YU7XYZ", "EA8XYZ"]
    croatia, usa, sicily, serbia, canary = qsos
    # CQZ is in the record, the rest comes from cty.dat
    assert (croatia.dxcc, croatia.country, croatia.cont, croatia.cq_zone, croatia.itu_zone) == (
        497,
        "Croatia",
        "EU",
        15,
        28,
    )
    assert_position(croatia, CTY_CROATIA, "cty")
    assert_origin(croatia, KN04FT)  # no MY_* fields: my station's locator
    assert croatia.my_gridsquare == "KN04ft"
    assert croatia.band == "20m" and croatia.mode == "CW"
    assert (usa.dxcc, usa.country, usa.cont, usa.cq_zone, usa.itu_zone) == (
        291,
        "United States",
        "NA",
        5,
        8,
    )
    # WAE-only entity: the country is the DXCC entity, the position Sicily's own
    assert (sicily.dxcc, sicily.country, sicily.cont) == (248, "Italy", "EU")
    assert_position(sicily, CTY_SICILY, "cty")
    assert (serbia.country, serbia.band) == ("Serbia", "40m")
    assert_position(serbia, CTY_SERBIA, "cty")
    # EA8 is not in the excerpt: no match, no position, only the logged CQ zone
    assert (canary.dxcc, canary.country, canary.cont, canary.cq_zone, canary.itu_zone) == (
        None,
        None,
        None,
        33,
        None,
    )
    assert_position(canary, None, None)
    assert (canary.distance_km, canary.bearing_deg) == (None, None)
    assert warnings == [NO_POSITION_SUMMARY.format(count=1)]


def test_without_cty_only_logged_values(cty_db):
    qsos, warnings = convert("n1mm.adi", None)
    assert all(q.loc_source is None and q.lat is None for q in qsos)
    assert [q.cq_zone for q in qsos] == [15, 5, 15, 15, 33]
    assert all(q.dxcc is None and q.country is None and q.cont is None for q in qsos)
    assert warnings == [NO_POSITION_SUMMARY.format(count=5)]


def test_lotw_confirmations_have_the_dedup_keys_of_the_wsjtx_log(cty_db):
    lotw, lotw_warnings = convert("lotw.adi", cty_db)
    wsjtx, _ = convert("wsjtx_log.adi", cty_db)
    assert lotw_warnings == []
    # FT4 is MODE=FT4 in LoTW and MODE=MFSK SUBMODE=FT4 in WSJT-X: the same QSO
    assert (lotw[1].mode, lotw[1].submode) == ("FT4", None)
    assert (wsjtx[3].mode, wsjtx[3].submode) == ("MFSK", "FT4")
    assert [lotw[0].dedup_key, lotw[1].dedup_key] == [wsjtx[1].dedup_key, wsjtx[3].dedup_key]
    assert lotw[0].dedup_key == "W1XYZ|202609121801|20m|FT8"
    assert lotw[1].dedup_key == "9A2XYZ|202609131920|40m|FT4"
    w1 = lotw[0]
    assert w1.band == "20m"  # BAND 20M
    assert w1.my_gridsquare == "KN04ft"  # MY_GRIDSQUARE KN04FT
    # LoTW values win (CQZ 05, ITUZ 08, COUNTRY as LoTW writes it); CONT from cty.dat
    assert (w1.dxcc, w1.country, w1.cont, w1.cq_zone, w1.itu_zone) == (
        291,
        "UNITED STATES OF AMERICA",
        "NA",
        5,
        8,
    )
    assert_position(w1, FN42, "grid")
    assert_origin(w1, KN04FT)


def test_bad_records_are_skipped_with_a_warning(cty_db):
    qsos, warnings = convert("no_eoh.adi", cty_db)
    assert [q.call for q in qsos] == ["YU1XYZ", "YU1XYZ"]
    assert warnings == [
        "Record 2 (20260801 0930): missing CALL, skipped",
        "Record 3 (YT1XYZ, 20260230 1000): missing or invalid QSO_DATE or TIME_ON, skipped",
        "Record 4 (YU1XYZ, 20260802 2460): missing or invalid QSO_DATE or TIME_ON, skipped",
        "Record 5 (YU1XYZ, 20260802 1015): invalid locator ZZ00 in GRIDSQUARE, not used for "
        "the position",
    ]
    first, bad_grid = qsos
    assert (first.band, first.mode, first.freq_mhz) == ("2m", "FM", 145.5)
    assert first.gridsquare == "KN04fr"
    assert_position(first, maidenhead.to_latlon("KN04fr"), "grid")
    # the invalid locator is kept as logged, but the position comes from cty.dat
    assert bad_grid.gridsquare == "ZZ00"
    assert_position(bad_grid, CTY_SERBIA, "cty")
    assert json.loads(bad_grid.adif_extra) == {"COMMENT": "invalid locator"}


def test_invalid_locator_typed_by_hand(cty_db):
    qsos, warnings = convert("qrz_export.adi", cty_db)
    assert [q.call for q in qsos] == ["VK3XYZ", "W5XYZ", "DL1XYZ"]
    assert warnings == [
        "Record 3 (DL1XYZ, 20260917 120000): invalid locator JO6 in GRIDSQUARE, not used for "
        "the position"
    ]
    dl = qsos[2]
    assert dl.gridsquare == "JO6"
    assert_position(dl, CTY_GERMANY, "cty")
    assert_origin(dl, BEOGRAD)  # MY_LAT / MY_LON
    # COUNTRY and DXCC from the log, the rest from cty.dat
    assert (dl.dxcc, dl.country, dl.cont, dl.cq_zone, dl.itu_zone) == (230, "Germany", "EU", 14, 28)
    vk = qsos[0]
    assert_position(vk, latlon("S037 53.500", "E145 12.000"), "latlon")
    # the logger's own DISTANCE stays in adif_extra; HamQ computes its own
    assert json.loads(vk.adif_extra)["DISTANCE"] == "15429"
    assert vk.distance_km == pytest.approx(15429, abs=1)
    assert json.loads(vk.adif_extra)["EMAIL"] == ""  # zero-length unmapped field kept


def test_band_from_freq(cty_db):
    qsos, warnings = convert("xlog.adi", cty_db)
    assert [(q.call, q.band, q.freq_mhz) for q in qsos] == [
        ("K5XYZ", "15m", 21.0),
        ("JA1XYZ", "15m", 21.0),
        ("DL7XYZ", "20m", 14.07015),
    ]
    k5 = qsos[0]
    # K5 prefix override in cty.dat: CQ 4, ITU 7
    assert (k5.dxcc, k5.country, k5.cont, k5.cq_zone, k5.itu_zone) == (
        291,
        "United States",
        "NA",
        4,
        7,
    )
    assert_position(k5, CTY_USA, "cty")
    # user-defined fields (xlog's contest serial numbers) are kept
    assert json.loads(k5.adif_extra) == {"SEQ (R)": "792", "SEQ (S)": "001"}
    assert warnings == [NO_POSITION_SUMMARY.format(count=1)]  # JA is not in the excerpt


def test_adif_extra_keeps_serbian_text_unescaped(cty_db):
    qsos, warnings = convert("utf8_name.adi", cty_db)
    assert warnings == []
    first = qsos[0]
    assert first.adif_extra == '{"COMMENT": "73!", "NAME": "Đorđe", "QTH": "Niš"}'
    assert json.loads(qsos[1].adif_extra)["COMMENT"] == "Хвала на вези"
    assert "\\u" not in "".join(q.adif_extra for q in qsos)


@pytest.mark.parametrize("path", sorted(ADIF_DIR.glob("*.adi")), ids=lambda p: p.name)
def test_every_fixture_converts_consistently(path, cty_db):
    records = read_records(path.name)
    qsos, warnings = records_to_qsos(records, station=HOME, cty=cty_db, source="adif:x.adi")
    assert all(isinstance(w, str) and w for w in warnings)
    assert len(qsos) <= len(records)
    for q in qsos:
        attributes = q.attributes()
        assert list(attributes) == list(FIELD_NAMES)
        assert q.call and q.call == q.call.strip().upper()
        assert q.qso_datetime.tzinfo is UTC
        assert q.source == "adif:x.adi"
        assert q.dedup_key.startswith(f"{q.call}|{q.qso_datetime:%Y%m%d%H%M}|")
        assert q.dedup_key.endswith(f"|{q.band or ''}|{q.display_mode}")
        extra = json.loads(q.adif_extra)
        assert isinstance(extra, dict)
        assert set(extra).isdisjoint(MAPPED)
        assert (q.lat is None) == (q.lon is None) == (q.loc_source is None)
        assert (q.my_lat is None) == (q.my_lon is None)
        has_path = q.lat is not None and q.my_lat is not None
        assert (q.distance_km is not None) == has_path == (q.bearing_deg is not None)
        if has_path:
            assert 0.0 <= q.bearing_deg < 360.0
            assert 0.0 <= q.distance_km <= math.pi * geo.EARTH_RADIUS_KM
        for name in ("dxcc", "cq_zone", "itu_zone"):
            assert getattr(q, name) is None or type(getattr(q, name)) is int
    # every kept record carries exactly its unmapped fields
    for record in records:
        q, _ = record_to_qso(record, station=HOME, cty=cty_db)
        if q is not None:
            assert json.loads(q.adif_extra) == {k: v for k, v in record.items() if k not in MAPPED}


# --- position and origin rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected", "source"),
    [
        pytest.param(
            {"LAT": "S033 52.128", "LON": "E151 12.558", "GRIDSQUARE": "JN95wg"},
            SYDNEY,
            "latlon",
            id="latlon-beats-grid",
        ),
        pytest.param({"GRIDSQUARE": "jn95WG"}, JN95WG, "grid", id="grid-beats-cty"),
        pytest.param({}, CTY_SERBIA, "cty", id="cty"),
        pytest.param({"CALL": "YU7XYZ/MM"}, None, None, id="mm-none"),
        pytest.param({"CALL": "JA1XYZ"}, None, None, id="unknown-entity"),
        pytest.param({"GRIDSQUARE": "ZZ99"}, CTY_SERBIA, "cty", id="invalid-grid-cty"),
        pytest.param(
            {"LAT": "N091 00.000", "LON": "E020 00.000", "GRIDSQUARE": "JN95wg"},
            JN95WG,
            "grid",
            id="invalid-latlon-grid",
        ),
    ],
)
def test_position_priority(record, expected, source, cty_db):
    full = {"CALL": "YU7XYZ"}
    full.update(record)
    q, _ = one(full, cty=cty_db)
    assert_position(q, expected, source)


def test_position_without_cty_database():
    q, warnings = one({"CALL": "YU7XYZ"})
    assert_position(q, None, None)
    assert warnings == [
        "QSO YU7XYZ, 20260915 1845: no position (no LAT/LON, valid locator or cty.dat entity), "
        "the QSO is not shown on the map"
    ]


@pytest.mark.parametrize(
    ("record", "station", "origin", "my_grid"),
    [
        pytest.param(
            {"MY_LAT": "N044 48.750", "MY_LON": "E020 27.672", "MY_GRIDSQUARE": "JN95wg"},
            HOME,
            BEOGRAD,
            "JN95wg",
            id="my-latlon-first",
        ),
        pytest.param({"MY_GRIDSQUARE": "jn95wg"}, HOME, JN95WG, "JN95wg", id="my-grid-second"),
        pytest.param({}, HOME, KN04FT, "KN04ft", id="station-third"),
        pytest.param({}, Station(grid="kn04FT"), KN04FT, "KN04ft", id="station-normalized"),
        pytest.param({}, None, None, None, id="no-station"),
        pytest.param({}, Station(call="YU1QQ"), None, None, id="station-without-grid"),
        pytest.param(
            {"MY_LAT": "N044 48.750", "MY_LON": "E020 27.672"},
            None,
            BEOGRAD,
            None,
            id="my-latlon-without-grid",
        ),
    ],
)
def test_origin_priority(record, station, origin, my_grid):
    full = {"CALL": "W1XYZ", "GRIDSQUARE": "FN42"}
    full.update(record)
    q, _ = one(full, station=station)
    assert_origin(q, origin)
    assert q.my_gridsquare == my_grid
    if origin is not None:
        assert q.distance_km == pytest.approx(geo.distance_km(*origin, *FN42), rel=1e-12)
        assert q.bearing_deg == pytest.approx(geo.bearing_deg(*origin, *FN42), rel=1e-12)


def test_invalid_my_gridsquare_falls_back_to_my_station():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "MY_GRIDSQUARE": "KN4"})
    assert q.my_gridsquare == "KN4"  # kept as logged
    assert_origin(q, KN04FT)
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: invalid locator KN4 in MY_GRIDSQUARE, not used for the position"
    ]


def test_my_ten_character_locator_is_cut():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "MY_GRIDSQUARE": "jn95WG04AB"})
    assert q.my_gridsquare == "JN95wg04"
    assert_origin(q, maidenhead.to_latlon("JN95wg04"))
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: locator jn95WG04AB in MY_GRIDSQUARE cut to JN95wg04"
    ]


def test_no_origin_is_reported_for_a_single_record():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42"}, station=None)
    assert_origin(q, None)
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: my QTH unknown (no MY_LAT/MY_LON, MY_GRIDSQUARE or locator "
        "in the HamQ settings), no distance and bearing"
    ]


def test_invalid_station_locator_is_reported_once():
    station = Station(call="YU1QQ", grid="KN04f")
    records = [{"CALL": f"W{i}XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845"} for i in range(3)]
    qsos, warnings = records_to_qsos(records, station=station)
    assert len(qsos) == 3
    assert all(q.my_gridsquare is None and q.my_lat is None for q in qsos)
    assert warnings == [
        "My locator KN04f is invalid, ignored",
        NO_POSITION_SUMMARY.format(count=3),
        NO_QTH_SUMMARY.format(count=3),
    ]
    _, single = record_to_qso(records[0], station=station)
    assert single[0] == "My locator KN04f is invalid, ignored"


@pytest.mark.parametrize(
    ("record", "warning"),
    [
        pytest.param(
            {"LAT": "N091 00.000", "LON": "E020 00.000"},
            "invalid value N091 00.000 in LAT, ignored",
            id="lat-out-of-range",
        ),
        pytest.param(
            {"LAT": "N044 48.750", "LON": "20.4612"},
            "invalid value 20.4612 in LON, ignored",
            id="lon-decimal",
        ),
        pytest.param(
            {"LAT": "E044 48.750", "LON": "E020 27.672"},
            "invalid value E044 48.750 in LAT, ignored",
            id="lat-with-east",
        ),
        pytest.param(
            {"LAT": "N044 48.750", "LON": "N020 27.672"},
            "invalid value N020 27.672 in LON, ignored",
            id="lon-with-north",
        ),
        pytest.param({"LAT": "N044 48.750"}, "LAT without LON, ignored", id="lat-only"),
        pytest.param({"LON": "E020 27.672"}, "LON without LAT, ignored", id="lon-only"),
        pytest.param(
            {"LAT": "N000 00.000", "LON": "E000 00.000"},
            "LAT/LON = 0/0 is not a real position, ignored",
            id="null-island",
        ),
    ],
)
def test_unusable_lat_lon(record, warning, cty_db):
    full = {"CALL": "YU7XYZ", "GRIDSQUARE": "JN95wg"}
    full.update(record)
    q, warnings = one(full, cty=cty_db)
    assert_position(q, JN95WG, "grid")
    assert warnings == [f"QSO YU7XYZ, 20260915 1845: {warning}"]
    # unmapped fields, so they stay in adif_extra even when they are not used
    assert json.loads(q.adif_extra) == record


def test_unusable_my_lat_my_lon():
    q, warnings = one(
        {"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "MY_LAT": "N000 00.000", "MY_LON": "W000 00.000"}
    )
    assert_origin(q, KN04FT)
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: MY_LAT/MY_LON = 0/0 is not a real position, ignored"
    ]
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "MY_LON": "E020 27.672"})
    assert_origin(q, KN04FT)
    assert warnings == ["QSO W1XYZ, 20260915 1845: MY_LON without MY_LAT, ignored"]


def test_lat_lon_on_the_equator_or_meridian_are_real():
    q, warnings = one({"CALL": "W1XYZ", "LAT": "N000 00.000", "LON": "W078 30.000"})
    assert warnings == []
    assert_position(q, (0.0, -78.5), "latlon")
    q, warnings = one({"CALL": "W1XYZ", "LAT": "N051 28.638", "LON": "E000 00.000"})
    assert warnings == []
    assert_position(q, (51.4773, 0.0), "latlon")


# --- distance and bearing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "distance", "bearing"),
    [
        ("S033 52.128", "E151 12.558", 15676.1, 91.0),  # Sydney
        ("N048 08.800", "E011 36.500", 773.6, 301.8),  # München
        ("N038 53.862", "W077 02.196", 7608.7, 303.9),  # Washington
    ],
)
def test_distance_and_bearing_against_the_skill(lat, lon, distance, bearing):
    record = {"CALL": "W1XYZ", "LAT": lat, "LON": lon}
    record.update({"MY_LAT": "N044 48.750", "MY_LON": "E020 27.672"})
    q, warnings = one(record, station=None)
    assert warnings == []
    assert abs(q.distance_km - distance) / distance < 0.005
    assert abs(q.bearing_deg - bearing) < 0.5


def test_same_place_has_zero_distance():
    q, _ = one({"CALL": "YU1XYZ", "GRIDSQUARE": "KN04ft"})
    assert q.distance_km == 0.0
    assert q.bearing_deg == 0.0


# --- band, mode, frequency --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "band", "freq"),
    [
        ({"BAND": "20m"}, "20m", None),
        ({"BAND": " 70CM "}, "70cm", None),
        ({"BAND": "20M", "FREQ": "7.074"}, "20m", 7.074),  # BAND wins over FREQ
        ({"FREQ": "14.074"}, "20m", 14.074),
        ({"FREQ": "144,300"}, "2m", 144.3),
        ({"FREQ": "1296.2"}, "23cm", 1296.2),
        ({"BAND": "", "FREQ": "3.573"}, "80m", 3.573),
        ({}, None, None),
    ],
)
def test_band(record, band, freq):
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", **record})
    assert (q.band, q.freq_mhz) == (band, freq)
    assert warnings == []


def test_unknown_band_takes_the_band_of_freq():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "BAND": "20", "FREQ": "14.074"})
    assert q.band == "20m"
    assert q.dedup_key == "W1XYZ|202609151845|20m|"
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: unknown band 20 in BAND, band 20m taken from FREQ"
    ]
    assert "BAND" not in json.loads(q.adif_extra)


def test_unknown_band_without_freq_is_kept():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "BAND": "11M"})
    assert q.band == "11m"
    assert warnings == ["QSO W1XYZ, 20260915 1845: unknown band 11M in BAND, kept as logged"]


def test_freq_outside_the_bands():
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "FREQ": "14074000"})
    assert (q.band, q.freq_mhz) == (None, 14074000.0)
    assert warnings == [
        "QSO W1XYZ, 20260915 1845: frequency 14074000 MHz is outside the amateur bands, band "
        "unknown"
    ]


@pytest.mark.parametrize("value", ["abc", "0", "-14.074", "14.074.1", "1e3", "inf"])
def test_invalid_freq(value):
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "BAND": "20m", "FREQ": value})
    assert (q.band, q.freq_mhz) == ("20m", None)
    assert warnings == [f"QSO W1XYZ, 20260915 1845: invalid value {value} in FREQ, ignored"]


@pytest.mark.parametrize(
    ("mode", "submode", "stored", "shown"),
    [
        ("FT8", None, ("FT8", None), "FT8"),
        ("ft8", "", ("FT8", None), "FT8"),
        ("MFSK", "FT4", ("MFSK", "FT4"), "FT4"),
        ("mfsk", "ft4", ("MFSK", "FT4"), "FT4"),
        ("FT4", None, ("FT4", None), "FT4"),
        ("SSB", "USB", ("SSB", "USB"), "USB"),
        (None, "JS8", (None, "JS8"), "JS8"),
        (None, None, (None, None), ""),
        ("  ", " ", (None, None), ""),
    ],
)
def test_mode_and_submode(mode, submode, stored, shown):
    record = {"CALL": "W1XYZ", "GRIDSQUARE": "FN42", "BAND": "20m"}
    if mode is not None:
        record["MODE"] = mode
    if submode is not None:
        record["SUBMODE"] = submode
    q, _ = one(record)
    assert (q.mode, q.submode) == stored
    assert q.display_mode == shown == display_mode(q.mode, q.submode)
    assert q.dedup_key == f"W1XYZ|202609151845|20m|{shown}"


def test_ft4_dedup_key_is_the_same_both_ways():
    base = {"CALL": "9a2xyz", "QSO_DATE": "20260913", "TIME_ON": "192030", "BAND": "40M"}
    new, _ = record_to_qso({**base, "MODE": "FT4"})
    old, _ = record_to_qso({**base, "MODE": "MFSK", "SUBMODE": "FT4", "TIME_ON": "1920"})
    assert new.dedup_key == old.dedup_key == "9A2XYZ|202609131920|40m|FT4"
    assert new.dedup_key == adif.dedup_key("9A2XYZ", "20260913", "192030", "40m", "FT4")
    other, _ = record_to_qso({**base, "MODE": "FT8"})
    assert other.dedup_key != new.dedup_key


def test_rst_values_are_kept_as_logged():
    q, _ = one({"CALL": "W1XYZ", "RST_SENT": "599", "RST_RCVD": "-12"})
    assert (q.rst_sent, q.rst_rcvd) == ("599", "-12")
    q, _ = one({"CALL": "W1XYZ", "RST_SENT": "", "RST_RCVD": "  "})
    assert (q.rst_sent, q.rst_rcvd) == (None, None)


# --- DXCC data ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "column", "expected"),
    [
        ("DXCC", "291", "dxcc", 291),
        ("DXCC", "0", "dxcc", 0),  # ADIF: not in any DXCC entity
        ("CQZ", "05", "cq_zone", 5),
        ("CQZ", "40", "cq_zone", 40),
        ("ITUZ", "08", "itu_zone", 8),
        ("ITUZ", "90", "itu_zone", 90),
        ("CONT", "na", "cont", "NA"),
        ("CONT", "AN", "cont", "AN"),
        ("COUNTRY", "United States of America", "country", "United States of America"),
    ],
)
def test_valid_dxcc_values(field, value, column, expected):
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", field: value})
    assert getattr(q, column) == expected
    assert warnings == []


@pytest.mark.parametrize(
    ("field", "value", "column", "from_cty"),
    [
        ("DXCC", "abc", "dxcc", 291),
        ("DXCC", "1000", "dxcc", 291),
        ("DXCC", "-291", "dxcc", 291),
        ("DXCC", "291.0", "dxcc", 291),
        ("DXCC", "２９１", "dxcc", 291),  # full-width digits
        ("DXCC", "2_91", "dxcc", 291),
        ("CQZ", "0", "cq_zone", 5),
        ("CQZ", "41", "cq_zone", 5),
        ("ITUZ", "0", "itu_zone", 8),
        ("ITUZ", "91", "itu_zone", 8),
        ("CONT", "XX", "cont", "NA"),
        ("CONT", "Europe", "cont", "NA"),
    ],
)
def test_invalid_dxcc_values_are_filled_from_cty(field, value, column, from_cty, cty_db):
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": "FN42", field: value}, cty=cty_db)
    assert getattr(q, column) == from_cty
    assert warnings == [f"QSO W1XYZ, 20260915 1845: invalid value {value} in {field}, ignored"]
    assert field not in json.loads(q.adif_extra)


def test_dxcc_codes_need_cty_csv():
    db = CtyDatabase.from_files(CTY_DIR / "cty_excerpt.dat")
    q, warnings = one({"CALL": "IT9XYZ", "DXCC": "248"}, cty=db)
    assert warnings == []  # no code to compare with
    assert (q.dxcc, q.country, q.cont) == (248, "Italy", "EU")
    q, _ = one({"CALL": "IT9XYZ"}, cty=db)
    assert (q.dxcc, q.country) == (None, "Italy")


def test_dxcc_zero_differs_from_an_exact_cty_entry(cty_db):
    # =N2NL/MM(7) is an exact cty.dat entry under United States
    q, warnings = one({"CALL": "N2NL/MM", "DXCC": "0"}, cty=cty_db)
    assert q.dxcc == 0
    assert (q.country, q.cq_zone) == ("United States", 7)
    assert warnings == [
        "QSO N2NL/MM, 20260915 1845: DXCC 0 in the log does not match cty.dat (United States, "
        "DXCC 291)"
    ]


def test_cty_is_not_consulted_when_nothing_is_missing():
    class Exploding:
        def lookup(self, call):
            raise AssertionError("lookup() must not be called")

    record = {
        "CALL": "W1XYZ",
        "GRIDSQUARE": "FN42",
        "DXCC": "291",
        "COUNTRY": "United States",
        "CONT": "NA",
        "CQZ": "5",
        "ITUZ": "8",
    }
    q, warnings = one(record, cty=Exploding())
    assert warnings == []
    assert q.loc_source == "grid"


# --- call, date, time, source -------------------------------------------------------------


@pytest.mark.parametrize("call", [None, "", "   "])
def test_missing_call_skips_the_record(call):
    record = {"QSO_DATE": "20260915", "TIME_ON": "1845", "GRIDSQUARE": "FN42"}
    if call is not None:
        record["CALL"] = call
    q, warnings = record_to_qso(record, station=HOME)
    assert q is None
    assert warnings == ["QSO 20260915 1845: missing CALL, skipped"]


def test_missing_call_without_date_and_time():
    q, warnings = record_to_qso({"NAME": "John"})
    assert q is None
    assert warnings == ["QSO: missing CALL, skipped"]


@pytest.mark.parametrize(
    ("date", "time_on", "shown"),
    [
        ("20260230", "1000", "20260230 1000"),  # 30 February
        ("20260802", "2460", "20260802 2460"),
        ("20260802", "", "20260802"),
        (None, "1845", "1845"),
        ("2026-09-15", "18:45", "2026-09-15 18:45"),
        ("19291231", "2359", "19291231 2359"),  # before the ADIF minimum
        ("20260915", "18", "20260915 18"),
    ],
)
def test_invalid_date_or_time_skips_the_record(date, time_on, shown):
    record = {"CALL": "yu1xyz", "TIME_ON": time_on}
    if date is not None:
        record["QSO_DATE"] = date
    q, warnings = record_to_qso(record, station=HOME)
    assert q is None
    assert warnings == [f"QSO YU1XYZ, {shown}: missing or invalid QSO_DATE or TIME_ON, skipped"]


def test_call_is_uppercase_and_stripped():
    q, _ = one({"CALL": "  yu1xyz/p "})
    assert q.call == "YU1XYZ/P"
    assert q.dedup_key.startswith("YU1XYZ/P|")


def test_datetime_is_aware_utc_with_seconds():
    q, _ = one({"CALL": "W1XYZ", "QSO_DATE": "20260915", "TIME_ON": "184559"})
    assert q.qso_datetime == datetime(2026, 9, 15, 18, 45, 59, tzinfo=UTC)
    assert q.qso_datetime.tzinfo is UTC
    assert q.qso_datetime.utcoffset().total_seconds() == 0
    # seconds are dropped from the dedup key (some loggers round them)
    assert q.dedup_key == "W1XYZ|202609151845||"


@pytest.mark.parametrize("source", ["", "wsjtx", "adif:Moj log Đorđe.adi", "manual"])
def test_source_is_passed_through(source):
    q, _ = one({"CALL": "W1XYZ"}, source=source)
    assert q.source == source
    qsos, _ = records_to_qsos(
        [{"CALL": "W1XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845"}], source=source
    )
    assert qsos[0].source == source


def test_source_defaults_to_empty():
    q, _ = record_to_qso({"CALL": "W1XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845"})
    assert q.source == ""
    q, _ = one({"CALL": "W1XYZ"}, source=None)
    assert q.source == ""  # the column stays text


# --- adif_extra -------------------------------------------------------------------------------


def test_adif_extra_is_exactly_the_unmapped_fields():
    record = {
        "CALL": "YU1XYZ",
        "QSO_DATE": "20260915",
        "TIME_ON": "1845",
        "BAND": "80m",
        "MODE": "SSB",
        "SUBMODE": "LSB",
        "FREQ": "3.720",
        "RST_SENT": "59",
        "RST_RCVD": "57",
        "GRIDSQUARE": "KN03wh",
        "MY_GRIDSQUARE": "KN04ft",
        "DXCC": "296",
        "COUNTRY": "Serbia",
        "CONT": "EU",
        "CQZ": "15",
        "ITUZ": "28",
        "NAME": "Đorđe Petrović",
        "QTH": "Niš",
        "COMMENT": "Хвала на вези, 73",
        "LAT": "N043 19.000",
        "LON": "E021 54.000",
        "MY_LAT": "N044 48.750",
        "MY_LON": "E020 27.672",
        "TIME_OFF": "1850",
        "APP_N1MM_EXCHANGE1": "",
        "SEQ (S)": "001",
    }
    q, warnings = record_to_qso(record, station=HOME)
    assert warnings == []
    expected = {k: v for k, v in record.items() if k not in MAPPED}
    assert q.adif_extra == json.dumps(expected, ensure_ascii=False, sort_keys=True)
    assert json.loads(q.adif_extra) == expected
    assert list(json.loads(q.adif_extra)) == sorted(expected)
    assert "Đorđe Petrović" in q.adif_extra and "Хвала" in q.adif_extra


def test_adif_extra_is_an_empty_object_when_nothing_is_extra():
    q, _ = one({"CALL": "W1XYZ", "BAND": "20m", "MODE": "FT8"})
    assert q.adif_extra == "{}"


def test_record_keys_are_case_insensitive_and_values_stripped():
    record = {
        "call": " w1xyz ",
        "qso_date": "20260915",
        "Time_On": "1845",
        "band": "20M",
        "gridsquare": " fn42 ",
        "name": " John ",
    }
    q, warnings = record_to_qso(record, station=HOME)
    assert warnings == []
    assert (q.call, q.band, q.gridsquare) == ("W1XYZ", "20m", "FN42")
    assert json.loads(q.adif_extra) == {"NAME": "John"}


def test_non_text_values_are_tolerated():
    record = {"CALL": "W1XYZ", "QSO_DATE": 20260915, "TIME_ON": "1845", "FREQ": 14.074}
    record["GRIDSQUARE"] = "FN42"
    record.update({"DXCC": 291, "NAME": None, "NR": 7})
    q, warnings = record_to_qso(record, station=HOME)
    assert warnings == []
    assert (q.freq_mhz, q.band, q.dxcc) == (14.074, "20m", 291)
    assert json.loads(q.adif_extra) == {"NR": "7"}


# --- attributes ----------------------------------------------------------------------------


def test_attributes_follow_qso_fields(cty_db):
    q, _ = one({"CALL": "IT9XYZ", "GRIDSQUARE": "JM77", "BAND": "20m", "MODE": "CW"}, cty=cty_db)
    attributes = q.attributes()
    assert list(attributes) == [name for name, _ in QSO_FIELDS]
    assert attributes == {name: getattr(q, name) for name in FIELD_NAMES}
    assert attributes["qso_datetime"].tzinfo is UTC
    kinds = dict(QSO_FIELDS)
    for name, value in attributes.items():
        if value is None:
            continue
        expected = {"int": int, "real": float, "text": str, "datetime": datetime}[kinds[name]]
        assert type(value) is expected, name
    attributes["call"] = "changed"
    assert q.call == "IT9XYZ"  # a fresh dict every time


# --- records_to_qsos ---------------------------------------------------------------------------


def test_records_to_qsos_matches_record_to_qso(cty_db):
    records = read_records("qrz_export.adi") + read_records("wsjtx_log.adi")
    qsos, _ = records_to_qsos(iter(records), station=HOME, cty=cty_db, source="x")
    singles = [record_to_qso(r, station=HOME, cty=cty_db, source="x")[0] for r in records]
    assert qsos == [q for q in singles if q is not None]


def test_records_to_qsos_with_nothing():
    assert records_to_qsos([]) == ([], [])
    assert records_to_qsos(None) == ([], [])  # type: ignore[arg-type]


def test_records_to_qsos_skips_items_that_are_not_records():
    good = {"CALL": "W1XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845", "GRIDSQUARE": "FN42"}
    qsos, warnings = records_to_qsos([None, "CALL", good], station=HOME)  # type: ignore[list-item]
    assert [q.call for q in qsos] == ["W1XYZ"]
    assert warnings == ["Record 1: missing CALL, skipped", "Record 2: missing CALL, skipped"]


def test_summaries_count_qsos_without_position_or_my_qth():
    records = [
        {"CALL": "W1XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845", "GRIDSQUARE": "FN42"},
        {"CALL": "W2XYZ", "QSO_DATE": "20260915", "TIME_ON": "1846"},
        {"CALL": "W3XYZ", "QSO_DATE": "20260915", "TIME_ON": "1847", "MY_GRIDSQUARE": "KN04ft"},
    ]
    qsos, warnings = records_to_qsos(records, station=None)
    assert len(qsos) == 3
    assert warnings == [NO_POSITION_SUMMARY.format(count=2), NO_QTH_SUMMARY.format(count=2)]


def test_warnings_are_capped():
    records = [
        {"CALL": f"W{i}XYZ", "QSO_DATE": "20260915", "TIME_ON": "1845", "GRIDSQUARE": "ZZ00"}
        for i in range(150)
    ]
    records.append({"QSO_DATE": "20260915", "TIME_ON": "1845"})
    qsos, warnings = records_to_qsos(records)
    assert len(qsos) == 150
    assert len(warnings) == 100 + 3
    assert warnings[0] == (
        "Record 1 (W0XYZ, 20260915 1845): invalid locator ZZ00 in GRIDSQUARE, not used for the "
        "position"
    )
    assert warnings[100:] == [
        "Further warnings not listed: 51",
        NO_POSITION_SUMMARY.format(count=150),
        NO_QTH_SUMMARY.format(count=150),
    ]


def test_long_values_are_shortened_in_warnings():
    garbage = "X" * 500
    q, warnings = one({"CALL": "W1XYZ", "GRIDSQUARE": garbage, "LAT": "line\nbreak"})
    assert q.gridsquare == garbage
    assert all(len(w) < 250 for w in warnings), warnings
    assert all("\n" not in w for w in warnings)
    assert "invalid value line break in LAT, ignored" in warnings[1]


# --- translation ---------------------------------------------------------------------------


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
        assert not re.search("[Ѐ-ӿ]", serbian), serbian
        assert sorted(re.findall(r"\{(\w*)\}", serbian)) == sorted(
            re.findall(r"\{(\w*)\}", english)
        )


def test_shared_messages_agree_with_other_catalogs():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for other in sorted(CATALOG.parent.glob("*.json")):
        if other != CATALOG:
            data = json.loads(other.read_text(encoding="utf-8"))
            for key in set(data) & set(catalog):
                assert data[key] == catalog[key], (other.name, key)


@pytest.fixture
def serbian(monkeypatch):
    monkeypatch.setattr(qso, "tr", i18n.tr)
    before = i18n.current_language()
    i18n.set_language(i18n.LANG_SR_LATN)
    yield
    i18n.set_language(before)


@pytest.mark.usefixtures("serbian")
def test_warnings_are_translated():
    q, warnings = record_to_qso({"QSO_DATE": "20260915", "TIME_ON": "1845"})
    assert q is None
    assert warnings == ["Veza 20260915 1845: nedostaje CALL, preskočeno"]
    records = [
        {"CALL": "yu1xyz", "QSO_DATE": "20260915", "TIME_ON": "1845", "GRIDSQUARE": "ZZ00"},
    ]
    _, warnings = records_to_qsos(records, station=HOME)
    assert warnings == [
        "Zapis 1 (YU1XYZ, 20260915 1845): neispravan lokator ZZ00 u polju GRIDSQUARE, ne "
        "koristi se za poziciju",
        "Veze bez pozicije, ne prikazuju se na mapi: 1",
    ]


@pytest.mark.usefixtures("serbian")
def test_warnings_in_cyrillic():
    i18n.set_language(i18n.LANG_SR_CYRL)
    _, warnings = record_to_qso({"CALL": "W1XYZ", "QSO_DATE": "20260230", "TIME_ON": "1845"})
    assert warnings == [
        "Веза W1XYZ, 20260230 1845: QSO_DATE или TIME_ON недостаје или није исправан, прескочено"
    ]


# --- robustness and speed ---------------------------------------------------------------------

TRICKY = [
    "",
    " ",
    "0",
    "-1",
    "05",
    "99999",
    "abc",
    "nan",
    "inf",
    "1e5",
    "14,074",
    "14.074",
    "20M",
    "KN04ft",
    "kn04FT",
    "ZZ00",
    "JN75xt74oj",
    "N044 48.750",
    "S091 00.000",
    "E020 27.672",
    "W181 00.000",
    "N000 00.000",
    "20260915",
    "20260230",
    "1845",
    "2460",
    "184559",
    "EU",
    "xx",
    "Đorđe",
    "٢٩١",
    "２９１",
    "2_91",
    "<EOR>",
    "\x00",
    "YU1XYZ/MM",
    "N2NL/MM",
    "IT9XYZ",
    "KG4AB",
    "A" * 300,
]
KEYS = sorted(MAPPED | {"LAT", "LON", "MY_LAT", "MY_LON", "NAME", "call"})


def test_random_records_never_raise(cty_db):
    rng = random.Random(20261001)
    records = []
    for _ in range(3000):
        record = {key: rng.choice(TRICKY) for key in rng.sample(KEYS, rng.randint(0, len(KEYS)))}
        if rng.random() < 0.7:
            record.update(CALL=rng.choice(["W1XYZ", "yu1xyz", "IT9XYZ", "OH2XYZ/MM", " "]))
            record.update(QSO_DATE="20260915", TIME_ON=rng.choice(["1845", "184559"]))
        records.append(record)
    for station in (HOME, None, Station(grid="garbage")):
        qsos, warnings = records_to_qsos(records, station=station, cty=cty_db, source="fuzz")
        assert all(isinstance(w, str) for w in warnings)
        for q in qsos:
            assert q.call and q.qso_datetime.tzinfo is UTC
            assert q.dxcc is None or 0 <= q.dxcc <= 999
            assert q.cq_zone is None or 1 <= q.cq_zone <= 40
            assert q.itu_zone is None or 1 <= q.itu_zone <= 90
            assert q.cont in (None, "EU", "AS", "AF", "NA", "SA", "OC", "AN")
            assert q.freq_mhz is None or (math.isfinite(q.freq_mhz) and q.freq_mhz > 0)
            if q.lat is not None:
                assert -90 <= q.lat <= 90 and -180 <= q.lon <= 180
            if q.distance_km is not None:
                assert math.isfinite(q.distance_km) and 0 <= q.bearing_deg < 360
            assert isinstance(json.loads(q.adif_extra), dict)
            assert list(q.attributes()) == list(FIELD_NAMES)


@pytest.mark.slow
def test_10000_records_are_fast(cty_db):
    calls = ["YU1XYZ", "9A2XYZ", "W1XYZ", "VK2XYZ", "IT9XYZ", "KH6XYZ", "DL1XYZ", "OH2XYZ/MM"]
    records = []
    for i in range(10000):
        record = {
            "CALL": calls[i % len(calls)],
            "QSO_DATE": "20260915",
            "TIME_ON": f"{(i // 60) % 24:02d}{i % 60:02d}00",
            "BAND": "20m",
            "MODE": "MFSK",
            "SUBMODE": "FT4",
            "FREQ": "14.081500",
            "RST_SENT": "-10",
            "RST_RCVD": "-12",
            "MY_GRIDSQUARE": "KN04ft",
            "STATION_CALLSIGN": "YU1QQ",
            "NAME": "Đorđe Petrović" if i % 3 == 0 else "John",
            "COMMENT": "tnx 73",
        }
        if i % 4:
            record["GRIDSQUARE"] = "JN95wg"
        if i % 5 == 0:
            record.update(LAT="S033 52.128", LON="E151 12.558")
        records.append(record)
    start = time.perf_counter()
    qsos, _ = records_to_qsos(records, station=HOME, cty=cty_db, source="adif:big.adi")
    elapsed = time.perf_counter() - start
    assert len(qsos) == 10000
    assert elapsed < 2.0, f"converting 10 000 records took {elapsed:.2f} s"
