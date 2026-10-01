"""GeoPackage storage of the QSO log: schema, inserts with deduplication, geodesic paths.

The log is one GeoPackage file (``HamQSettings.gpkg_path``) with three tables:

``qso`` (Point, EPSG:4326)
    One row per QSO: ``fid``, the point and the ``core.qso.QSO_FIELDS`` columns. The point
    is the other station's position; a QSO without a position has no geometry.
    ``dedup_key`` has a UNIQUE index (``qso_dedup_key_idx``).
``qso_path`` (MultiLineString, EPSG:4326)
    The geodesic line from my QTH to the other station (:func:`geodesic_path`) for every
    QSO with both points, with the ``core.qso.PATH_FIELDS`` columns: ``qso_fid`` (the
    QSO's ``fid``, indexed), ``distance_km`` and ``bearing_deg`` (the QSO's values),
    ``band`` and ``mode`` (the display mode: SUBMODE if present, else MODE).
``hamq_meta`` (attribute table)
    ``key`` / ``value`` rows; ``schema_version`` is :data:`SCHEMA_VERSION`, for migrations.

QGIS (GDAL) creates the tables, so they are registered like any GeoPackage layer.
Datetimes are converted with ``fields.to_qdatetime`` and stored as UTC ISO 8601 text
(``2026-09-15T18:45:00.000Z``), the form GDAL writes; :func:`read_qso_rows` returns
aware UTC ``datetime`` objects.

How rows are written
--------------------
Rows are written with plain SQL on a SQLite connection of this module, never through a
QGIS layer, in one ``BEGIN IMMEDIATE`` transaction per chunk:

- QGIS layers that write a GeoPackage from a worker thread while the main thread starts
  or saves an edit session of the same file were seen to deadlock, crash, or keep a
  connection stuck with "file is not a database" (QGIS 4.2 stress tests). SQLite's own
  locking has none of that: a writer waits for the lock (``busy_timeout``, which also
  releases the Python GIL) and then has the file to itself.
- Each row gets its ``fid`` from SQLite, and an error stays with its row.
- Geometries are GeoPackage binary blobs (header, envelope for lines, ISO WKB) as GDAL
  writes them. The spatial index is kept by GDAL's R-tree triggers, which call
  ``ST_IsEmpty`` and ``ST_MinX`` .. ``ST_MaxY``; this module gives its connection those
  functions. Every write clears the cached feature counts and extents of the two tables
  (``gpkg_ogr_contents``, ``gpkg_contents``), so GDAL computes them again.
- Writes in this process take turns (a lock per transaction), e.g. a live WSJT-X QSO and
  an import running in the background.

So the user's edit session is never touched, even when the layers are edited (in a
transaction group too). In the main thread :func:`insert_qsos` and :func:`recalculate`
then refresh the layers of the file that are loaded in the project
(``layers.refresh_layers``), so new QSOs show up at once. Layers in edit mode are left
alone, and a warning says that the new data shows up after the edits are saved or
discarded. From a worker thread (a Processing algorithm, a ``QgsTask``) nothing in the
project is touched: the caller emits ``events().dataChanged(path)`` from the main thread
when it is done (``postProcessAlgorithm``, ``QgsTask.finished``), and
``layers.connect_events()`` turns that signal into ``layers.refresh_layers``.

Threads and errors
------------------
Every function here may run in a worker thread; each call opens its own connections. A
write waits up to 10 s for a file locked by another connection (1 s in the main thread,
which must not freeze). Functions that read return empty results for a file that does
not exist. A file that cannot be created or read, or that is not a GeoPackage HamQ can
use, raises :class:`GpkgError` with a translated message. A bad QSO never stops an
insert: it is counted in ``InsertResult.failed`` with a warning.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sqlite3
import struct
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsDistanceArea,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsVectorFileWriter,
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime, QThread

from ..core import geo, maidenhead
from ..core.i18n import tr
from ..core.modes import display_mode
from ..core.qso import PATH_FIELDS, QSO_FIELDS, Station, record_to_qso
from . import compat
from .fields import from_qdatetime, make_fields, to_qdatetime

if TYPE_CHECKING:
    from ..core.cty import CtyDatabase
    from ..core.qso import Qso

__all__ = [
    "META_TABLE",
    "PATH_LAYER",
    "PATH_STEP_KM",
    "QSO_LAYER",
    "SCHEMA_VERSION",
    "GpkgError",
    "InsertResult",
    "ensure_gpkg",
    "existing_dedup_keys",
    "geodesic_path",
    "insert_qsos",
    "layer_uri",
    "read_qso_rows",
    "recalculate",
]

QSO_LAYER, PATH_LAYER = "qso", "qso_path"
#: Attribute table with ``key`` / ``value`` rows (``schema_version``).
META_TABLE = "hamq_meta"
#: Version of the table layout described in the module docstring. Raise it, and add a step
#: to ``_migrate``, whenever the layout changes (AGENTS.md: update PLAN.md too).
SCHEMA_VERSION = 1
#: Largest distance between two vertices of a QSO path.
PATH_STEP_KM = 100.0

_DEDUP_INDEX = "qso_dedup_key_idx"
_PATH_INDEX = "qso_path_qso_fid_idx"
_META_INDEX = "hamq_meta_key_idx"
_META_FIELDS: tuple[tuple[str, str], ...] = (("key", "text"), ("value", "text"))
_SCHEMA_KEY = "schema_version"
_EPSG_4326 = "EPSG:4326"
_HAMQ_TABLES = (QSO_LAYER, PATH_LAYER, META_TABLE)
_GEOMETRY_TYPE_NAMES = {QSO_LAYER: "POINT", PATH_LAYER: "MULTILINESTRING"}
_FIELD_SPECS = {QSO_LAYER: QSO_FIELDS, PATH_LAYER: PATH_FIELDS}
_WKB_TYPES = {QSO_LAYER: compat.WKB_POINT, PATH_LAYER: compat.WKB_MULTILINESTRING}
# Column types GDAL gives the field kinds in a GeoPackage (used to add missing columns).
_SQL_TYPES = {"text": "TEXT", "int": "MEDIUMINT", "real": "REAL", "datetime": "DATETIME"}
# Shorter "paths" (both stations in the same locator cell) are not drawn.
_MIN_PATH_KM = 0.001
_MAX_WARNINGS = 100  # per call, like the ADIF parser; a count of the rest follows
_SQLITE_TIMEOUT_S = 10.0  # reads, and writes in worker threads
_MAIN_THREAD_TIMEOUT_S = 1.0  # writes in the main thread
_WRITE_CHUNK = 1000  # rows per transaction in recalculate()
_SQL_BATCH = 500  # values per "IN (...)" query (SQLite before 3.32 allows 999 variables)
_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1
_LOG_TAG = "HamQ"
# Precision rank of a position source: a better source found later replaces the point.
_SOURCE_RANK = {"latlon": 3, "grid": 2, "cty": 1}

_logged_once: set[tuple[str, str]] = set()
# Files where old duplicates prevented the UNIQUE index on dedup_key (tried once per session).
_unique_index_tried: set[str] = set()
# Every write of this module holds this lock for one transaction, so writes from several
# threads of this process take turns. ensure_gpkg() holds it too: two threads creating the
# same new file would overwrite each other's tables.
_write_lock = threading.RLock()


class GpkgError(Exception):
    """The GeoPackage cannot be created, read or used by HamQ; the message is translated."""


@dataclass
class InsertResult:
    """What :func:`insert_qsos` did.

    ``fids`` are the ``fid`` values of the inserted QSOs, in input order; ``paths`` counts
    the path lines written; ``canceled`` is true when the feedback canceled the insert
    (QSOs of the chunks written before stay in the file).
    """

    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    fids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    paths: int = 0
    canceled: bool = False


def layer_uri(path: str, layer: str) -> str:
    """OGR data source of one layer of the GeoPackage: ``f"{path}|layername={layer}"``."""
    return f"{path}|layername={layer}"


# --- schema -------------------------------------------------------------------------------------


def ensure_gpkg(path: str) -> None:
    """Make ``path`` a GeoPackage with the HamQ tables; nothing changes when it is one.

    - A missing (or empty) file is created with the ``qso``, ``qso_path`` and
      ``hamq_meta`` tables, its folder too.
    - In an existing GeoPackage, missing tables are added and missing columns are added to
      ``qso`` and ``qso_path`` (other columns and all data stay).
    - The UNIQUE index on ``qso.dedup_key``, the index on ``qso_path.qso_fid`` and the
      schema version are created when missing. When old duplicates prevent the UNIQUE
      index, a plain index is made and a warning is logged; inserts skip duplicates anyway.

    A complete file is only read. Raises :class:`GpkgError` when the file cannot be
    created or written, is not a GeoPackage, or has a ``qso`` / ``qso_path`` /
    ``hamq_meta`` table of another kind (a geometry other than Point / MultiLineString, a
    CRS other than EPSG:4326, a table GeoPackage does not register). Such a file is never
    changed.
    """
    path = _checked_path(path)
    with _write_lock:
        _ensure(path)


def _ensure(path: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise GpkgError(
            tr("The folder {folder} could not be created: {error}").format(folder=folder, error=exc)
        ) from exc
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        _create_layer(path, QSO_LAYER, QSO_FIELDS, compat.WKB_POINT, new_file=True)
        _create_layer(path, PATH_LAYER, PATH_FIELDS, compat.WKB_MULTILINESTRING)
        _create_layer(path, META_TABLE, _META_FIELDS, compat.WKB_NO_GEOMETRY)
    else:
        schema = _read_schema(path)
        # Check everything first: a file HamQ cannot use is never changed.
        for name in (QSO_LAYER, PATH_LAYER):
            table = schema.get(name)
            if table is not None:
                _check_table(path, name, table)
        meta = schema.get(META_TABLE)
        if meta is not None and (
            meta.data_type != "attributes" or not {"key", "value"} <= set(meta.columns)
        ):
            raise GpkgError(
                tr("{path} has a table {table} that HamQ cannot use").format(
                    path=path, table=META_TABLE
                )
            )
        for name in (QSO_LAYER, PATH_LAYER):
            if schema.get(name) is None:
                _create_layer(path, name, _FIELD_SPECS[name], _WKB_TYPES[name])
        if meta is None:
            _create_layer(path, META_TABLE, _META_FIELDS, compat.WKB_NO_GEOMETRY)
        _add_missing_fields(path, schema)
    _finish_schema(path)


def _checked_path(path: object) -> str:
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path).strip():
        raise GpkgError(tr("No GeoPackage file is set"))
    text = os.fspath(path)
    if not isinstance(text, str):
        text = os.fsdecode(text)
    if os.path.isdir(text):
        raise GpkgError(tr("{path} is a folder, not a GeoPackage file").format(path=text))
    return text


def _create_layer(
    path: str, name: str, spec: Sequence[tuple[str, str]], wkb: Any, *, new_file: bool = False
) -> None:
    """Create table ``name`` with QGIS (GDAL registers it properly in the GeoPackage)."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = name
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (
        compat.WRITER_CREATE_OR_OVERWRITE_FILE
        if new_file
        else compat.WRITER_CREATE_OR_OVERWRITE_LAYER
    )
    crs = (
        QgsCoordinateReferenceSystem()
        if wkb == compat.WKB_NO_GEOMETRY
        else QgsCoordinateReferenceSystem(_EPSG_4326)
    )
    writer = QgsVectorFileWriter.create(
        path, make_fields(spec), wkb, crs, QgsCoordinateTransformContext(), options
    )
    error = "" if writer.hasError() == compat.WRITER_NO_ERROR else writer.errorMessage()
    del writer  # closes the file
    if error:
        raise GpkgError(
            tr("Layer {layer} could not be created in {path}: {error}").format(
                layer=name, path=path, error=error
            )
        )


@dataclass
class _Table:
    """A table registered in ``gpkg_contents``."""

    data_type: str  # "features" | "attributes" | ...
    geometry_type: str | None  # "POINT", ... (upper case); None without a geometry column
    epsg: int | None  # EPSG code of the geometry column, None when unknown
    columns: dict[str, str]  # lower-case name -> declared type


def _read_schema(path: str) -> dict[str, _Table]:
    """Tables of the GeoPackage by lower-case name; :class:`GpkgError` for other files.

    A ``qso`` / ``qso_path`` / ``hamq_meta`` table that GeoPackage does not register is an
    error: creating the layer would replace it.
    """
    with _sqlite(path) as connection:
        try:
            contents = connection.execute(
                "SELECT table_name, data_type FROM gpkg_contents"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise GpkgError(
                tr("{path} is not a GeoPackage: {error}").format(path=path, error=exc)
            ) from exc
        try:
            geometry_rows = connection.execute(
                "SELECT g.table_name, g.geometry_type_name, s.organization, "
                "s.organization_coordsys_id FROM gpkg_geometry_columns AS g "
                "LEFT JOIN gpkg_spatial_ref_sys AS s ON s.srs_id = g.srs_id"
            ).fetchall()
            tables: dict[str, _Table] = {}
            for name, data_type in contents:
                if not isinstance(name, str):
                    continue
                key = name.lower()
                columns = _columns(connection, name) if key in _HAMQ_TABLES else {}
                tables[key] = _Table(str(data_type or "").lower(), None, None, columns)
            for name, type_name, organization, code in geometry_rows:
                table = tables.get(str(name).lower())
                if table is None:
                    continue
                table.geometry_type = str(type_name or "").upper()
                if str(organization or "").upper() == "EPSG" and isinstance(code, int):
                    table.epsg = code
            for name in _HAMQ_TABLES:
                if name not in tables and _columns(connection, name):
                    raise GpkgError(
                        tr("{path} has a table {table} that HamQ cannot use").format(
                            path=path, table=name
                        )
                    )
        except sqlite3.DatabaseError as exc:
            raise GpkgError(
                tr("{path} could not be read: {error}").format(path=path, error=exc)
            ) from exc
    return tables


def _check_table(path: str, name: str, table: _Table) -> None:
    expected = _GEOMETRY_TYPE_NAMES[name]
    if table.data_type != "features" or table.geometry_type != expected:
        raise GpkgError(
            tr(
                "Layer {layer} in {path} has the geometry type {found}; HamQ needs {expected}"
            ).format(layer=name, path=path, found=table.geometry_type or "-", expected=expected)
        )
    if table.epsg != 4326:
        raise GpkgError(
            tr("Layer {layer} in {path} is not in EPSG:4326").format(layer=name, path=path)
        )


def _add_missing_fields(path: str, schema: dict[str, _Table]) -> None:
    """Add the HamQ columns an existing ``qso`` / ``qso_path`` table lacks (plain SQL)."""
    missing = {
        name: [(column, kind) for column, kind in _FIELD_SPECS[name] if column not in table.columns]
        for name, table in schema.items()
        if name in _FIELD_SPECS
    }
    missing = {name: columns for name, columns in missing.items() if columns}
    if not missing:
        return
    try:
        with _Database(path) as database, database.transaction() as connection:
            for name, columns in missing.items():
                for column, kind in columns:
                    connection.execute(
                        f"ALTER TABLE {_quote(name)} ADD COLUMN {_quote(column)} {_SQL_TYPES[kind]}"
                    )
    except sqlite3.Error as exc:
        raise GpkgError(
            tr("Fields could not be added to layer {layer} in {path}: {error}").format(
                layer=", ".join(missing), path=path, error=exc
            )
        ) from exc
    for name, columns in missing.items():
        _log(
            tr("Missing fields added to layer {layer} in {path}: {fields}").format(
                layer=name, path=path, fields=", ".join(column for column, _ in columns)
            ),
            compat.MSG_INFO,
        )


def _finish_schema(path: str) -> None:
    """Indexes and the schema version (plain SQL, in one transaction when anything is
    missing; a complete file is only read, so no write lock is taken)."""
    with _sqlite(path) as connection:
        try:
            if _schema_complete(connection, path):
                return
        except sqlite3.Error as exc:
            raise GpkgError(
                tr("{path} could not be prepared for HamQ: {error}").format(path=path, error=exc)
            ) from exc
    try:
        with _Database(path) as database, database.transaction() as connection:
            _ensure_dedup_index(connection, path)
            connection.execute(
                f'CREATE INDEX IF NOT EXISTS "{_PATH_INDEX}" ON "{PATH_LAYER}" ("qso_fid")'
            )
            _ensure_version(connection, path)
    except sqlite3.Error as exc:
        raise GpkgError(
            tr("{path} could not be prepared for HamQ: {error}").format(path=path, error=exc)
        ) from exc


def _schema_complete(connection: sqlite3.Connection, path: str) -> bool:
    """True when the indexes and the current schema version are there already."""
    indexes = {
        str(name).lower()
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    if _PATH_INDEX not in indexes or _META_INDEX not in indexes:
        return False
    if not _has_unique_index(connection, QSO_LAYER, "dedup_key") and not (
        _DEDUP_INDEX in indexes and _file_key(path) in _unique_index_tried
    ):
        return False
    row = connection.execute(
        f'SELECT "value" FROM "{META_TABLE}" WHERE "key" = ?', (_SCHEMA_KEY,)
    ).fetchone()
    if row is not None and _version(row[0]) > SCHEMA_VERSION:
        _warn_newer(path, _version(row[0]))
        return True
    return row is not None and _version(row[0]) == SCHEMA_VERSION


def _ensure_dedup_index(connection: sqlite3.Connection, path: str) -> None:
    if _has_unique_index(connection, QSO_LAYER, "dedup_key"):
        return
    if _file_key(path) in _unique_index_tried and _index_exists(connection, _DEDUP_INDEX):
        return  # old duplicates prevented the UNIQUE index in this session already
    _unique_index_tried.add(_file_key(path))
    connection.execute("SAVEPOINT hamq_dedup")
    try:
        connection.execute(f'DROP INDEX IF EXISTS "{_DEDUP_INDEX}"')
        connection.execute(f'CREATE UNIQUE INDEX "{_DEDUP_INDEX}" ON "{QSO_LAYER}" ("dedup_key")')
    except sqlite3.IntegrityError:
        connection.execute("ROLLBACK TO hamq_dedup")
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "{_DEDUP_INDEX}" ON "{QSO_LAYER}" ("dedup_key")'
        )
        _log_once(
            path,
            "dedup",
            tr(
                "{path} has duplicate QSOs, so dedup_key cannot get a UNIQUE index; "
                "imports still skip duplicates"
            ).format(path=path),
        )
    connection.execute("RELEASE hamq_dedup")


def _index_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND lower(name) = ?", (name,)
        ).fetchone()
        is not None
    )


def _has_unique_index(connection: sqlite3.Connection, table: str, column: str) -> bool:
    for row in connection.execute(f"PRAGMA index_list({_quote(table)})"):
        name, unique = row[1], row[2]
        if not unique:
            continue
        columns = [info[2] for info in connection.execute(f"PRAGMA index_info({_quote(name)})")]
        if [str(c).lower() for c in columns] == [column]:
            return True
    return False


def _ensure_version(connection: sqlite3.Connection, path: str) -> None:
    connection.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS "{_META_INDEX}" ON "{META_TABLE}" ("key")'
    )
    row = connection.execute(
        f'SELECT "value" FROM "{META_TABLE}" WHERE "key" = ?', (_SCHEMA_KEY,)
    ).fetchone()
    if row is None:
        connection.execute(
            f'INSERT INTO "{META_TABLE}" ("key", "value") VALUES (?, ?)',
            (_SCHEMA_KEY, str(SCHEMA_VERSION)),
        )
        return
    found = _version(row[0])
    if found > SCHEMA_VERSION:
        _warn_newer(path, found)
        return
    if found < SCHEMA_VERSION:
        _migrate(connection, found)
        connection.execute(
            f'UPDATE "{META_TABLE}" SET "value" = ? WHERE "key" = ?',
            (str(SCHEMA_VERSION), _SCHEMA_KEY),
        )


def _version(value: object) -> int:
    """A stored schema version; 0 when it is not a number."""
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _warn_newer(path: str, found: int) -> None:
    _log_once(
        path,
        "version",
        tr(
            "{path} was written by a newer HamQ (schema {found}, this version knows "
            "{known}); update HamQ if something does not work"
        ).format(path=path, found=found, known=SCHEMA_VERSION),
    )


def _migrate(connection: sqlite3.Connection, version: int) -> None:
    """Bring the tables from schema ``version`` (0: unknown) to :data:`SCHEMA_VERSION`.

    Version 1 is the first layout; missing tables, columns and indexes are added by
    :func:`ensure_gpkg` itself, so there is nothing to convert yet.
    """


# --- reading -------------------------------------------------------------------------------------


def existing_dedup_keys(path: str, keys: Iterable[str | None] | None = None) -> set[str]:
    """Every ``dedup_key`` in the ``qso`` table; empty when the file or table is missing.

    With ``keys``, only those of them that are in the table (indexed lookups, fast in a
    big log; ``None`` items are ignored). Reads with plain SQL, no geometry. Raises
    :class:`GpkgError` when the file cannot be read.
    """
    if not os.path.isfile(path):
        return set()
    with _sqlite(path) as connection:
        try:
            if "dedup_key" not in _columns(connection, QSO_LAYER):
                return set()
            return _stored_keys(connection, keys)
        except sqlite3.DatabaseError as exc:
            raise GpkgError(
                tr("{path} could not be read: {error}").format(path=path, error=exc)
            ) from exc


def _stored_keys(connection: sqlite3.Connection, keys: Iterable[str | None] | None) -> set[str]:
    if keys is None:
        return {
            str(key)
            for (key,) in connection.execute(
                f'SELECT "dedup_key" FROM "{QSO_LAYER}" WHERE "dedup_key" IS NOT NULL'
            )
        }
    wanted = sorted({key for key in keys if key is not None})
    found: set[str] = set()
    for start in range(0, len(wanted), _SQL_BATCH):
        batch = wanted[start : start + _SQL_BATCH]
        marks = ", ".join("?" * len(batch))
        found.update(
            str(key)
            for (key,) in connection.execute(
                f'SELECT "dedup_key" FROM "{QSO_LAYER}" WHERE "dedup_key" IN ({marks})', batch
            )
        )
    return found


def read_qso_rows(path: str) -> list[dict[str, object]]:
    """Attributes of every QSO, ordered by ``fid``, for ``core.stats.compute_stats``.

    Each row has the key ``fid`` and every ``QSO_FIELDS`` name (``None`` for NULL and for
    a column the file lacks). ``qso_datetime`` is an aware UTC ``datetime`` (``None``
    when empty or unreadable); a time stored without a zone counts as UTC. The rows come
    from plain SQL without geometries: about 70 ms for 10 000 QSOs. Empty when the file
    or table is missing; :class:`GpkgError` when the file cannot be read.
    """
    if not os.path.isfile(path):
        return []
    names = ["fid"] + [name for name, _ in QSO_FIELDS]
    times = [name for name, kind in QSO_FIELDS if kind == "datetime"]
    with _sqlite(path) as connection:
        try:
            info = connection.execute(f'PRAGMA table_info("{QSO_LAYER}")').fetchall()
            if not info:
                return []
            columns = {str(row[1]).lower(): str(row[1]) for row in info}
            key_column = next((str(row[1]) for row in info if row[5] == 1), None)
            if key_column is not None:
                columns["fid"] = key_column
            present = [name for name in names if name in columns]
            select = ", ".join(_quote(columns[name]) for name in present)
            order = f" ORDER BY {_quote(key_column)}" if key_column else ""
            cursor = connection.execute(f'SELECT {select} FROM "{QSO_LAYER}"{order}')
            rows = []
            for values in cursor:
                row: dict[str, object] = dict.fromkeys(names)
                row.update(zip(present, values))
                for name in times:
                    row[name] = from_qdatetime(row[name])
                rows.append(row)
            return rows
        except sqlite3.DatabaseError as exc:
            raise GpkgError(
                tr("{path} could not be read: {error}").format(path=path, error=exc)
            ) from exc


# --- inserting -----------------------------------------------------------------------------------


def insert_qsos(
    path: str, qsos: Sequence[Qso], feedback: Any = None, chunk_size: int = 1000
) -> InsertResult:
    """Write ``qsos`` to the GeoPackage ``path``; returns an :class:`InsertResult`.

    - The file is created when needed (:func:`ensure_gpkg`).
    - A QSO whose ``dedup_key`` is in the file already, or earlier in ``qsos``, is a
      duplicate and is skipped. A QSO without a ``dedup_key`` is not saved (failed).
    - Each QSO becomes a ``qso`` row; it gets a point when ``lat`` / ``lon`` are known
      (an impossible position is dropped with a warning). A QSO with both points also
      gets its geodesic path (:func:`geodesic_path`) in ``qso_path``, linked by
      ``qso_fid``; (nearly) antipodal points get no path and a warning.
    - QSOs are written in chunks of ``chunk_size``, each chunk in one transaction. A QSO
      with a value that does not fit its column only counts in ``failed`` (with a
      warning) and never stops the rest. When a whole chunk cannot be written (the file
      stays locked by another program, the disk is full) its QSOs count as failed.
    - ``feedback`` (a ``QgsFeedback``, optional) gets the progress in percent and can
      cancel between chunks; ``InsertResult.canceled`` then says so.
    - In the main thread, project layers of the file are refreshed afterwards (see the
      module docstring); when one of them is in edit mode, a warning says why the new
      QSOs are not shown yet.

    Raises :class:`GpkgError` when the file cannot be created or opened.
    """
    result = InsertResult()
    items = list(qsos) if qsos is not None else []
    if not items:
        return result
    chunk_size = max(1, int(chunk_size))
    ensure_gpkg(path)
    warnings = _Warnings(result.warnings)
    main_thread = _in_main_thread()
    if main_thread and _editing_layers(path):
        warnings.add(
            tr(
                "The QSO layers are in edit mode. The new QSOs were saved in the GeoPackage "
                "and show up after you save or discard your edits."
            )
        )
    with _Database(path, main_thread=main_thread) as database:
        writer = _Writer(database, result, warnings)
        total = len(items)
        _set_progress(feedback, 0.0)
        for start in range(0, total, chunk_size):
            if _is_canceled(feedback):
                result.canceled = True
                break
            writer.write_chunk(items[start : start + chunk_size])
            _set_progress(feedback, 100.0 * min(total, start + chunk_size) / total)
    warnings.finish()
    if main_thread and (result.inserted or result.paths):
        _refresh_project_layers(path)
    return result


@dataclass
class _Row:
    """A QSO ready for writing (prepared outside the write transaction)."""

    qso: Any
    key: str
    values: list[object]  # in the order of _Writer.qso_names
    point: bytes | None  # GeoPackage blob
    path_values: list[object] | None  # in the order of _Writer.path_names, qso_fid first
    path: bytes | None  # GeoPackage blob


class _Writer:
    """Writes chunks of QSOs and their paths."""

    def __init__(self, database: _Database, result: InsertResult, warnings: _Warnings) -> None:
        self.database = database
        self.result = result
        self.warnings = warnings
        self.qso_names = [name for name, _ in QSO_FIELDS if name in database.qso.columns]
        self.qso_kinds = dict(QSO_FIELDS)
        self.path_names = [name for name, _ in PATH_FIELDS if name in database.paths.columns]
        self.path_kinds = dict(PATH_FIELDS)
        self.keys: set[str] = set()  # keys written by this insert
        self.distance_area = _distance_area()

    def write_chunk(self, chunk: Sequence[Qso]) -> None:
        result, warnings = self.result, self.warnings
        try:  # known duplicates skip the path computation (checked again when writing)
            known = _stored_keys(self.database.connection, [_dedup_key(qso) for qso in chunk])
        except sqlite3.Error:
            known = set()
        rows: list[_Row] = []
        for qso in chunk:
            if _dedup_key(qso) in known:
                result.duplicates += 1
                continue
            try:
                row = self._prepare(qso)
            except _BadValue as exc:
                result.failed += 1
                warnings.add(
                    tr("QSO {qso}: not saved, invalid value {value} in {field}").format(
                        qso=_label(qso), value=exc.value, field=exc.field
                    )
                )
                continue
            except Exception as exc:  # one bad QSO never stops the others
                result.failed += 1
                warnings.add(
                    tr("QSO {qso}: could not be saved: {error}").format(qso=_label(qso), error=exc)
                )
                continue
            if row is not None:
                rows.append(row)
        if not rows:
            return
        try:
            written = self._write(rows)
        except sqlite3.Error as exc:  # the chunk was rolled back
            for row in rows:
                result.failed += 1
                warnings.add(
                    tr("QSO {qso}: could not be saved: {error}").format(
                        qso=_label(row.qso), error=exc
                    )
                )
            return
        for row, outcome, has_path in written:
            if outcome == "duplicate":
                result.duplicates += 1
            elif isinstance(outcome, int):
                self.keys.add(row.key)
                result.inserted += 1
                result.fids.append(outcome)
                result.paths += has_path
            else:
                result.failed += 1
                warnings.add(
                    tr("QSO {qso}: could not be saved: {error}").format(
                        qso=_label(row.qso), error=outcome
                    )
                )

    def _prepare(self, qso: Any) -> _Row | None:
        """Values and blobs of one QSO; ``None`` for a duplicate within this insert."""
        key = _dedup_key(qso)
        if key is None:
            raise _BadValue("dedup_key", getattr(qso, "dedup_key", None))
        if key in self.keys:
            self.result.duplicates += 1
            return None
        values = [
            _sql_value(getattr(qso, name, None), self.qso_kinds[name], name)
            for name in self.qso_names
        ]
        lat, lon = getattr(qso, "lat", None), getattr(qso, "lon", None)
        target = _point(lat, lon)
        if target is None and (lat is not None or lon is not None):
            self.warnings.add(
                tr("QSO {qso}: invalid position {lat}, {lon}, saved without a point").format(
                    qso=_label(qso), lat=lat, lon=lon
                )
            )
        point = None if target is None else _point_blob(*target)
        path_values = path = None
        origin = _point(getattr(qso, "my_lat", None), getattr(qso, "my_lon", None))
        if origin is not None and target is not None:
            if geo.is_antipodal(origin[0], origin[1], target[0], target[1]):
                self.warnings.add(_antipodal_warning(_label(qso)))
            else:
                geometry = _geodesic(self.distance_area, origin, target)
                if geometry is not None:
                    path = _geometry_blob(geometry)
                    attributes = {
                        "distance_km": _real(getattr(qso, "distance_km", None)),
                        "bearing_deg": _real(getattr(qso, "bearing_deg", None)),
                        "band": getattr(qso, "band", None),
                        "mode": display_mode(
                            getattr(qso, "mode", None), getattr(qso, "submode", None)
                        )
                        or None,
                    }
                    path_values = [
                        _sql_value(attributes.get(name), self.path_kinds[name], name)
                        for name in self.path_names
                        if name != "qso_fid"
                    ]
        return _Row(qso, key, values, point, path_values, path)

    def _write(self, rows: list[_Row]) -> list[tuple[_Row, object, int]]:
        """Insert the rows in one transaction; ``(row, fid | "duplicate" | error, paths)``."""
        database = self.database
        qso_sql = database.insert_sql(database.qso, self.qso_names)
        linked = "qso_fid" in self.path_names
        path_names = [name for name in self.path_names if name != "qso_fid"]
        path_sql = database.insert_sql(database.paths, ["qso_fid", *path_names]) if linked else ""
        outcomes: list[tuple[_Row, object, int]] = []
        with database.transaction() as connection:
            stored = _stored_keys(connection, [row.key for row in rows])
            for row in rows:
                if row.key in stored:
                    outcomes.append((row, "duplicate", 0))
                    continue
                try:  # a failed statement is undone by SQLite, the transaction goes on
                    fid = connection.execute(qso_sql, [*row.values, row.point]).lastrowid
                except sqlite3.IntegrityError as exc:  # a constraint of the file
                    duplicate = row.key in _stored_keys(connection, [row.key])
                    outcomes.append((row, "duplicate" if duplicate else str(exc), 0))
                    continue
                stored.add(row.key)  # the same key again in this chunk
                paths = 0
                if row.path is not None and linked:
                    try:
                        connection.execute(path_sql, [fid, *row.path_values, row.path])
                        paths = 1
                    except sqlite3.IntegrityError as exc:  # the QSO stays, without its path
                        self.warnings.add(
                            tr("QSO {qso}: the path could not be saved: {error}").format(
                                qso=_label(row.qso), error=exc
                            )
                        )
                outcomes.append((row, int(fid), paths))
        return outcomes


class _BadValue(ValueError):
    def __init__(self, field_name: str, value: object) -> None:
        super().__init__(f"{field_name}={value!r}")
        self.field = field_name
        self.value = _shown(value)


def _sql_value(value: object, kind: str, name: str) -> object:
    """``value`` as stored in a column of ``kind``; :class:`_BadValue` if impossible.

    Datetimes go through ``fields.to_qdatetime`` and are stored as UTC ISO 8601 text.
    """
    if value is None:
        return None
    if kind == "text":
        return value if isinstance(value, str) else str(value)
    if kind == "datetime":
        if isinstance(value, datetime):
            value = to_qdatetime(value)
        if isinstance(value, QDateTime) and value.isValid():
            return value.toUTC().toString(compat.DATE_FORMAT_ISO_MS)
        raise _BadValue(name, value)
    if isinstance(value, bool):
        raise _BadValue(name, value)
    if kind == "int":
        number = None
        if isinstance(value, int):
            number = value
        elif isinstance(value, float) and value.is_integer():
            number = int(value)
        elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
            number = int(value.strip())
        if number is None or not _INT32_MIN <= number <= _INT32_MAX:  # QGIS int fields
            raise _BadValue(name, value)
        return number
    if kind == "real":
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                raise _BadValue(name, value) from None
        else:
            raise _BadValue(name, value)
        return number if math.isfinite(number) else None
    raise ValueError(f"unknown field kind {kind!r}")


# --- geodesic paths ------------------------------------------------------------------------------


def geodesic_path(my_lat: Any, my_lon: Any, lat: Any, lon: Any) -> QgsGeometry | None:
    """The geodesic (WGS84) from my position to the other station as a MultiLineString.

    Vertices are at most :data:`PATH_STEP_KM` apart (``QgsDistanceArea.geodesicLine``),
    and the line is split at the antimeridian: no segment spans more than 180 degrees of
    longitude. ``None`` when a point is missing or invalid, when both are the same place
    (closer than 1 m) and when they are (nearly) antipodal (``core.geo.is_antipodal``),
    where no unique path exists. Coordinates are in degrees, latitude first.
    """
    origin, target = _point(my_lat, my_lon), _point(lat, lon)
    if origin is None or target is None:
        return None
    if geo.is_antipodal(origin[0], origin[1], target[0], target[1]):
        return None
    return _geodesic(_distance_area(), origin, target)


def _geodesic(
    distance_area: QgsDistanceArea, origin: tuple[float, float], target: tuple[float, float]
) -> QgsGeometry | None:
    length_m = geo.distance_km(origin[0], origin[1], target[0], target[1]) * 1000.0
    if length_m < _MIN_PATH_KM * 1000.0:
        return None
    # geodesicLine() always puts its first vertex one interval along the line: on a path
    # shorter than the interval that vertex lies beyond the end (QGIS 3.34 to 4.2).
    interval = min(PATH_STEP_KM * 1000.0, length_m / 2.0)
    parts = distance_area.geodesicLine(
        QgsPointXY(origin[1], origin[0]), QgsPointXY(target[1], target[0]), interval, True
    )
    parts = _clean_parts(parts)
    if not parts:
        return None
    return QgsGeometry.fromMultiPolylineXY(parts)


def _clean_parts(parts: list[list[QgsPointXY]]) -> list[list[QgsPointXY]]:
    """Split segments that jump more than 180 degrees of longitude, drop repeated vertices
    and parts that are not lines.

    ``geodesicLine`` splits most antimeridian crossings itself; near a pole a 100 km step
    can jump from 152 to -116 degrees without being split. Such a segment is cut at the
    antimeridian, at the linearly interpolated latitude.
    """
    cleaned: list[list[QgsPointXY]] = []
    for part in parts:
        if len(part) >= 2:
            box = QgsGeometry.fromPolylineXY(part).boundingBox()
            # narrower than 180 degrees: no jump; not a single point: a real line
            if box.width() <= 180.0 and (box.width() > 0.0 or box.height() > 0.0):
                cleaned.append(part)
                continue
        xs = [point.x() for point in part]
        ys = [point.y() for point in part]
        pieces: list[list[tuple[float, float]]] = [[]]
        for x, y in zip(xs, ys):
            current = pieces[-1]
            if current:
                px, py = current[-1]
                if (px, py) == (x, y):
                    continue
                if abs(x - px) > 180.0:
                    boundary = 180.0 if px > 0.0 else -180.0
                    unwrapped = x + 360.0 if px > 0.0 else x - 360.0
                    fraction = (boundary - px) / (unwrapped - px)
                    crossing = py + fraction * (y - py)
                    if px != boundary:
                        current.append((boundary, crossing))
                    pieces.append([(-boundary, crossing)] if x != -boundary else [])
                    current = pieces[-1]
            current.append((x, y))
        for piece in pieces:
            if len(piece) >= 2:
                cleaned.append([QgsPointXY(x, y) for x, y in piece])
    return cleaned


def _distance_area() -> QgsDistanceArea:
    """A WGS84 ``QgsDistanceArea`` for EPSG:4326 (cheap after the first one; per call, so
    every thread has its own)."""
    distance_area = QgsDistanceArea()
    distance_area.setSourceCrs(
        QgsCoordinateReferenceSystem(_EPSG_4326), QgsCoordinateTransformContext()
    )
    distance_area.setEllipsoid("WGS84")
    return distance_area


def _antipodal_warning(label: str) -> str:
    return tr(
        "QSO {qso}: the other station is (nearly) on the opposite side of the Earth, so "
        "there is no single shortest path; no path is drawn"
    ).format(qso=label)


# --- GeoPackage geometry blobs --------------------------------------------------------------------
#
# GeoPackage binary: "GP", version 0, flags (bit 0: little-endian header, bits 1-3: envelope
# kind, bit 4: empty), srs_id, the envelope (minx, maxx, miny, maxy, ...) and ISO WKB. Like
# GDAL, points get no envelope and other geometries an XY one.

_GP_POINT = b"GP\x00\x01" + struct.pack("<i", 4326)
_GP_WITH_ENVELOPE = b"GP\x00\x03" + struct.pack("<i", 4326)
_WKB_POINT_LE = struct.Struct("<BIdd")
_XY_LE = struct.Struct("<dd")
_ENVELOPE_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


def _point_blob(lat: float, lon: float) -> bytes:
    return _GP_POINT + _WKB_POINT_LE.pack(1, 1, lon, lat)


def _geometry_blob(geometry: QgsGeometry) -> bytes:
    box = geometry.boundingBox()
    envelope = struct.pack("<4d", box.xMinimum(), box.xMaximum(), box.yMinimum(), box.yMaximum())
    return _GP_WITH_ENVELOPE + envelope + bytes(geometry.asWkb())


def _blob_envelope(blob: object) -> tuple[float, float, float, float] | None:
    """``(minx, maxx, miny, maxy)`` of a GeoPackage blob; ``None`` when empty or unreadable."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    data = bytes(blob)
    if len(data) == 29 and data[:4] == b"GP\x00\x01" and data[8:13] == b"\x01\x01\x00\x00\x00":
        x, y = _XY_LE.unpack_from(data, 13)  # the common case: a 2D little-endian point
        return None if math.isnan(x) or math.isnan(y) else (x, x, y, y)
    if len(data) < 8 or data[:2] != b"GP":
        return None
    flags = data[3]
    if flags & 0x10:
        return None  # empty geometry
    doubles = _ENVELOPE_DOUBLES.get((flags >> 1) & 0x07)
    if doubles is None:
        return None
    try:
        if doubles:
            envelope = struct.unpack_from(("<" if flags & 1 else ">") + f"{doubles}d", data, 8)
            minx, maxx, miny, maxy = envelope[:4]
        else:
            bounds = _wkb_bounds(data, 8)
            if bounds is None:
                return None
            minx, maxx, miny, maxy = bounds
    except (struct.error, ValueError, RecursionError):
        return None
    if math.isnan(minx) or math.isnan(maxx) or math.isnan(miny) or math.isnan(maxy):
        return None  # an empty point is written as NaN coordinates
    return minx, maxx, miny, maxy


def _wkb_bounds(data: bytes, offset: int) -> tuple[float, float, float, float] | None:
    """Bounds of a (Multi)Point / LineString / Polygon / GeometryCollection in WKB."""
    xs: list[float] = []
    ys: list[float] = []

    def read(position: int) -> int:
        order = "<" if data[position] == 1 else ">"
        (code,) = struct.unpack_from(order + "I", data, position + 1)
        position += 5
        dims = 2
        if code & 0x80000000:  # EWKB Z flag
            dims += 1
        if code & 0x40000000:  # EWKB M flag
            dims += 1
        code &= 0x0FFFFFFF
        thousands, base = divmod(code, 1000)
        dims += {0: 0, 1: 1, 2: 1, 3: 2}.get(thousands, 0)

        def points(count: int, at: int) -> int:
            values = struct.unpack_from(order + f"{count * dims}d", data, at)
            xs.extend(values[0::dims])
            ys.extend(values[1::dims])
            return at + 8 * dims * count

        if base == 1:
            return points(1, position)
        (count,) = struct.unpack_from(order + "I", data, position)
        position += 4
        if base == 2:
            return points(count, position)
        if base == 3:
            for _ in range(count):
                (ring,) = struct.unpack_from(order + "I", data, position)
                position = points(ring, position + 4)
            return position
        if base in (4, 5, 6, 7):
            for _ in range(count):
                position = read(position)
            return position
        raise ValueError(f"unsupported WKB type {code}")

    read(offset)
    pairs = [(x, y) for x, y in zip(xs, ys) if not (math.isnan(x) or math.isnan(y))]
    if not pairs:
        return None
    return (
        min(x for x, _ in pairs),
        max(x for x, _ in pairs),
        min(y for _, y in pairs),
        max(y for _, y in pairs),
    )


def _blob_point(blob: object) -> tuple[float, float] | None:
    """``(lat, lon)`` of a point blob (the lower-left corner of other geometries)."""
    envelope = _blob_envelope(blob)
    return None if envelope is None else _point(envelope[2], envelope[0])


def _st_is_empty(blob: object) -> int | None:
    if blob is None:
        return None
    return 1 if _blob_envelope(blob) is None else 0


def _st_coordinate(position: int) -> Any:
    def function(blob: object) -> float | None:
        envelope = _blob_envelope(blob)
        return None if envelope is None else envelope[position]

    return function


def _register_functions(connection: sqlite3.Connection) -> None:
    """The GeoPackage SQL functions GDAL's R-tree triggers call."""
    connection.create_function("ST_IsEmpty", 1, _st_is_empty)
    for position, name in enumerate(("ST_MinX", "ST_MaxX", "ST_MinY", "ST_MaxY")):
        connection.create_function(name, 1, _st_coordinate(position))


# --- recalculation --------------------------------------------------------------------------------


def recalculate(
    path: str,
    station: Station,
    cty: CtyDatabase | None = None,
    feedback: Any = None,
    *,
    force_station: bool = False,
) -> int:
    """Recompute distance, bearing and path of every QSO; returns the number of QSOs changed.

    For each QSO, with the rules of an import (``core.qso.record_to_qso``):

    - My position (origin): ``MY_LAT`` / ``MY_LON`` kept in ``adif_extra``, else the
      QSO's ``my_gridsquare`` when it is a valid locator, else ``station.grid``. So QSOs
      logged with their own QTH keep it, and QSOs without one follow the station locator
      on every recalculation (``my_gridsquare`` is not changed). With
      ``force_station=True`` every QSO uses ``station.grid`` and gets it as
      ``my_gridsquare`` (I moved, or the log has a wrong locator); this is ignored, with
      a warning, when ``station.grid`` is not a valid locator.
    - Missing ``dxcc``, ``country``, ``cont``, ``cq_zone`` and ``itu_zone`` are filled from
      ``cty`` (values in the file always win).
    - The other station's point: a QSO without a point gets one (``LAT`` / ``LON`` in
      ``adif_extra``, the locator, cty.dat). A point is replaced when a more precise
      source is now known (a locator for a cty.dat point) or when its locator was changed
      so that the point no longer lies in the locator's cell; otherwise it is kept, also
      when it was moved by hand.
    - ``distance_km`` / ``bearing_deg`` from the origin to the point (``None`` when
      either is unknown); the paths of these QSOs are built again, and paths of QSOs that
      no longer exist are removed.

    ``feedback`` (optional ``QgsFeedback``) gets the progress and can cancel until the
    writing starts; a canceled recalculation changes nothing and returns 0. Warnings are
    logged and sent to ``feedback.pushWarning`` when it has one. In the main thread,
    project layers of the file are refreshed afterwards. Raises :class:`GpkgError` when
    the file cannot be created or opened.
    """
    ensure_gpkg(path)
    station = station if station is not None else Station()
    station_grid = _valid_locator(getattr(station, "grid", ""))
    collected: list[str] = []
    warnings = _Warnings(collected)
    if force_station and station_grid is None:
        warnings.add(
            tr("My locator {locator} is not valid, so the QSOs keep their own QTH").format(
                locator=_shown(getattr(station, "grid", ""))
            )
        )
        force_station = False
    main_thread = _in_main_thread()
    if main_thread and _editing_layers(path):
        warnings.add(
            tr(
                "The QSO layers are in edit mode. The recalculated values were saved in the "
                "GeoPackage; saving your edits may overwrite some of them."
            )
        )
    with _Database(path, main_thread=main_thread) as database:
        updated = _recalculate(
            database, station, station_grid, cty, feedback, force_station, warnings
        )
    if updated is None:  # canceled
        return 0
    warnings.finish()
    for message in collected:
        _log(message)
        push = getattr(feedback, "pushWarning", None)
        if callable(push):
            push(message)
    if main_thread:
        _refresh_project_layers(path)
    return updated


@dataclass
class _StoredQso:
    """A QSO as read from the ``qso`` table: fid, values by column name, point (lat, lon)."""

    fid: int
    values: dict[str, object]
    point: tuple[float, float] | None


def _recalculate(
    database: _Database,
    station: Station,
    station_grid: str | None,
    cty: CtyDatabase | None,
    feedback: Any,
    force_station: bool,
    warnings: _Warnings,
) -> int | None:
    """The work of :func:`recalculate`; ``None`` when canceled before writing."""
    kinds = dict(QSO_FIELDS)
    _set_progress(feedback, 0.0)
    rows = database.read_qsos()
    if _is_canceled(feedback):
        return None
    total = max(1, len(rows))
    _set_progress(feedback, 10.0)

    path_names = [name for name, _ in PATH_FIELDS if name in database.paths.columns]
    path_kinds = dict(PATH_FIELDS)
    distance_area = _distance_area()
    attribute_changes: dict[int, dict[str, object]] = {}
    geometry_changes: dict[int, bytes] = {}
    paths: list[tuple[int, list[object], bytes]] = []
    for number, row in enumerate(rows, 1):
        if number % 200 == 0:
            if _is_canceled(feedback):
                return None
            _set_progress(feedback, 10.0 + 50.0 * number / total)
        new_values, point, geometry = _recalculated(
            row, station, station_grid, cty, force_station, distance_area, warnings
        )
        changes = {
            name: _sql_value(value, kinds[name], name)
            for name, value in new_values.items()
            if name in database.qso.columns and not _same(row.values.get(name), value)
        }
        if changes:
            attribute_changes[row.fid] = changes
        if point is not None:
            geometry_changes[row.fid] = _point_blob(*point)
        if geometry is not None:
            attributes = {
                "qso_fid": row.fid,
                "distance_km": new_values.get("distance_km"),
                "bearing_deg": new_values.get("bearing_deg"),
                "band": row.values.get("band"),
                "mode": display_mode(row.values.get("mode"), row.values.get("submode")) or None,
            }
            values = [
                _sql_value(attributes.get(name), path_kinds[name], name) for name in path_names
            ]
            paths.append((row.fid, values, _geometry_blob(geometry)))
    if _is_canceled(feedback):
        return None

    # Writing: not cancelable any more, so the file stays consistent.
    _set_progress(feedback, 60.0)
    changed = sorted(set(attribute_changes) | set(geometry_changes))
    written = 0
    for start in range(0, len(changed), _WRITE_CHUNK):
        chunk = changed[start : start + _WRITE_CHUNK]
        try:
            with database.transaction() as connection:
                for fid in chunk:
                    _update_row(
                        connection,
                        database,
                        fid,
                        attribute_changes.get(fid),
                        geometry_changes.get(fid),
                    )
            written += len(chunk)
        except sqlite3.Error as exc:
            warnings.add(tr("Recalculated values could not be saved: {error}").format(error=exc))
        _set_progress(
            feedback, 60.0 + 10.0 * min(len(changed), start + _WRITE_CHUNK) / max(1, len(changed))
        )
    _rebuild_paths(database, [row.fid for row in rows], path_names, paths, warnings, feedback)
    _set_progress(feedback, 100.0)
    return written


def _update_row(
    connection: sqlite3.Connection,
    database: _Database,
    fid: int,
    attributes: dict[str, object] | None,
    geometry: bytes | None,
) -> None:
    assignments = [f"{_quote(database.qso.columns[name])} = ?" for name in attributes or {}]
    values = list((attributes or {}).values())
    if geometry is not None:
        assignments.append(f"{_quote(database.qso.geometry)} = ?")
        values.append(geometry)
    if assignments:
        connection.execute(
            f"UPDATE {_quote(QSO_LAYER)} SET {', '.join(assignments)} "
            f"WHERE {_quote(database.qso.key)} = ?",
            [*values, fid],
        )


def _rebuild_paths(
    database: _Database,
    fids: list[int],
    path_names: list[str],
    paths: list[tuple[int, list[object], bytes]],
    warnings: _Warnings,
    feedback: Any,
) -> None:
    """Delete the paths of ``fids`` and of QSOs that no longer exist, then insert ``paths``
    (in chunks). Paths of QSOs added meanwhile (a live QSO) stay."""
    table = database.paths
    qso_fid = _quote(table.columns.get("qso_fid", "qso_fid"))
    try:
        with database.transaction() as connection:
            for start in range(0, len(fids), _SQL_BATCH):
                batch = fids[start : start + _SQL_BATCH]
                marks = ", ".join("?" * len(batch))
                connection.execute(
                    f"DELETE FROM {_quote(PATH_LAYER)} WHERE {qso_fid} IN ({marks})", batch
                )
            connection.execute(
                f"DELETE FROM {_quote(PATH_LAYER)} WHERE {qso_fid} IS NULL OR {qso_fid} NOT IN "
                f"(SELECT {_quote(database.qso.key)} FROM {_quote(QSO_LAYER)})"
            )
    except sqlite3.Error as exc:
        warnings.add(tr("The old QSO paths could not be deleted: {error}").format(error=exc))
        return
    sql = database.insert_sql(table, path_names)
    total = max(1, len(paths))
    for start in range(0, len(paths), _WRITE_CHUNK):
        chunk = paths[start : start + _WRITE_CHUNK]
        try:
            with database.transaction() as connection:
                connection.executemany(sql, [[*values, blob] for _, values, blob in chunk])
        except sqlite3.Error as exc:
            warnings.add(
                tr("QSO {qso}: the path could not be saved: {error}").format(
                    qso=", ".join(str(fid) for fid, _, _ in chunk[:3]), error=exc
                )
            )
        _set_progress(feedback, 70.0 + 30.0 * min(total, start + _WRITE_CHUNK) / total)


def _recalculated(
    row: _StoredQso,
    station: Station,
    station_grid: str | None,
    cty: CtyDatabase | None,
    force_station: bool,
    distance_area: QgsDistanceArea,
    warnings: _Warnings,
) -> tuple[dict[str, object], tuple[float, float] | None, QgsGeometry | None]:
    """New column values, a new point (``None``: keep) and the path of one QSO."""
    values = row.values
    call = values.get("call")
    call = call.strip() if isinstance(call, str) else ""
    extra = _json_object(values.get("adif_extra"))
    # A minimal ADIF record: record_to_qso applies the import rules to it. Date and time
    # only have to be valid; they are not used.
    record = {"CALL": call or "UNKNOWN", "QSO_DATE": "20000101", "TIME_ON": "0000"}
    origin_keys = ("LAT", "LON") if force_station else ("LAT", "LON", "MY_LAT", "MY_LON")
    for key in origin_keys:
        text = extra.get(key)
        if isinstance(text, str) and text.strip():
            record[key] = text
    columns = [
        ("gridsquare", "GRIDSQUARE"),
        ("dxcc", "DXCC"),
        ("country", "COUNTRY"),
        ("cont", "CONT"),
        ("cq_zone", "CQZ"),
        ("itu_zone", "ITUZ"),
    ]
    if not force_station:
        columns.append(("my_gridsquare", "MY_GRIDSQUARE"))
    for name, key in columns:
        value = values.get(name)
        if value is not None and str(value).strip():
            record[key] = str(value)
    qso, _warnings = record_to_qso(record, station=station, cty=cty if call else None)
    if qso is None:  # cannot happen with the record above; keep the row as it is
        return {}, None, None

    new: dict[str, object] = {
        "dxcc": qso.dxcc,
        "country": qso.country,
        "cont": qso.cont,
        "cq_zone": qso.cq_zone,
        "itu_zone": qso.itu_zone,
    }
    if force_station:
        new["my_gridsquare"] = station_grid
    position = row.point
    new_point = None
    derived = _point(qso.lat, qso.lon)
    if derived is not None and _replaces(row, derived, qso.loc_source):
        position = new_point = derived
        new["loc_source"] = qso.loc_source
    origin = _point(qso.my_lat, qso.my_lon)
    distance = bearing = None
    geometry = None
    if origin is not None and position is not None:
        distance = geo.distance_km(origin[0], origin[1], position[0], position[1])
        bearing = geo.bearing_deg(origin[0], origin[1], position[0], position[1])
        if geo.is_antipodal(origin[0], origin[1], position[0], position[1]):
            warnings.add(_antipodal_warning(_stored_label(values)))
        else:
            geometry = _geodesic(distance_area, origin, position)
    new["distance_km"] = distance
    new["bearing_deg"] = bearing
    return new, new_point, geometry


def _replaces(row: _StoredQso, derived: tuple[float, float], source: str | None) -> bool:
    """Whether the position ``derived`` from the data replaces the stored point."""
    if row.point is None:
        return True
    stored_source = row.values.get("loc_source")
    stored_rank = _SOURCE_RANK.get(stored_source if isinstance(stored_source, str) else "", 0)
    if stored_rank == 0:  # no or an unknown source: placed by hand
        return False
    new_rank = _SOURCE_RANK.get(source or "", 0)
    if new_rank > stored_rank:
        return True
    if new_rank == stored_rank == _SOURCE_RANK["grid"]:
        locator = _valid_locator(row.values.get("gridsquare"))
        if locator is None:
            return False
        lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
        lat, lon = row.point
        return not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max)
    return False


# --- the write connection -------------------------------------------------------------------------


@dataclass
class _TableInfo:
    """Column names of a table (lower-case name -> real name), its key and geometry column."""

    columns: dict[str, str]
    key: str
    geometry: str


class _Database:
    """A SQLite connection of this module for writing, with the functions GDAL's R-tree
    triggers need. Writes go through :meth:`transaction`."""

    def __init__(self, path: str, *, main_thread: bool | None = None) -> None:
        self.file = path
        if main_thread is None:
            main_thread = _in_main_thread()
        self.timeout = _MAIN_THREAD_TIMEOUT_S if main_thread else _SQLITE_TIMEOUT_S
        self.connection = _connect(path, write=True, timeout=self.timeout)
        _register_functions(self.connection)
        try:
            self.qso = self._table(QSO_LAYER)
            self.paths = self._table(PATH_LAYER)
        except sqlite3.Error as exc:
            self.connection.close()
            raise GpkgError(
                tr("{path} could not be read: {error}").format(path=path, error=exc)
            ) from exc

    def __enter__(self) -> _Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` .. ``COMMIT`` under the module's write lock; rolled back when
        the block raises. The cached feature counts and extents of the HamQ tables are
        cleared in the same transaction."""
        with _write_lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                _clear_caches(self.connection)
                self.connection.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self.connection.execute("ROLLBACK")
                raise

    def insert_sql(self, table: _TableInfo, names: list[str]) -> str:
        columns = [_quote(table.columns[name]) for name in names] + [_quote(table.geometry)]
        marks = ", ".join("?" * len(columns))
        name = QSO_LAYER if table is self.qso else PATH_LAYER
        return f"INSERT INTO {_quote(name)} ({', '.join(columns)}) VALUES ({marks})"

    def read_qsos(self) -> list[_StoredQso]:
        """Every QSO with the columns ``recalculate`` uses and its point."""
        names = [name for name, _ in QSO_FIELDS if name in self.qso.columns]
        select = ", ".join(
            [_quote(self.qso.key)]
            + [_quote(self.qso.columns[name]) for name in names]
            + [_quote(self.qso.geometry)]
        )
        rows = []
        for record in self.connection.execute(f"SELECT {select} FROM {_quote(QSO_LAYER)}"):
            values = dict(zip(names, record[1:-1]))
            rows.append(_StoredQso(int(record[0]), values, _blob_point(record[-1])))
        return rows

    def _table(self, name: str) -> _TableInfo:
        info = self.connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
        columns = {str(row[1]).lower(): str(row[1]) for row in info}
        key = next((str(row[1]) for row in info if row[5] == 1), "fid")
        row = self.connection.execute(
            "SELECT column_name FROM gpkg_geometry_columns WHERE lower(table_name) = ?", (name,)
        ).fetchone()
        geometry = str(row[0]) if row is not None else "geom"
        return _TableInfo(columns, key, geometry)


def _clear_caches(connection: sqlite3.Connection) -> None:
    """Make GDAL count the rows and compute the extent of ``qso`` and ``qso_path`` again.

    GDAL caches both per connection and stores them in ``gpkg_ogr_contents`` /
    ``gpkg_contents``; other connections that write the tables (this module, the user's
    edits) leave them stale.
    """
    names = (QSO_LAYER, PATH_LAYER)
    if _columns(connection, "gpkg_ogr_contents"):
        connection.execute(
            "UPDATE gpkg_ogr_contents SET feature_count = NULL WHERE lower(table_name) IN (?, ?)",
            names,
        )
    connection.execute(
        "UPDATE gpkg_contents SET min_x = NULL, min_y = NULL, max_x = NULL, max_y = NULL, "
        "last_change = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE lower(table_name) IN (?, ?)",
        names,
    )


# --- project layers ------------------------------------------------------------------------------


def _editing_layers(path: str) -> list[Any]:
    from .layers import matching_layers

    try:
        return [layer for layer in matching_layers(path) if layer.isEditable()]
    except Exception:  # never let the check stop a write
        return []


def _refresh_project_layers(path: str) -> None:
    from .layers import refresh_layers

    try:
        refresh_layers(path)
    except Exception as exc:  # the data is written; a failed refresh must not hide that
        _log(tr("The HamQ layers could not be refreshed: {error}").format(error=exc))


def _in_main_thread() -> bool:
    app = QCoreApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


# --- helpers -------------------------------------------------------------------------------------


def _connect(path: str, *, write: bool, timeout: float = _SQLITE_TIMEOUT_S) -> sqlite3.Connection:
    """A plain SQLite connection to an existing file (never creates one), autocommit."""
    mode = "rw" if write or os.access(path, os.W_OK) else "ro"
    uri = Path(os.path.abspath(path)).as_uri() + f"?mode={mode}"
    try:
        return sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    except sqlite3.Error as exc:
        # SQLite rejects URIs with a host (Windows UNC paths); open the existing file by name.
        if not os.path.isfile(path):
            raise GpkgError(
                tr("{path} could not be opened: {error}").format(path=path, error=exc)
            ) from exc
        try:
            return sqlite3.connect(path, timeout=timeout, isolation_level=None)
        except sqlite3.Error as second:
            raise GpkgError(
                tr("{path} could not be opened: {error}").format(path=path, error=second)
            ) from second


@contextlib.contextmanager
def _sqlite(path: str) -> Iterator[sqlite3.Connection]:
    """A plain SQLite connection for reading (never creates a file)."""
    connection = _connect(path, write=False)
    try:
        yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    """Lower-case column name -> declared type; empty when the table does not exist."""
    return {
        str(row[1]).lower(): str(row[2] or "")
        for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
    }


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _point(lat: Any, lon: Any) -> tuple[float, float] | None:
    """``(lat, lon)`` as floats when both are finite and in range (longitude wrapped)."""
    if lat is None or lon is None or isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not -90.0 <= lat <= 90.0:
        return None
    if not -180.0 <= lon <= 180.0:
        lon = math.fmod(lon, 360.0)
        if lon > 180.0:
            lon -= 360.0
        elif lon < -180.0:
            lon += 360.0
    return lat, lon


def _valid_locator(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return maidenhead.normalize(value)
    except ValueError:
        return None


def _real(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _same(old: object, new: object) -> bool:
    """Equal column values; numbers within 1e-9 relative."""
    if old is None or new is None:
        return old is None and new is None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return math.isclose(float(old), float(new), rel_tol=1e-9, abs_tol=1e-9)
    return old == new


def _json_object(text: object) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _dedup_key(qso: Any) -> str | None:
    key = getattr(qso, "dedup_key", None)
    return key if isinstance(key, str) and key else None


def _label(qso: Any) -> str:
    """How a warning names a QSO: ``YU1ABC 2026-09-15 18:45``."""
    call = getattr(qso, "call", None)
    when = getattr(qso, "qso_datetime", None)
    parts = [_shown(call) if call else "?"]
    if isinstance(when, datetime):
        parts.append(when.strftime("%Y-%m-%d %H:%M"))
    return " ".join(parts)


def _stored_label(values: dict[str, object]) -> str:
    call = values.get("call")
    return _shown(call) if call else "?"


def _shown(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= 40 else text[:39] + "…"


def _set_progress(feedback: Any, percent: float) -> None:
    if feedback is not None:
        feedback.setProgress(percent)


def _is_canceled(feedback: Any) -> bool:
    return feedback is not None and bool(feedback.isCanceled())


def _log(message: str, level: Any = None) -> None:
    QgsMessageLog.logMessage(message, _LOG_TAG, compat.MSG_WARNING if level is None else level)


def _file_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _log_once(path: str, kind: str, message: str) -> None:
    key = (_file_key(path), kind)
    if key in _logged_once:
        return
    _logged_once.add(key)
    _log(message)


class _Warnings:
    """Appends warnings to a list, at most ``_MAX_WARNINGS``, then counts the rest."""

    __slots__ = ("dropped", "items")

    def __init__(self, items: list[str]) -> None:
        self.items = items
        self.dropped = 0

    def add(self, message: str) -> None:
        if len(self.items) < _MAX_WARNINGS:
            self.items.append(message)
        else:
            self.dropped += 1

    def finish(self) -> None:
        if self.dropped:
            self.items.append(tr("Further warnings not listed: {count}").format(count=self.dropped))
            self.dropped = 0
