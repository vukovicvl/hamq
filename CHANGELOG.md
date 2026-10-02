# Changelog

All notable changes to HamQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-10-02

### Changed

- The plugin zip passes every check of the plugins.qgis.org automatic scan with no
  findings, run with the site's tools and rules: Bandit with its 74 rules,
  detect-secrets, Flake8 with its codes (and with E203 and E501 at 120 characters),
  the file checks and the Qt6 check (`pyqt5_to_pyqt6.py`). The SQL statements of the GeoPackage storage were audited
  (table and column names are quoted identifiers, values are bound parameters) and
  marked for Bandit with the reason. `scripts/qgis_repo_scan.py` runs the same scan
  on a plugin zip, and CI runs it on every push and pull request.
- The plugin description (`about` in `metadata.txt`) says that WSJT-X, JTDX and
  Hamlib are separate programs: the live QSOs need WSJT-X or JTDX running, the radio
  and rotator features need Hamlib `rigctld` / `rotctld` running.
- `metadata.txt` no longer sets `category=Plugins`, which is not one of the values
  the QGIS documentation allows (Raster, Vector, Database, Mesh, Web); HamQ stays in
  the *Plugins* menu.

### Fixed

- A QSO with MY_LAT/MY_LON and no MY_GRIDSQUARE gets the locator of that position
  as `my_gridsquare`. It got the station's QTH locator, which *Recalculate
  distances and DXCC data* then changed while the path stayed at MY_LAT/MY_LON.
- *Recalculate distances and DXCC data* lets the position of a 2-character
  locator yield to the cty.dat position inside that field, as the import does.

## [0.1.0] - 2026-10-01

First release, experimental.

### Added

- QGIS plugin for QGIS 3.34 to 4.x (Qt5 and Qt6) without third-party packages:
  the *Plugins > HamQ* menu (panel, Import ADIF, Listen to WSJT-X, Log QSO, Point
  antenna on map, Azimuthal map, Maidenhead grid, Locator to point, Recalculate
  distances and DXCC data, Download cty.dat, Settings, Language, About), the HamQ
  toolbar with the locator search and the EN / SR / СР switch, the HamQ panel and
  the Processing provider `hamq` with four algorithms. At the first start, hints in
  the message bar offer to download cty.dat and to set the QTH locator. One
  compatibility layer for QGIS 3.34 to 4.x, typed settings, a packaging script for
  plugins.qgis.org and CI. Unloading or upgrading the plugin releases all of its
  modules, so an update takes effect without stale code (M0, INT-01).
- Maidenhead locators with 2 to 8 characters: conversion both ways, cell bounds
  and centre, validation. Processing algorithms *Locator to point* and
  *Generate Maidenhead grid* (field to extended square, any extent CRS,
  antimeridian and poles). Locator search in a compact toolbar field centres the
  map and highlights the cell (M1).
- ADIF (`.adi`) import that tolerates real-world logs: character- or
  byte-counted field lengths, latin-1 fallback, BOM, missing `<EOH>`/`<EOR>`,
  wrong lengths. Tested with sample logs in the formats of WSJT-X, N1MM, Log4OM,
  QRZ.com, LoTW and Xlog (M2).
- QSO normalization: validation, band from frequency; one QSO for the duplicate
  check however a logger wrote its mode (FT4 as `MODE=FT4` or `MFSK`/`FT4`, SSB
  with or without its sideband `USB`/`LSB` or as `MODE=USB`, `PSK31`/`PSK`,
  `JT65B`/`JT65` and the other ADIF submode variants); position from LAT/LON,
  then the locator, then cty.dat (a 2-character locator yields to the cty.dat
  position inside that field); path origin from MY_LAT/MY_LON, then
  MY_GRIDSQUARE, then my QTH, where a coarse MY_GRIDSQUARE such as the WSJT-X
  "My Grid" `KN04` is refined to my `KN04ft` (M2).
- GeoPackage log (schema 2) with deduplicated inserts and geodesic QSO paths
  split at the antimeridian. Deleting a QSO, in QGIS or in any other program,
  deletes its path; saved edits of the HamQ layers refresh the layers and the
  statistics. *Import ADIF*: re-importing a file adds nothing, bad records are
  skipped and reported, files above 200 MB or with more than 10 000 records per
  MB of that limit are refused (advanced parameter *Largest ADIF file to read
  (MB)*), and so is anything that is not a regular file. *Recalculate distances
  and DXCC data* for a new QTH or a new cty.dat; QSOs imported without
  MY_GRIDSQUARE follow a changed QTH locator. A read-only GeoPackage or folder is
  reported with what to check. 10 000 QSOs import in about 3 s (M2, M3).
- HamQ layer group with translated layer names, field aliases and legend labels,
  colour-blind aware styles by band, azimuthal equidistant map centred on my QTH
  with distance rings and azimuth lines; a project saved with the azimuthal map
  on opens with it again, without a question about temporary layers when QGIS
  closes (M3).
- DXCC entity, continent, CQ and ITU zone from the callsign with AD1C cty.dat
  and cty.csv (exact calls, portable prefixes, WAE entities counted as their
  DXCC entity). The files are downloaded with one click (offered at the first
  start, refreshed from the menu or the settings), cached in the QGIS profile and
  never bundled; without an internet connection the message says that the server
  cannot be reached (M4).
- Statistics panel: QSOs, DXCC entities, unique callsigns, grid squares, the
  longest QSO, the first and the last QSO, and counts and shares by continent,
  band and mode, computed in the background after every change of the log (M4).
- WSJT-X and JTDX live: UDP listener (unicast or multicast) on the address set
  in HamQ, started from the toolbar or the panel, or when QGIS starts; logged QSOs
  are saved and appear on the map within about a second with a "New QSO" message,
  and the panel shows the dial frequency, mode and DX call. A busy port or an
  address that is not one of this computer is explained (M5).
- Interface in English, Srpski (latinica) and Српски (ћирилица), switched from
  the toolbar, the menu or the settings without restarting QGIS: menus,
  tooltips, panel, dialogs, calendar month names, Processing algorithms, layer
  names, field aliases and legend labels (M6).
- Hamlib: rigctld client (frequency and mode, set from the panel), rotctld
  client (azimuth on a compass, turn, stop, long path), map tool that turns the
  antenna toward a clicked point with a beam line (first use confirmed with
  *Turn* / *Cancel*), rotator ranges such as 0-450 degrees, and manual QSO entry
  with frequency and mode taken from the radio. Connection errors say what to do
  (start rigctld or rotctld, check the address and port), the clients reconnect
  by themselves, and a QGIS that was busy for a few seconds does not cause a
  false disconnect (M7).
- Settings dialog: station, storage, WSJT-X, Hamlib, language and cty.dat
  download; decimal numbers accept a decimal point or comma.
- Documentation: README in English and Serbian with screenshots, the release
  procedure (`docs/RELEASING.md`), a fictional demo log (`docs/demo/demo_log.adi`,
  150 QSOs from KN04ft to six continents, made by `scripts/make_demo_log.py`) and
  `scripts/make_screenshots.py`, which takes the README screenshots with the real
  QGIS desktop.

### Security

- The WSJT-X listener binds the address set in HamQ, by default 127.0.0.1, so
  only programs on this computer can add QSOs to the log; listening on the
  network takes `0.0.0.0`, a LAN address, multicast or broadcast in the
  settings. The UDP socket never goes through a proxy.
- Callsigns, countries, client names and error texts from log files and the
  network are shown as plain text in the panel and in the message bar, never as
  HTML or links.

### Known limitations

- Checked with datagrams captured from a real WSJT-X 2.7.0 and with the Hamlib 4.6
  dummy radio and rotator, not yet on the air with real radios, rotators and a
  running WSJT-X or JTDX.
- A log written in Windows-1250 is read as Latin-1, so Š, Ž, Č, Ć and Đ in names
  come out wrong; save such a log as UTF-8 before importing it.
- HamQ waits 2 s for each answer of rigctld and rotctld; a radio or rotator that
  needs longer for one command keeps reconnecting.
- After a language switch, a problem already shown in the panel keeps the language
  it was shown in until it changes, and an open Processing dialog keeps its texts
  until it is opened again.
- Not yet supported: ADX (XML) logs, exporting ADIF, IPv6 addresses for WSJT-X.

### Tested

- QGIS 4.2 and 4.0 (Qt6), 3.44, 3.40 and 3.34 (Qt5): CI on 3.34, 3.44, 4.0 and
  4.2, `scripts/test_qgis.sh` locally, and the whole QGIS test suite on 3.40
  before the release; pure-Python core on Python 3.9 to 3.14.
- The QGIS desktop application (4.2 on Linux, 3.44 in Docker), offscreen with a
  clean profile: menus, toolbar, panel, a WSJT-X QSO on the map in 0.1 s (the first
  one on 3.44 in 1.1 s), the language switch, the Hamlib dummy daemons, the cty.dat
  download, and unloading and loading the plugin again.

[Unreleased]: https://github.com/vukovicvl/hamq/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/vukovicvl/hamq/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vukovicvl/hamq/releases/tag/v0.1.0
