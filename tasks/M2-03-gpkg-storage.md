# M2-03: GeoPackage storage and project layers

**Milestone:** M2 (with M3 paths, see M3-02)
**Status:** done
**Skills:** pyqgis-plugin, geodesy, adif

## Goal
Store the QSO log in one GeoPackage (`qso` points, `qso_path` geodesic lines, `hamq_meta`
schema version) with deduplication, fast reads for the statistics and a recalculation for a
new QTH or a new cty.dat. Show the log in the project: a "HamQ" group with translated layer
names, default styles and translated field aliases that follow the language switch.

## Scope
- `hamq/qgis_io/gpkg.py`, `hamq/qgis_io/layers.py`
- `tests/qgis/test_gpkg.py`, `tests/qgis/test_layers.py`
- `hamq/i18n/sr_Latn/qgis_io_gpkg.json`, `hamq/i18n/sr_Latn/qgis_io_layers.json`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: appended block "M2-03 / M3-02"
- `tasks/M2-03-gpkg-storage.md`
(styles, the `.qml` files and `scripts/make_styles.py`: see M3-02)

## Out of scope
- Processing algorithms (Import ADIF, Recalculate), the controller and the plugin wiring
  (`connect_events`, `dataChanged`), the statistics dock.
- `CHANGELOG.md`, `PLAN.md`, `docs/ARCHITECTURE.md`, `README.md` (requests in Notes).

## Checklist
- [x] `ensure_gpkg(path)`: folder, file, `qso` (Point, EPSG:4326, `QSO_FIELDS` via
      `make_fields`), `qso_path` (MultiLineString, `PATH_FIELDS`), `hamq_meta`
      (`schema_version` = 1); UNIQUE index `qso_dedup_key_idx`, index
      `qso_path_qso_fid_idx`; idempotent (a complete file is only read)
- [x] Existing files: schema checked first (geometry type, EPSG:4326, registered tables,
      `hamq_meta` columns), a file HamQ cannot use is never changed (`GpkgError`); missing
      tables and columns are added; old duplicates get a plain index and a warning;
      version 0 is migrated, a newer version is kept with a warning (logged once)
- [x] `insert_qsos`: duplicates from the file and within the batch skipped; points only with
      a valid position; paths for QSOs with both points, linked by `qso_fid`; chunks of
      `chunk_size` (one transaction each); progress + cancel; one bad QSO never stops the
      rest (`failed` + warning); datetimes through `fields.to_qdatetime`, stored as UTC
- [x] Writes show up at once in loaded layers (main thread: `layers.refresh_layers`); layers
      in edit mode are left alone (written through a separate connection) and a warning
      says so; worker threads never touch the project
- [x] Writes from several threads take turns (a write lock per transaction); a file locked by
      another program is waited for (10 s, 1 s in the main thread)
- [x] `recalculate(path, station, cty=None, feedback=None, *, force_station=False)`:
      origin = `MY_LAT`/`MY_LON` in `adif_extra` > stored valid `my_gridsquare` >
      `station.grid` (documented); missing DXCC data and positions from cty; points follow a
      changed locator, hand-placed points are kept; paths rebuilt; returns updated count
- [x] `read_qso_rows` / `existing_dedup_keys`: plain SQL, no geometry, aware UTC datetimes
      (zone-less text counts as UTC); empty for a missing file, which is not created
- [x] `layers.py`: `find_layers` by source (HamQ group first, unfiltered next, then tree
      order; invalid layers skipped), `load_layers` (group on top, QSOs above paths,
      translated names, default style unless the GeoPackage has the user's default style,
      aliases, display expression), `refresh_layers`, `apply_field_aliases` (user aliases
      kept), `retranslate_layers`, `connect_events`, `matching_layers`
- [x] Slots never let exceptions reach Qt; `connect_events()` returns a disconnect that is
      safe to call twice
- [x] Serbian (Latin) catalogs (glossary; shared texts use the other catalogs' wording)
- [x] Tests on QGIS 4.2 (host), 3.44, 4.0 and 3.34 (Docker); stress tests of concurrent
      writes (scratch scripts, see Result)
- [x] Self-review (contract, AGENTS.md, skills, Qt5/Qt6, 3.34 API, threads, cleanup,
      strings)

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes (incl. the i18n catalog guard, also with
      `HAMQ_STRICT_I18N=1`)
- [x] `ruff check` / `ruff format --check` clean (`hamq tests`)
- [x] Re-import of the same QSOs: 0 inserted, all duplicates; within-batch duplicates; a QSO
      without position: no geometry, no path
- [x] 10 000 QSOs with paths in < 10 s (PLAN M2): 2.35 s (4.2), 2.92 s (3.44), 3.34 s (4.0),
      3.30 s (3.34)
- [x] `scripts/test_qgis.sh all -k "test_gpkg or test_layers or test_styles or test_compat"`
      passes on all four targets

## Result

### What changed
- `hamq/qgis_io/gpkg.py` (new). Contract names and signatures unchanged; additions:
  `GpkgError(Exception)` (translated message), `InsertResult` fields with defaults plus
  `paths: int` and `canceled: bool`, `existing_dedup_keys(path, keys=None)` (only those
  keys, indexed lookups), `recalculate(..., *, force_station=False)`, constants
  `SCHEMA_VERSION`, `META_TABLE`, `PATH_STEP_KM`.
- `hamq/qgis_io/layers.py` (new). Additions: `matching_layers(path)` (every qso/qso_path
  layer of the file), `retranslate_layers()`, `connect_events() -> disconnect`,
  `GROUP_NAME`, `LAYER_NAMES`, `FIELD_ALIASES`.
- `hamq/qgis_io/compat.py` / `tests/qgis/test_compat.py`, block "M2-03 / M3-02":
  `LABEL_PLACEMENT_OVER_POINT`, `LABEL_PROPERTY_SHOW`, `STYLE_CATEGORY_SYMBOLOGY`,
  `STYLE_CATEGORY_LABELING`, `BRUSH_NONE`, `DATE_FORMAT_ISO_MS`, each with a test.
- Catalogs `qgis_io_gpkg.json` (27 strings), `qgis_io_layers.json` (27 strings).
- `tests/qgis/test_gpkg.py` (91 tests), `tests/qgis/test_layers.py` (21 tests).

### How writes work (and why not through QGIS layers)
Rows are written with plain SQL on a SQLite connection of `gpkg.py`, one `BEGIN IMMEDIATE`
transaction per chunk; QGIS (GDAL) only creates the tables. Geometries are GeoPackage blobs
as GDAL writes them (a point blob is byte-identical to GDAL's, tested); GDAL's R-tree
triggers keep the spatial index, with `ST_IsEmpty` / `ST_MinX` .. `ST_MaxY` registered on
the connection (the only functions GDAL's triggers use on 3.34-4.2, checked). Each
transaction clears the cached feature counts / extents in `gpkg_ogr_contents` /
`gpkg_contents`, so GDAL recomputes them.

The first version wrote through private `QgsVectorLayer` providers (the spec's
`addFeatures`). Stress tests on QGIS 4.2 (scratch scripts: a worker-thread import while the
main thread starts and saves edit sessions of the loaded QSO layer hundreds of times):
- two providers writing the same file from two threads: "database is locked" failures, and
  one deadlock (both threads inside `addFeatures`);
- worker `addFeatures` / layer opening vs. main-thread `startEditing`: hangs (5 of 8 runs
  with a reopen-on-error variant), a segfault (1 of 8 without it), and connections stuck
  with "file is not a database" (4 of 8: the rest of the import lost its paths).
PyQGIS releases the GIL in these calls (checked), so this is inside QGIS's OGR provider
dataset sharing. With the SQLite writer: 8 of 8 runs on 4.2 and 2 each on 3.44, 4.0, 3.34
finished, no hang or crash, all 13 900 QSOs and 14 000 paths written, `integrity_check` ok.
Realistic case (one edit/save at a random moment during an import, 100 runs, provider
version): no hang; the import never failed.

### Behaviour callers can rely on
- `insert_qsos` returns `fids` in input order; a QSO without `dedup_key` fails (contract:
  the key is required). Datetimes: `to_qdatetime(dt).toUTC().toString(ISODateWithMs)` →
  `2026-09-15T18:45:07.250Z`; QGIS reads them as UTC `QDateTime`.
- Path attributes: `distance_km` / `bearing_deg` are the QSO's (spherical, core.geo);
  `mode` is the display mode (FT4 for MFSK/FT4). The geometry is the WGS84 geodesic.
- Main thread: insert/recalculate refresh loaded layers; a warning when a layer of the file
  is in edit mode. Worker thread: nothing in the project is touched; the caller emits
  `events().dataChanged(path)` from the main thread; `layers.connect_events()` refreshes.
- `recalculate` returns the number of QSO rows changed; canceled before writing → 0 and
  nothing changed. Warnings are logged and pushed to `feedback.pushWarning`.
- `refresh_layers` / `retranslate_layers` do nothing outside the main thread.

### Timings (scratch scripts and the 10k test)
| | 4.2 (host) | 3.44 | 4.0 | 3.34 |
|---|---|---|---|---|
| 10 000 QSOs with world-wide paths | 2.35 s | 2.92 s | 3.34 s | 3.30 s |
| `read_qso_rows`, 10 000 QSOs | 70 ms | 74 ms | 82 ms | 77 ms |
| one live QSO into a 10k log, layers loaded (main thread) | 7 ms | | | 20 ms |
| `recalculate`, 10 000 QSOs | 2.6 s | | | 3.4 s |
Re-importing 10 000 duplicates: 0.02 s. Live QSOs during a worker import: 16 ms average,
75 ms max, none failed.

### Commands and outcomes
- `scripts/test_qgis.sh all -q -rs -k "test_gpkg or test_layers or test_styles or test_compat"`
  (final run):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2.1 (Qt6) | PASS: 312 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 312 passed |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 312 passed |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 312 passed |

  (149 of them are mine: test_gpkg 91, test_layers 21, test_styles 37; plus test_compat.)
- `scripts/test_qgis.sh local -q`: whole `tests/qgis`, 871 passed, 2 skipped (live Hamlib
  daemon tests).
- `python3 -m pytest tests/core -q`: 3196 passed, 8 xfailed.
- `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q`: 261 passed.
- `ruff check hamq tests`, `ruff format --check hamq tests`: clean.

### Manual checks still needed
- Desktop QGIS 3.34 / 3.40 and 4.x with the plugin wired: Import ADIF → layers appear in
  the "HamQ" group; a WSJT-X QSO appears at once; switch the language: layer names,
  aliases and the "Other bands" legend entry change; a renamed layer keeps its name.
- Windows / macOS installs: Python's `sqlite3` must have the R-tree module (true on Linux
  and in the Docker images; OSGeo4W and the macOS bundles are expected to, not tested).

## Notes
- **GDAL message noise (not from HamQ code):** opening a second layer of a GeoPackage while
  a layer of the same file is in edit mode prints `ERROR 1: ... unable to open database
  file` (QGIS 3.34, 3.44, 4.2; reproduced with plain QGIS API calls). A QGIS "nolock" read
  connection prints the same when a writer removes the WAL file. Harmless; layers work.
- **Saving edits during an import:** while HamQ writes (an import, a recalculation), the
  user's save of an edit session of the same file can fail with "database is locked"
  (SQLite has one writer at a time; QGIS does not wait). Saving again works.
- **Stale counts:** a user's save after HamQ wrote through its own connection can store
  GDAL's stale feature count; the next HamQ write clears it.
- **For the integration (controller / plugin / algorithms):**
  - `plugin.add_cleanup(layers.connect_events())` (dataChanged → refresh_layers,
    languageChanged → retranslate_layers); call `layers.retranslate_layers()` after a
    project is read.
  - Algorithms: never write the log through QGIS layers in worker threads (see above); use
    `insert_qsos` / `recalculate`, emit `events().dataChanged(path)` in
    `postProcessAlgorithm`. Recalculate: expose `force_station` ("use my locator for all
    QSOs"). Before a long write, `prepareAlgorithm` (main thread) can warn when
    `layers.matching_layers(path)` has a layer in edit mode.
- **PLAN.md / ARCHITECTURE.md** (not edited, outside scope): the schema gained `hamq_meta`
  and two indexes (version 1); see contract_change_requests in the report.
- **CHANGELOG.md** (not edited): "GeoPackage QSO log: schema with version, deduplicated
  inserts, geodesic paths split at the antimeridian, recalculation for a new QTH or
  cty.dat, HamQ layer group with translated names and aliases (M2-03, M3-02)."
