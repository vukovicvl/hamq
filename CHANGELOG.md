# Changelog

All notable changes to HamQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Plugin scaffold: HamQ menu and toolbar, Processing provider `hamq`, one
  compatibility layer for QGIS 3.34 to 4.x (Qt5 and Qt6), typed settings,
  packaging script for plugins.qgis.org and CI (M0). Unloading or upgrading the
  plugin releases all of its modules, so an update takes effect without stale
  code.
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
  deletes its path; saved edits of the HamQ layers refresh the paths and the
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
  start, refreshed from the settings), cached and never bundled; without an
  internet connection the message says that the server cannot be reached (M4).
- Statistics panel: QSOs, DXCC entities, unique callsigns, grid squares,
  longest QSO, and counts by continent, band and mode (M4).
- WSJT-X and JTDX live: UDP listener (unicast or multicast) on the address set
  in HamQ, logged QSOs appear on the map within about a second, dial frequency,
  mode and DX call in the panel; a busy port or an address that is not one of
  this computer is explained (M5).
- Interface in English, Srpski (latinica) and Српски (ћирилица), switched from
  the toolbar, the menu or the settings without restarting QGIS: menus,
  tooltips, panel, dialogs, calendar month names, Processing algorithms, layer
  names, field aliases and legend labels (M6).
- Hamlib: rigctld client (frequency and mode, set from the panel), rotctld
  client (azimuth, turn, stop), map tool that turns the antenna toward a
  clicked point with a beam line (first use confirmed with *Turn* / *Cancel*),
  rotator ranges such as 0-450 degrees, and manual QSO entry with frequency and
  mode taken from the radio. Connection errors say what to do (start rigctld or
  rotctld, check the address and port), and a QGIS that was busy for a few
  seconds does not cause a false disconnect (M7).
- Settings dialog: station, storage, WSJT-X, Hamlib, language and cty.dat
  download; decimal numbers accept a decimal point or comma.

### Security

- The WSJT-X listener binds the address set in HamQ, by default 127.0.0.1, so
  only programs on this computer can add QSOs to the log; listening on the
  network takes `0.0.0.0`, a LAN address, multicast or broadcast in the
  settings. The UDP socket never goes through a proxy.
- Callsigns, countries, client names and error texts from log files and the
  network are shown as plain text in the panel and in the message bar, never as
  HTML or links.

### Tested

- QGIS 4.2 and 4.0 (Qt6), 3.44, 3.40 and 3.34 (Qt5): `scripts/test_qgis.sh`
  locally, and CI on 3.34, 3.44, 4.0 and 4.2; pure-Python core on Python 3.9 to
  3.14.
