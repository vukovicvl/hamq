# M6-03: Release v0.1.0: README with screenshots, demo log, CHANGELOG

**Milestone:** M6
**Status:** done
**Skills:** pyqgis-plugin, wsjtx-udp, hamlib, maidenhead, dxcc-cty

## Goal
Make the release documentation of v0.1.0: a README in English with a complete Serbian
section, screenshots taken headless with the real QGIS 4.2 desktop and the real plugin
code, a reproducible fictional demo log for them, and `CHANGELOG.md` turned into the
0.1.0 section with link references.

## Scope
Files that may be created or changed:
- `README.md`, `CHANGELOG.md`
- `docs/images/*.png` (new), `docs/demo/demo_log.adi` (new), `docs/RELEASING.md` (the
  screenshot step and the changelog links), `docs/ARCHITECTURE.md` (the layout lists the
  new scripts and `docs/`)
- `scripts/make_demo_log.py`, `scripts/make_screenshots.py` (new)
- `tasks/M6-03-release.md`

## Out of scope
- Plugin code, tests, `hamq/metadata.txt` (its `changelog` was already done), `PLAN.md`,
  `AGENTS.md`.
- Committing, tagging, the GitHub release and the upload to plugins.qgis.org
  (`docs/RELEASING.md`, steps 4 and 5).

## Checklist
- [x] `scripts/make_demo_log.py` writes `docs/demo/demo_log.adi`: 150 fictional QSOs of
      YU1ZZZ in KN04ft, January to September 2026, six continents (EU 68, AS 22, NA 22,
      AF 13, OC 13, SA 12), 70 DXCC entities, 138 callsigns, 110 grid squares; real
      prefixes and call areas with random suffixes, locators of real cities (4 characters
      for FT8 / FT4, 6 for the other modes), plausible bands, modes, times, reports and
      frequencies; `COUNTRY`, `DXCC`, `CONT`, `CQZ`, `ITUZ` as a logger exports them.
      Reproducible (seed), `--check` mode, the same file on Python 3.9 and 3.14
- [x] Every record checked against the real AD1C files (release 2026-09-15, downloaded to
      a scratch folder only): the entity, ADIF DXCC code, name (as HamQ names it),
      continent and both zones of each callsign match `CtyDatabase.lookup`
- [x] `scripts/make_screenshots.py`: real QGIS desktop (`qgis --code`, offscreen,
      throw-away profile with this checkout linked), the demo log imported with
      `hamq:import_adif` onto QGIS's bundled `world_map.gpkg`, real UDP traffic to the
      WSJT-X listener and TCP stand-ins for `rigctld` / `rotctld`; fails on any HamQ
      critical log message; PNGs reduced to 256 colours with Pillow when available
- [x] (a) `qso-map.png` QSO map with band legend and panel, English; (b)
      `panel-statistics-sr-latn.png`; (c) `panel-live-sr-cyrl.png` (WSJT-X and Radio
      tabs); (d) `settings-en.png` (Station, Radio and rotator); (e) `azimuthal-map.png`
- [x] README: features, screenshots, requirements, installation (zip, plugins.qgis.org,
      development link for QGIS 3 and 4 on Linux / Windows / macOS), first steps, WSJT-X
      and JTDX (unicast, multicast, "Accept UDP requests" not needed), Hamlib (install,
      `rigctl -l`, model numbers, `rigctld` / `rotctld` commands, dummy devices, checks,
      sharing the radio with WSJT-X), rotator map tool, manual QSO entry, azimuthal map,
      language switch, data storage, Processing algorithms, troubleshooting, development,
      credits, licence; the same essentials in the section "Srpski"
- [x] `CHANGELOG.md`: `## [0.1.0] - 2026-10-01` below an empty `## [Unreleased]`, entries
      checked against the code, "Known limitations", link references
- [x] Links and images of the touched Markdown files resolve

## Acceptance criteria
- [x] `python3 scripts/make_screenshots.py` runs end to end with exit status 0 and writes
      the five PNG files, each below 300 KB and at most 1200 px wide
- [x] `python3 scripts/make_demo_log.py --check` passes (Python 3.14 and 3.9)
- [x] Every relative link, image and `#anchor` in README.md, CHANGELOG.md and the touched
      docs exists; every external link of the README answers HTTP 200
- [x] `ruff check` / `ruff format --check` clean for `hamq tests scripts`
- [x] `scripts/package.py` accepts the CHANGELOG (`## [0.1.0]` found)

## Result

### What changed
- `scripts/make_demo_log.py` (new): the demo log generator. The entity table holds per
  city the position, callsign prefixes (with the region letter where cty.dat needs it:
  `UA9O`, `UA0A`, `UA0L`, `BD7I`) and the CQ / ITU zones; bands follow the distance
  (160 m / 80 m at night, 6 m only in the sporadic-E season, no SSB on 30 m), FT4 is
  written as `MODE=MFSK SUBMODE=FT4` as WSJT-X does; about 8 % of the QSOs are with a
  station worked before on another band or day. Uses `hamq.core.maidenhead`,
  `hamq.core.geo` and `hamq.core.adif.format_record` (no QGIS needed).
- `docs/demo/demo_log.adi` (new, 47 KB): its output.
- `scripts/make_screenshots.py` (new): see its docstring. Host side: writes a profile
  (`.../.local/share/QGIS/QGIS4/profiles/default`, so the settings dialog shows a
  familiar path) with `PythonPlugins/hamq=true`, QGIS's plugin update check off, the HamQ
  settings (YU1ZZZ, KN04ft, default GeoPackage path, a free UDP port) and the cty.dat
  test excerpt, runs
  `qgis --profiles-path ... --nologo --noversioncheck --lang en_US --code <itself>`
  with `QT_QPA_PLATFORM=offscreen`, `PYTHONDONTWRITEBYTECODE=1` and `TMPDIR` inside the
  temporary folder, then reads `report.json`, reduces the colours and checks the sizes.
  QGIS side, in order: window 1200x700 with the Layers panel and the HamQ panel (other
  panels and toolbars except Project, Map Navigation, Attributes and HamQ hidden), the
  world map, *Import ADIF* through `processing.run("hamq:import_adif")`, Equal Earth
  view, grab (a); the real `SettingsDialog` with the radio settings stored but no
  `settingsChanged`, grabs of two tabs side by side (d); `sr_Latn` through the
  `LanguageManager`, floating panel made tall enough for the whole tab (b); `sr_Cyrl`,
  listener started, heartbeat / status / Logged ADIF datagrams from `hamq.core.wsjtx`,
  the two TCP stand-ins enabled through `settingsChanged`, the rotator turned with the
  panel's own *Turn* button (target typed into its spin box), WSJT-X and Radio tabs side
  by side (c); `en`, the plugin's *Azimuthal map* action, the canvas map settings
  rendered square (e).
- `docs/images/*.png` (new): the five screenshots.
- `README.md`: rewritten for the release (English, then "Srpski").
- `CHANGELOG.md`: release section and link references; entries completed with the final
  behaviour (menu and toolbar, first-run hints, statistics computed in the background,
  listener start, rotator compass and reconnects, documentation), "Known limitations"
  (no on-air test yet, Windows-1250 logs, 2 s Hamlib timeout, problems shown before a
  language switch, open Processing dialogs, no ADX / ADIF export / IPv6) and the desktop
  smoke runs under "Tested".
- `docs/RELEASING.md`: the screenshot step names the two scripts; the changelog step adds
  the link references.
- `docs/ARCHITECTURE.md`: the layout lists the two scripts and `docs/`.

### Screenshots (QGIS 4.2.1, Qt 6.10.2, HamQ 0.1.0 from this checkout)

| File | Pixels | Size |
|---|---|---|
| `docs/images/qso-map.png` | 1200 x 700 | 90 KB (92 375 bytes) |
| `docs/images/settings-en.png` | 972 x 552 | 27 KB (27 982 bytes) |
| `docs/images/panel-statistics-sr-latn.png` | 350 x 980 | 25 KB (25 274 bytes) |
| `docs/images/panel-live-sr-cyrl.png` | 712 x 520 | 24 KB (24 732 bytes) |
| `docs/images/azimuthal-map.png` | 880 x 880 | 105 KB (108 012 bytes) |

All are 256-colour PNG files (Pillow with libimagequant); the two pictures put side by
side have a transparent 12 px gap.

What they show was read back from the run: import 150 / 0 duplicates / 0 skipped,
statistics 150 QSOs and 70 DXCC entities, longest QSO ZL2YI 17 926 km; the WSJT-X QSO
VK3FTZ (QF22, 15 393 km, Australia from the cty.dat excerpt) arrived over UDP and made
151 QSOs; rotator target 100° (bearing KN04ft -> QF22), shown on its way at 78°.

### Commands and outcomes
```
python3 scripts/make_demo_log.py                  -> 150 QSOs, 138 callsigns, 70 DXCC entities,
                                                     continents AF AS EU NA OC SA
python3 scripts/make_demo_log.py --check          -> up to date
docker python:3.9-slim (read-only, own user): make_demo_log.py --check -> up to date;
    a fresh run is byte-identical; make_screenshots.py parses; a missing QGIS -> exit 1
scratch verify script (real cty.dat / cty.csv 2026-09-15, hamq.core.cty):
    150/150 records: entity, DXCC code, name, continent, CQ and ITU zone match;
    record_to_qso: no warnings, 150 unique dedup keys
python3 scripts/make_screenshots.py --keep        -> exit 0, five PNG files (table above), 12 s;
    report.json: ok, no HamQ warning or error in the QGIS log; QGIS's temporary files stay
    in the kept folder; probe in the same QGIS: plugin-manager/automatically-check-for-updates
    is read as False from the profile
scratch link checker (relative files, GitHub anchors, HTTP):
    README.md 56 links/images, CHANGELOG.md 4, docs/RELEASING.md 1 -> 0 problems;
    the 8 external README links -> HTTP 200; a mutated copy (bad anchor, bad image) -> caught
ruff check hamq tests scripts / ruff format --check hamq tests scripts -> clean
python3 -m pytest -p no:cacheprovider tests/core -q            -> 3472 passed, 7 xfailed
docker python:3.9-slim, tests/core (read-only, own user)       -> 3471 passed, 1 skipped, 7 xfailed
HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q -> 310 passed
scripts/test_qgis.sh all -q -p no:cacheprovider (plugin code unchanged by this task):
    local   QGIS 4.2.1 (host)              PASS  1086 passed, 2 skipped
    3.44    qgis/qgis:3.44-trixie          PASS  1085 passed, 3 skipped
    4.0     qgis/qgis:4.0-trixie           PASS  1085 passed, 3 skipped
    3.34    camptocamp/qgis-server:3.34    PASS  1071 passed, 17 skipped
    the 2 local skips (live daemons) with HAMQ_RIGCTLD / HAMQ_ROTCTLD on a hamq/hamlib-dummy
    container (Hamlib 4.6.2, unique name, removed) -> test_real_rigctld, test_real_rotctld passed
whole tests/qgis in qgis/qgis:3.40 (3.40.15) and qgis/qgis:4.2-trixie (4.2.3), read-only,
    own user, as in docs/RELEASING.md -> 1085 passed, 3 skipped each
python3 scripts/package.py --output-dir <scratch>  -> hamq-0.1.0.zip, 89 files
package.release_problems()  -> only "uncommitted ... hamq/i18n/sr_Latn/gui_rotator_tool.json,
    processing_alg_import_adif.json" (changes of the release pass, not of this task);
    the CHANGELOG and metadata checks pass
```

### Manual checks still needed
- Look at the README on github.com after the push (light and dark theme; the side by
  side pictures have a transparent gap).
- The release itself: commit, CI green on the release commit, tag `v0.1.0`, GitHub
  release with `dist/hamq-0.1.0.zip`, upload to plugins.qgis.org (`docs/RELEASING.md`).

## Notes
- **CI on the pushed HEAD (`0144db5`) failed** in the job "QGIS tests (QGIS 4.2, Qt6,
  qgis/qgis:4.2-trixie)"; the other seven jobs passed. The job log needs admin rights,
  but the failure reproduces locally: `tests/qgis/test_fields.py` of HEAD fails in
  `qgis/qgis:4.2-trixie` (QGIS 4.2.3) in
  `test_provider_add_features_never_stores_a_python_datetime` (1 failed, 27 passed), the
  working-tree version passes (28 passed). That fix (M0-02) is not committed yet. The
  CHANGELOG's "Tested" line assumes the release commit is green in CI.
- `PLAN.md` (not in this task's scope) still lists "README sa uputstvom i slikama ekrana,
  CHANGELOG za 0.1.0" as open under M6 "Stanje"; its owner should mark them done.
- The demo log carries its own DXCC data, so the screenshots do not depend on a
  downloaded cty.dat; the screenshot profile gets the test excerpt only so that no
  first-run hint covers the map and the live WSJT-X QSO resolves to Australia.
- `make_screenshots.py` needs the QGIS desktop on the computer (`--qgis` for another
  executable). It was run with QGIS 4.2.1 only; the pictures depend on the installed
  fonts and on the QGIS version, so they may differ slightly between computers.
- QGIS writes "pxbackend ... Could not query proxy" lines to its output in this sandbox
  (libproxy); they come from QGIS, not HamQ, and do not affect the run.
