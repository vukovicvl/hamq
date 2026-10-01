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
  `to_qdatetime(dt)` / `from_qdatetime(value)`. A Python `datetime` set as an
  attribute is never converted by QGIS: `QgsVectorFileWriter` rejects the
  feature on every version ("Could not convert value"), and the OGR provider
  (`layer.dataProvider().addFeatures`, the insert path of the future
  `gpkg.py`) reports success and silently stores NULL on 3.34, 3.40, 3.44
  and 4.0 and rejects the feature only on 4.2 ("wrong data type ... expected
  QDateTime"). A UTC `QDateTime` works on both paths and is stored as
  `2024-05-17T12:34:56Z` (4.x) / `...56.000Z` (3.x). (The first round's
  docstring said the provider rejects it too; corrected in the fix round.)
- `hamq/settings.py`: `HamQSettings` with all 19 contract properties as typed
  descriptors. Setting and reading go through the same steps: conversion to
  `bool`/`int`/`float`/`str`, normalization, validation; the setter raises
  `ValueError`, the getter returns the default when a step fails, so a
  hand-edited settings file gets the same treatment. Normalization: texts
  stripped, `my_call` uppercased, `my_grid` through
  `hamq.core.maidenhead.normalize` (10 characters cut to 8; text that is not a
  locator kept, stripped), `language` / `last_serbian` to the canonical codes
  (`" SR-latn "` -> `sr_Latn`). Invalid: ports outside 1..65535,
  `rig_poll_ms <= 0`, non-finite numbers, a language other than
  auto/en/sr_Latn/sr_Cyrl, a `last_serbian` other than sr_Latn/sr_Cyrl, and
  (since the fix round) an empty `gpkg_path`, `wsjtx_addr`, `rig_host` or
  `rot_host`. `station()` (lazy import of `core.qso.Station`),
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
  QBuffer/QFile), `test_fields.py` (incl. both datetime insert paths),
  `test_settings_events.py`, `test_plugin_load.py` (load/unload twice with no
  warning or critical log message, provider/menu/toolbar/actions and signal
  receivers restored, idempotent unload, `initProcessing` only and
  idempotent, retranslation, `refreshAlgorithms()` on a language change,
  Processing Toolbox visibility, cleanups, docks, About box, `add_action`
  options, provider algorithm list, `processing.run("hamq:dummy")`, icons),
  `tests/core/test_architecture.py` (AST scan of `hamq/core/**/*.py` incl.
  dynamic imports and imports of non-core HamQ modules, plus scanner
  self-tests; `from __future__ import annotations` in every `hamq/**/*.py`).

- `scripts/test_qgis.sh`: targets local / 3.44 / 4.0 / 3.34 / all; Docker
  runs mount the repo read-only as the calling user with `HOME=/tmp`,
  `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, offscreen Qt; on 3.34
  `pytest==8.3.5 PyYAML==6.0.2 psycopg2-binary==2.9.10` go to `/tmp/pylib`
  inside the container (the last two let the processing plugin load, as on
  desktop QGIS); `all` prints a summary table and fails if any target failed.
  shellcheck clean.

Review fix round (2026-09-30), findings of the M0 review:

- fields (should): reproduced the provider-path behaviour on 3.34.15,
  3.44.15, 4.0.3 and 4.2.1 (and 3.40.15 through the new test); docstring
  corrected; new tests `test_provider_add_features_stores_qdatetime` (write
  through `dataProvider().addFeatures` with `to_qdatetime`, read back the
  same UTC time) and `test_provider_add_features_never_stores_a_python_datetime`
  (records silent NULL on < 4.1 and rejection on 4.2, and that the value is
  never stored).
- settings (should + nit): reproduced `gpkg_path = ""` and `wsjtx_addr = "  "`
  being stored and read back as `""`, `language = "klingon"` accepted,
  `my_grid = "KN04FT12AB"` kept unchanged (core gives `KN04ft12`), and
  `last_serbian = " sr_Cyrl "` rejected. Fixed as described above; new
  parametrized cases in `test_stored_values_are_converted_or_defaulted` and
  `test_invalid_values_are_rejected` (which now also checks that nothing was
  stored), plus `test_empty_stored_gpkg_path_reads_as_default`,
  `test_language_is_stored_as_a_canonical_code`,
  `test_last_serbian_is_stripped`, `test_grid_normalization_matches_core`.
  26 of the new cases failed before the fix.
- Mutation check (scratch script, local QGIS 4.2.1): getter skipping
  normalization/validation, empty text allowed, grid not normalized by core,
  language not canonicalized, any language accepted, `last_serbian` not
  stripped, empty `gpkg_path` or `rot_host` allowed -> all killed.

Commands and outcomes (2026-09-30, after the fix round):

- `scripts/test_qgis.sh all -q` (PASS on every target):

  | target | environment | result |
  |---|---|---|
  | local | QGIS 4.2.1, Qt 6.10.2, Python 3.14 | 306 passed, 2 skipped |
  | 3.44 | qgis/qgis:3.44-trixie, Qt 5.15.15 | 306 passed, 2 skipped |
  | 4.0 | qgis/qgis:4.0-trixie, Qt 6.8.2 | 306 passed, 2 skipped |
  | 3.34 | camptocamp/qgis-server:3.34, Qt 5.15.3, Python 3.10 | 293 passed, 15 skipped |
  | (extra) | qgis/qgis:3.40, QGIS 3.40.15, Qt 5.15.13, Python 3.12, same docker flags | 306 passed, 2 skipped |

  Skips: 2 x `core/qso.py is not written yet` (settings `station()` and
  `make_fields(QSO_FIELDS)`), 13 x `QtSvg is not installed in this image`
  (3.34 server image only; the XML and size checks of the icons still run).
  Local 4.2.1 and 3.44 also pass with deprecation warnings as errors.
  (First round, 2026-09-29: 265 passed / 252 passed on 3.34.)
- `python3 -m pytest tests/core/test_smoke.py tests/core/test_architecture.py
  tests/core/test_package.py -q`: 99 passed, 2 xfailed on Python 3.14 and on
  Python 3.9.25 (Docker `python:3.9-slim`); the architecture tests are
  parametrized over the modules, so the count grows as modules are added.
- `tests/core/test_i18n_catalog.py` (project-wide i18n guard): 141 passed.
- Every Python file of this task compiles with Python 3.9.
- ruff 0.16.9 check and format on the 19 M0 Python files: clean.

Manual checks still needed: none for this task (no GUI beyond M0-01).

## Notes

- Contract change requests of the first round (fields datetime helpers,
  `connect_message_log`, the compat names, `ALGORITHMS`) are now in
  docs/ARCHITECTURE.md "Contract changes".
- New contract change requests (docs/ARCHITECTURE.md not edited):
  1. the `qgis_io/fields.py` entry says a Python `datetime` "is rejected by
     `QgsVectorFileWriter`"; true for the writer, but through the OGR provider
     (`layer.dataProvider().addFeatures`, the path gpkg.py will use) QGIS
     3.34, 3.40, 3.44 and 4.0 report success and silently store NULL, only 4.2
     rejects it. Suggested: "... rejected by `QgsVectorFileWriter`, and
     silently stored as NULL by the OGR provider (`addFeatures` reports
     success) on 3.34 - 4.0; 4.2 rejects it there".
  2. the `settings.py` entry: add that empty `gpkg_path`, `wsjtx_addr`,
     `rig_host`, `rot_host` and a `language` other than auto/en/sr_Latn/sr_Cyrl
     are invalid; language codes are stored canonical (case, `-`/`_` and
     surrounding blanks ignored); `my_grid` is normalized with
     `core.maidenhead.normalize` (non-locator text kept, stripped); getters
     apply the same normalization and validation to stored values.
- The memory provider ignores `REQUEST_NO_GEOMETRY` (features keep their
  geometry); the OGR / GeoPackage provider honors it. Tests use a GeoPackage.
- `qgis.PyQt.QtGui.QAction` exists on QGIS 3.34 too (QGIS re-exports it), but
  `QActionGroup` does not: use `compat.QActionGroup` for language menus.
- **Release pass (2026-10-01):**
  - New compat names (docs/ARCHITECTURE.md "Contract changes"): `TEXT_PLAIN`,
    `LOG_PANEL_SHOWS_HTML` (QGIS 3.34 to 3.40.6 and 3.42.0 / 3.42.1 render Log Messages
    as HTML), `SOCKET_ERROR_CONNECTION_REFUSED`, `SOCKET_ERROR_HOST_NOT_FOUND`,
    `SOCKET_ERROR_TIMEOUT`, `SOCKET_ERROR_ADDRESS_NOT_AVAILABLE`, `NET_CONNECTION_REFUSED`,
    `NET_HOST_NOT_FOUND`, `NET_TIMEOUT`, `NET_TEMPORARY_NETWORK_FAILURE`,
    `NET_NETWORK_SESSION_FAILED`, `NET_UNKNOWN_NETWORK_ERROR`, each with a test.
  - Contract request 1 above moved again: QGIS 4.2.3 (`qgis/qgis:4.2-trixie`, image of
    2026-09-27) reports success for a Python `datetime` through the OGR provider and
    stores NULL, like 3.34 to 4.0; 4.2.1 (host) rejects it.
    `tests/qgis/test_fields.py::test_provider_add_features_never_stores_a_python_datetime`
    asserted the 4.2.1 behaviour for every 4.2 (`>= 40200: assert not ok`) and failed on
    4.2.3 (1 failed, 1084 passed, 3 skipped in that image), so the CI job on
    `qgis/qgis:4.2-trixie` would have been red. HamQ itself is not affected (it writes
    datetimes as `to_qdatetime` text through SQLite). Fixed in the docs pass: from 4.1 on
    the test accepts both outcomes and asserts only that the time is lost (NULL stored or
    the feature rejected); `test_fields.py` passes on 4.2.1 (host), 4.2.3, 4.0, 3.44 and
    3.34, and the whole `tests/qgis` on 4.2.3 with it.
  - Full `tests/qgis` in the release pass: `qgis/qgis:3.34` (3.34.15) and `qgis/qgis:3.40`
    (3.40.15) 1085 passed, 3 skipped each (the two live-Hamlib tests and the privileged
    port check); the `scripts/test_qgis.sh all` results are in INT-01.
