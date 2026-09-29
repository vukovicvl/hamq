# HamQ

HamQ is a QGIS plugin for amateur radio operators: Maidenhead (QTH) locators,
your QSO log on the map, DXCC statistics and live QSOs from WSJT-X. It runs on
QGIS 3.34 and newer (Qt5) and on QGIS 4.x (Qt6), needs no extra Python
packages, and its interface is in English and Serbian (Latin or Cyrillic).

> **Status:** early development, version 0.1.0 (experimental). The plugin
> skeleton is in place; features arrive milestone by milestone, see
> [PLAN.md](PLAN.md).

## Planned for v0.1.0

- Maidenhead locator to coordinates and back (4, 6 and 8 characters), locator
  search from the toolbar, Maidenhead grid as a vector layer
- ADIF (`.adi`) import into a GeoPackage, without duplicates
- QSOs on the map as points and as geodesic paths from your QTH, with distance
  and bearing
- DXCC entity, continent, CQ and ITU zone from the callsign (AD1C `cty.dat`,
  downloaded on first use, never bundled)
- Statistics panel: QSOs, DXCC entities, continents, bands, modes
- Live QSOs from WSJT-X / JTDX over UDP
- Hamlib `rigctld` / `rotctld`: frequency and mode from the radio, turn the
  antenna with a click on the map
- English / Srpski (latinica) / Српски (ћирилица), switched with one click
  without restarting QGIS

## Srpski

HamQ je QGIS dodatak za radio-amatere: QTH lokator (Maidenhead), log veza na
mapi, DXCC statistika i veze uživo iz WSJT-X-a. Radi u QGIS-u 3.34 i novijem i
u QGIS-u 4.x, bez dodatnih Python paketa. Interfejs je na engleskom i srpskom
(latinica ili ćirilica), a jezik se menja jednim klikom, bez ponovnog
pokretanja QGIS-a. Plan razvoja je u [PLAN.md](PLAN.md).

## Install for development

Clone the repository and link the `hamq` package into your QGIS profile
(Linux; the plugin folder must be called `hamq`):

```bash
git clone https://github.com/vukovicvl/hamq.git
cd hamq

# QGIS 4.x
mkdir -p ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins
ln -sfn "$PWD/hamq" ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/hamq

# QGIS 3.x
mkdir -p ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins
ln -sfn "$PWD/hamq" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/hamq
```

On Windows the profile is in `%APPDATA%\QGIS\QGIS4\profiles\default` (use
`mklink /D`), on macOS in `~/Library/Application Support/QGIS/QGIS4/profiles/default`.

Restart QGIS, open *Plugins > Manage and Install Plugins*, enable *Show also
experimental plugins* in *Settings*, then enable **HamQ** under *Installed*.
The *HamQ* menu appears under *Plugins* and the provider *HamQ* in the
Processing Toolbox. The *Plugin Reloader* plugin helps while developing.

## Development

Rules for contributors (and coding agents) are in [AGENTS.md](AGENTS.md), the
module contract in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```bash
# core unit tests (pure Python, no QGIS needed)
python3 -m pytest tests/core -q

# lint and format
ruff check hamq tests scripts
ruff format --check hamq tests scripts

# QGIS integration tests: local QGIS 4.x, or Docker QGIS 3.44 / 4.0 / 3.34
scripts/test_qgis.sh local
scripts/test_qgis.sh all

# build the release zip: dist/hamq-<version>.zip
python3 scripts/package.py
```

`hamq/core/` is pure Python 3.9+ without `qgis` or Qt imports; everything that
touches QGIS lives in `qgis_io/`, `processing/`, `gui/` and `net/`. Version
differences between QGIS 3.34 .. 4.x and Qt5 / Qt6 are resolved in one place,
`hamq/qgis_io/compat.py`.

## Credits and license

- DXCC data: `cty.dat` by Jim Reisert, AD1C ([country-files.com](https://www.country-files.com/)),
  downloaded by the plugin on first use.
- WSJT-X UDP protocol: Joe Taylor, K1JT, and the WSJT Development Group.

HamQ is free software, licensed under the GNU General Public License version 3
(see [LICENSE](LICENSE)).
