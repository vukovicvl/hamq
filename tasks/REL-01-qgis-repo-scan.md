# REL-01: Release 0.1.1 that passes the plugins.qgis.org scan with no findings

**Milestone:** release 0.1.1 (after M6-03)
**Status:** done
**Skills:** pyqgis-plugin, adif

## Goal
Upload HamQ to plugins.qgis.org with every check of the site's automatic scan clean: Bandit,
detect-secrets, Flake8, file checks and the Qt6 check. On the live site any finding of an
enabled Bandit rule blocks the version, and 0.1.0 has 17. Release the fixes, two small bug
fixes found since 0.1.0 and a scan that CI runs on every change as 0.1.1.

## Scope
Files that may be created or changed:
- `hamq/qgis_io/gpkg.py`, `hamq/net/wsjtx_listener.py`, `hamq/gui/dock.py`,
  `hamq/net/hamlib_client.py`: the Bandit findings
- `hamq/core/adif.py`, `hamq/core/i18n.py`, `hamq/core/qso.py`, `hamq/core/wsjtx.py`,
  `hamq/gui/dock.py`, `hamq/qgis_io/gpkg.py`: Flake8 E203 and E501 (max line length 120)
- `hamq/core/qso.py`, `hamq/processing/alg_import_adif.py`, `hamq/qgis_io/gpkg.py` and
  their tests: the two bug fixes
- `scripts/qgis_repo_scan.py` (new), `.github/workflows/ci.yml`, `docs/RELEASING.md`,
  `AGENTS.md` (Commands), `.github/prompts/release-check.prompt.md`
- `pyproject.toml`, `hamq/metadata.txt`, `CHANGELOG.md`, tests that assert the version or
  the metadata
- `docs/ARCHITECTURE.md` (`## Contract changes`) where a documented behaviour changes
- `.github/skills/adif/SKILL.md`, `.github/skills/pyqgis-plugin/SKILL.md`, `PLAN.md`: the
  changed `my_gridsquare` rule and the dropped `category` key
- this task file

## Out of scope
- New features. Refactoring for C901 (the site runs Flake8 without `--max-complexity`, so
  C901 cannot be reported).
- W503/W504: the site disables both, and they contradict each other; `ruff format` breaks
  lines before binary operators.
- Uploading to plugins.qgis.org (the owner's account and email).

## Checklist
- [x] Bandit 1.9 with the site's 74 enabled rules finds nothing in the plugin zip. Every
      `# nosec` names its rule and says why the code is safe; no `.bandit` file.
- [x] detect-secrets 1.5 with the site's options and plugins finds nothing.
- [x] Flake8 7.3 with the site's options (`--max-line-length=120`, `--select` of its enabled
      codes) finds nothing; also with E203 and E501 added.
- [x] No hidden, executable or binary file in the zip; `pyqt5_to_pyqt6.py --dry_run` from
      `ghcr.io/qgis/pyqgis4-checker:main-ubuntu` exits 0 with no proposed change.
- [x] Fix: a record with MY_LAT/MY_LON and no MY_GRIDSQUARE stores the 6-character locator
      of MY_LAT/MY_LON as `my_gridsquare`, without the APP_HAMQ_STATION_GRID mark, so the
      column always matches the path origin and Recalculate does not rewrite it.
- [x] Fix: Recalculate lets a stored `grid` point from a 2-character GRIDSQUARE yield to the
      cty.dat position inside that field, the same rule as the import.
- [x] `scripts/qgis_repo_scan.py` reproduces the site's Bandit, detect-secrets, Flake8 and
      file checks on a built zip; CI runs it on every push and pull request.
- [x] Version 0.1.1 in `pyproject.toml` and `hamq/metadata.txt`; plain-text `changelog`;
      `CHANGELOG.md` `## [0.1.1] - 2026-10-02` below an empty `## [Unreleased]`.
- [x] `about` in `metadata.txt` says that WSJT-X/JTDX and Hamlib `rigctld`/`rotctld` are
      separate programs that the live and radio features need.

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes, also on Python 3.9 (Docker)
- [x] `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q` passes
- [x] `ruff check hamq tests scripts` and `ruff format --check hamq tests scripts` pass
- [x] `scripts/test_qgis.sh all` passes on local 4.x, 3.44, 4.0 and 3.34
- [x] `scripts/qgis_repo_scan.py` reports no finding on the 0.1.1 zip
- [x] The Qt6 checker exits 0 on the 0.1.1 zip

## Result
(filled by the agents: what changed, commands run, manual checks still needed)

### Release tooling and version (2026-10-02)

- `scripts/qgis_repo_scan.py` (new, stdlib, Python 3.9+): builds the zip with
  `scripts/package.py` in a temporary folder (or `--zip PATH`), extracts it and runs the
  site's commands: Bandit `-r -f json --quiet -t <74 rules>`, detect-secrets
  `scan --all-files` with the two `--exclude-files` and `--disable-plugin IPPublicDetector`
  in the extracted folder, Flake8 `--format=json --max-line-length=120 --select <26 codes>`
  plus E203/E501 (reported apart as HamQ's margin, both disabled on the site), and the file
  checks of `_check_file_permissions` / `_check_suspicious_files`, plus a margin: any
  executable, hidden (FILE_HIDDEN is disabled on the site and allows `.bandit`, `.flake8`,
  `.secrets.baseline`) or binary file (ELF/PE/Mach-O, `.pyc`; the site lists FILE_BINARY
  but has no check). Exit 0 clean, 1 on a finding or a tool failure (a Bandit parse error
  also fails, the site ignores it), 2 when bandit, detect-secrets, flake8 or flake8-json is
  missing (prints the pip command with the pinned versions). Rule lists are constants
  dated 2026-10-02 with their source files. Tests: `tests/core/test_qgis_repo_scan.py`
  (canned tool output, no tools needed).
- Proof (Docker `python:3.12-slim`, bandit 1.9.4, detect-secrets 1.5.0, flake8 7.4.1,
  flake8-json 24.4.0): `--zip dist/hamq-0.1.0.zip` reports 17 Bandit (14 B608, 2 B104,
  1 B110), 0 secrets, 0 Flake8 with the site's codes, 11 E203 and 3 E501 in the margin,
  no file finding, exit 1. The working tree on 2026-10-02 (0.1.1, with the other agent's
  fixes) builds a zip with no finding, exit 0.
- Qt6 check: `pyqt5_to_pyqt6.py --dry_run` exits 0 on 0.1.0 and on the 0.1.1 tree with no
  proposed change. It also exits 0 when it proposes changes (checked with a Qt5-only
  file: two lines `path:line:col - ...`, exit 0), so CI and `docs/RELEASING.md` also fail
  on any line after its log header.
- CI: job *plugins.qgis.org scan* (`qgis-repo-scan`) on every push and pull request: pinned
  tool versions and the checker image in the workflow `env`, builds the zip once into
  `$RUNNER_TEMP`, runs `scripts/qgis_repo_scan.py --zip` and the Qt6 check on it.
  `actionlint` (Docker `rhysd/actionlint`) passes.
- Version 0.1.1 in `pyproject.toml` and `hamq/metadata.txt`; plain-text `changelog`
  naming 0.1.1 (then 0.1.0); `CHANGELOG.md` `## [0.1.1] - 2026-10-02` below an empty
  `## [Unreleased]`, link references updated; README version and download names 0.1.1.
- `metadata.txt`: `about` says that WSJT-X, JTDX and Hamlib are separate programs and
  that the live QSOs need WSJT-X or JTDX running, the radio and rotator features Hamlib
  `rigctld` / `rotctld` running; the cty.dat sentence is kept. `category=Plugins`
  removed: the QGIS documentation (PyQGIS cookbook, "Plugin metadata", QGIS 3.44 docs,
  updated 2026-08-10) allows only Raster, Vector, Database, Mesh and Web, and plugins sit
  in the Plugins menu by default; the site's validator does not read the key.
  `scripts/package.py` no longer requires it. `supportsQt6=True` kept.
- Tests: `tests/core/test_package.py` (metadata read with the site's default
  ConfigParser, `about`, `category`, newest CHANGELOG section = version),
  `tests/qgis/test_plugin_load.py::test_plugin_metadata` (changelog, about, category).
- Docs: `docs/RELEASING.md` step 1 (scan and Qt6 commands, pass rules, no tuning files),
  step 2 (no `category`), step 5 (no security rule skipped, confirmation email to the
  metadata address, upload Monday to Thursday); `AGENTS.md` Commands;
  `.github/prompts/release-check.prompt.md` scan item.
- Commands run: `python3 -m pytest tests/core -q` (host and Python 3.9 Docker),
  strict catalog test, ruff 0.16.9 check/format (Docker), `scripts/qgis_repo_scan.py
  --help`, the scans above, `scripts/test_qgis.sh all` on the tree of 2026-10-02 (with the
  other agent's changes at that moment): local 1090 passed, 3.44 1089, 4.0 1089, 3.34 1075
  passed (skips as before), all PASS.
- Still manual at that point: the pyqgis-plugin skill showed `category=Plugins`, and the
  README status needed the 3.40 run of `docs/RELEASING.md`. Both done since: the adif and
  pyqgis-plugin skills and `PLAN.md` follow the new rules (final pass), and the fix round
  ran the whole `tests/qgis` on 3.40 and 4.2.

### Scan findings and bug fixes in `hamq/` (2026-10-02)

- Bandit B608 (14, `qgis_io/gpkg.py`): every statement audited. Identifiers are module
  constants (`QSO_LAYER`, `PATH_LAYER`, `META_TABLE`, `_PATH_TRIGGER`) or go through
  `_quote()`: the fid, geometry and column names read from the user's file (`PRAGMA
  table_info`, `gpkg_geometry_columns`). Values are bound parameters; `IN (...)` lists are
  `?` marks only. Nothing unquoted was found. Each statement carries
  `# nosec B608 # <reason>` on the line Bandit reports (the first line of the string;
  the `# reason` after a second `#` keeps Bandit from reading the words as test ids). No
  `.bandit`. A comment above `_quote()` states the rule.
- Bandit B104 (2): `compat.HOST_ANY_IPV4_TEXT = QHostAddress(HOST_ANY_IPV4).toString()`
  (computed once by Qt); `wsjtx_listener._ANY_ADDRESS` and the dock's "Listening on"
  text use it. Binding is unchanged.
- Bandit B110 (1): `hamlib_client._guarded` logs through `contextlib.suppress(Exception)`.
- Flake8 E203 (11): slices with a computed bound name the bound first (`end = start + n;
  items[start:end]`); E501 (3, `core/qso.py`): the long `tr()` texts are split into
  implicit concatenations (same catalog keys).
- Fix 1: `core/qso.py` gives a record with a usable MY_LAT/MY_LON and no MY_GRIDSQUARE
  `maidenhead.to_locator(MY_LAT, MY_LON, 6)` as `my_gridsquare` (live, manual and
  `save_records` QSOs go through it too; no controller change needed). New
  `core.qso.my_position(record)`. *Import ADIF* marks APP_HAMQ_STATION_GRID only without
  MY_GRIDSQUARE and without a usable MY_LAT/MY_LON. `gpkg.recalculate` gives 0.1.0 rows
  (mark, or empty `my_gridsquare`, plus MY_LAT/MY_LON in `adif_extra`) the locator of
  that position and drops the mark.
- Fix 2: `gpkg._replaces` lets a stored `grid` point of a 2-character GRIDSQUARE yield
  to a `cty` position (which `record_to_qso` only gives inside that field).
- Tests (fail without the fixes): `tests/core/test_qso.py` (MY_LAT/MY_LON cell, log,
  corners, unusable pairs, `my_position`; `my-latlon-without-grid` now expects
  `KN04ft`), `tests/qgis/test_gpkg.py` (Recalculate of 0.1.0 rows, field locator vs
  cty; the station-grid test no longer carries the buggy MY_LAT/MY_LON row),
  `tests/qgis/test_processing.py` (import + Recalculate), `tests/qgis/test_integration.py`
  (live QSO), `tests/qgis/test_compat.py` (`HOST_ANY_IPV4_TEXT`).
- `docs/ARCHITECTURE.md`: rules of `record_to_qso`, `## Contract changes` (REL-01) and
  the adif skill difference.
- Commands run: `python3 -m pytest tests/core -q` (3516 passed, 6 xfailed; Python 3.9
  Docker 3515 passed, 1 skipped), strict catalog test (310 passed), ruff 0.16.9 check and
  format --check (Docker), zip built with `scripts/package.py --output-dir` and scanned
  with the shell replica of the site scan (Bandit 0 results 0 errors, secrets 0, Flake8 0,
  no file finding; `--select E203,E501` 0), Qt6 checker exit 0 with no proposed change,
  `scripts/test_qgis.sh all` (local 1090 passed, 3.44 1089, 4.0 1089, 3.34 1075, all PASS).
- Still open: `PLAN.md` (`my_gridsquare` / `adif_extra` rows) and
  `.github/skills/adif/SKILL.md` still describe the old `my_gridsquare` rule (listed in
  `docs/ARCHITECTURE.md` "Skills and this contract"); both are outside this task's scope.

### Review round 1 (2026-10-02)

- `docs/RELEASING.md`: the `# nosec` example is now `# nosec B608 # <why it is safe>`;
  the reason goes after a second `#` (with `B608: words` Bandit 1.9 reads each word as a
  test id and warns), the form `qgis_io/gpkg.py` and `docs/ARCHITECTURE.md` use.
- `gpkg._replaces`: a stored `grid` point of a 2-character GRIDSQUARE yields to the
  cty.dat position only when it is still the field centre the import wrote (within
  1e-7 degrees) or lies outside the field (the locator was changed); a point moved by
  hand inside the field is kept. Docstring of `recalculate` and the REL-01 contract say
  so. New test `test_recalculate_keeps_a_field_point_moved_by_hand_inside_the_field`
  (fails when the centre check is removed, checked on a scratch copy).
- `docs/ARCHITECTURE.md` REL-01: "the column always names the cell the path starts in"
  is now limited to new QSOs and 0.1.0 *Import ADIF* rows, and the cases that remain are
  named (logged `MY_GRIDSQUARE` and `MY_LAT`/`MY_LON` that disagree; 0.1.0 live, manual
  or `save_records` rows with `MY_LAT`/`MY_LON`, which cannot be told from a logged
  locator; `force_station` followed by a normal Recalculate).
- "Skills and this contract": a pyqgis-plugin line (no `category` in `metadata.txt`; the
  skill's `version=0.1.0` is an example).
- Acceptance box of `scripts/qgis_repo_scan.py` ticked.
- README 3.40 / 4.2: run on this tree with the `docs/RELEASING.md` 3.40 command
  (`qgis/qgis:3.40`: 1090 passed, 3 skipped) and the CI 4.2 command
  (`qgis/qgis:4.2-trixie`: 1090 passed, 3 skipped). CI's 4.2 job still has to be green.
- Commands run: `python3 -m pytest tests/core -q` (3516 passed, 6 xfailed), strict
  catalog test (310 passed), ruff 0.16.9 check and format --check (Docker: all passed,
  94 files formatted), fresh zip `hamq-0.1.1.zip` (89 files), `scripts/qgis_repo_scan.py
  --zip` in `python:3.12-slim` (bandit 1.9.4, detect-secrets 1.5.0, flake8 7.4.1,
  flake8-json 24.4.0: every check OK, exit 0), shell replica (Bandit 0 results 0 errors,
  secrets 0, Flake8 0, no file finding), Qt6 checker (log header only, exit 0),
  `scripts/test_qgis.sh all` (local 1091 passed, 3.44 1090, 4.0 1090, 3.34 1076, all
  PASS).

## Notes
Research 2026-10-01/02: site source `qgis/QGIS-Plugins-Website`
(`qgis-app/plugins/security_scanner.py`, `qgis-app/plugins/management/commands/data/*.json`,
`dockerize/docker/REQUIREMENTS.txt`: bandit~=1.9, detect-secrets~=1.5, flake8~=7.3,
flake8-json~=24.4; `qt6-validator/plugins/tasks/run_check_qt6.py`). The Bandit check is
"critical" and passes only with zero results, whatever the rule's own severity label. 0.1.0:
17 Bandit results (14 B608 in `qgis_io/gpkg.py`, 2 B104, 1 B110), 0 detect-secrets, 0 Flake8
with the site's codes (11 E203 and 3 E501 with stricter codes), Qt6 check exit 0.
