"""hamq.qgis_io.fields: field creation and a GeoPackage round trip on every QGIS version."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QDateTime

from hamq.qgis_io import compat
from hamq.qgis_io.fields import (
    FIELD_KINDS,
    from_qdatetime,
    make_field,
    make_fields,
    to_qdatetime,
)

SPEC = [("n", "int"), ("x", "real"), ("s", "text"), ("t", "datetime")]
KIND_TYPES = {
    "int": compat.FIELD_TYPE_INT,
    "real": compat.FIELD_TYPE_REAL,
    "text": compat.FIELD_TYPE_TEXT,
    "datetime": compat.FIELD_TYPE_DATETIME,
}
QSO_TIME = datetime(2024, 5, 17, 12, 34, 56, tzinfo=timezone.utc)


def is_null(value) -> bool:
    """NULL attribute: None on QGIS 4, a null QVariant on QGIS 3."""
    return value is None or (hasattr(value, "isNull") and value.isNull())


@pytest.mark.parametrize(("kind", "expected"), sorted(KIND_TYPES.items()))
def test_make_field(kind, expected):
    field = make_field("value", kind)
    assert isinstance(field, QgsField)
    assert field.name() == "value"
    assert field.type() == expected


def test_field_kinds_cover_contract():
    assert set(FIELD_KINDS) == {"int", "real", "text", "datetime"}


def test_make_field_unknown_kind():
    with pytest.raises(ValueError, match="bool"):
        make_field("flag", "bool")


def test_make_fields_keeps_order():
    fields = make_fields(SPEC)
    assert isinstance(fields, QgsFields)
    assert fields.names() == ["n", "x", "s", "t"]
    assert [fields.field(i).type() for i in range(fields.count())] == [
        KIND_TYPES[kind] for _name, kind in SPEC
    ]


def test_make_fields_empty():
    assert make_fields([]).count() == 0


def test_make_fields_from_qso_fields():
    qso = pytest.importorskip("hamq.core.qso", reason="core/qso.py is not written yet")
    fields = make_fields(qso.QSO_FIELDS)
    assert fields.names() == [name for name, _kind in qso.QSO_FIELDS]


def _create_gpkg(path: str, fields: QgsFields) -> None:
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "qso"
    options.actionOnExistingFile = compat.WRITER_CREATE_OR_OVERWRITE_FILE
    writer = QgsVectorFileWriter.create(
        path,
        fields,
        compat.WKB_POINT,
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance().transformContext(),
        options,
    )
    assert writer.hasError() == compat.WRITER_NO_ERROR, writer.errorMessage()

    full = QgsFeature(fields)
    full.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(20.4612, 44.8125)))
    full.setAttributes([42, 1234.5, "YU1ABČĆŽ Đorđe", to_qdatetime(QSO_TIME)])
    empty = QgsFeature(fields)
    empty.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(0.0, 0.0)))
    empty.setAttributes([None, None, None, None])
    assert writer.addFeature(full), writer.errorMessage()
    assert writer.addFeature(empty), writer.errorMessage()
    del writer  # flush and close the GeoPackage


def test_geopackage_round_trip(tmp_gpkg):
    _create_gpkg(tmp_gpkg, make_fields(SPEC))

    layer = QgsVectorLayer(f"{tmp_gpkg}|layername=qso", "qso", "ogr")
    assert layer.isValid()
    assert layer.wkbType() == compat.WKB_POINT
    assert layer.crs().authid() == "EPSG:4326"
    for name, kind in SPEC:
        assert layer.fields().field(name).type() == KIND_TYPES[kind], name

    features = sorted(layer.getFeatures(), key=lambda f: f.id())
    assert len(features) == 2
    full, empty = features
    assert full["n"] == 42
    assert full["x"] == pytest.approx(1234.5)
    assert full["s"] == "YU1ABČĆŽ Đorđe"
    assert isinstance(full["t"], QDateTime)
    assert from_qdatetime(full["t"]) == QSO_TIME
    point = full.geometry().asPoint()
    assert (point.x(), point.y()) == pytest.approx((20.4612, 44.8125))
    assert all(is_null(empty[name]) for name, _kind in SPEC)
    assert from_qdatetime(empty["t"]) is None


def test_geopackage_stores_utc_iso_text(tmp_gpkg):
    _create_gpkg(tmp_gpkg, make_fields(SPEC))
    with contextlib.closing(sqlite3.connect(tmp_gpkg)) as connection:
        (stored,) = connection.execute("SELECT t FROM qso WHERE n = 42").fetchone()
    assert stored.startswith("2024-05-17T12:34:56")
    assert stored.endswith("Z")


def test_python_datetime_attribute_is_rejected_by_writer(tmp_gpkg):
    """Why to_qdatetime() exists: a Python datetime is not converted by the writer."""
    fields = make_fields([("t", "datetime")])
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "qso"
    writer = QgsVectorFileWriter.create(
        tmp_gpkg,
        fields,
        compat.WKB_POINT,
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance().transformContext(),
        options,
    )
    feature = QgsFeature(fields)
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(1.0, 2.0)))
    feature.setAttributes([QSO_TIME])
    accepted = writer.addFeature(feature)
    del writer
    assert not accepted


def test_to_qdatetime_is_utc():
    value = to_qdatetime(QSO_TIME)
    assert value.isValid()
    assert value.toUTC().toString("yyyy-MM-ddTHH:mm:ss") == "2024-05-17T12:34:56"
    assert value.offsetFromUtc() == 0


def test_to_qdatetime_converts_other_zones_and_naive_values():
    belgrade = timezone(timedelta(hours=2))
    local = datetime(2024, 5, 17, 14, 34, 56, tzinfo=belgrade)
    assert from_qdatetime(to_qdatetime(local)) == QSO_TIME
    naive = datetime(2024, 5, 17, 12, 34, 56)
    assert from_qdatetime(to_qdatetime(naive)) == QSO_TIME


def test_to_qdatetime_keeps_milliseconds():
    value = datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    assert from_qdatetime(to_qdatetime(value)) == value.replace(microsecond=678000)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-05-17T12:34:56Z", QSO_TIME),
        ("2024-05-17T12:34:56.000Z", QSO_TIME),
        ("2024-05-17T14:34:56+02:00", QSO_TIME),
        ("2024-05-17 12:34:56", QSO_TIME),
        (QSO_TIME, QSO_TIME),
        (datetime(2024, 5, 17, 12, 34, 56), QSO_TIME),
        ("not a date", None),
        ("", None),
        (None, None),
        (12345, None),
    ],
)
def test_from_qdatetime_values(value, expected):
    assert from_qdatetime(value) == expected


def test_from_qdatetime_null_and_invalid():
    assert from_qdatetime(QDateTime()) is None
