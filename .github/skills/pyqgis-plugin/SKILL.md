---
name: pyqgis-plugin
description: Structure and rules for the HamQ QGIS plugin - layout, metadata.txt, plugin lifecycle and registry, Processing provider, QgsTask, Qt5/Qt6 and QGIS 3.34-4.x compatibility (compat.py), GeoPackage writing, translations, settings, tests inside QGIS. Read before touching anything under hamq/ outside core/.
---

# PyQGIS plugin structure

The contract between modules is `docs/ARCHITECTURE.md`. Where this skill and the
contract differ, the contract and the code win.

## Layout

```
hamq/
  __init__.py          # classFactory(iface): imports every subpackage -> HamQPlugin
  plugin.py            # HamQPlugin: actions, menu, toolbar, dock, provider (thin, registry)
  controller.py        # HamQController(QObject): glue between GUI, GeoPackage and network
  settings.py          # HamQSettings: typed QgsSettings keys under hamq/
  events.py            # events(): signals dataChanged, languageChanged, settingsChanged
  metadata.txt
  core/                # pure Python 3.9+, no qgis / PyQt imports
    maidenhead.py  adif.py  qso.py  geo.py  cty.py  stats.py  wsjtx.py
    hamlib.py  rigmode.py  bands.py  modes.py  i18n.py
  qgis_io/             # core objects <-> QGIS layers/features
    compat.py          # names that differ between QGIS 3.34..4.x and Qt5/Qt6
    fields.py          # make_field / make_fields, to_qdatetime / from_qdatetime
    gpkg.py            # GeoPackage log: schema, inserts with dedup, paths, reads (SQLite)
    layers.py          # project layers: load, refresh, aliases, retranslate
    styles.py          # default styles by band (resources/styles/*.qml)
  processing/
    provider.py        # HamQProvider (id "hamq"), ALGORITHMS
    common.py          # HamQAlgorithm base class, groups "maidenhead" / "log", helpers
    alg_locator_to_point.py  alg_grid.py  alg_import_adif.py  alg_recalculate.py
  gui/
    __init__.py        # ICONS_DIR, icon_path(), get_icon()
    dock.py  settings_dialog.py  locator_search.py  azimuthal.py  language.py
    rotator_tool.py  qso_dialog.py  message_box.py
  net/
    cty_download.py  wsjtx_listener.py  hamlib_client.py
  resources/
    icons/*.svg  styles/*.qml
  i18n/
    sr_Latn/*.json     # one catalog per module: English source -> Serbian Latin
tests/
  core/  qgis/  fixtures/
scripts/
  package.py  test_qgis.sh  make_styles.py  make_wsjtx_fixtures.py
  make_demo_log.py  make_screenshots.py
```

## metadata.txt

```ini
[general]
name=HamQ
qgisMinimumVersion=3.34
qgisMaximumVersion=4.99
supportsQt6=True
description=Amateur radio tools: Maidenhead locators, QSO log map, DXCC statistics and live QSOs from WSJT-X
version=0.1.1
changelog=0.1.1: ... 0.1.0: first experimental release. ...
author=Vladimir Vuković
email=...
about=...
tracker=https://github.com/vukovicvl/hamq/issues
repository=https://github.com/vukovicvl/hamq
homepage=https://github.com/vukovicvl/hamq
tags=amateur radio,ham radio,maidenhead,qth locator,adif,dxcc,wsjt-x,jtdx,ft8,qso,log,hamlib,antenna,serbian
icon=resources/icons/hamq.svg
experimental=True
deprecated=False
hasProcessingProvider=yes
license=GPL-3.0-or-later
```

- `version` equals `[project] version` in `pyproject.toml`. `scripts/package.py` checks
  the required keys and the icon and refuses to build when the versions differ.
- `changelog`: plain text (no HTML) that names the version (`x.y.z: ...`); the plugin
  manager and plugins.qgis.org show it, `package.py --release` checks it.
- Keep `supportsQt6=True`. plugins.qgis.org marks the key as deprecated and warns on
  upload (QGIS 4 support follows from `qgisMaximumVersion`). The warning is expected:
  QGIS 3.x builds on Qt6 mark a plugin without the key as incompatible, and
  `tests/core/test_package.py` and `tests/qgis/test_plugin_load.py` assert it.
- `experimental=True` while the version is 0.x. Release steps: `docs/RELEASING.md`.

## Lifecycle

```python
# __init__.py
def classFactory(iface):
    # Import every subpackage by name: QGIS records them, so unloadPlugin()
    # removes all HamQ modules and an upgrade never runs stale code.
    from . import core, gui, net, processing, qgis_io  # noqa: F401
    from .plugin import HamQPlugin
    return HamQPlugin(iface)
```

```python
# plugin.py (shortened)
class HamQPlugin:
    def initProcessing(self):                  # also called by qgis_process; idempotent
        if self.provider is None:
            provider = HamQProvider()
            if QgsApplication.processingRegistry().addProvider(provider):
                self.provider = provider       # the registry owns and deletes it

    def initGui(self):
        try:
            self._init_gui()
        except Exception:
            self.unload()                      # a failing initGui() undoes itself
            raise

    def _init_gui(self):
        from .controller import HamQController  # lazy: qgis_process never loads the GUI

        self.connect_signal(events().languageChanged, self._on_language_changed)
        self.controller = HamQController(self.iface, parent=self.iface.mainWindow())
        self.add_cleanup(self._release_controller)
        self.initProcessing()
        self.toolbar = self.iface.addToolBar(TOOLBAR_TITLE)
        self.add_dock_widget(self.controller.dock)
        self.add_cleanup(layers.connect_events())  # returns its disconnect function
        self.import_action = self.add_action(
            "import_adif.svg", tr_noop("Import ADIF..."), self._on_import_triggered
        )
        ...
        self.retranslate_ui()
        self.controller.start()

    def unload(self):                          # safe to call more than once
        self._disconnect_all()                 # every connect_signal()
        self._run_cleanups()                   # every add_cleanup(), last in first out
        self._remove_docks()
        self._remove_actions()                 # menu "&HamQ" and toolbar
        self._remove_toolbar()
        self._remove_provider()
```

Registry of `HamQPlugin` (extend through it, never bypass it):

- `add_action(icon, text, callback=None, *, add_to_menu=True, add_to_toolbar=True,
  checkable=False, checked=False, tooltip=None, enabled=True, object_name=None) -> QAction`:
  `text` / `tooltip` are English sources marked with `tr_noop("...")`; `retranslate_ui()`
  translates them again after every language change.
- `add_dock_widget(dock, area)`, `connect_signal(signal, slot)`, `add_cleanup(callback)`
  (a failing callback is logged and the unload goes on).
- `_on_language_changed(language)`: `retranslate_ui()` and `provider.refreshAlgorithms()`.
- Slots are wrapped (`_guarded`): an exception is logged in the HamQ tab with its
  traceback and never reaches Qt.
- The work is done by `HamQController` (settings, language manager, cty.dat, WSJT-X
  listener, Hamlib clients, azimuthal map, rotator map tool, dock): `start()` once,
  `cleanup()` on unload.

`unload()` must undo everything `initGui()` did. Leaking a signal connection
or a socket is the most common reload bug; `tests/qgis/test_plugin_load.py` loads and
unloads twice and counts the receivers of the process-wide signals.

## Qt5 / Qt6 and QGIS 3.34 .. 4.x

```python
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDockWidget

from ..qgis_io import compat
from ..qgis_io.compat import MSG_WARNING, WKB_POINT, QAction, QActionGroup
```

- Scoped enums always: `Qt.DockWidgetArea.RightDockWidgetArea` (`compat.DOCK_RIGHT`).
- `exec()` not `exec_()`; `dialog.exec() == compat.DIALOG_ACCEPTED` works on both.
- `qgis_io/compat.py` is the only place that spells a version-specific name: QGIS
  enums as the scoped `Qgis.*` name (most exist since 3.36), else the legacy
  class-scoped name (3.34); `QAction` / `QActionGroup` from QtGui (Qt6), else QtWidgets
  (Qt5; QGIS 3.34 does not re-export `QActionGroup` from `qgis.PyQt.QtGui`). A name
  that cannot be resolved is `None` and listed in `compat.MISSING`, which
  `tests/qgis/test_compat.py` asserts empty on every target. Append a missing
  constant to `compat.py` with a test.
- Fields: `fields.make_field(name, kind)` / `make_fields(spec)` with kind `int`, `real`,
  `text` or `datetime` (`QMetaType.Type` on 3.38+, `QVariant` before; QGIS 4 removed
  the `QVariant` constructor of `QgsField`).
- Datetimes: set attributes with `fields.to_qdatetime(dt)` (UTC `QDateTime`, stored as
  `...Z`), read them with `fields.from_qdatetime(value)` (aware UTC `datetime`). A
  Python `datetime` in `setAttributes` is rejected by `QgsVectorFileWriter` ("Could not
  convert value"); the OGR provider rejects it or reports success and stores NULL,
  depending on the version.
- Message log: QGIS 4 emits only `QgsMessageLog.messageReceivedWithFormat`, QGIS 3
  only `messageReceived`. Use `disconnect = compat.connect_message_log(slot)` with
  `slot(message, tag, level)`.
- Log with `QgsMessageLog.logMessage(msg, "HamQ", compat.MSG_WARNING)`. Text from files
  or the network is escaped (`html.escape(text, quote=False)`) where
  `compat.LOG_PANEL_SHOWS_HTML` (QGIS 3.34 to 3.40.6, 3.42.0 / 3.42.1 render the Log
  Messages panel as HTML); message bar texts are always escaped; labels use
  `compat.TEXT_PLAIN`.
- Qt names the standard buttons of `QMessageBox` in the QGIS language and has no
  Serbian: use `gui/message_box.py` `MessageBox.question()` / `MessageBox.about()`.

## Processing algorithm skeleton

```python
from qgis.core import QgsProcessingParameterFeatureSink, QgsProcessingParameterString

from ..core.i18n import tr
from ..qgis_io import compat
from .common import GROUP_MAIDENHEAD, HamQAlgorithm


class LocatorToPointAlgorithm(HamQAlgorithm):  # gives tr, createInstance, group, groupId, icon
    LOCATORS, OUTPUT = "LOCATORS", "OUTPUT"
    ICON = "locator.svg"                        # in resources/icons
    GROUP_ID = GROUP_MAIDENHEAD                 # or GROUP_LOG

    def name(self): return "locator_to_point"
    def displayName(self): return tr("Locator to point")
    def shortHelpString(self): return tr("...")
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            self.LOCATORS, tr("Maidenhead locators"), multiLine=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, tr("Locator points"), type=compat.SOURCE_VECTOR_POINT))
    def processAlgorithm(self, parameters, context, feedback): ...
```

Register it by appending the class to `ALGORITHMS` in `processing/provider.py`;
`HamQProvider.loadAlgorithms()` adds one instance of each (ids `hamq:locator_to_point`,
`hamq:maidenhead_grid`, `hamq:import_adif`, `hamq:recalculate`).

- `processAlgorithm` runs in a worker thread when started from the toolbox: never touch
  the project, layers or GUI there. Write the log only with `gpkg.insert_qsos` /
  `gpkg.recalculate`, then emit `events().dataChanged(path)` from `postProcessAlgorithm`
  (main thread) with `common.notify_data_changed(path)`. Processing skips
  `postProcessAlgorithm` for a canceled run, so a canceled import announces what it
  saved itself.
- Log algorithms share `common.add_station_parameters` (`MY_GRID`, `USE_CTY`),
  `load_cty` (cached cty.dat, no network) and `report_warnings`.
- Sinks: `sink.addFeature(feature, compat.SINK_FAST_INSERT)`; on failure raise
  `QgsProcessingException(self.writeFeatureError(sink, parameters, self.OUTPUT))`.

Check `feedback.isCanceled()` in loops. Report progress with `feedback.setProgress()`.

## Background work

- Long work that is not a Processing algorithm: subclass `QgsTask`, add via
  `QgsApplication.taskManager().addTask(task)`. Never touch layers or GUI from
  `run()`; do it in `finished()`.
- Example, the statistics refresh (`controller.py`, `_StatsTask`): `run()` calls
  `gpkg.read_qso_rows` and `core.stats.compute_stats`; flags `compat.TASK_CAN_CANCEL`,
  `TASK_CANCEL_WITHOUT_PROMPT`, `TASK_HIDDEN` and `TASK_SILENT` (not shown in the task
  manager or the status bar, no notification, QGIS may cancel it without asking);
  `finished()` hands the result to the dock. Requests that arrive while it runs are
  merged into one more refresh; a module-level set keeps the task object alive until
  `finished()`; when `addTask()` returns 0 the refresh runs at once (`refresh_now()`).
- Network: `QgsNetworkAccessManager.instance()` (respects QGIS proxy settings), used
  for the cty.dat download.
- UDP: `QUdpSocket` with `readyRead` signal. No threads needed. The WSJT-X listener
  binds the configured address (127.0.0.1 by default) and uses no proxy
  (`QNetworkProxy(compat.NET_PROXY_NONE)`).
- TCP (Hamlib): `QTcpSocket` without proxy, signals and `QTimer` only: one command in
  flight, flushed at once, 2 s timeout, reconnect every 5 s. Never `waitFor*()`.
- The cty.dat check after a download runs in a daemon `threading.Thread` polled by a
  `QTimer`; the thread touches no Qt object.

## GeoPackage

- One log per user (`HamQSettings.gpkg_path`, default `<profile>/hamq/hamq.gpkg`),
  EPSG:4326: `qso` (Point), `qso_path` (MultiLineString), `hamq_meta`
  (`schema_version`, now 2). The schema is in `PLAN.md` and the `gpkg.py` docstring.
  The file is created lazily: first import, live or manual QSO.
- `gpkg.ensure_gpkg(path)` creates the tables with `QgsVectorFileWriter.create()`,
  driver `GPKG` (`compat.WRITER_CREATE_OR_OVERWRITE_FILE` for the first table,
  `WRITER_CREATE_OR_OVERWRITE_LAYER` for the others), so GDAL registers them; the
  indexes (UNIQUE `qso_dedup_key_idx`), the trigger `qso_delete_paths` and the schema
  version are added with `sqlite3`. It upgrades an older schema (`_migrate`) and never
  changes a file HamQ cannot use (`GpkgError`).
- **Rows are written only by `gpkg.insert_qsos(path, qsos, feedback=None,
  chunk_size=1000)` and `gpkg.recalculate(path, station, cty=None, feedback=None, *,
  force_station=False)`**: plain SQL on their own `sqlite3` connection, one
  `BEGIN IMMEDIATE` transaction per chunk, under a process-wide write lock; a locked
  file is waited for (10 s, 1 s in the main thread). Why: QGIS layers or data providers
  that wrote the GeoPackage from a worker thread while the main thread started or saved
  an edit session of the same file deadlocked, crashed or left a connection stuck with
  "file is not a database" in QGIS 4.2 stress tests. So never `provider.addFeatures()`
  or a `QgsVectorFileWriter` for log rows.
- Deduplication: UNIQUE index on `dedup_key`; `insert_qsos` skips keys already in the
  file and keys repeated within the batch.
- Datetimes are stored as `to_qdatetime` UTC text (`2026-09-15T18:45:00.000Z`);
  `read_qso_rows` returns aware UTC `datetime` objects.
- In the main thread `insert_qsos` / `recalculate` refresh the loaded layers of the
  file (layers in edit mode are left alone, with a warning). From a worker thread
  nothing in the project is touched: the caller emits `events().dataChanged(path)` from
  the main thread, and `layers.connect_events()` turns it into `layers.refresh_layers`.
- Load with `layers.load_layers(path)` (group "HamQ", default style, translated names
  and aliases); the source is `gpkg.layer_uri(path, "qso")`, i.e.
  `f"{path}|layername=qso"`, provider `ogr`.
- A read-only file or folder raises `GpkgError` with a translated message that says
  what to check, before anything is written.

## Settings

Store in `QgsSettings()` under the prefix `hamq/`, always through `HamQSettings`
(`hamq/settings.py`, typed properties):
`hamq/my_call`, `hamq/my_grid`, `hamq/gpkg_path`, `hamq/wsjtx_addr`, `hamq/wsjtx_port`,
`hamq/wsjtx_autostart`, `hamq/language`, `hamq/last_serbian`, `hamq/cty_downloaded`,
`hamq/rig_enabled`, `hamq/rig_host`, `hamq/rig_port`, `hamq/rig_poll_ms`,
`hamq/rot_enabled`, `hamq/rot_host`, `hamq/rot_port`, `hamq/rot_min_az`,
`hamq/rot_max_az`, `hamq/rot_confirmed`.

Defaults: `gpkg_path` `default_gpkg_path()` (`<profile>/hamq/hamq.gpkg`), `wsjtx_addr`
`127.0.0.1`, `wsjtx_port` 2237, `language` `auto` (or `en`, `sr_Latn`, `sr_Cyrl`),
`last_serbian` `sr_Latn`, `rig_host` / `rot_host` `127.0.0.1`, `rig_port` 4532,
`rot_port` 4533, `rig_poll_ms` 1000, `rot_min_az` 0, `rot_max_az` 360; the flags
`False`, the other texts `""`.

- Getters never fail: a missing or invalid stored value gives the default. Setters
  normalize (callsign uppercase, locator `KN04ft`, texts stripped, language codes
  canonical) and raise `ValueError` for an invalid value (port outside 1..65535,
  `rig_poll_ms <= 0`, empty path, address or host, unknown language).
- Setters only store; whoever saves settings from the UI emits
  `events().settingsChanged` once afterwards.
- Plugin files live in `profile_dir()` (`<QGIS settings dir>/hamq`): the GeoPackage and
  the cty.dat cache (`cty_cache_path()`, `cty.csv` next to it).

## Translations

```python
from ..core.i18n import tr, tr_noop

LABELS = {"total": tr_noop("Total QSOs")}   # marked at import time (tr() there is an error)
label.setText(tr(LABELS["total"]))          # translated when shown
text = tr("Record {index}: missing CALL, skipped").format(index=i)  # format after tr()

class SomeDialog(QDialog):
    def tr(self, text): return tr(text)     # the HamQ translator, never QObject.tr
```

- Never `QCoreApplication.translate` or `QObject.tr`. The strings of
  `hamq/gui/dock.py` go into `hamq/i18n/sr_Latn/gui_dock.json` (`plugin.py` ->
  `plugin.json`): English source -> Serbian Latin, a flat JSON object, keys sorted,
  2-space indent, UTF-8. Cyrillic is derived at run time. Glossary and style rules:
  `docs/ARCHITECTURE.md`.
- Every widget with text implements `retranslate()` and connects to
  `events().languageChanged` (disconnected in its `cleanup()`). Layer names, aliases
  and legend labels follow through `layers.connect_events()`, the Processing toolbox
  through `provider.refreshAlgorithms()`.
- `tests/core/test_i18n_catalog.py` checks every `tr()` / `tr_noop()` argument, every
  catalog and the placeholders; with `HAMQ_STRICT_I18N=1` unused keys fail too.

## Tests inside QGIS

`scripts/test_qgis.sh <local|3.44|4.0|3.34|all> [pytest args]` runs `tests/qgis`:
`local` on the host QGIS 4.x (`QGIS_PYTHONPATH`, default
`/usr/share/qgis/python:/usr/share/qgis/python/plugins`), the others in Docker
(`qgis/qgis:3.44-trixie`, `qgis/qgis:4.0-trixie`, `camptocamp/qgis-server:3.34`, which
lacks QtSvg, so those checks skip) with the repository mounted read-only as your own
user; `all` runs every target and prints a summary. CI also runs `qgis/qgis:3.34` and
`qgis/qgis:4.2-trixie`.

`tests/qgis/conftest.py` starts one offscreen `QgsApplication` per session with a
throw-away profile (it refuses to run on a real one), initializes Processing and ends
the session with `os._exit(<pytest exit status>)`, because QGIS can crash while Python
shuts down (`HAMQ_TEST_NO_OS_EXIT=1` turns that off). Fixtures:

- `qgis_app`; `qgis_processing` (skips when the build has no Processing plugin);
- `iface`: `qgis.testing.mocked.get_iface()` wired to `FakeIface`, a real `QMainWindow`
  with plugin menu, toolbars, docks, map canvas and message bar;
- `tmp_gpkg` (path of a GeoPackage that does not exist yet), `clean_settings` (removes
  `hamq/` before and after), `clean_project` (`QgsProject.instance()`, cleared after);
- `process_events` (pending events and `deleteLater`), `log_messages`
  (`(message, tag, level)` of every log message, through `compat.connect_message_log`).

`tests/qgis/fake_hamlib.py` is an in-process `rigctld` / `rotctld`; `HAMQ_RIGCTLD` /
`HAMQ_ROTCTLD` (`host:port`) enable the optional tests against real daemons.
