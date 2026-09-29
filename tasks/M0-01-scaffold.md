# M0-01: Repository and plugin scaffold

**Milestone:** M0
**Status:** done
**Skills:** pyqgis-plugin

## Goal
A plugin that loads in QGIS 3.40 and 4.x, shows a HamQ menu and toolbar,
and registers an empty Processing provider `hamq`.

## Scope
- `hamq/__init__.py`, `hamq/plugin.py`, `hamq/metadata.txt`
- `hamq/processing/__init__.py`, `hamq/processing/provider.py`
- `hamq/core/__init__.py`, `hamq/qgis_io/__init__.py`, `hamq/gui/__init__.py`, `hamq/net/__init__.py`
- `hamq/resources/icons/hamq.svg`
- `tests/core/test_smoke.py`, `pyproject.toml` (ruff + pytest config)
- `scripts/package.py`
- `.github/workflows/ci.yml`
- `README.md`, `CHANGELOG.md`, `LICENSE`

## Out of scope
- Any real feature.

## Checklist
- [x] Directory layout as in the pyqgis-plugin skill
- [x] `metadata.txt` with `qgisMinimumVersion=3.34`, `qgisMaximumVersion=4.99`, `supportsQt6=True`
- [x] Menu "HamQ" with an "About" action
- [x] Processing provider registered on load, removed on unload
- [x] `scripts/package.py` builds `dist/hamq-<version>.zip`
- [x] CI runs ruff and `pytest tests/core`

## Acceptance criteria
- [x] `pytest tests/core -q` passes (for the M0 tests; see Notes for the whole directory)
- [x] `ruff check hamq tests` passes (for the M0 files; see Notes)
- [x] Plugin enables and disables in QGIS 3.40 without errors in the log panel
      (verified on 3.34 and 3.44 instead of 3.40, automated, see Result)
- [x] Plugin enables and disables in QGIS 4.x without errors in the log panel
      (4.0 and 4.2, automated)
- [x] Provider "HamQ" is visible in the Processing Toolbox (registered in the
      Processing registry; the toolbox itself was not opened, see Result)

## Result

Done together with `tasks/M0-02-compat-and-test-harness.md` (same agent run).

What changed (new files):

- `hamq/metadata.txt`: name HamQ, QGIS 3.34 .. 4.99, `supportsQt6=True`,
  version 0.1.0, author/email, repository/homepage/tracker on
  github.com/vukovicvl/hamq, `experimental=True`, `hasProcessingProvider=yes`,
  GPL-3.0, tags, English description and a multi-sentence about (locator tools
  and grid, ADIF import into GeoPackage, map with geodesic paths, distance and
  bearing, DXCC/continent/zones from AD1C cty.dat downloaded on first use,
  statistics panel, WSJT-X/JTDX live over UDP, Hamlib rig/rotator (added to
  v0.1.0 by the PLAN.md update), English/Serbian Latin/Cyrillic with a
  one-click switch).
- `hamq/plugin.py`: `HamQPlugin(iface)` with `initProcessing()` (idempotent,
  also used by `qgis_process`), `initGui()`, `unload()` (idempotent). Menu
  `&HamQ` via `iface.addPluginToMenu` with "About HamQ"; own toolbar "HamQ"
  (`objectName` `HamQToolbar`, About is also on it for now). Registries for
  later milestones: `add_action(icon, text, callback, *, add_to_menu,
  add_to_toolbar, checkable, checked, tooltip, enabled, object_name)`,
  `add_dock_widget(dock, area)`, `connect_signal(signal, slot)`,
  `add_cleanup(callback)` (LIFO, failures logged, unload continues);
  `retranslate_ui()` re-applies all action texts from their `tr_noop`
  sources and runs on `events().languageChanged` (then
  `provider.refreshAlgorithms()`); `tr()` delegates to `hamq.core.i18n.tr`.
  About box: `QMessageBox.about` with version from metadata.txt, description,
  author, license, repository link, credits (AD1C cty.dat, WSJT-X UDP protocol
  by K1JT and the WSJT Development Group). `plugin_metadata()` reads
  metadata.txt.
- `hamq/processing/provider.py`: `HamQProvider` (id `hamq`, name `HamQ`,
  translated `longName`, icon, `svgIconPath`), `loadAlgorithms()` iterates the
  module-level `ALGORITHMS` list (empty).
- `hamq/processing/__init__.py`, `hamq/qgis_io/__init__.py`,
  `hamq/gui/__init__.py` (`ICONS_DIR`, `icon_path(name)`, `get_icon(name)`),
  `hamq/net/__init__.py`: package docstrings.
- `hamq/resources/icons/`: hamq, panel, import_adif, wsjtx, grid, locator,
  azimuthal, settings, language, refresh, paths, plus radio and rotator for M7
  (flat 24x24 SVG, 255-717 bytes each, no fonts or raster, checked on light
  and dark backgrounds at 16/24/64 px).
- `hamq/i18n/sr_Latn/plugin.json` (11 strings), `processing_provider.json` (1).
- `scripts/package.py`, `tests/core/test_smoke.py`, `tests/core/test_package.py`,
  `.github/workflows/ci.yml`, `README.md`, `LICENSE` (GPL-3.0 text from
  `/usr/share/common-licenses/GPL-3`).

Commands and outcomes (2026-09-29):

- `python3 -m pytest -p no:cacheprovider tests/core/test_smoke.py tests/core/test_architecture.py tests/core/test_package.py -q`
  -> 73 passed (host Python 3.14 and Docker `python:3.9-slim`, Python 3.9.25).
- ruff 0.16.9 `check` and `format --check` on all 19 M0 Python files -> clean.
- `scripts/test_qgis.sh all -q` -> local QGIS 4.2.1: 265 passed, 2 skipped;
  3.44.15: 265 passed, 2 skipped; 4.0.3: 265 passed, 2 skipped; 3.34.15:
  252 passed, 15 skipped (13 SVG render checks need QtSvg, missing in the
  server image; 2 wait for `core/qso.py`). Includes load/unload twice.
- Realistic load check (scratch script, not in the repo): the zip from
  `scripts/package.py` extracted into a temporary plugin path and driven
  through `qgis.utils.loadPlugin/startPlugin/unloadPlugin` twice on 4.2, 4.0,
  3.44 and 3.34: provider and toolbar present after start, gone after unload,
  no warning or critical message from Python/Plugins/HamQ in the log.
- `python3 scripts/package.py` -> `dist/hamq-0.1.0.zip`, 40 files, single
  `hamq/` folder, LICENSE included, no caches (the `dist/` folder was removed
  again afterwards).
- CI YAML parsed with PyYAML and checked with `actionlint` (clean); Docker Hub
  tags `qgis/qgis:3.44-trixie`, `qgis/qgis:4.0-trixie`, `python:3.9-slim`,
  `python:3.12-slim` verified with the Docker Hub API.

Manual checks still needed:

- Enable/disable HamQ in the desktop QGIS 4.x and a QGIS 3.x (3.40 LTR if
  available) with the log panel open; look at the icons in the real light and
  dark themes; open the Processing Toolbox and see the empty "HamQ" provider.

## Notes

- `CHANGELOG.md` is written by the orchestrator; `pyproject.toml`,
  `hamq/__init__.py` and `hamq/core/__init__.py` were already in place and
  were not changed.
- Whole-directory status at the end of this task (other agents' files, not in
  this scope): `pytest tests/core -q` stops at a collection error in
  `tests/core/test_adif.py` (test-first, `hamq/core/adif.py` not written yet);
  `ruff check` reports 6 unused imports in `tests/core/test_adif.py`; `ruff
  format --check` wants to reformat `hamq/core/stats.py` and
  `tests/core/test_adif.py`.
- `qgis.utils.unloadPlugin("hamq")` leaves the implicitly imported packages
  `hamq.core`, `hamq.processing` and `hamq.qgis_io` in `sys.modules` (QGIS
  tracks only modules imported through its import hook). Their submodules are
  unloaded, so a reload works; only edits to those `__init__.py` files need a
  QGIS restart.
