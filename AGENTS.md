# AGENTS.md

Instructions for AI coding agents working in this repository.

## Project

HamQ is a QGIS plugin for amateur radio operators. It maps QSO logs (ADIF),
works with Maidenhead locators, resolves DXCC entities from callsigns,
receives live QSOs from WSJT-X (and JTDX) over UDP and controls the radio and
the rotator through Hamlib `rigctld` / `rotctld`. The interface switches
between English, Srpski (latinica) and Српски (ћирилица) without restarting
QGIS. The first release is v0.1.0 (experimental); changes are in `CHANGELOG.md`.

The plan and milestones are in `PLAN.md`. Work items are in `tasks/`. The
contract between modules (public names and signatures, translation rules,
Serbian glossary, `## Contract changes`) is in `docs/ARCHITECTURE.md`.

## Workflow

1. Read `PLAN.md`, `docs/ARCHITECTURE.md` and the task file you were given in `tasks/`.
2. Read the relevant skills in `.github/skills/` before writing code. They were
   written before the code: where a skill differs from `docs/ARCHITECTURE.md`
   (section "Skills and this contract"), the contract and the code win. Do not
   "fix" the implementation back to a skill.
3. Work only inside the scope of the task file. If something outside the
   scope is broken, note it at the end of the task file under `## Notes`.
4. Write or update tests first for anything in `hamq/core/`.
5. Run the checks (see Commands). Do not report a task as done if they fail.
6. Update the task file: tick the checklist, fill `## Result`.

## Architecture rules

- `hamq/core/` is pure Python 3.9+. **No `qgis` or `PyQt` imports there.**
  All parsing, math and protocol decoding lives here and is tested with pytest.
  Core never raises on bad input from files or the network: it returns `None`
  plus a translated warning (`ValueError` only for programmer errors).
- Every module has `from __future__ import annotations` after its docstring and
  runs on Python 3.9 (no `match`, no runtime `X | Y`). `tests/core/test_architecture.py`
  checks the future import and the core imports.
- `hamq/qgis_io/` converts core objects to QGIS layers and features (`compat`,
  `fields`, `gpkg`, `layers`, `styles`).
- All writes to the QSO log go through `qgis_io/gpkg.py` (`insert_qsos`,
  `recalculate`), which writes with plain SQLite on its own connection, one
  transaction per chunk. Never write the log through QGIS layers or data
  providers from a worker thread: on QGIS 4.2 that deadlocked or crashed in
  stress tests while the main thread saved an edit session of the same file.
  A worker-thread caller emits `events().dataChanged(path)` from the main
  thread (`postProcessAlgorithm`, `QgsTask.finished`).
- `hamq/processing/` holds the Processing provider (`hamq`) and algorithms.
  Algorithms derive from `common.HamQAlgorithm` and are registered by appending
  their class to `ALGORITHMS` in `provider.py`.
- `hamq/gui/` holds docks, dialogs and map tools. Keep logic out of GUI code.
  Every widget with text implements `retranslate()` and follows
  `events().languageChanged`.
- `hamq/net/` holds network code (cty.dat download, WSJT-X UDP listener, Hamlib
  `rigctld` / `rotctld` TCP clients). Asynchronous only (Qt sockets,
  `QgsNetworkAccessManager`, `QTimer`), never a blocking `waitFor*()`.
- `hamq/plugin.py` stays thin: it creates `HamQController` (`controller.py`, the
  glue between GUI, storage and network) and registers everything through
  `add_action`, `add_dock_widget`, `connect_signal` and `add_cleanup`, so
  `unload()` undoes it all. Extend through this registry, never bypass it.
- `hamq/settings.py` (`HamQSettings`, typed `QgsSettings` keys under `hamq/`)
  and `hamq/events.py` (`events()`: `dataChanged`, `languageChanged`,
  `settingsChanged`) connect the parts.
- No third-party packages. Stdlib plus what ships with QGIS only.

## Qt5 / Qt6 compatibility

The plugin must run on QGIS 3.34+ (Qt5) and QGIS 4.x (Qt6). Tests run on 3.34,
3.44, 4.0 and 4.2 (CI), and on 3.40 before a release.

- Import Qt only via `qgis.PyQt` (`from qgis.PyQt.QtCore import ...`).
- Use fully scoped enums: `Qt.AlignmentFlag.AlignLeft`, not `Qt.AlignLeft`.
- Use `dialog.exec()`, not `exec_()`.
- Every enum, constant or class whose name differs between QGIS 3.34 .. 4.x or
  Qt5 / Qt6 is resolved once in `hamq/qgis_io/compat.py` (the scoped `Qgis.*`
  name, else the 3.34 name). Import it from there
  (`from ..qgis_io.compat import MSG_WARNING, WKB_POINT, QAction`); a missing
  one is appended there with a test in `tests/qgis/test_compat.py`.
- `QAction` and `QActionGroup` come from `compat` (QtGui in Qt6, QtWidgets in
  Qt5; QGIS 3.34 does not export `QActionGroup` from `qgis.PyQt.QtGui`).
- Create fields with `qgis_io/fields.py` `make_field` / `make_fields`
  (`QMetaType` on 3.38+, `QVariant` before).
- Watch the message log with `compat.connect_message_log(slot)`: QGIS 4 emits
  only `messageReceivedWithFormat`, QGIS 3 only `messageReceived`.

## Conventions

- Python style: PEP 8, type hints on public functions, docstrings on public API.
- Format with `ruff format`, lint with `ruff check`.
- All times are timezone-aware UTC, stored as ISO 8601 UTC text in the GeoPackage
  (`2026-09-15T18:45:00.000Z`). Convert datetime attributes only with
  `qgis_io/fields.py` `to_qdatetime` / `from_qdatetime`: a Python `datetime` in
  `setAttributes` is rejected or silently stored as NULL, depending on the QGIS version.
- Callsigns are stored uppercase and stripped.
- Coordinates are EPSG:4326, lat/lon order only in core code, x/y order in QGIS code.
- User-facing strings go through `tr()` from `hamq.core.i18n` (a Qt class may
  define `def tr(self, text): return tr(text)`), never `QCoreApplication.translate`
  or `QObject.tr`. The argument is one plain string literal; format after
  translating: `tr("QSO: {count}").format(count=n)`. A text needed at import time
  is marked with `tr_noop("...")` and translated where it is shown.
- A module's strings go into its own catalog `hamq/i18n/sr_Latn/<package>_<module>.json`
  (`core_adif.json`, `gui_dock.json`, `plugin.json`): English source -> Serbian
  Latin, keys sorted; glossary and style rules in `docs/ARCHITECTURE.md`. Only
  Latin is stored; Cyrillic is derived at run time.
- Never block the UI thread for more than ~100 ms. Long work runs in a Processing
  algorithm or a `QgsTask`, network code is asynchronous. Worker threads never
  touch the project, layers or widgets.
- Log with `QgsMessageLog.logMessage(msg, "HamQ", level)` (`compat.MSG_*`), not
  `print`. A Qt slot never lets an exception reach Qt: it logs it instead.
- Text from files or the network (calls, client ids, error texts) is shown as plain
  text: labels use `compat.TEXT_PLAIN`, message bar texts are HTML-escaped, log
  lines are escaped where `compat.LOG_PANEL_SHOWS_HTML`.

## Commands

```bash
# unit tests (core, no QGIS needed)
python3 -m pytest tests/core -q

# the same on Python 3.9, the oldest supported version (Docker)
docker run --rm -v "$PWD":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim \
  sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core -q"

# translation catalogs, strict: unused catalog keys fail too
HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q

# lint and format
ruff check hamq tests scripts
ruff format --check hamq tests scripts

# QGIS integration tests (tests/qgis): local QGIS 4.x, Docker QGIS 3.44 / 4.0 / 3.34, or all
scripts/test_qgis.sh local
scripts/test_qgis.sh all                 # extra arguments go to pytest, e.g. -k compat

# link plugin into local QGIS profile for manual testing (Linux)
ln -sfn "$PWD/hamq" ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/hamq   # QGIS 4.x
ln -sfn "$PWD/hamq" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/hamq   # QGIS 3.x

# build the plugin zip dist/hamq-<version>.zip (--release adds the release checks)
python3 scripts/package.py
python3 scripts/package.py --release

# plugins.qgis.org scan of a built zip (needs bandit~=1.9 detect-secrets~=1.5 flake8~=7.3 flake8-json~=24.4)
python3 scripts/qgis_repo_scan.py        # --zip PATH scans a given zip; Docker command in docs/RELEASING.md

# demo log and README screenshots (QGIS desktop, offscreen)
python3 scripts/make_demo_log.py         # --check: is docs/demo/demo_log.adi up to date?
python3 scripts/make_screenshots.py
```

Releases follow `docs/RELEASING.md` (all checks, also the whole `tests/qgis` on
QGIS 3.40; version, changelog, zip, publishing).

## Definition of done

- Task checklist complete.
- `pytest tests/core`, the strict catalog test (`HAMQ_STRICT_I18N=1`), `ruff check`
  and `ruff format --check` pass.
- New core code has tests, including at least one edge case.
- No new `qgis`/`PyQt` import inside `hamq/core/`.
- New user-visible strings are in the module's catalog.
- QGIS-side changes: `scripts/test_qgis.sh all` passes on all four targets
  (local 4.x, 3.44, 4.0, 3.34).
- Plugin loads without errors in QGIS (manual check noted in task file if GUI changed).
- `CHANGELOG.md` updated under `## [Unreleased]` (when it is outside the task scope,
  suggest the entry under `## Notes`).

## Do not

- Do not add dependencies or vendor external code without asking.
- Do not bundle `cty.dat` / `cty.csv` in the repository or the plugin zip (users
  download them; only the test excerpt in `tests/fixtures/cty/` is committed).
- Do not change the GeoPackage schema without updating `PLAN.md` and adding a migration
  (raise `gpkg.SCHEMA_VERSION`, add a step to `gpkg._migrate`).
- Do not change or remove a public name or signature listed in `docs/ARCHITECTURE.md`;
  record an additive change under its `## Contract changes` with the reason.
- Do not write the QSO log through QGIS layers or data providers (see Architecture rules).
- Do not remove `supportsQt6=True` from `metadata.txt`: plugins.qgis.org warns that the
  key is deprecated, but QGIS 3.x builds on Qt6 need it, and the tests assert it.
- Do not hand-edit generated files: `hamq/resources/styles/*.qml`
  (`scripts/make_styles.py`, run on QGIS 3.34), the WSJT-X golden packets in
  `tests/fixtures/wsjtx/` (`scripts/make_wsjtx_fixtures.py`), `docs/demo/demo_log.adi`
  (`scripts/make_demo_log.py`).
- Do not rewrite unrelated files for style.
