"""Field helpers that work on QGIS 3.34 .. 4.x.

``QgsField`` takes a ``QMetaType.Type`` since QGIS 3.38 and a ``QVariant.Type``
before; QGIS 4 removed the ``QVariant`` constructor. The right type values are
resolved once in :mod:`hamq.qgis_io.compat`; this module maps HamQ field kinds
(the ``kind`` strings used by ``core.qso.QSO_FIELDS``) to them.

Datetime attributes must be set as ``QDateTime``. A Python ``datetime`` set with
``QgsFeature.setAttributes`` (or ``feature[name] = ...``) stays a Python object
that QGIS does not convert:

* ``QgsVectorFileWriter.addFeature`` rejects the feature ("Could not convert
  value") on every supported version;
* the OGR provider (``layer.dataProvider().addFeatures``) is worse: QGIS 3.34,
  3.40, 3.44 and 4.0 report success and silently store NULL, only QGIS 4.2
  rejects the feature ("wrong data type ... expected QDateTime").

Always convert with :func:`to_qdatetime` when writing (a UTC ``QDateTime`` is
stored as ``...Z`` and reads back as UTC) and use :func:`from_qdatetime` when
reading.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from qgis.core import QgsField, QgsFields
from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone

from . import compat

#: HamQ field kind -> QGIS field type on the running version.
FIELD_KINDS = {
    "int": compat.FIELD_TYPE_INT,
    "real": compat.FIELD_TYPE_REAL,
    "text": compat.FIELD_TYPE_TEXT,
    "datetime": compat.FIELD_TYPE_DATETIME,
}


def make_field(name: str, kind: str) -> QgsField:
    """Create a ``QgsField`` called ``name`` of ``kind`` (int, real, text or datetime).

    Raises ``ValueError`` for an unknown kind (a programmer error).
    """
    try:
        field_type = FIELD_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"unknown field kind {kind!r} for field {name!r}; expected one of {sorted(FIELD_KINDS)}"
        ) from None
    return QgsField(name, field_type)


def make_fields(spec: Iterable[tuple[str, str]]) -> QgsFields:
    """Create ``QgsFields`` from ``(name, kind)`` pairs, keeping their order."""
    fields = QgsFields()
    for name, kind in spec:
        fields.append(make_field(name, kind))
    return fields


def to_qdatetime(value: datetime) -> QDateTime:
    """Convert a Python ``datetime`` to a UTC ``QDateTime`` for a feature attribute.

    Aware values are converted to UTC; naive values are taken as UTC already.
    Precision is milliseconds (what GeoPackage stores).
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return QDateTime(
        QDate(value.year, value.month, value.day),
        QTime(value.hour, value.minute, value.second, value.microsecond // 1000),
        QTimeZone.utc(),
    )


def from_qdatetime(value: object) -> datetime | None:
    """Convert an attribute value read from a layer to an aware UTC ``datetime``.

    Accepts ``QDateTime`` (any time zone), ``datetime`` and ISO 8601 text
    (``2024-05-17T12:34:56Z``). ``None``, NULL, invalid or unparsable values
    give ``None``.
    """
    if isinstance(value, QDateTime):
        if value.isNull() or not value.isValid():
            return None
        utc = value.toUTC()
        day, time = utc.date(), utc.time()
        return datetime(
            day.year(),
            day.month(),
            day.day(),
            time.hour(),
            time.minute(),
            time.second(),
            time.msec() * 1000,
            tzinfo=timezone.utc,
        )
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return from_qdatetime(parsed)
    return None
