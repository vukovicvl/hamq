"""hamq.qgis_io.gpkg: schema, inserts with deduplication, geodesic paths, reads, recalculation."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsDistanceArea,
    QgsFeature,
    QgsFeedback,
    QgsGeometry,
    QgsPointXY,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from hamq.core import geo, maidenhead
from hamq.core.cty import CtyDatabase
from hamq.core.qso import PATH_FIELDS, QSO_FIELDS, Station, records_to_qsos
from hamq.qgis_io import compat, gpkg, layers
from hamq.qgis_io.fields import FIELD_KINDS, make_fields

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
UTC = timezone.utc
STATION = Station(call="YU1XX", grid="KN04ft")
BEOGRAD = (44.8125, 20.4612)
SYDNEY = (-33.8688, 151.2093)
TOKYO = (35.6895, 139.6917)
LOS_ANGELES = (34.0522, -118.2437)


# --- helpers ------------------------------------------------------------------------------------


def record(call: str, minute: int = 0, **fields: str) -> dict[str, str]:
    """An ADIF record of 2026-09-15 at 18:mm UTC, 20 m FT8, plus ``fields``."""
    result = {
        "CALL": call,
        "QSO_DATE": "20260915",
        "TIME_ON": f"18{minute:02d}00",
        "BAND": "20m",
        "MODE": "FT8",
    }
    result.update(fields)
    return result


def adif_coordinates(lat: float, lon: float) -> dict[str, str]:
    """ADIF ``LAT`` / ``LON`` values (``XDDD MM.MMM``) of a point."""

    def value(number: float, positive: str, negative: str) -> str:
        degrees = int(abs(number))
        minutes = (abs(number) - degrees) * 60.0
        return f"{positive if number >= 0 else negative}{degrees:03d} {minutes:06.3f}"

    return {"LAT": value(lat, "N", "S"), "LON": value(lon, "E", "W")}


def antipode(lat: float, lon: float) -> tuple[float, float]:
    return -lat, lon - 180.0 if lon > 0 else lon + 180.0


def flat(parts) -> list[float]:
    return [coordinate for part in parts for point in part for coordinate in point]


def make_qsos(records, station=STATION, cty=None, source="adif:test.adi"):
    qsos, _warnings = records_to_qsos(records, station=station, cty=cty, source=source)
    assert len(qsos) == len(records)
    return qsos


def sample_qsos():
    return make_qsos(
        [
            record("VK2ABC", 1, GRIDSQUARE="QF56od"),
            record("W1AW", 2, GRIDSQUARE="FN31pr", BAND="40m", MODE="MFSK", SUBMODE="FT4"),
            record("JA1XYZ", 3, GRIDSQUARE="PM95", RST_SENT="-10", RST_RCVD="-12"),
            record("DL1ABC", 4, LAT="N051 30.000", LON="E010 00.000", NAME="Đorđe"),
            record("NOPOS", 5),
        ]
    )


def fake_spatial_functions(connection: sqlite3.Connection) -> None:
    """GDAL's R-tree triggers call ST_* functions, which plain SQLite lacks. Their
    conditions are false for the updates made here, but the functions must exist."""
    for name in ("ST_IsEmpty", "ST_MinX", "ST_MaxX", "ST_MinY", "ST_MaxY"):
        connection.create_function(name, 1, lambda geometry: None)


def sql(path: str, statement: str, parameters=()) -> list[tuple]:
    with contextlib.closing(sqlite3.connect(path)) as connection:
        fake_spatial_functions(connection)
        rows = connection.execute(statement, parameters).fetchall()
        connection.commit()
    return rows


def layer(path: str, name: str) -> QgsVectorLayer:
    result = QgsVectorLayer(gpkg.layer_uri(path, name), name, "ogr")
    assert result.isValid()
    return result


def features(path: str, name: str) -> list[QgsFeature]:
    return sorted(layer(path, name).getFeatures(), key=lambda feature: feature.id())


def wgs84() -> QgsDistanceArea:
    distance_area = QgsDistanceArea()
    distance_area.setSourceCrs(
        QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateTransformContext()
    )
    distance_area.setEllipsoid("WGS84")
    return distance_area


def parts_of(geometry: QgsGeometry) -> list[list[tuple[float, float]]]:
    return [[(point.x(), point.y()) for point in part] for part in geometry.asMultiPolyline()]


def longest_jump(parts) -> float:
    return max(
        (abs(x2 - x1) for part in parts for (x1, _), (x2, _) in zip(part, part[1:])),
        default=0.0,
    )


def is_null(value) -> bool:
    return value is None or (type(value).__name__ == "QVariant" and value.isNull())


def create_layer(path: str, name: str, spec, wkb, crs="EPSG:4326", new_file=True) -> None:
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = name
    options.actionOnExistingFile = (
        compat.WRITER_CREATE_OR_OVERWRITE_FILE
        if new_file
        else compat.WRITER_CREATE_OR_OVERWRITE_LAYER
    )
    writer = QgsVectorFileWriter.create(
        path,
        make_fields(spec),
        wkb,
        QgsCoordinateReferenceSystem(crs),
        QgsCoordinateTransformContext(),
        options,
    )
    assert writer.hasError() == compat.WRITER_NO_ERROR, writer.errorMessage()
    del writer


@pytest.fixture
def cty() -> CtyDatabase:
    return CtyDatabase.from_files(
        FIXTURES / "cty" / "cty_excerpt.dat", FIXTURES / "cty" / "cty_excerpt.csv"
    )


@pytest.fixture
def english():
    from hamq.core.i18n import LANG_EN, set_language

    set_language(LANG_EN)
    yield
    set_language(LANG_EN)


# --- schema ------------------------------------------------------------------------------------


def test_layer_uri():
    assert gpkg.layer_uri("/data/log.gpkg", "qso") == "/data/log.gpkg|layername=qso"
    assert (gpkg.QSO_LAYER, gpkg.PATH_LAYER) == ("qso", "qso_path")


def test_ensure_gpkg_creates_the_schema(tmp_path):
    path = str(tmp_path / "new" / "folder" / "log.gpkg")
    gpkg.ensure_gpkg(path)
    assert os.path.isfile(path)
    contents = dict(sql(path, "SELECT table_name, data_type FROM gpkg_contents"))
    assert contents == {"qso": "features", "qso_path": "features", "hamq_meta": "attributes"}
    geometry = {
        name: (kind, srs)
        for name, kind, srs in sql(
            path, "SELECT table_name, geometry_type_name, srs_id FROM gpkg_geometry_columns"
        )
    }
    assert geometry == {"qso": ("POINT", 4326), "qso_path": ("MULTILINESTRING", 4326)}

    qso_layer = layer(path, "qso")
    assert qso_layer.wkbType() == compat.WKB_POINT
    assert qso_layer.crs().authid() == "EPSG:4326"
    assert qso_layer.fields().names() == ["fid"] + [name for name, _ in QSO_FIELDS]
    for name, kind in QSO_FIELDS:
        assert qso_layer.fields().field(name).type() == FIELD_KINDS[kind], name
    path_layer = layer(path, "qso_path")
    assert path_layer.wkbType() == compat.WKB_MULTILINESTRING
    assert path_layer.crs().authid() == "EPSG:4326"
    assert path_layer.fields().names() == ["fid"] + [name for name, _ in PATH_FIELDS]

    indexes = {
        name: (unique, [column for _, _, column in sql(path, f'PRAGMA index_info("{name}")')])
        for _, name, unique, *_ in sql(path, 'PRAGMA index_list("qso")')
    }
    assert indexes["qso_dedup_key_idx"] == (1, ["dedup_key"])
    path_indexes = {
        name: unique for _, name, unique, *_ in sql(path, 'PRAGMA index_list("qso_path")')
    }
    assert path_indexes["qso_path_qso_fid_idx"] == 0
    assert [column for _, _, column in sql(path, 'PRAGMA index_info("qso_path_qso_fid_idx")')] == [
        "qso_fid"
    ]
    assert sql(path, 'SELECT "key", "value" FROM hamq_meta') == [
        ("schema_version", str(gpkg.SCHEMA_VERSION))
    ]


def test_ensure_gpkg_is_idempotent(tmp_gpkg):
    gpkg.ensure_gpkg(tmp_gpkg)
    gpkg.ensure_gpkg(tmp_gpkg)
    result = gpkg.insert_qsos(tmp_gpkg, sample_qsos())
    tables = sql(tmp_gpkg, "SELECT name FROM sqlite_master ORDER BY name")
    gpkg.ensure_gpkg(tmp_gpkg)
    assert sql(tmp_gpkg, "SELECT name FROM sqlite_master ORDER BY name") == tables
    assert len(gpkg.read_qso_rows(tmp_gpkg)) == result.inserted == 5
    assert sql(tmp_gpkg, "SELECT count(*) FROM hamq_meta") == [(1,)]


def test_ensure_gpkg_turns_an_empty_file_into_a_geopackage(tmp_gpkg):
    Path(tmp_gpkg).write_bytes(b"")
    gpkg.ensure_gpkg(tmp_gpkg)
    assert layer(tmp_gpkg, "qso").featureCount() == 0


def test_ensure_gpkg_adds_missing_tables_and_fields(tmp_gpkg):
    create_layer(tmp_gpkg, "qso", [("call", "text"), ("dedup_key", "text")], compat.WKB_POINT)
    old = layer(tmp_gpkg, "qso")
    feature = QgsFeature(old.fields())
    feature.setAttributes([None, "YU1AB", "YU1AB|202609151845|20m|FT8"])
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(20.0, 44.0)))
    assert old.dataProvider().addFeatures([feature])[0]
    del old

    gpkg.ensure_gpkg(tmp_gpkg)

    upgraded = layer(tmp_gpkg, "qso")
    names = upgraded.fields().names()
    assert set(names) == {"fid"} | {name for name, _ in QSO_FIELDS}
    assert upgraded.fields().field("qso_datetime").type() == FIELD_KINDS["datetime"]
    (kept,) = list(upgraded.getFeatures())
    assert kept["call"] == "YU1AB"
    assert kept.geometry().asPoint() == QgsPointXY(20.0, 44.0)
    assert layer(tmp_gpkg, "qso_path").fields().names() == ["fid"] + [n for n, _ in PATH_FIELDS]
    assert sql(tmp_gpkg, "SELECT value FROM hamq_meta") == [("1",)]
    assert gpkg.existing_dedup_keys(tmp_gpkg) == {"YU1AB|202609151845|20m|FT8"}


def test_ensure_gpkg_upgrades_an_old_schema_version(tmp_gpkg):
    gpkg.ensure_gpkg(tmp_gpkg)
    sql(tmp_gpkg, "UPDATE hamq_meta SET value = '0'")
    gpkg.ensure_gpkg(tmp_gpkg)
    assert sql(tmp_gpkg, "SELECT value FROM hamq_meta") == [("1",)]


def test_ensure_gpkg_keeps_a_newer_schema_version(tmp_gpkg, log_messages):
    gpkg.ensure_gpkg(tmp_gpkg)
    sql(tmp_gpkg, "UPDATE hamq_meta SET value = '99'")
    gpkg.ensure_gpkg(tmp_gpkg)
    gpkg.ensure_gpkg(tmp_gpkg)
    assert sql(tmp_gpkg, "SELECT value FROM hamq_meta") == [("99",)]
    newer = [m for m, tag, _ in log_messages if tag == "HamQ" and "newer HamQ" in m]
    assert len(newer) == 1  # logged once per file


def test_ensure_gpkg_with_old_duplicates_uses_a_plain_index(tmp_gpkg, log_messages):
    create_layer(tmp_gpkg, "qso", QSO_FIELDS, compat.WKB_POINT)
    old = layer(tmp_gpkg, "qso")
    duplicates = []
    for call in ("A1A", "A1A"):
        feature = QgsFeature(old.fields())
        attributes = [None] * old.fields().count()
        attributes[old.fields().lookupField("call")] = call
        attributes[old.fields().lookupField("dedup_key")] = "A1A|202609151800|20m|FT8"
        feature.setAttributes(attributes)
        duplicates.append(feature)
    assert old.dataProvider().addFeatures(duplicates)[0]
    del old

    gpkg.ensure_gpkg(tmp_gpkg)

    indexes = {name: unique for _, name, unique, *_ in sql(tmp_gpkg, 'PRAGMA index_list("qso")')}
    assert indexes["qso_dedup_key_idx"] == 0
    assert any("UNIQUE" in m for m, tag, _ in log_messages if tag == "HamQ")
    again = make_qsos([record("A1A", 0)])
    result = gpkg.insert_qsos(tmp_gpkg, again)
    assert (result.inserted, result.duplicates) == (0, 1)


@pytest.mark.parametrize(
    "content",
    [b"this is not a database", b"SQLite format 3\x00 but broken" + b"\x00" * 100],
    ids=["text", "broken"],
)
def test_ensure_gpkg_rejects_other_files(tmp_gpkg, content):
    Path(tmp_gpkg).write_bytes(content)
    with pytest.raises(gpkg.GpkgError):
        gpkg.ensure_gpkg(tmp_gpkg)
    assert Path(tmp_gpkg).read_bytes() == content


def test_ensure_gpkg_rejects_a_plain_sqlite_database(tmp_gpkg):
    with contextlib.closing(sqlite3.connect(tmp_gpkg)) as connection:
        connection.execute("CREATE TABLE qso (call TEXT)")
        connection.commit()
    before = Path(tmp_gpkg).read_bytes()
    with pytest.raises(gpkg.GpkgError, match="GeoPackage"):
        gpkg.ensure_gpkg(tmp_gpkg)
    assert Path(tmp_gpkg).read_bytes() == before


@pytest.mark.parametrize(
    ("name", "spec", "wkb", "crs"),
    [
        ("qso", QSO_FIELDS, "WKB_POLYGON", "EPSG:4326"),
        ("qso", QSO_FIELDS, "WKB_POINT", "EPSG:3857"),
        ("qso_path", PATH_FIELDS, "WKB_LINESTRING", "EPSG:4326"),
        ("qso", QSO_FIELDS, "WKB_NO_GEOMETRY", ""),
        ("hamq_meta", [("other", "text")], "WKB_NO_GEOMETRY", ""),
    ],
)
def test_ensure_gpkg_rejects_tables_of_another_kind(tmp_gpkg, name, spec, wkb, crs):
    create_layer(tmp_gpkg, name, spec, getattr(compat, wkb), crs)
    before = Path(tmp_gpkg).read_bytes()
    with pytest.raises(gpkg.GpkgError, match=name):
        gpkg.ensure_gpkg(tmp_gpkg)
    assert Path(tmp_gpkg).read_bytes() == before


def test_ensure_gpkg_rejects_an_unregistered_table_of_the_same_name(tmp_gpkg):
    create_layer(tmp_gpkg, "other", [("x", "int")], compat.WKB_POINT)
    sql(tmp_gpkg, "CREATE TABLE qso_path (id INTEGER PRIMARY KEY, data TEXT)")
    sql(tmp_gpkg, "INSERT INTO qso_path (data) VALUES ('keep me')")
    with pytest.raises(gpkg.GpkgError, match="qso_path"):
        gpkg.ensure_gpkg(tmp_gpkg)
    assert sql(tmp_gpkg, "SELECT data FROM qso_path") == [("keep me",)]


def test_ensure_gpkg_rejects_folders_and_empty_paths(tmp_path):
    with pytest.raises(gpkg.GpkgError, match="folder"):
        gpkg.ensure_gpkg(str(tmp_path))
    for path in ("", "   "):
        with pytest.raises(gpkg.GpkgError):
            gpkg.ensure_gpkg(path)


def test_ensure_gpkg_reports_a_folder_it_cannot_create(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(gpkg.GpkgError, match="folder"):
        gpkg.ensure_gpkg(str(blocker / "sub" / "log.gpkg"))


def test_ensure_gpkg_message_is_translated(tmp_path, english):
    from hamq.core.i18n import LANG_SR_LATN, set_language

    set_language(LANG_SR_LATN)
    with pytest.raises(gpkg.GpkgError, match="fascikla"):
        gpkg.ensure_gpkg(str(tmp_path))


# --- inserting ----------------------------------------------------------------------------------


def test_insert_and_reimport(tmp_gpkg):
    qsos = sample_qsos()
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    assert (result.inserted, result.duplicates, result.failed) == (5, 0, 0)
    assert result.fids == [1, 2, 3, 4, 5]
    assert result.paths == 4  # NOPOS has no position
    assert result.warnings == []
    assert not result.canceled

    again = gpkg.insert_qsos(tmp_gpkg, sample_qsos())
    assert (again.inserted, again.duplicates, again.failed) == (0, 5, 0)
    assert again.fids == [] and again.paths == 0
    assert len(features(tmp_gpkg, "qso")) == 5
    assert len(features(tmp_gpkg, "qso_path")) == 4
    assert gpkg.existing_dedup_keys(tmp_gpkg) == {qso.dedup_key for qso in qsos}


def test_duplicates_within_one_batch(tmp_gpkg):
    qsos = make_qsos(
        [
            record("A1A", 1),
            record("A1A", 1),
            record("B2B", 2, MODE="FT4"),
            record("B2B", 2, MODE="MFSK", SUBMODE="FT4"),  # the same QSO
            record("B2B", 3, MODE="FT4"),  # one minute later: another QSO
        ]
    )
    result = gpkg.insert_qsos(tmp_gpkg, qsos, chunk_size=2)
    assert (result.inserted, result.duplicates, result.failed) == (3, 2, 0)
    assert sorted(r["call"] for r in gpkg.read_qso_rows(tmp_gpkg)) == ["A1A", "B2B", "B2B"]


def test_insert_empty_list_creates_nothing(tmp_gpkg):
    result = gpkg.insert_qsos(tmp_gpkg, [])
    assert result == gpkg.InsertResult(0, 0, 0, [], [])
    assert not os.path.exists(tmp_gpkg)


def test_qso_without_position_or_origin(tmp_gpkg):
    qsos = make_qsos([record("NOPOS", 1), record("NOQTH", 2, GRIDSQUARE="JN95")], station=None)
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    assert (result.inserted, result.paths) == (2, 0)
    nopos, noqth = features(tmp_gpkg, "qso")
    assert nopos.geometry().isNull()
    assert not noqth.geometry().isNull()
    assert noqth.geometry().asPoint() == QgsPointXY(*reversed(maidenhead.to_latlon("JN95")))
    assert features(tmp_gpkg, "qso_path") == []


def test_attributes_and_path_round_trip(tmp_gpkg):
    qsos = sample_qsos()
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    rows = gpkg.read_qso_rows(tmp_gpkg)
    assert [row["fid"] for row in rows] == result.fids
    for qso, row in zip(qsos, rows):
        assert set(row) == {"fid"} | {name for name, _ in QSO_FIELDS}
        for name, value in qso.attributes().items():
            if isinstance(value, float):
                assert row[name] == pytest.approx(value), name
            else:
                assert row[name] == value, name
        assert row["qso_datetime"].tzinfo is not None
        assert row["qso_datetime"] == datetime(2026, 9, 15, 18, qso.qso_datetime.minute, tzinfo=UTC)
    assert json.loads(rows[3]["adif_extra"])["NAME"] == "Đorđe"

    stored = {feature["qso_fid"]: feature for feature in features(tmp_gpkg, "qso_path")}
    assert set(stored) == set(result.fids[:4])
    for fid, qso in zip(result.fids[:4], qsos):
        path = stored[fid]
        assert path["band"] == qso.band
        assert path["mode"] == qso.display_mode
        assert path["distance_km"] == pytest.approx(qso.distance_km)
        assert path["bearing_deg"] == pytest.approx(qso.bearing_deg)
        parts = parts_of(path.geometry())
        assert parts[0][0] == pytest.approx((qso.my_lon, qso.my_lat))
        assert parts[-1][-1] == pytest.approx((qso.lon, qso.lat))
    assert stored[result.fids[1]]["mode"] == "FT4"  # MODE=MFSK SUBMODE=FT4


def test_datetime_is_stored_as_utc_text(tmp_gpkg):
    gpkg.insert_qsos(tmp_gpkg, sample_qsos()[:1])
    ((stored,),) = sql(tmp_gpkg, "SELECT qso_datetime FROM qso")
    assert stored.startswith("2026-09-15T18:01:00")
    assert stored.endswith("Z")


def test_read_qso_rows_converts_stored_times_to_utc(tmp_gpkg):
    gpkg.insert_qsos(tmp_gpkg, make_qsos([record(f"A{i}A", i) for i in range(5)]))
    for fid, text in enumerate(
        [
            "2026-09-15T18:45:00",  # no zone: UTC
            "2026-09-15 18:45:00.000",
            "2026-09-15T20:45:00+02:00",
            "not a time",
            None,
        ],
        1,
    ):
        sql(tmp_gpkg, "UPDATE qso SET qso_datetime = ? WHERE fid = ?", (text, fid))
    times = [row["qso_datetime"] for row in gpkg.read_qso_rows(tmp_gpkg)]
    expected = datetime(2026, 9, 15, 18, 45, tzinfo=UTC)
    assert times == [expected, expected, expected, None, None]


def test_existing_dedup_keys_of_some_keys(tmp_gpkg):
    qsos = make_qsos(
        [record(f"K{i}K", i % 60, TIME_ON=f"{i // 60:02d}{i % 60:02d}") for i in range(1200)]
    )
    gpkg.insert_qsos(tmp_gpkg, qsos[:1100])
    wanted = [qso.dedup_key for qso in qsos[1000:]] + [None, "nonsense"]
    assert gpkg.existing_dedup_keys(tmp_gpkg, wanted) == {q.dedup_key for q in qsos[1000:1100]}
    assert gpkg.existing_dedup_keys(tmp_gpkg, []) == set()
    assert len(gpkg.existing_dedup_keys(tmp_gpkg)) == 1100


def test_reads_of_a_missing_file_create_nothing(tmp_gpkg):
    assert gpkg.read_qso_rows(tmp_gpkg) == []
    assert gpkg.existing_dedup_keys(tmp_gpkg) == set()
    assert not os.path.exists(tmp_gpkg)


def test_reads_of_a_broken_file_raise(tmp_gpkg):
    Path(tmp_gpkg).write_bytes(b"not a database at all" * 10)
    with pytest.raises(gpkg.GpkgError):
        gpkg.read_qso_rows(tmp_gpkg)
    with pytest.raises(gpkg.GpkgError):
        gpkg.existing_dedup_keys(tmp_gpkg)


def test_bad_qsos_never_stop_an_insert(tmp_gpkg, english):
    good = make_qsos([record(f"G{i}G", i, GRIDSQUARE="JN95") for i in range(4)])
    bad_zone = dataclasses.replace(good[1], cq_zone="abc")
    bad_time = dataclasses.replace(good[2], qso_datetime="yesterday")
    nan_position = dataclasses.replace(good[3], lat=float("nan"))
    qsos = [good[0], bad_zone, bad_time, nan_position]
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    assert (result.inserted, result.failed, result.duplicates) == (2, 2, 0)
    assert result.paths == 1
    assert any("G1G" in w and "cq_zone" in w and "abc" in w for w in result.warnings)
    assert any("G2G" in w and "qso_datetime" in w for w in result.warnings)
    assert any("G3G" in w and "nan" in w for w in result.warnings)
    rows = gpkg.read_qso_rows(tmp_gpkg)
    assert [row["call"] for row in rows] == ["G0G", "G3G"]
    # a failed QSO is not a duplicate: it can be inserted once fixed
    retry = gpkg.insert_qsos(tmp_gpkg, [good[1]])
    assert retry.inserted == 1


def test_a_key_missed_by_the_lookup_is_still_a_duplicate(tmp_gpkg, monkeypatch):
    first = make_qsos([record(f"F{i}F", i) for i in range(3)])
    gpkg.insert_qsos(tmp_gpkg, first)
    # The UNIQUE index catches what the lookup of the chunk missed, row by row.
    real_lookup = gpkg._stored_keys

    def missing_chunk_keys(connection, keys):
        keys = list(keys)
        return set() if len(keys) > 1 else real_lookup(connection, keys)

    monkeypatch.setattr(gpkg, "_stored_keys", missing_chunk_keys)
    second = make_qsos([record(f"F{i}F", i) for i in range(4)])
    result = gpkg.insert_qsos(tmp_gpkg, second)
    assert (result.inserted, result.duplicates, result.failed) == (1, 3, 0)
    assert result.warnings == []
    rows = gpkg.read_qso_rows(tmp_gpkg)
    assert [row["call"] for row in rows] == ["F0F", "F1F", "F2F", "F3F"]
    assert [row["fid"] for row in rows] == [1, 2, 3, result.fids[0]]


def test_antipodal_qso_gets_no_path(tmp_gpkg, english):
    qsos = make_qsos(
        [
            record(
                "ZL9ANT",
                1,
                MY_LAT="N044 48.750",
                MY_LON="E020 27.672",
                **adif_coordinates(*antipode(*BEOGRAD)),
            )
        ]
    )
    assert geo.is_antipodal(qsos[0].my_lat, qsos[0].my_lon, qsos[0].lat, qsos[0].lon)
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    assert (result.inserted, result.paths) == (1, 0)
    assert len(result.warnings) == 1 and "ZL9ANT" in result.warnings[0]
    assert "opposite side" in result.warnings[0]


def test_warnings_are_capped(tmp_gpkg, english):
    qsos = [
        dataclasses.replace(qso, cq_zone="x")
        for qso in make_qsos(
            [record(f"W{i:03d}W", i % 60, TIME_ON=f"{i // 60:02d}{i % 60:02d}") for i in range(120)]
        )
    ]
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    assert result.failed == 120
    assert len(result.warnings) == 101
    assert result.warnings[-1] == "Further warnings not listed: 20"


def test_feedback_progress_and_cancel(tmp_gpkg):
    feedback = QgsFeedback()
    progress = []
    feedback.progressChanged.connect(progress.append)
    result = gpkg.insert_qsos(
        tmp_gpkg, make_qsos([record(f"P{i}P", i) for i in range(5)]), feedback, 2
    )
    assert result.inserted == 5
    assert progress[-1] == 100 and progress == sorted(progress)

    canceling = QgsFeedback()

    def cancel_after_first_chunk(value: float) -> None:
        if value > 0:
            canceling.cancel()

    canceling.progressChanged.connect(cancel_after_first_chunk)
    qsos = make_qsos([record(f"C{i}C", i) for i in range(30)])
    result = gpkg.insert_qsos(tmp_gpkg, qsos, canceling, chunk_size=10)
    assert result.canceled
    assert result.inserted == 10
    assert len(gpkg.read_qso_rows(tmp_gpkg)) == 15


def test_ten_thousand_qsos_with_paths_in_under_ten_seconds(tmp_gpkg):
    """PLAN.md M2 acceptance: 10 000 QSOs (here with paths all over the world) < 10 s."""
    rng = random.Random(20260915)
    bands = ["160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"]
    records = []
    for i in range(10_000):
        grid = maidenhead.to_locator(rng.uniform(-60.0, 75.0), rng.uniform(-180.0, 180.0), 6)
        records.append(
            record(
                f"T{i:05d}X",
                TIME_ON=f"{i // 600:02d}{i // 10 % 60:02d}",
                BAND=rng.choice(bands),
                GRIDSQUARE=grid,
            )
        )
    qsos = make_qsos(records)
    start = time.perf_counter()
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    elapsed = time.perf_counter() - start
    print(f"\n10 000 QSOs with paths: {elapsed:.2f} s ({compat.QGIS_VERSION_INT})")
    assert result.inserted == 10_000
    assert result.paths == 10_000 - len(result.warnings)
    assert elapsed < 10.0
    reread = time.perf_counter()
    rows = gpkg.read_qso_rows(tmp_gpkg)
    print(f"read_qso_rows: {time.perf_counter() - reread:.3f} s")
    assert len(rows) == 10_000


def test_concurrent_inserts_from_a_worker_and_the_main_thread(tmp_gpkg, clean_project):
    """An import in a worker thread while live QSOs arrive in the main thread: the writes
    take turns (without the lock two QGIS providers deadlocked or failed)."""
    layers.load_layers(tmp_gpkg)
    rng = random.Random(7)
    imported = make_qsos(
        [
            record(
                f"I{i:04d}I",
                TIME_ON=f"{i // 60:02d}{i % 60:02d}",
                GRIDSQUARE=maidenhead.to_locator(rng.uniform(-60, 70), rng.uniform(-180, 180), 4),
            )
            for i in range(1440)
        ]
    )
    outcome = {}

    def work() -> None:
        try:
            outcome["result"] = gpkg.insert_qsos(tmp_gpkg, imported, chunk_size=200)
        except BaseException as exc:  # reported below
            outcome["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    live = []
    for minute in range(15):
        live.append(
            gpkg.insert_qsos(
                tmp_gpkg, make_qsos([record(f"L{minute}L", minute, GRIDSQUARE="FN31")])
            )
        )
    thread.join(120)
    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert (outcome["result"].inserted, outcome["result"].failed) == (1440, 0)
    assert [(r.inserted, r.failed) for r in live] == [(1, 0)] * 15
    assert len(gpkg.read_qso_rows(tmp_gpkg)) == 1455
    assert len(features(tmp_gpkg, "qso_path")) == 1455 - sum(
        len(r.warnings) for r in [outcome["result"], *live]
    )


def hold_write_lock(path: str, seconds: float) -> threading.Thread:
    """Another program writing: a connection holds the write lock for ``seconds``."""
    ready = threading.Event()

    def hold() -> None:
        with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(seconds)
            connection.execute("ROLLBACK")

    thread = threading.Thread(target=hold)
    thread.start()
    assert ready.wait(10)
    return thread


def test_a_locked_file_is_waited_for(tmp_gpkg):
    gpkg.ensure_gpkg(tmp_gpkg)
    holder = hold_write_lock(tmp_gpkg, 0.3)
    start = time.perf_counter()
    result = gpkg.insert_qsos(tmp_gpkg, make_qsos([record("B1B", 1), record("B2B", 2)]))
    holder.join()
    assert (result.inserted, result.failed) == (2, 0)
    assert time.perf_counter() - start >= 0.2


def test_a_file_locked_for_long_fails_cleanly(tmp_gpkg, monkeypatch, english):
    gpkg.ensure_gpkg(tmp_gpkg)
    monkeypatch.setattr(gpkg, "_MAIN_THREAD_TIMEOUT_S", 0.1)
    qsos = make_qsos([record(f"C{i}C", i) for i in range(4)])
    holder = hold_write_lock(tmp_gpkg, 1.0)
    result = gpkg.insert_qsos(tmp_gpkg, qsos)
    holder.join()
    assert (result.inserted, result.failed) == (0, 4)
    assert all("locked" in warning for warning in result.warnings)
    assert gpkg.read_qso_rows(tmp_gpkg) == []
    assert gpkg.insert_qsos(tmp_gpkg, qsos).inserted == 4  # nothing half-written


def test_a_row_the_file_rejects_fails_alone(tmp_gpkg, english):
    gpkg.ensure_gpkg(tmp_gpkg)
    sql(
        tmp_gpkg,
        "CREATE TRIGGER reject_e3e BEFORE INSERT ON qso WHEN NEW.call = 'E3E' "
        "BEGIN SELECT RAISE(ABORT, 'rejected E3E'); END",
    )
    result = gpkg.insert_qsos(
        tmp_gpkg, make_qsos([record(f"E{i}E", i, GRIDSQUARE="JN95") for i in range(6)])
    )
    assert (result.inserted, result.failed, result.paths) == (5, 1, 5)
    assert len(result.warnings) == 1
    assert "E3E" in result.warnings[0] and "rejected E3E" in result.warnings[0]
    assert sorted(row["call"] for row in gpkg.read_qso_rows(tmp_gpkg)) == [
        f"E{i}E" for i in range(6) if i != 3
    ]
    assert sorted(feature["qso_fid"] for feature in features(tmp_gpkg, "qso_path")) == sorted(
        result.fids
    )


def test_a_qso_without_dedup_key_is_not_saved(tmp_gpkg, english):
    (qso,) = make_qsos([record("NOKEY", 1)])
    result = gpkg.insert_qsos(tmp_gpkg, [dataclasses.replace(qso, dedup_key="")])
    assert (result.inserted, result.failed) == (0, 1)
    assert "dedup_key" in result.warnings[0]


# --- GeoPackage blobs and the spatial index -------------------------------------------------------


def test_point_blob_is_what_gdal_writes(tmp_gpkg):
    gpkg.ensure_gpkg(tmp_gpkg)
    writer = layer(tmp_gpkg, "qso")
    feature = QgsFeature(writer.fields())
    feature["call"] = "GDAL"
    feature["dedup_key"] = "gdal"
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(20.4612, 44.8125)))
    assert writer.dataProvider().addFeatures([feature])[0]
    del writer
    ((blob,),) = sql(tmp_gpkg, "SELECT geom FROM qso")
    assert gpkg._point_blob(44.8125, 20.4612) == blob
    assert gpkg._blob_envelope(blob) == (20.4612, 20.4612, 44.8125, 44.8125)
    assert gpkg._blob_point(blob) == (44.8125, 20.4612)


def test_line_blobs_read_back_in_qgis(tmp_gpkg):
    gpkg.insert_qsos(
        tmp_gpkg, make_qsos([record("W6TP", GRIDSQUARE="DM04")], station=Station("JA1XX", "PM95"))
    )
    expected = gpkg.geodesic_path(*maidenhead.to_latlon("PM95"), *maidenhead.to_latlon("DM04"))
    (path,) = features(tmp_gpkg, "qso_path")
    assert path.geometry().wkbType() == compat.WKB_MULTILINESTRING
    assert flat(parts_of(path.geometry())) == pytest.approx(flat(parts_of(expected)))
    ((blob,),) = sql(tmp_gpkg, "SELECT geom FROM qso_path")
    box = expected.boundingBox()
    assert gpkg._blob_envelope(blob) == pytest.approx(
        (box.xMinimum(), box.xMaximum(), box.yMinimum(), box.yMaximum())
    )


@pytest.mark.parametrize(
    ("wkt", "envelope"),
    [
        ("POINT (1 2)", (1, 1, 2, 2)),
        ("POINT Z (1 2 3)", (1, 1, 2, 2)),
        ("MULTIPOINT ((1 2), (-3 5))", (-3, 1, 2, 5)),
        ("LINESTRING (0 0, 4 -1)", (0, 4, -1, 0)),
        ("POLYGON ((0 0, 2 0, 2 3, 0 0))", (0, 2, 0, 3)),
        ("MULTILINESTRING M ((0 0 7, 1 1 7), (5 5 7, 6 -2 7))", (0, 6, -2, 5)),
        ("GEOMETRYCOLLECTION (POINT (9 9), LINESTRING (0 0, 1 1))", (0, 9, 0, 9)),
    ],
)
def test_blob_envelope_of_wkb_without_header_envelope(wkt, envelope):
    geometry = QgsGeometry.fromWkt(wkt)
    blob = b"GP\x00\x01" + (4326).to_bytes(4, "little") + bytes(geometry.asWkb())
    assert gpkg._blob_envelope(blob) == pytest.approx(envelope)
    assert gpkg._st_is_empty(blob) == 0


def test_blob_envelope_edge_cases():
    import struct

    big_endian = b"GP\x00\x02" + struct.pack(">i", 4326) + struct.pack(">4d", 1, 2, 3, 4)
    assert gpkg._blob_envelope(big_endian + b"\x00") == (1, 2, 3, 4)
    empty_flag = (
        b"GP\x00\x11" + struct.pack("<i", 4326) + bytes(QgsGeometry.fromWkt("POINT (1 2)").asWkb())
    )
    nan_point = gpkg._GP_POINT + struct.pack("<BIdd", 1, 1, float("nan"), float("nan"))
    for blob in (empty_flag, nan_point, b"XX\x00\x01", b"", b"GP\x00\x0f12345678", "text", 5):
        assert gpkg._blob_envelope(blob) is None
    assert gpkg._st_is_empty(nan_point) == 1
    assert gpkg._st_is_empty(None) is None
    assert gpkg._blob_point(None) is None


def test_the_spatial_index_follows_inserts_and_moves(tmp_gpkg):
    from qgis.core import QgsFeatureRequest, QgsRectangle

    qsos = make_qsos([record("NEAR", 1, GRIDSQUARE="JN95"), record("FAR", 2, GRIDSQUARE="FN31")])
    gpkg.insert_qsos(tmp_gpkg, qsos)
    rtree = dict(
        (fid, (minx, maxx, miny, maxy))
        for fid, minx, maxx, miny, maxy in sql(
            tmp_gpkg, "SELECT id, minx, maxx, miny, maxy FROM rtree_qso_geom"
        )
    )
    lat, lon = maidenhead.to_latlon("JN95")
    assert rtree[1] == pytest.approx((lon, lon, lat, lat), abs=1e-5)  # float32 in the R-tree
    europe = QgsFeatureRequest().setFilterRect(QgsRectangle(0, 40, 30, 50))
    assert [f["call"] for f in layer(tmp_gpkg, "qso").getFeatures(europe)] == ["NEAR"]
    assert len(sql(tmp_gpkg, "SELECT id FROM rtree_qso_path_geom")) == 2

    sql(tmp_gpkg, "UPDATE qso SET gridsquare = 'PM95' WHERE call = 'NEAR'")
    gpkg.recalculate(tmp_gpkg, STATION)
    assert [f["call"] for f in layer(tmp_gpkg, "qso").getFeatures(europe)] == []
    japan = QgsFeatureRequest().setFilterRect(QgsRectangle(130, 30, 150, 40))
    assert [f["call"] for f in layer(tmp_gpkg, "qso").getFeatures(japan)] == ["NEAR"]


def test_writes_clear_the_cached_counts_and_extents(tmp_gpkg):
    gpkg.insert_qsos(tmp_gpkg, make_qsos([record("A1A", 1, GRIDSQUARE="JN95")]))
    reader = layer(tmp_gpkg, "qso")
    assert reader.featureCount() == 1
    del reader  # GDAL may store the count it computed
    gpkg.insert_qsos(tmp_gpkg, make_qsos([record("B1B", 2, GRIDSQUARE="FN31")]))
    assert sql(
        tmp_gpkg, "SELECT feature_count FROM gpkg_ogr_contents WHERE table_name = 'qso'"
    ) == [(None,)]
    assert sql(tmp_gpkg, "SELECT min_x, max_x FROM gpkg_contents WHERE table_name = 'qso'") == [
        (None, None)
    ]
    reader = layer(tmp_gpkg, "qso")
    assert reader.featureCount() == 2
    extent = reader.extent()
    assert extent.xMinimum() < -70 and extent.xMaximum() > 18


def test_stored_times_read_back_in_qgis_as_utc(tmp_gpkg):
    when = datetime(2026, 9, 15, 18, 45, 7, 250000, tzinfo=UTC)
    (qso,) = make_qsos([record("T1T", 45)])
    gpkg.insert_qsos(tmp_gpkg, [dataclasses.replace(qso, qso_datetime=when)])
    assert sql(tmp_gpkg, "SELECT qso_datetime FROM qso") == [("2026-09-15T18:45:07.250Z",)]
    (feature,) = features(tmp_gpkg, "qso")
    from hamq.qgis_io.fields import from_qdatetime

    assert from_qdatetime(feature["qso_datetime"]) == when
    assert gpkg.read_qso_rows(tmp_gpkg)[0]["qso_datetime"] == when


# --- geodesic paths ------------------------------------------------------------------------------


def test_beograd_sydney_path():
    geometry = gpkg.geodesic_path(*BEOGRAD, *SYDNEY)
    assert geometry is not None
    assert geometry.wkbType() == compat.WKB_MULTILINESTRING
    parts = parts_of(geometry)
    assert len(parts) == 1
    assert parts[0][0] == pytest.approx((BEOGRAD[1], BEOGRAD[0]))
    assert parts[0][-1] == pytest.approx((SYDNEY[1], SYDNEY[0]))
    distance_area = wgs84()
    assert distance_area.measureLength(geometry) / 1000.0 == pytest.approx(15676.1, rel=0.005)
    segments = [
        distance_area.measureLine(QgsPointXY(*a), QgsPointXY(*b)) / 1000.0
        for a, b in zip(parts[0], parts[0][1:])
    ]
    assert max(segments) <= gpkg.PATH_STEP_KM + 0.5
    assert len(segments) >= 156


def test_beograd_sydney_qso_in_the_geopackage(tmp_gpkg):
    qsos = make_qsos(
        [
            record(
                "VK2BG",
                MY_LAT="N044 48.750",
                MY_LON="E020 27.672",
                LAT="S033 52.128",
                LON="E151 12.558",
            )
        ]
    )
    assert qsos[0].distance_km == pytest.approx(15676.1, rel=0.005)
    gpkg.insert_qsos(tmp_gpkg, qsos)
    (path,) = features(tmp_gpkg, "qso_path")
    assert wgs84().measureLength(path.geometry()) / 1000.0 == pytest.approx(15676.1, rel=0.005)
    assert path["distance_km"] == pytest.approx(15676.1, rel=0.005)
    assert path["bearing_deg"] == pytest.approx(91.0, abs=0.5)


@pytest.mark.parametrize(("start", "end"), [(TOKYO, LOS_ANGELES), (LOS_ANGELES, TOKYO)])
def test_trans_pacific_path_is_split_at_the_antimeridian(start, end):
    geometry = gpkg.geodesic_path(*start, *end)
    parts = parts_of(geometry)
    assert len(parts) >= 2
    assert longest_jump(parts) <= 180.0
    assert all(len(part) >= 2 for part in parts)
    assert parts[0][0] == pytest.approx((start[1], start[0]))
    assert parts[-1][-1] == pytest.approx((end[1], end[0]))
    for first, second in zip(parts, parts[1:]):
        assert abs(first[-1][0]) == 180.0 and second[0][0] == -first[-1][0]
        assert first[-1][1] == pytest.approx(second[0][1])
    expected = geo.distance_km(*start, *end)
    assert wgs84().measureLength(geometry) / 1000.0 == pytest.approx(expected, rel=0.006)


def test_trans_pacific_qso_is_split_in_the_geopackage(tmp_gpkg):
    qsos = make_qsos([record("W6TP", GRIDSQUARE="DM04")], station=Station("JA1XX", "PM95"))
    gpkg.insert_qsos(tmp_gpkg, qsos)
    (path,) = features(tmp_gpkg, "qso_path")
    parts = parts_of(path.geometry())
    assert len(parts) == 2
    assert longest_jump(parts) <= 180.0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ((88.0, 100.0), (88.0, -100.0)),
        ((80.0, 20.0), (80.0, -160.0)),
        ((-85.0, 170.0), (-87.0, -60.0)),
    ],
)
def test_paths_near_the_poles_never_jump_across_the_map(start, end):
    parts = parts_of(gpkg.geodesic_path(*start, *end))
    assert longest_jump(parts) <= 180.0
    assert all(len(part) >= 2 for part in parts)
    assert parts[0][0] == pytest.approx((start[1], start[0]))
    assert parts[-1][-1] == pytest.approx((end[1], end[0]))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ((10.0, 180.0), (20.0, -170.0)),
        ((10.0, 170.0), (20.0, -180.0)),
        ((10.0, -180.0), (5.0, 170.0)),
    ],
)
def test_paths_touching_the_antimeridian_have_no_empty_parts(start, end):
    parts = parts_of(gpkg.geodesic_path(*start, *end))
    assert parts
    assert all(len(part) >= 2 and len(set(part)) >= 2 for part in parts)
    assert longest_jump(parts) <= 180.0


def test_short_path_has_no_detour():
    geometry = gpkg.geodesic_path(45.0, 20.0, 45.0, 20.001)
    xs = [x for part in parts_of(geometry) for x, _ in part]
    ys = [y for part in parts_of(geometry) for _, y in part]
    assert min(xs) >= 20.0 - 1e-9 and max(xs) <= 20.001 + 1e-9
    assert max(abs(y - 45.0) for y in ys) < 1e-6
    distance_area = wgs84()
    assert distance_area.measureLength(geometry) == pytest.approx(
        distance_area.measureLine(QgsPointXY(20.0, 45.0), QgsPointXY(20.001, 45.0)), rel=1e-6
    )


@pytest.mark.parametrize(
    "points",
    [
        (None, 20.0, 45.0, 21.0),
        (45.0, None, 45.0, 21.0),
        (45.0, 20.0, None, None),
        (float("nan"), 20.0, 45.0, 21.0),
        (45.0, 20.0, 91.0, 21.0),
        (45.0, 20.0, 45.0, float("inf")),
        (45.0, 20.0, 45.0, 20.0),  # the same place
        (*BEOGRAD, *antipode(*BEOGRAD)),
        (*BEOGRAD, antipode(*BEOGRAD)[0] + 0.1, antipode(*BEOGRAD)[1]),  # 11 km off
        ("45", "20", "x", "21"),
    ],
)
def test_no_path(points):
    assert gpkg.geodesic_path(*points) is None


def test_nearly_antipodal_but_far_enough():
    lat, lon = antipode(*BEOGRAD)
    geometry = gpkg.geodesic_path(*BEOGRAD, lat + 0.5, lon)  # 55 km from the antipode
    assert geometry is not None
    assert longest_jump(parts_of(geometry)) <= 180.0


def test_longitudes_outside_the_range_are_wrapped():
    wrapped = gpkg.geodesic_path(45.0, 380.0, 50.0, 10.0)
    plain = gpkg.geodesic_path(45.0, 20.0, 50.0, 10.0)
    assert flat(parts_of(wrapped)) == pytest.approx(flat(parts_of(plain)))


# --- writes and the project ----------------------------------------------------------------------


def test_insert_shows_up_in_loaded_layers(tmp_gpkg, clean_project):
    qso_layer, path_layer = layers.load_layers(tmp_gpkg)
    assert qso_layer.featureCount() == 0
    result = gpkg.insert_qsos(tmp_gpkg, sample_qsos())
    assert result.warnings == []
    assert qso_layer.featureCount() == 5
    assert path_layer.featureCount() == 4
    assert len(list(qso_layer.getFeatures())) == 5
    extent = qso_layer.extent()
    assert extent.xMinimum() < 0 < extent.xMaximum()


def test_insert_while_the_layer_is_edited(tmp_gpkg, clean_project, english):
    qsos = sample_qsos()
    gpkg.insert_qsos(tmp_gpkg, qsos[:2])
    qso_layer, _path_layer = layers.load_layers(tmp_gpkg)
    assert qso_layer.startEditing()
    fid = min(feature.id() for feature in qso_layer.getFeatures())
    index = qso_layer.fields().lookupField("rst_sent")
    assert qso_layer.changeAttributeValue(fid, index, "599")

    result = gpkg.insert_qsos(tmp_gpkg, qsos[2:])

    assert result.inserted == 3
    assert len(result.warnings) == 1 and "edit mode" in result.warnings[0]
    assert qso_layer.isEditable()
    assert qso_layer.editBuffer().changedAttributeValues() == {fid: {index: "599"}}
    assert qso_layer.commitChanges(), qso_layer.commitErrors()
    rows = gpkg.read_qso_rows(tmp_gpkg)
    assert len(rows) == 5
    assert rows[0]["rst_sent"] == "599"
    layers.refresh_layers(tmp_gpkg)
    assert qso_layer.featureCount() == 5


def test_insert_from_a_worker_thread_leaves_the_project_alone(tmp_gpkg, clean_project, monkeypatch):
    qso_layer, _path_layer = layers.load_layers(tmp_gpkg)
    touched = []
    monkeypatch.setattr(layers, "refresh_layers", lambda path: touched.append("refresh"))
    monkeypatch.setattr(layers, "matching_layers", lambda path: touched.append("match") or [])
    outcome = {}

    def work() -> None:
        try:
            outcome["result"] = gpkg.insert_qsos(tmp_gpkg, sample_qsos())
        except BaseException as exc:  # reported below
            outcome["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(60)
    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"].inserted == 5
    assert touched == []  # no project layer was looked at from the worker thread
    monkeypatch.undo()
    layers.refresh_layers(tmp_gpkg)
    assert qso_layer.featureCount() == 5


def test_feature_counts_are_recomputed_after_an_edit_session(tmp_gpkg, clean_project):
    """GDAL caches feature counts per connection; a commit by an edited layer can store a
    stale count. The next HamQ write clears the stored count, so it heals."""
    qsos = sample_qsos()
    gpkg.insert_qsos(tmp_gpkg, qsos[:2])
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    qso_layer.startEditing()
    gpkg.insert_qsos(tmp_gpkg, qsos[2:4])
    feature = QgsFeature(qso_layer.fields())
    feature.setAttribute("call", "EDIT1")
    feature.setAttribute("dedup_key", "EDIT1|x")
    assert qso_layer.addFeature(feature)
    assert qso_layer.commitChanges(), qso_layer.commitErrors()
    gpkg.insert_qsos(tmp_gpkg, qsos[4:])
    assert qso_layer.featureCount() == 6
    assert layer(tmp_gpkg, "qso").featureCount() == 6


# --- recalculation --------------------------------------------------------------------------------


def path_links(path: str) -> dict[int, QgsFeature]:
    return {feature["qso_fid"]: feature for feature in features(path, "qso_path")}


def test_recalculate_with_a_new_station(tmp_gpkg):
    qsos = make_qsos(
        [
            record("VK2ABC", 1, GRIDSQUARE="QF56od"),
            record("W1AW", 2, GRIDSQUARE="FN31pr"),
            record("NOPOS", 3),
            record("OWNQTH", 4, GRIDSQUARE="JO62", MY_GRIDSQUARE="FN31"),
        ],
        station=None,
    )
    gpkg.insert_qsos(tmp_gpkg, qsos)
    before = gpkg.read_qso_rows(tmp_gpkg)
    assert [row["distance_km"] is None for row in before] == [True, True, True, False]
    assert set(path_links(tmp_gpkg)) == {4}

    assert gpkg.recalculate(tmp_gpkg, Station("YU1XX", "KN04ft")) == 2
    first = gpkg.read_qso_rows(tmp_gpkg)
    origin = maidenhead.to_latlon("KN04ft")
    for row, target in zip(first[:2], ("QF56od", "FN31pr")):
        assert row["distance_km"] == pytest.approx(
            geo.distance_km(*origin, *maidenhead.to_latlon(target))
        )
        assert row["bearing_deg"] == pytest.approx(
            geo.bearing_deg(*origin, *maidenhead.to_latlon(target))
        )
        assert row["my_gridsquare"] is None  # follows the station on every recalculation
    assert first[2]["distance_km"] is None
    assert first[3]["distance_km"] == pytest.approx(before[3]["distance_km"])  # its own QTH
    links = path_links(tmp_gpkg)
    assert set(links) == {1, 2, 4}
    assert links[1]["distance_km"] == pytest.approx(first[0]["distance_km"])
    assert links[1]["band"] == "20m" and links[1]["mode"] == "FT8"

    assert gpkg.recalculate(tmp_gpkg, Station("YU1XX", "JN95")) == 2
    second = gpkg.read_qso_rows(tmp_gpkg)
    for old, new in zip(first[:2], second[:2]):
        assert new["distance_km"] != pytest.approx(old["distance_km"])
    assert second[3]["distance_km"] == pytest.approx(before[3]["distance_km"])
    assert set(path_links(tmp_gpkg)) == {1, 2, 4}
    parts = parts_of(path_links(tmp_gpkg)[1].geometry())
    assert parts[0][0] == pytest.approx(tuple(reversed(maidenhead.to_latlon("JN95"))))

    assert gpkg.recalculate(tmp_gpkg, Station("YU1XX", "JN95")) == 0  # nothing new


def test_recalculate_with_force_station(tmp_gpkg, log_messages, english):
    gpkg.insert_qsos(tmp_gpkg, sample_qsos())
    assert gpkg.recalculate(tmp_gpkg, Station("YU1XX", "JN95"), force_station=True) == 5
    rows = gpkg.read_qso_rows(tmp_gpkg)
    assert {row["my_gridsquare"] for row in rows} == {"JN95"}
    origin = maidenhead.to_latlon("JN95")
    assert rows[0]["distance_km"] == pytest.approx(
        geo.distance_km(*origin, *maidenhead.to_latlon("QF56od"))
    )
    assert gpkg.recalculate(tmp_gpkg, Station("YU1XX", "nonsense"), force_station=True) == 0
    assert any("nonsense" in m for m, tag, _ in log_messages if tag == "HamQ")
    assert {row["my_gridsquare"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {"JN95"}


def test_recalculate_fills_dxcc_data_and_positions_from_cty(tmp_gpkg, cty):
    qsos = make_qsos(
        [
            record("DL1ABC", 1),
            record("VK2XYZ", 2, GRIDSQUARE="QF56"),
            record("9A1A", 3, COUNTRY="Hrvatska", CQZ="14"),
        ]
    )
    gpkg.insert_qsos(tmp_gpkg, qsos)
    assert features(tmp_gpkg, "qso")[0].geometry().isNull()

    assert gpkg.recalculate(tmp_gpkg, STATION, cty) == 3

    germany, australia, croatia = gpkg.read_qso_rows(tmp_gpkg)
    assert (germany["dxcc"], germany["country"], germany["cont"]) == (
        230,
        "Fed. Rep. of Germany",
        "EU",
    )
    assert (germany["cq_zone"], germany["itu_zone"], germany["loc_source"]) == (14, 28, "cty")
    assert germany["distance_km"] is not None
    assert (australia["dxcc"], australia["cont"], australia["loc_source"]) == (150, "OC", "grid")
    assert (croatia["country"], croatia["cq_zone"], croatia["itu_zone"]) == ("Hrvatska", 14, 28)
    assert croatia["dxcc"] == 497
    point = features(tmp_gpkg, "qso")[0].geometry().asPoint()
    assert (point.y(), point.x()) == pytest.approx((51.0, 10.0))
    assert set(path_links(tmp_gpkg)) == {1, 2, 3}


def test_recalculate_follows_a_changed_locator_but_keeps_moved_points(tmp_gpkg):
    qsos = make_qsos(
        [
            record("CHANGED", 1, GRIDSQUARE="JN95"),
            record("REFINED", 2, GRIDSQUARE="JN95"),
            record("MANUAL", 3, GRIDSQUARE="JN95"),
        ]
    )
    gpkg.insert_qsos(tmp_gpkg, qsos)
    edited = layer(tmp_gpkg, "qso")
    provider = edited.dataProvider()
    fields = provider.fields()
    assert provider.changeAttributeValues({1: {fields.lookupField("gridsquare"): "JN96"}})
    assert provider.changeGeometryValues(
        {
            2: QgsGeometry.fromPointXY(QgsPointXY(19.3, 45.2)),  # moved inside JN95
            3: QgsGeometry.fromPointXY(QgsPointXY(0.0, 0.0)),
        }
    )
    assert provider.changeAttributeValues({3: {fields.lookupField("loc_source"): None}})
    del provider, edited

    assert gpkg.recalculate(tmp_gpkg, STATION) == 3

    changed, refined, manual = features(tmp_gpkg, "qso")
    jn96 = maidenhead.to_latlon("JN96")
    assert changed.geometry().asPoint() == QgsPointXY(jn96[1], jn96[0])
    assert changed["loc_source"] == "grid"
    assert changed["distance_km"] == pytest.approx(
        geo.distance_km(*maidenhead.to_latlon("KN04ft"), *jn96)
    )
    assert refined.geometry().asPoint() == QgsPointXY(19.3, 45.2)
    assert refined["distance_km"] == pytest.approx(
        geo.distance_km(*maidenhead.to_latlon("KN04ft"), 45.2, 19.3)
    )
    assert manual.geometry().asPoint() == QgsPointXY(0.0, 0.0)
    assert is_null(manual["loc_source"])
    assert manual["distance_km"] == pytest.approx(
        geo.distance_km(*maidenhead.to_latlon("KN04ft"), 0.0, 0.0)
    )


def test_recalculate_rebuilds_all_paths(tmp_gpkg):
    result = gpkg.insert_qsos(tmp_gpkg, sample_qsos())
    edited = layer(tmp_gpkg, "qso_path")
    provider = edited.dataProvider()
    first_path = min(feature.id() for feature in provider.getFeatures())
    assert provider.deleteFeatures([first_path])
    orphan = QgsFeature(provider.fields())
    orphan.setAttributes([None, 9999, 1.0, 2.0, "20m", "FT8"])
    orphan.setGeometry(QgsGeometry.fromMultiPolylineXY([[QgsPointXY(0, 0), QgsPointXY(1, 1)]]))
    assert provider.addFeatures([orphan])[0]
    del provider, edited

    gpkg.recalculate(tmp_gpkg, STATION)

    links = path_links(tmp_gpkg)
    assert sorted(links) == result.fids[:4]
    assert len(features(tmp_gpkg, "qso_path")) == 4


def test_recalculate_can_be_canceled(tmp_gpkg):
    gpkg.insert_qsos(tmp_gpkg, make_qsos([record("A1A", 1, GRIDSQUARE="JN95")], station=None))
    feedback = QgsFeedback()
    feedback.cancel()
    assert gpkg.recalculate(tmp_gpkg, STATION, feedback=feedback) == 0
    assert gpkg.read_qso_rows(tmp_gpkg)[0]["distance_km"] is None
    assert features(tmp_gpkg, "qso_path") == []


def test_recalculate_reports_progress_and_warnings(tmp_gpkg, english):
    qsos = make_qsos(
        [
            record("ANTI", 1, **adif_coordinates(*antipode(*BEOGRAD))),
            record("NEAR", 2, GRIDSQUARE="JN95"),
        ],
        station=None,
    )
    gpkg.insert_qsos(tmp_gpkg, qsos)

    class Feedback(QgsFeedback):
        def __init__(self) -> None:
            super().__init__()
            self.warnings: list[str] = []

        def pushWarning(self, message: str) -> None:  # noqa: N802 (QGIS API name)
            self.warnings.append(message)

    feedback = Feedback()
    progress = []
    feedback.progressChanged.connect(progress.append)
    station = Station("YU1XX", maidenhead.to_locator(*BEOGRAD, 8))
    assert gpkg.recalculate(tmp_gpkg, station, feedback=feedback) == 2
    assert progress[-1] == 100 and progress == sorted(progress)
    assert len(feedback.warnings) == 1 and "ANTI" in feedback.warnings[0]
    assert set(path_links(tmp_gpkg)) == {2}


def test_recalculate_while_the_layer_is_edited(tmp_gpkg, clean_project, log_messages, english):
    gpkg.insert_qsos(tmp_gpkg, make_qsos([record("A1A", 1, GRIDSQUARE="JN95")], station=None))
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    qso_layer.startEditing()
    assert gpkg.recalculate(tmp_gpkg, STATION) == 1
    assert qso_layer.isEditable()
    assert any("edit mode" in m for m, tag, _ in log_messages if tag == "HamQ")
    qso_layer.rollBack()


def test_recalculate_a_missing_file(tmp_gpkg):
    assert gpkg.recalculate(tmp_gpkg, STATION) == 0
    assert os.path.isfile(tmp_gpkg)
    assert math.isclose(len(gpkg.read_qso_rows(tmp_gpkg)), 0)
