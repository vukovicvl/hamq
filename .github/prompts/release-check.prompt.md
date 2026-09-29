---
description: Pre-release checklist for plugins.qgis.org
---
Check the repository for release readiness:
- `hamq/metadata.txt`: name, qgisMinimumVersion, qgisMaximumVersion, supportsQt6,
  version matches CHANGELOG, description, about, tracker, repository, homepage,
  tags, icon exists, experimental flag correct.
- `LICENSE` is GPL-3.0 and included in the zip.
- `python scripts/package.py` builds a zip without `__pycache__`, tests, `.github`.
- No `print(` in `hamq/`, no hardcoded paths, no debug flags.
- README has install steps and at least one screenshot.
Report each item as OK or FAIL with the reason.
