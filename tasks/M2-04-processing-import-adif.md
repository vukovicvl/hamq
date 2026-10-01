# M2-04: Processing algorithms "Import ADIF" and "Recalculate"

**Milestone:** M2 (Recalculate: M3/M4)
**Status:** done
**Skills:** pyqgis-plugin, adif, geodesy, dxcc-cty

## Goal
Import an ADIF log into the HamQ GeoPackage from the Processing toolbox, scripts and
`qgis_process`, with deduplication, cty.dat lookups and a clear report. Recalculate
distances, bearings, paths and missing DXCC data after the QTH locator or cty.dat changed.
After a run, `events().dataChanged(path)` refreshes the panel and the layers. PLAN M2:
10 000 QSOs in < 10 s; importing again adds nothing; a bad record is skipped and logged.

## Scope
- `hamq/processing/alg_import_adif.py`, `hamq/processing/alg_recalculate.py` (new)
- `hamq/processing/common.py` (shared with M1-03; see M1-03 Notes: deviation)
- `hamq/processing/provider.py` (registration, M1-03)
- `hamq/i18n/sr_Latn/processing_alg_import_adif.json`, `processing_alg_recalculate.json`
- `tests/qgis/test_processing.py` (shared with M1-03)
- `tasks/M2-04-processing-import-adif.md`

## Out of scope
- `core/adif.py`, `core/qso.py`, `qgis_io/gpkg.py`, `qgis_io/layers.py`,
  `net/cty_download.py` (used as they are; no core bug found).
- Controller / plugin wiring (dock "Import ADIF..." button, `layers.connect_events()`).
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md`: requests in Notes.

## Checklist
- [x] `hamq:import_adif` parameters:
      - `INPUT`: file, ADIF filter plus "All files", because Processing matches patterns
        case-sensitively;
      - `GPKG`: file destination, default `HamQSettings.gpkg_path` read in `initAlgorithm`;
      - `MY_GRID`: optional, default `HamQSettings.my_grid`, validated in
        `checkParameterValues` and again in `processAlgorithm`; empty allowed;
      - `USE_CTY`: default True;
      - `LOAD_LAYERS`: default True, an addition.
- [x] `read_adi` -> `records_to_qsos(station, cty, source="adif:<file name>")` ->
      `ensure_gpkg` -> `insert_qsos(feedback)`. Progress runs over 3 steps; cancel is
      checked between the steps and between the insert chunks.
- [x] `USE_CTY` -> `net.cty_download.load_cached_cty()`. A missing cty.dat gives a warning
      that suggests downloading it in the HamQ settings.
- [x] Outputs `IMPORTED`, `DUPLICATES`, `SKIPPED`, `GPKG`. `SKIPPED` = records without CALL
      or a valid date/time, plus QSOs `insert_qsos` could not save.
- [x] Warnings of the parser, the conversion and the insert: the first 20 go to the
      feedback, then "More warnings ...: N", and all of them go to the QGIS message log as
      one message (tag HamQ).
- [x] `postProcessAlgorithm` (main thread) does two things:
      - `layers.load_layers(GPKG)` when `LOAD_LAYERS` is set, in the main thread, and not
        in `qgis_process`;
      - `events().dataChanged(GPKG)`, guarded so that it never raises (scripts,
        `qgis_process`).
- [x] Canceled import: the QSOs saved before stay and are announced at once. Processing
      skips `postProcessAlgorithm` for a canceled task; from a worker thread Qt queues the
      signal into the main thread. It is never sent twice.
- [x] Layers in edit mode are checked in `prepareAlgorithm` (main thread). The warning of
      `insert_qsos` / `recalculate` is added when they run in a worker thread, where they
      cannot check; it appears exactly once.
- [x] `hamq:recalculate`:
      - parameters `GPKG` (existing file), `MY_GRID`, `USE_CTY` and `FORCE_STATION`
        (addition: `gpkg.recalculate(force_station=True)`, "use my locator for every QSO");
      - `qgis_io.gpkg.recalculate` -> `UPDATED` (and the output `GPKG`);
      - `dataChanged`;
      - a missing file is an error and is not created.
- [x] Strings through `tr()`. Group "QSO log" (stable id `log`), icons `import_adif.svg`
      and `refresh.svg`. Serbian (Latin) catalogs; the shared texts are identical to those
      of `qgis_io_gpkg.json` / `gui_settings_dialog.json`.
- [x] Tests (fixtures `tests/fixtures/adif`, cty excerpt through a monkeypatched
      `load_cached_cty`), performance tests marked slow, `qgis_process` smoke test;
      self-review

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes; `ruff check` / `ruff format --check` clean
- [x] 10 000 QSOs (generated with `core.adif.format_document`) in < 10 s:
      2.82 s (4.2), 3.34 s (3.44), 3.42 s (4.0), 3.66 s (3.34);
      importing the file again takes 0.44-0.65 s and adds 0 (10 000 duplicates)
- [x] A bad record is skipped, counted in `SKIPPED` and logged, and the import goes on
      (`no_eoh.adi`: 2 imported, 3 skipped)
- [x] `scripts/test_qgis.sh all -k test_processing` passes on all four targets

## Result

### What changed
- `alg_import_adif.py`: `ImportAdifAlgorithm` (`hamq:import_adif`).
- `alg_recalculate.py`: `RecalculateAlgorithm` (`hamq:recalculate`).
- `common.py` (with M1-03): `add_station_parameters`, `station_grid` /
  `check_station_grid`, `load_cty`, `report_warnings` (`FEEDBACK_WARNINGS = 20`),
  `notify_data_changed`, `editing_layers`, `in_main_thread`, `is_qgis_process`
  (`QgsApplication.platform() == "qgis_process"`, checked inside `qgis_process`),
  `settings_value`.
- Catalogs: `processing_alg_import_adif.json` (25), `processing_alg_recalculate.json` (13).
- `tests/qgis/test_processing.py`, M2 part:
  - WSJT-X log: 6 QSOs; FT4 as MFSK/FT4; Sicily -> Italy from cty.dat; /MM without
    position; 5 paths; distances equal to `core.geo`.
  - Import twice -> 6 duplicates; the LoTW confirmations are duplicates too.
  - `no_eoh.adi`: 3 skipped, and the log report names them.
  - Without cty.dat (warning) and with `USE_CTY` off (cty.dat not loaded).
  - N1MM positions from cty.dat and my QTH from `MY_GRID` (also lowercase); empty
    `MY_GRID` gives no distances.
  - Invalid `MY_GRID` (both checks); missing file; file without records.
  - Defaults from `HamQSettings`.
  - Warnings: 20 to the feedback, 30 to the log.
  - `LOAD_LAYERS` (idempotent).
  - Background `QgsProcessingAlgRunnerTask`: `processAlgorithm` in a worker,
    `dataChanged` in the main thread.
  - Cancel through `processing.run` and through the task: QSOs kept, one notification in
    the main thread.
  - Edit-mode warning exactly once in the main thread and in the worker.
  - 10 000 QSOs (slow).
  - Recalculate:
    - after setting my locator;
    - keeping the logged QTH, and `FORCE_STATION`;
    - DXCC data filled from cty.dat;
    - background task;
    - `LOG.GPKG` file name;
    - missing GeoPackage;
    - invalid locator;
    - edit mode.
  - Notification guard.
  - `qgis_process`: `plugins enable hamq`, then `import_adif`, `locator_to_point` and
    `recalculate` with `--json`.
- Main-thread work after an import of 10 000 QSOs: `load_layers` 40 ms the first time, 2 ms
  when the layers are loaded already, notification < 1 ms (4.2).

### Commands and outcomes
- `scripts/test_qgis.sh all -q -p no:cacheprovider -rs -k test_processing` (final run):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2.1 (Qt 6.10) | PASS: 65 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 65 passed |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 65 passed |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 64 passed, 1 skipped (`qgis_process` cannot start in that image: missing `libQt53DExtras.so.5`) |

  `qgis_process` itself was checked by hand in the 3.44 and 4.0 images too: `plugins enable
  hamq` and `--json run hamq:import_adif` -> `IMPORTED 6`.
- `scripts/test_qgis.sh all -s -k "test_processing and (under_2_s or under_10_s)"`: timings
  above.
- `scripts/test_qgis.sh local -q` (whole `tests/qgis`): 933 passed, 2 skipped, 2 failed in
  `test_plugin_load.py` (M0 scaffold assumes no algorithms; see M1-03 Notes).
- `python3 -m pytest tests/core -q`: 3236 passed, 8 xfailed; strict i18n guard: passed.
- `ruff check hamq tests`, `ruff format --check hamq tests`: clean.

### Manual checks still needed
- QGIS desktop 3.34 and 4.x with the plugin loaded:
  - run "Import ADIF" from the toolbox on a real log (WSJT-X, N1MM, LoTW);
  - the dialog stays responsive and the progress bar shows the three steps;
  - the HamQ layers appear in the "HamQ" group and the panel refreshes;
  - cancel a large import: the saved QSOs show up;
  - run "Recalculate" after changing the QTH locator in the settings;
  - run Import with the QSO layer in edit mode: the warning, then the QSOs after saving.
- Picking an existing GeoPackage with the "..." button of the file destination: the save
  dialog asks whether to replace the file. Answering yes appends (nothing is replaced).
  Same as QGIS's own "Package layers". Check that the wording is acceptable or document it.

## Notes
- **Integration (controller / plugin owner):**
  - The dock's `importRequested` -> `processing.execAlgorithmDialog("hamq:import_adif", {})`
    (defaults come from the settings).
  - Connect `layers.connect_events()` (`plugin.add_cleanup(...)`) so `dataChanged` refreshes
    the layers.
  - Pass `LOAD_LAYERS=False` if the controller loads the layers itself;
    `layers.load_layers` is idempotent anyway.
  - "Recalculate" fits after the settings dialog changed `my_grid` or after a cty.dat
    download.
- `GPKG` of the import is a `QgsProcessingParameterFileDestination` ("output file"):
  - no "open after running" option is offered for it (checked with the QGIS 4.2 widget);
  - an existing file is appended to;
  - "Save to a temporary file" would import into a throw-away GeoPackage.
- Additions to the contract's parameter lists (optional, with defaults):
  - `LOAD_LAYERS` (import);
  - `FORCE_STATION` (recalculate);
  - output `GPKG` of recalculate.
  See contract_change_requests in the report.
- **CHANGELOG.md** (not edited): "Processing: Import ADIF (deduplicated, cty.dat, report of
  skipped records, layers added to the project) and Recalculate distances and DXCC data
  (M2-04)."
