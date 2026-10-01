# Releasing HamQ

How a HamQ version gets from `main` to plugins.qgis.org. The version lives in two
places, `hamq/metadata.txt` (`version`) and `pyproject.toml` (`[project] version`);
`scripts/package.py` refuses to build when they differ.

## 1. Checks

Run everything on the commit that becomes the release:

```bash
python3 -m pytest tests/core -q                      # host Python
docker run --rm -v "$PWD":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim \
  sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core -q"
HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q
ruff check hamq tests scripts
ruff format --check hamq tests scripts
scripts/test_qgis.sh all                             # local 4.x, Docker 3.44, 4.0, 3.34

# QGIS 3.40 (no test_qgis.sh target yet): run like the CI image jobs, read-only, own user
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -e QT_QPA_PLATFORM=offscreen -e XDG_RUNTIME_DIR=/tmp/xdg -v "$PWD":/app:ro -w /app \
  qgis/qgis:3.40 sh -c 'mkdir -p /tmp/xdg && chmod 700 /tmp/xdg &&
    python3 -m pytest -p no:cacheprovider tests/qgis -q'
```

CI runs the same core, catalog and lint checks (core on Python 3.9 and 3.12) and
`tests/qgis` on QGIS 3.34 (`qgis/qgis:3.34`, the minimum version), 3.44, 4.0 and 4.2
(`qgis/qgis:4.2-trixie`); all jobs must be green.

## 2. Version, changelog and metadata

1. `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)): rename
   `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD` and put a new, empty `## [Unreleased]`
   above it. At the bottom, point `[Unreleased]` at `compare/vx.y.z...HEAD` and add
   `[x.y.z]: https://github.com/vukovicvl/hamq/compare/vPREVIOUS...vx.y.z` (the first
   release links to `releases/tag/v0.1.0`).
2. `hamq/metadata.txt`:
   - `version=x.y.z`, the same in `pyproject.toml`;
   - `changelog=`: plain text (no HTML) that names the version, for example
     `x.y.z: ...`; the QGIS plugin manager and plugins.qgis.org show it;
   - `experimental=True` while the version is 0.x;
   - `qgisMinimumVersion=3.34`, `qgisMaximumVersion=4.99`;
   - keep `supportsQt6=True`. plugins.qgis.org now marks the key as deprecated and shows a
     warning on upload (QGIS 4 support follows from `qgisMaximumVersion`). The warning is
     expected: QGIS 3.x builds on Qt6 mark a plugin without the key as incompatible, and
     `tests/core/test_package.py` and `tests/qgis/test_plugin_load.py` assert it.
3. README (English and the Serbian section): the feature list, the install steps and
   the screenshots in `docs/images/` match the release. `python3 scripts/make_screenshots.py`
   takes them again with the QGIS desktop on this computer, without a display and
   with a throw-away profile (about 15 s; `--qgis` names another QGIS executable,
   `--keep` keeps the profile, the raw pictures and the QGIS output). It imports
   `docs/demo/demo_log.adi`, which `python3 scripts/make_demo_log.py` makes (the same
   file every time; `--check` tells whether it is up to date). Each picture must stay
   below 300 KB; with Pillow installed the script reduces them to 256 colours.
4. Commit. A release is built from committed sources only.

## 3. Build the zip

```bash
python3 scripts/package.py --release        # -> dist/hamq-x.y.z.zip
```

Without `--release` the script validates `metadata.txt` (required keys, icon, version
equal to `pyproject.toml`) and builds the zip; CI does this on every push and keeps the
zip as the `hamq-plugin-zip` artifact. `--release` also requires:

- a `## [x.y.z]` section in `CHANGELOG.md`;
- a plain-text `changelog` in `metadata.txt` that names `x.y.z`;
- no uncommitted or untracked files under `hamq/` (and `LICENSE`) in a git checkout.

The zip has one top folder, `hamq/`, with the plugin package and `LICENSE`. It never
contains `__pycache__`, tests, hidden files or data files: `cty.dat` / `cty.csv` (AD1C's
country files are downloaded by the user and must not be bundled), `*.gpkg*`, `*.adi`,
`*.sqlite` and similar; data files found under `hamq/` are listed as "not packed". Any
other unexpected file type stops the build.

## 4. Try the zip

On a clean QGIS profile, at least on QGIS 3.34 (Qt5) and on the newest 4.x (Qt6):
*Plugins > Manage and Install Plugins > Install from ZIP*, choose
`dist/hamq-x.y.z.zip`, then check that the HamQ menu, toolbar, panel and the four
Processing algorithms appear, that the language switch works and that the plugin can be
disabled and enabled again without errors in *View > Panels > Log Messages* (HamQ tab).

## 5. Publish

1. Tag the release commit and push the tag:
   `git tag -a vx.y.z -m "HamQ x.y.z"` and `git push origin vx.y.z`.
2. GitHub: create a release from the tag, with the `CHANGELOG.md` section as its notes and
   `dist/hamq-x.y.z.zip` attached.
3. plugins.qgis.org: sign in with an OSGeo ID and upload the same zip (a new plugin
   through the upload page, a later version from the plugin's own page). Check that the
   page shows the version, the changelog and the experimental flag. A new plugin may
   wait for approval by the repository maintainers before it is listed.

## 6. After the release

Keep the empty `## [Unreleased]` section of `CHANGELOG.md` for the next changes, and
change `version` in `metadata.txt` and `pyproject.toml` together for the next release.
