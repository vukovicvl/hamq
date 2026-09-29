---
name: pyqgis-plugin
description: Structure and rules for the HamQ QGIS plugin - layout, metadata.txt, plugin lifecycle, Processing provider, QgsTask, Qt5/Qt6 compatibility, GeoPackage writing. Read before touching anything under hamq/ outside core/.
---

# PyQGIS plugin structure

## Layout

```
hamq/
  __init__.py          # classFactory(iface) -> HamQPlugin
  plugin.py            # HamQPlugin: initGui / unload
  metadata.txt
  core/                # pure Python, no qgis imports
    maidenhead.py  adif.py  geo.py  cty.py  wsjtx.py
  qgis_io/             # core objects <-> QGIS layers/features
    gpkg.py  styles.py
  processing/
    provider.py  alg_locator_to_point.py  alg_grid.py  alg_import_adif.py
  gui/
    dock.py  settings_dialog.py  locator_search.py
  net/
    cty_download.py  wsjtx_listener.py
  resources/
    icons/  styles/*.qml
  i18n/
tests/
  core/  qgis/  fixtures/
scripts/package.py
```

## metadata.txt (minimum)

```ini
[general]
name=HamQ
qgisMinimumVersion=3.34
qgisMaximumVersion=4.99
supportsQt6=True
description=Amateur radio tools: Maidenhead locator, QSO log map, DXCC, WSJT-X live
version=0.1.0
author=Vladimir Vuković
email=...
about=...
tracker=https://github.com/<owner>/hamq/issues
repository=https://github.com/<owner>/hamq
homepage=https://github.com/<owner>/hamq
tags=amateur radio,ham radio,maidenhead,adif,dxcc,wsjt-x
category=Plugins
icon=resources/icons/hamq.svg
experimental=True
hasProcessingProvider=yes
license=GPL-3.0
```

## Lifecycle

```python
# __init__.py
def classFactory(iface):
    from .plugin import HamQPlugin
    return HamQPlugin(iface)
```

```python
# plugin.py
from qgis.core import QgsApplication
from .processing.provider import HamQProvider

class HamQPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []

    def initProcessing(self):
        self.provider = HamQProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        # create actions, add to menu "&HamQ" and a toolbar

    def unload(self):
        for a in self.actions:
            self.iface.removePluginMenu("&HamQ", a)
            self.iface.removeToolBarIcon(a)
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        # stop UDP listener, close dock, disconnect signals
```

`unload()` must undo everything `initGui()` did. Leaking a signal connection
or a socket is the most common reload bug.

## Qt5 / Qt6

```python
from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtWidgets import QDockWidget
try:
    from qgis.PyQt.QtGui import QAction          # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction      # Qt5
```

- Scoped enums always: `Qt.DockWidgetArea.RightDockWidgetArea`.
- `exec()` not `exec_()`.
- `QVariant` types for fields: prefer `QMetaType.Type` on 3.38+; for 3.34
  compatibility, create fields with `QgsField(name, QVariant.String)` wrapped
  in a small helper `make_field(name, kind)` in `qgis_io/` that picks the right API.

## Processing algorithm skeleton

```python
from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterString,
                       QgsProcessingParameterFeatureSink, QgsFeatureSink)

class LocatorToPoint(QgsProcessingAlgorithm):
    INPUT, OUTPUT = "LOCATOR", "OUTPUT"
    def name(self): return "locator_to_point"
    def displayName(self): return self.tr("Locator to point")
    def group(self): return self.tr("Maidenhead")
    def groupId(self): return "maidenhead"
    def createInstance(self): return LocatorToPoint()
    def tr(self, s): return QCoreApplication.translate("HamQ", s)
    def initAlgorithm(self, config=None): ...
    def processAlgorithm(self, parameters, context, feedback): ...
```

Check `feedback.isCanceled()` in loops. Report progress with `feedback.setProgress()`.

## Background work

- Long work that is not a Processing algorithm: subclass `QgsTask`, add via
  `QgsApplication.taskManager().addTask(task)`. Never touch layers or GUI from
  `run()`; do it in `finished()`.
- Network: `QgsNetworkAccessManager.instance()` (respects QGIS proxy settings).
- UDP: `QUdpSocket` with `readyRead` signal. No threads needed.

## GeoPackage

- Create with `QgsVectorFileWriter.create()` or
  `QgsVectorFileWriter.writeAsVectorFormatV3()`, driver `GPKG`, EPSG:4326.
- Add a second layer to the same file with
  `options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer`.
- Load with `QgsVectorLayer(f"{path}|layername=qso", "QSO", "ogr")`.
- Deduplication: UNIQUE index on `dedup_key`, create via
  `sqlite3` on the gpkg after creation, or check existing keys before insert.
- Batch inserts: `provider.addFeatures(list)` in chunks of ~1000.

## Settings

Store in `QgsSettings()` under the prefix `hamq/`:
`hamq/my_call`, `hamq/my_grid`, `hamq/gpkg_path`, `hamq/wsjtx_port`, `hamq/wsjtx_addr`.
