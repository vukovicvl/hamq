# M0-01: Repository and plugin scaffold

**Milestone:** M0
**Status:** todo
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
- [ ] Directory layout as in the pyqgis-plugin skill
- [ ] `metadata.txt` with `qgisMinimumVersion=3.34`, `qgisMaximumVersion=4.99`, `supportsQt6=True`
- [ ] Menu "HamQ" with an "About" action
- [ ] Processing provider registered on load, removed on unload
- [ ] `scripts/package.py` builds `dist/hamq-<version>.zip`
- [ ] CI runs ruff and `pytest tests/core`

## Acceptance criteria
- [ ] `pytest tests/core -q` passes
- [ ] `ruff check hamq tests` passes
- [ ] Plugin enables and disables in QGIS 3.40 without errors in the log panel
- [ ] Plugin enables and disables in QGIS 4.x without errors in the log panel
- [ ] Provider "HamQ" is visible in the Processing Toolbox

## Result

## Notes
