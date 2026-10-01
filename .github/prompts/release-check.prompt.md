---
description: Pre-release checklist for plugins.qgis.org
---
Check the repository for release readiness, following `docs/RELEASING.md` (steps 1 to 3):
- Checks: the commands of its step 1 pass (`tests/core` on the host and on Python 3.9,
  `HAMQ_STRICT_I18N=1` catalog test, ruff check / format on `hamq tests scripts`,
  `scripts/test_qgis.sh all`, the whole `tests/qgis` on QGIS 3.40); CI is green.
- `CHANGELOG.md`: `## [x.y.z] - YYYY-MM-DD` below an empty `## [Unreleased]`, link
  references at the bottom.
- `hamq/metadata.txt`: `version` equal to `pyproject.toml`, plain-text `changelog` that
  names the version, `experimental=True` while 0.x, `qgisMinimumVersion=3.34`,
  `qgisMaximumVersion=4.99`, `supportsQt6=True` kept (the deprecation warning on upload
  is expected), description, about, tracker, repository, homepage, tags, license, icon exists.
- `LICENSE` is the GNU GPL v3 (`license=GPL-3.0-or-later`) and is in the zip as `hamq/LICENSE`.
- `python3 scripts/package.py --release` builds `dist/hamq-x.y.z.zip` from committed
  sources: one top folder `hamq/`, no `__pycache__`, tests, hidden files or data files
  (`cty.dat`, `cty.csv`, `*.gpkg*`, `*.adi`).
- No `print(` in `hamq/`, no hardcoded paths, no debug flags.
- README (English and the Serbian section): features, install steps and the screenshots
  in `docs/images/` match the release (each below 300 KB);
  `python3 scripts/make_demo_log.py --check` passes.
Report each item as OK or FAIL with the reason. Trying the zip on a clean profile and
publishing (steps 4 to 6) are manual; list them as still to do.
