# Changelog

All notable changes to HamQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Plugin scaffold: HamQ menu and toolbar, Processing provider `hamq`, one
  compatibility layer for QGIS 3.34 to 4.x (Qt5 and Qt6), typed settings,
  packaging script for plugins.qgis.org and CI (M0).
- Maidenhead locators with 2 to 8 characters: conversion both ways, cell bounds
  and centre, validation. Processing algorithms *Locator to point* and
  *Generate Maidenhead grid* (field to extended square, any extent CRS,
  antimeridian and poles). Locator search in the toolbar centres the map and
  highlights the cell (M1).
- ADIF (`.adi`) import that tolerates real-world logs: character- or
  byte-counted field lengths, latin-1 fallback, BOM, missing `<EOH>`/`<EOR>`,
  wrong lengths. Tested with logs from WSJT-X, N1MM, Log4OM, QRZ.com, LoTW and
  Xlog (M2).
- QSO normalization: validation, band from frequency, FT4 deduplicated across
  loggers, position from LAT/LON, then the locator, then cty.dat; path origin
  from MY_LAT/MY_LON, then MY_GRIDSQUARE, then my QTH (M2).
- GeoPackage log with a schema version table, deduplicated inserts and
  geodesic QSO paths split at the antimeridian; *Import ADIF* (re-importing a
  file adds nothing, bad records are skipped and reported) and *Recalculate
  distances and paths* for a new QTH or a new cty.dat. 10 000 QSOs import in
  about 3 s (M2, M3).
- HamQ layer group with translated layer names and field aliases, colour-blind
  aware styles by band, azimuthal equidistant map centred on my QTH with
  distance rings and azimuth lines (M3).
- DXCC entity, continent, CQ and ITU zone from the callsign with AD1C cty.dat
  and cty.csv (exact calls, portable prefixes, WAE entities counted as their
  DXCC entity). The files are downloaded on first use and cached, never bundled
  (M4).
- Statistics panel: QSOs, DXCC entities, unique callsigns, grid squares,
  longest QSO, and counts by continent, band and mode (M4).
- WSJT-X and JTDX live: UDP listener (unicast or multicast), logged QSOs
  appear on the map within about a second, dial frequency, mode and DX call in
  the panel (M5).
- Interface in English, Srpski (latinica) and Српски (ћирилица), switched from
  the toolbar, the menu or the settings without restarting QGIS: menus, panel,
  dialogs, Processing algorithms, layer names and field aliases (M6).
- Hamlib: rigctld client (frequency and mode, set from the panel), rotctld
  client (azimuth, turn, stop), map tool that turns the antenna toward a
  clicked point with a beam line, rotator ranges such as 0-450 degrees, and
  manual QSO entry with frequency and mode taken from the radio (M7).
- Settings dialog: station, storage, WSJT-X, Hamlib, language and cty.dat
  download.

### Tested

- QGIS 4.2 and 4.0 (Qt6), 3.44 and 3.34 (Qt5) through `scripts/test_qgis.sh`;
  pure-Python core on Python 3.9 to 3.14.
