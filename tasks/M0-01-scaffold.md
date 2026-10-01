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
      (automated: `tests/qgis` on QGIS 3.40.15 in Docker `qgis/qgis:3.40`, also on
      3.34 and 3.44; load/unload twice with no warning or critical log message)
- [x] Plugin enables and disables in QGIS 4.x without errors in the log panel
      (4.0 and 4.2, automated, same checks; also a desktop QGIS 4.2 run, see Result)
- [x] Provider `hamq` ("HamQ") is registered in the Processing registry on load
      and removed on unload; it shows in the Processing Toolbox once the first
      algorithm is in `ALGORITHMS` (M1). **Reworded in the review fix round**: the
      original criterion "Provider "HamQ" is visible in the Processing Toolbox"
      cannot hold in M0, because QGIS hides providers without algorithms (see Notes).

## Result

Done together with `tasks/M0-02-compat-and-test-harness.md` (same agent run);
review fixes applied on 2026-09-30, see "Review fix round" below.

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
  `hamq/net/__init__.py`: package docstrings and `from __future__ import annotations`.
- `hamq/resources/icons/`: hamq, panel, import_adif, wsjtx, grid, locator,
  azimuthal, settings, language, refresh, paths, plus radio and rotator for M7
  (flat 24x24 SVG, 255-717 bytes each, no fonts or raster, checked on light
  and dark backgrounds at 16/24/64 px).
- `hamq/i18n/sr_Latn/plugin.json` (11 strings), `processing_provider.json` (1).
- `scripts/package.py`, `tests/core/test_smoke.py`, `tests/core/test_package.py`,
  `.github/workflows/ci.yml`, `README.md`, `LICENSE` (GPL-3.0 text from
  `/usr/share/common-licenses/GPL-3`).

Review fix round (2026-09-30), findings of the M0 review:

- Processing Toolbox (must): reproduced that QGIS hides a provider without
  algorithms (toolbox model with the Toolbox filter: no "HamQ" row on 3.34.15,
  3.44.15, 4.0.3 and 4.2.1, a "HamQ" row once one algorithm is loaded; the real
  Processing Toolbox dock of desktop QGIS 4.2.1 shows 31 rows and no "HamQ"
  after the plugin started). Criterion reworded (see above and Notes),
  `README.md` fixed (the provider shows in the Toolbox with the first
  algorithm), the impossible manual check removed. New test
  `test_toolbox_shows_provider_only_with_algorithms`.
- `provider.refreshAlgorithms()` after a language change (should) is now
  tested: `test_language_change_refreshes_provider_algorithms` (algorithms are
  re-created, `algorithmsLoaded` fires once, a parameter label follows the new
  text) and `test_open_toolbox_follows_language_change` (an open toolbox
  model shows the new group name). Replacing the call with `pass` fails both.
- `initProcessing()` idempotency (nit): the test now checks that
  `plugin.provider` stays the same object and that nothing is logged under
  the HamQ tag (a second registration logs a warning; the provider count
  alone could not show it). `checked=` of `add_action` (nit):
  `test_add_action_checked_state`. `test_load_and_unload_twice` now also
  asserts that no warning or critical message reaches the log. An autouse
  fixture removes a provider left registered by a failed test, so one failure
  no longer cascades into ~30.
- `plugin.json`: "Credits" is now "Zasluge" (usual Serbian UI term; GNOME GTK
  sr@latin uses it) instead of "Zahvalnice".
- `from __future__ import annotations` added to `hamq/processing/__init__.py`,
  `hamq/qgis_io/__init__.py`, `hamq/net/__init__.py`; the new
  `test_module_has_future_annotations` in `tests/core/test_architecture.py`
  checks every `hamq/**/*.py` (two files outside this scope are xfail, see Notes).
- Mutation check (scratch script, local QGIS 4.2.1): no refresh on language
  change, `initProcessing` without the guard, `checked` ignored, no retranslate,
  a warning or a critical message logged during `initGui` -> all killed.

Commands and outcomes (2026-09-30, after the fix round):

- `python3 -m pytest -p no:cacheprovider tests/core/test_smoke.py tests/core/test_architecture.py tests/core/test_package.py -q`
  -> 99 passed, 2 xfailed (host Python 3.14 and Docker `python:3.9-slim`,
  Python 3.9.25). The xfails are the future-import check of
  `hamq/__init__.py` and `hamq/core/__init__.py` (outside this scope).
- ruff 0.16.9 `check` and `format --check` on all 19 M0 Python files -> clean.
- `scripts/test_qgis.sh all -q` -> PASS on every target: local QGIS 4.2.1: 306
  passed, 2 skipped; 3.44.15: 306 passed, 2 skipped; 4.0.3: 306 passed, 2
  skipped; 3.34.15: 293 passed, 15 skipped (13 SVG render checks need QtSvg,
  missing in the server image; 2 wait for `core/qso.py`). Includes
  load/unload twice. Also `tests/qgis` in Docker `qgis/qgis:3.40` (QGIS
  3.40.15, Qt 5.15.13): 306 passed, 2 skipped. Local 4.2.1 and 3.44 also pass
  with `-W error::DeprecationWarning -W error::PendingDeprecationWarning`.
- Desktop check (scratch script, not in the repo; `qgis --code`, offscreen,
  fresh profile, QGIS 4.2.1): the zip from `scripts/package.py` loaded,
  started and unloaded twice through `qgis.utils`; provider, `&HamQ` menu and
  HamQ toolbar present after start and gone after unload; menu text "About
  HamQ" / "O programu HamQ" / "О програму HamQ" after switching to EN /
  sr_Latn / sr_Cyrl; About box opens; no warning or critical message and
  nothing from Python/Plugins/HamQ in the log.
- `python3 scripts/package.py --output-dir <scratch>` -> `hamq-0.1.0.zip`, 43
  files (other agents' core modules and catalogs are in it now), single
  `hamq/` folder, LICENSE included, no caches or tests; nothing written into
  the repository.
- First round (2026-09-29, unchanged since): CI YAML parsed with PyYAML and
  checked with `actionlint` (clean); Docker Hub tags `qgis/qgis:3.44-trixie`,
  `qgis/qgis:4.0-trixie`, `python:3.9-slim`, `python:3.12-slim` verified with
  the Docker Hub API; the same `qgis.utils` load check also passed on 4.0, 3.44
  and 3.34.

Manual checks still needed:

- Enable/disable HamQ in a desktop QGIS 4.x and a QGIS 3.x (3.40 LTR if
  available) on screen with the log panel open, and look at the icons in the
  real light and dark themes. The Processing Toolbox is expected to show no
  "HamQ" entry until M1 adds the first algorithm (automated test).

## Notes

- Why the Toolbox criterion was reworded: in the QGIS source
  (`src/gui/processing/qgsprocessingtoolboxmodel.cpp`,
  `QgsProcessingToolboxProxyModel::filterAcceptsRow`) "groups/providers are
  shown only if they have visible children". PLAN.md M0 asks
  for an empty provider ("Prazan Processing provider `hamq`"), so the provider
  is right and the old wording was wrong; it had been ticked on the strength
  of the registry check alone. When M1 appends its first algorithm class to
  `ALGORITHMS`, "HamQ" appears in the Toolbox (covered by
  `test_toolbox_shows_provider_only_with_algorithms`).
- `hamq/__init__.py` and `hamq/core/__init__.py` lack `from __future__ import
  annotations` (ARCHITECTURE "General rules"); they are outside this task's
  scope, so `test_module_has_future_annotations` reports them as xfail and
  passes once their owner adds the import (then drop them from
  `FUTURE_IMPORT_PENDING` in `tests/core/test_architecture.py`).
- `CHANGELOG.md` is written by the orchestrator; `pyproject.toml`,
  `hamq/__init__.py` and `hamq/core/__init__.py` were already in place and
  were not changed.
- Whole-directory status (other agents' files, not in this scope) moves while
  they work. At the end of the fix round (2026-09-30): one `pytest tests/core
  -q` run failed in `tests/core/test_adif.py` while that file was being
  edited, the next run passed (2709 passed, 8 xfailed; 2 of the xfails are the
  future-import check above); `ruff check hamq tests scripts` is clean; `ruff
  format --check` flags only other agents' ADIF files (`hamq/core/adif.py` /
  `tests/core/test_adif.py`).
- `qgis.utils.unloadPlugin("hamq")` leaves the implicitly imported packages
  `hamq.core`, `hamq.processing` and `hamq.qgis_io` in `sys.modules` (QGIS
  tracks only modules imported through its import hook). Their submodules are
  unloaded, so a reload works; only edits to those `__init__.py` files need a
  QGIS restart.
