# M0-02: QGIS compatibility layer, settings, events and QGIS test harness

**Milestone:** M0
**Status:** done
**Skills:** pyqgis-plugin

## Goal
One place (`qgis_io/compat.py`) that resolves every enum and class whose name
differs between QGIS 3.34, 3.44, 4.0, 4.2 and Qt5 / Qt6; field helpers,
typed settings and process-wide signals per docs/ARCHITECTURE.md; a QGIS test
harness that runs `tests/qgis` on the local QGIS 4.x and in Docker (3.44,
4.0, 3.34) with one command.

## Scope
- `hamq/qgis_io/compat.py`, `hamq/qgis_io/fields.py`
- `hamq/settings.py`, `hamq/events.py`
- `hamq/i18n/sr_Latn/*.json` of these modules (none needed: no user-visible strings)
- `tests/core/test_architecture.py`
- `tests/qgis/conftest.py`, `tests/qgis/test_plugin_load.py`, `tests/qgis/test_compat.py`,
  `tests/qgis/test_fields.py`, `tests/qgis/test_settings_events.py`
- `scripts/test_qgis.sh`

## Out of scope
- Features (GeoPackage schema, layers, docks, network code).
- Contract changes (requested in the Result instead).

## Checklist
- [x] compat: WKB_POINT, WKB_LINESTRING, WKB_MULTILINESTRING, WKB_POLYGON (+ MULTIPOLYGON, NO_GEOMETRY)
- [x] compat: processing source types, file behavior File (+ Folder), number types Integer/Double,
      algorithm flag NoThreading (+ parameter flags Optional/Advanced)
- [x] compat: MSG_INFO / MSG_WARNING / MSG_CRITICAL / MSG_SUCCESS
- [x] compat: QgsVectorFileWriter actions (CreateOrOverwriteFile / CreateOrOverwriteLayer /
      AppendToLayerNoNewFields) and WriterError NoError; sink FastInsert; request NoGeometry
- [x] compat: QAction (and QActionGroup), Qt scoped enums for docks, dialog and message box
      buttons, header resize modes, edit triggers, size policies, alignment, host addresses,
      socket bind flags and states, network request attributes, network reply errors,
      QIODevice open modes; each one documented
- [x] compat: every name tested for value and type on 3.34, 3.44, 4.0 and 4.2
- [x] fields: `make_field` / `make_fields` (QMetaType on >= 3.38, QVariant before), GeoPackage
      round trip with every kind, UTC datetime included
- [x] settings: every contract property (incl. the M7 rig/rot keys) with defaults, round trip,
      invalid values, `profile_dir()`, `default_gpkg_path()`, `cty_cache_path()`, `station()`
- [x] events: singleton QObject with dataChanged / languageChanged / settingsChanged
- [x] conftest: offscreen session app, throw-away profile, Processing initialized, repo root
      importable, fixtures, clean exit with pytest's exit status
- [x] test_plugin_load: classFactory -> initGui -> checks -> unload -> checks, twice
- [x] `scripts/test_qgis.sh <local|3.44|4.0|3.34|all>` with summary table
- [x] architecture test: no qgis / PyQt / sip imports in `hamq/core`

## Acceptance criteria
- [x] `pytest tests/core -q` passes for the tests of this task
- [x] `ruff check` / `ruff format --check` pass on the files of this task
- [x] `scripts/test_qgis.sh all` passes (skips only where the 3.34 server image lacks a module)
- [x] Plugin loads and unloads cleanly twice on each version

## Result

What changed (new files):

- `hamq/qgis_io/compat.py`: 97 resolved enum values plus `QAction` / `QActionGroup`
  (QtGui first, QtWidgets fallback; 3.34 does not re-export `QActionGroup`
  from QtGui), `QGIS_VERSION_INT`, `QT_MAJOR`, `IS_QT6`,
  `FIELD_TYPES_USE_QMETATYPE`, `FIELD_TYPE_INT/REAL/TEXT/DATETIME`. Each name
  prefers the scoped `Qgis.*` enum and falls back to the legacy spelling via
  `_resolve(name, *candidates)`; an unresolvable name becomes `None` and is
  listed in `MISSING` (the plugin still loads); `NAMES` lists all names.
  Findings from probing all four versions: `Qgis.ProcessingSourceType`,
  `ProcessingFileParameterBehavior`, `ProcessingNumberParameterType`,
  `ProcessingAlgorithmFlag`, `ProcessingParameterFlag` and
  `FeatureRequestFlag` do not exist on 3.34; unscoped Qt names
  (`Qt.AlignLeft`) are gone on Qt6 while scoped names work on PyQt5 5.15;
  `QNetworkRequest.FollowRedirectsAttribute` is gone on Qt6 (use
  `NET_ATTR_REDIRECT_POLICY`, value 25 on Qt5 / 22 on Qt6).
  Also `connect_message_log(slot) -> disconnect`: QGIS 4.0+ emits only
  `QgsMessageLog.messageReceivedWithFormat(message, tag, level, format)`, the
  three-argument `messageReceived` is no longer emitted there.
- `hamq/qgis_io/fields.py`: `make_field`, `make_fields`, `FIELD_KINDS`, and
  `to_qdatetime(dt)` / `from_qdatetime(value)`: on every version a Python
  `datetime` set as an attribute is rejected by `QgsVectorFileWriter`
  ("Could not convert value"), a UTC `QDateTime` works and is stored as
  `2024-05-17T12:34:56Z` (4.x) / `...56.000Z` (3.x).
- `hamq/settings.py`: `HamQSettings` with all 19 contract properties as typed
  descriptors (`bool`/`int`/`float`/`str` conversion of stored text, invalid
  or out-of-range stored values read as the default, setters raise
  `ValueError` on invalid input, `my_call` uppercased, `my_grid` in
  Maidenhead case), `station()` (lazy import of `core.qso.Station`),
  `profile_dir()`, `default_gpkg_path()`, `cty_cache_path()`,
  `SETTINGS_PREFIX`.
- `hamq/events.py`: `HamQEvents` and thread-safe `events()` singleton, moved
  to the application thread if first created elsewhere.
- `tests/qgis/conftest.py`: starts QGIS with `qgis.testing.start_app` in
  `pytest_configure` (historic hook, so `pytest tests` works too), refuses to
  run unless the profile is the throw-away `QGIS_CUSTOM_CONFIG_PATH`, adds the
  processing plugin directory (found next to the `qgis` package on 3.34) and
  initializes Processing, header with QGIS/Qt/Python/Processing status, ends
  with `os._exit(session.exitstatus)` after stopping capture, removing the
  profile and flushing (checked: exit 1 on a failing test, 5 when nothing is
  collected; `HAMQ_TEST_NO_OS_EXIT=1` disables it). Fixtures: `qgis_app`,
  `qgis_processing` (skips when unavailable), `iface`
  (`mocked.get_iface()` with `spec=QgisInterface`, wired to a `FakeIface`
  with a real `QMainWindow`, menus, toolbars and docks; the fake alone when
  the mock is unavailable), `tmp_gpkg`, `clean_settings`, `clean_project`,
  `process_events` (includes deferred deletes), `log_messages`.
- Tests: `test_compat.py` (value and type table for every name, plus a
  functional use of each group: layers, parameters, flags, writer actions,
  message levels, widgets, UDP bind/state/address-in-use, network request,
  QBuffer/QFile), `test_fields.py`, `test_settings_events.py`,
  `test_plugin_load.py` (load/unload twice, provider/menu/toolbar/actions and
  signal receivers restored, idempotent unload, `initProcessing` only,
  retranslation, cleanups, docks, About box, provider algorithm list,
  `processing.run("hamq:dummy")`, icons), `tests/core/test_architecture.py`
  (AST scan of `hamq/core/**/*.py` incl. dynamic imports and imports of
  non-core HamQ modules, plus scanner self-tests).
- `scripts/test_qgis.sh`: targets local / 3.44 / 4.0 / 3.34 / all; Docker
  runs mount the repo read-only as the calling user with `HOME=/tmp`,
  `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, offscreen Qt; on 3.34
  `pytest==8.3.5 PyYAML==6.0.2 psycopg2-binary==2.9.10` go to `/tmp/pylib`
  inside the container (the last two let the processing plugin load, as on
  desktop QGIS); `all` prints a summary table and fails if any target failed.
  shellcheck clean.

Commands and outcomes (2026-09-29):

- `scripts/test_qgis.sh all -q`:

  | target | environment | result |
  |---|---|---|
  | local | QGIS 4.2.1, Qt 6.10.2, Python 3.14 | 265 passed, 2 skipped |
  | 3.44 | qgis/qgis:3.44-trixie, Qt 5.15.15 | 265 passed, 2 skipped |
  | 4.0 | qgis/qgis:4.0-trixie, Qt 6.8.2 | 265 passed, 2 skipped |
  | 3.34 | camptocamp/qgis-server:3.34, Qt 5.15.3, Python 3.10 | 252 passed, 15 skipped |

  Skips: 2 x `core/qso.py is not written yet` (settings `station()` and
  `make_fields(QSO_FIELDS)`), 13 x `QtSvg is not installed in this image`
  (3.34 server image only; the XML and size checks of the icons still run).
- `python3 -m pytest tests/core/test_smoke.py tests/core/test_architecture.py
  tests/core/test_package.py -q`: 73 passed on Python 3.14 and on Python 3.9
  (Docker `python:3.9-slim`); the architecture test is parametrized over the
  core modules, so the count grows as modules are added.
- Every Python file of this task compiles with Python 3.9.
- ruff 0.16.9 check and format: clean.

Manual checks still needed: none for this task (no GUI beyond M0-01).

## Notes

- Contract change requests (docs/ARCHITECTURE.md not edited):
  1. add `to_qdatetime(dt)` and `from_qdatetime(value)` to `qgis_io/fields.py`
     (gpkg.py must convert `qso_datetime` with them, a Python `datetime`
     attribute is rejected by the writer on all versions);
  2. add `connect_message_log(slot)` to `qgis_io/compat.py` (log listeners
     must use it on QGIS 4);
  3. document the compat names (see the module; notably `SOURCE_VECTOR_*`,
     `FILE_BEHAVIOR_FILE`, `NUMBER_INTEGER` / `NUMBER_DOUBLE`,
     `ALG_FLAG_NO_THREADING`, `WRITER_*`, `SINK_FAST_INSERT`,
     `REQUEST_NO_GEOMETRY`, `DOCK_*`, `DIALOG_*`, `MSGBOX_*`, `HOST_*`,
     `BIND_*`, `SOCKET_*`, `NET_*`, `IO_*`);
  4. `processing/provider.py` registers algorithms from the module-level list
     `ALGORITHMS`; the M1..M5 algorithm classes must be appended there.
- The memory provider ignores `REQUEST_NO_GEOMETRY` (features keep their
  geometry); the OGR / GeoPackage provider honors it. Tests use a GeoPackage.
- `qgis.PyQt.QtGui.QAction` exists on QGIS 3.34 too (QGIS re-exports it), but
  `QActionGroup` does not: use `compat.QActionGroup` for language menus.
