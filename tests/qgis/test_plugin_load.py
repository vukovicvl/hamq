"""Plugin lifecycle: classFactory -> initGui -> unload, twice (reload bug check), the QGIS
plugin loader (qgis.utils, in a new interpreter), and the wiring of the actions, menu,
toolbar widgets and panel (INT-01)."""

from __future__ import annotations

import functools
import gc
import json
import os
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProject,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QModelIndex, Qt
from qgis.PyQt.QtWidgets import QDockWidget, QMenu, QPushButton, QToolBar

import hamq
from hamq import controller as controller_module
from hamq import plugin as plugin_module
from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, set_language, tr
from hamq.events import events
from hamq.gui import ICONS_DIR, icon_path
from hamq.gui.dock import HamQDock
from hamq.gui.language import SWITCH_TITLE, LanguageSwitchButton
from hamq.gui.locator_search import LocatorSearchWidget
from hamq.gui.settings_dialog import SettingsDialog
from hamq.net.cty_download import CtyManager
from hamq.processing import provider as provider_module
from hamq.qgis_io import compat
from hamq.settings import HamQSettings

#: Plugins > HamQ, in order (the language submenu is the bilingual SWITCH_TITLE).
MENU_TEXTS = [
    "Show HamQ panel",
    "Import ADIF...",
    "Listen to WSJT-X",
    "Log QSO...",
    "Point antenna on map",
    "Azimuthal map",
    "Maidenhead grid...",
    "Locator to point...",
    "Recalculate distances and DXCC data...",
    "Download cty.dat",
    "Settings...",
    SWITCH_TITLE,
    "About HamQ",
]
#: Actions in the HamQ toolbar, before the locator search and the language switch.
TOOLBAR_TEXTS = [
    "Show HamQ panel",
    "Import ADIF...",
    "Listen to WSJT-X",
    "Point antenna on map",
    "Azimuthal map",
    "Settings...",
]
ALGORITHM_IDS = [
    "hamq:locator_to_point",
    "hamq:maidenhead_grid",
    "hamq:import_adif",
    "hamq:recalculate",
]

REQUIRED_ICONS = (
    "hamq.svg",
    "panel.svg",
    "import_adif.svg",
    "wsjtx.svg",
    "grid.svg",
    "locator.svg",
    "azimuthal.svg",
    "settings.svg",
    "language.svg",
    "refresh.svg",
    "paths.svg",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Prefix of the result line printed by LOADER_SCRIPT.
LOADER_RESULT = "HAMQ-LOADER-RESULT "
#: Run in a new interpreter by test_qgis_plugin_loader_leaves_nothing_behind: loads, starts
#: and unloads HamQ three times with qgis.utils, the way QGIS desktop and its Plugin Manager
#: do. QGIS records the modules a plugin imports (qgis.utils._import, installed as
#: builtins.__import__) and unloadPlugin() removes exactly those from sys.modules.
LOADER_SCRIPT = r'''
import builtins
import contextlib
import gc
import io
import json
import os
import shutil
import sys
import time
import weakref

repo_root = sys.argv[1]
sys.path.insert(0, repo_root)
from qgis.testing import start_app

with contextlib.redirect_stdout(io.StringIO()):
    start_app(cleanup=False)  # a new temporary QGIS profile
import qgis.utils
from qgis.core import Qgis, QgsApplication

from tests.qgis.conftest import FakeIface, _process_events

profile = os.environ["QGIS_CUSTOM_CONFIG_PATH"]
settings_dir = os.path.realpath(QgsApplication.qgisSettingsDirPath())
if not settings_dir.startswith(os.path.realpath(profile)):
    print("HAMQ-LOADER-RESULT " + json.dumps({"error": "no temporary profile"}), flush=True)
    os._exit(3)

problems = []


def on_message(message, tag, level, *_format):
    if tag == "HamQ" and level in (Qgis.MessageLevel.Warning, Qgis.MessageLevel.Critical):
        problems.append(message)


log = QgsApplication.messageLog()
getattr(log, "messageReceivedWithFormat", log.messageReceived).connect(on_message)

qgis.utils.iface = FakeIface()  # not a mock: a mock keeps every argument it was given
qgis.utils.plugin_paths = [repo_root]
qgis.utils.updateAvailablePlugins()


def hamq_modules():
    return sorted(name for name in sys.modules if name.split(".")[0] == "hamq")


def detached():
    """Modules that are not the attribute of their package (a reused stale package)."""
    result = []
    for name in hamq_modules():
        package, _dot, child = name.rpartition(".")
        if package and getattr(sys.modules.get(package), child, None) is not sys.modules[name]:
            result.append(name)
    return result


def settle():
    deadline = time.monotonic() + 10
    while QgsApplication.taskManager().countActiveTasks() and time.monotonic() < deadline:
        _process_events()
        time.sleep(0.01)
    for _round in range(3):
        _process_events()
        gc.collect()


def hamq_objects():
    """Classes of the objects of HamQ classes that are still alive."""
    names = set()
    for obj in gc.get_objects():
        module = getattr(type(obj), "__module__", None) or ""
        if module.split(".")[0] == "hamq":
            names.add(f"{module}.{type(obj).__qualname__}")
    return sorted(names)


result = {
    "hooked": builtins.__import__ is qgis.utils._import,
    "available": "hamq" in qgis.utils.available_plugins,
    "before": hamq_modules(),
    "cycles": [],
}
for _cycle in range(3):
    cycle = {"loaded": qgis.utils.loadPlugin("hamq"), "started": qgis.utils.startPlugin("hamq")}
    cycle["provider"] = QgsApplication.processingRegistry().providerById("hamq") is not None
    cycle["detached"] = detached()
    references = {name: weakref.ref(sys.modules[name]) for name in hamq_modules()}
    cycle["unloaded"] = qgis.utils.unloadPlugin("hamq")
    settle()
    cycle["left"] = hamq_modules()
    cycle["alive"] = sorted(name for name, ref in references.items() if ref() is not None)
    cycle["objects"] = hamq_objects()
    result["cycles"].append(cycle)
result["problems"] = problems
print("HAMQ-LOADER-RESULT " + json.dumps(result), flush=True)
shutil.rmtree(profile, ignore_errors=True)
os._exit(0)
'''


def provider_ids() -> list[str]:
    return [provider.id() for provider in QgsApplication.processingRegistry().providers()]


def hamq_menu(main_window):
    """The "&HamQ" submenu of the Plugins menu, or None."""
    for top in main_window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.menu() is not None and action.text() == plugin_module.MENU_TITLE:
                return action.menu()
    return None


def language_receivers() -> int:
    obj = events()
    return obj.receivers(obj.languageChanged)


def all_receivers() -> dict[str, int]:
    """Receivers of every process-wide signal the plugin connects to.

    Objects of earlier tests that only wait for Python's garbage collector or for a
    deferred delete are collected first: they would leave while the test runs.
    """
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    hub = events()
    project = QgsProject.instance()
    return {
        "languageChanged": hub.receivers(hub.languageChanged),
        "settingsChanged": hub.receivers(hub.settingsChanged),
        "dataChanged": hub.receivers(hub.dataChanged),
        "readProject": project.receivers(project.readProject),
        "cleared": project.receivers(project.cleared),
        "crsChanged": project.receivers(project.crsChanged),
    }


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


def wait_until(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        QgsApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.002)


def bar_texts(iface) -> list[str]:
    return [item.text() for item in iface.messageBar().items()]


def action_by_text(plugin, text):
    return next(action for action in plugin.actions() if action.text() == text)


@pytest.fixture(autouse=True)
def plugin_env(clean_settings, tmp_path, monkeypatch):
    """English, the log and a cty.dat cache in tmp_path; the project is cleared afterwards."""
    settings = HamQSettings()
    settings.language = LANG_EN
    settings.gpkg_path = str(tmp_path / "hamq_log.gpkg")
    set_language(LANG_EN)
    monkeypatch.setattr(
        controller_module,
        "CtyManager",
        functools.partial(CtyManager, cache_path=str(tmp_path / "cty" / "cty.dat")),
    )
    yield settings
    set_language(LANG_EN)
    QgsProject.instance().clear()


def toolbox_model():
    """A Processing Toolbox tree model with the toolbox filter (as in the QGIS dock)."""
    gui = pytest.importorskip("qgis.gui", reason="qgis.gui is not available in this build")
    proxy_class = gui.QgsProcessingToolboxProxyModel
    try:
        toolbox_filter = proxy_class.Filter.Toolbox
    except AttributeError:  # QGIS 3.34 has only the old name
        toolbox_filter = proxy_class.FilterToolbox
    model = proxy_class(None, QgsApplication.processingRegistry())
    model.setFilters(toolbox_filter)
    return model


def toolbox_rows(model, parent=None) -> list[tuple[str, list]]:
    """``(display text, children)`` of every visible row below ``parent`` (the root)."""
    parent = QModelIndex() if parent is None else parent
    rows = []
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        rows.append((model.data(index, Qt.ItemDataRole.DisplayRole), toolbox_rows(model, index)))
    return rows


def toolbox_children(model, name: str) -> list[tuple[str, list]] | None:
    """Children of the top-level toolbox row ``name`` (a provider), None when not shown."""
    for text, children in toolbox_rows(model):
        if text == name:
            return children
    return None


@pytest.fixture(autouse=True)
def _no_leftover_provider():
    """Remove a provider that a failed test left registered, so failures do not cascade.

    Runs after the test and its other fixtures; tests that check unload() assert on
    the registry themselves before this runs.
    """
    yield
    registry = QgsApplication.processingRegistry()
    leftover = registry.providerById("hamq")
    if leftover is not None:
        registry.removeProvider(leftover)


@pytest.fixture
def loaded(iface, process_events):
    """A plugin after initGui(); unloaded after the test."""
    plugin = hamq.classFactory(iface)
    plugin.initGui()
    yield plugin
    plugin.unload()
    process_events()


def test_class_factory_returns_plugin(iface):
    plugin = hamq.classFactory(iface)
    assert isinstance(plugin, plugin_module.HamQPlugin)
    assert plugin.iface is iface


def test_load_and_unload_twice(iface, process_events, log_messages):
    iface.mapCanvas()  # made on first use; the canvas itself connects to the project
    iface.messageBar()
    process_events()  # objects of earlier tests waiting for deletion still count otherwise
    receivers_before = all_receivers()
    for _round in range(2):
        plugin = hamq.classFactory(iface)
        plugin.initGui()

        assert provider_ids().count("hamq") == 1
        provider = QgsApplication.processingRegistry().providerById("hamq")
        assert provider is not None
        assert provider.name() == "HamQ"
        assert sorted(alg.id() for alg in provider.algorithms()) == sorted(ALGORITHM_IDS)

        toolbar = iface.mainWindow().findChild(QToolBar, plugin_module.TOOLBAR_OBJECT_NAME)
        assert toolbar is not None
        assert toolbar is plugin.toolbar
        assert toolbar.windowTitle() == "HamQ"

        menu = hamq_menu(iface.mainWindow())
        assert menu is not None
        assert [action.text() for action in menu.actions()] == MENU_TEXTS
        assert plugin.about_action not in toolbar.actions()

        controller = plugin.controller
        dock = iface.mainWindow().findChild(QDockWidget, "HamQDock")
        assert isinstance(dock, HamQDock)
        assert dock is controller.dock
        assert controller.is_started()
        loaded_receivers = all_receivers()
        for name in ("languageChanged", "settingsChanged", "dataChanged"):
            assert loaded_receivers[name] > receivers_before[name], name

        actions = plugin.actions()
        assert len(actions) == len(MENU_TEXTS) - 1  # the language submenu is no action
        language_menu = plugin.language_menu
        search, button = plugin.locator_search, plugin.language_button
        tool = controller.rotator_tool
        canvas = iface.mapCanvas()

        plugin.unload()
        process_events()

        assert "hamq" not in provider_ids()
        assert plugin.provider is None
        assert plugin.toolbar is None
        assert plugin.controller is None
        assert iface.mainWindow().findChild(QToolBar, plugin_module.TOOLBAR_OBJECT_NAME) is None
        assert sip.isdeleted(toolbar)
        assert hamq_menu(iface.mainWindow()) is None
        assert all(sip.isdeleted(action) for action in actions)
        assert plugin.actions() == []
        assert iface.mainWindow().findChild(QDockWidget, "HamQDock") is None
        for obj in (dock, controller, language_menu, search, button, tool):
            assert sip.isdeleted(obj)
        assert canvas.mapTool() is None
        assert all_receivers() == receivers_before

    # nothing for the log panel: no warning or error from HamQ, Processing or Qt
    problems = [m for m in log_messages if m[2] in (compat.MSG_WARNING, compat.MSG_CRITICAL)]
    assert problems == []


def test_unload_is_idempotent(iface, process_events):
    plugin = hamq.classFactory(iface)
    plugin.initGui()
    plugin.unload()
    plugin.unload()
    process_events()
    assert "hamq" not in provider_ids()


def test_init_processing_only_then_unload(iface, process_events, log_messages):
    """qgis_process calls initProcessing() without initGui()."""
    plugin = hamq.classFactory(iface)
    plugin.initProcessing()
    first = plugin.provider
    assert first is not None
    plugin.initProcessing()  # idempotent: no second provider, no failed registration
    assert plugin.provider is first
    assert provider_ids().count("hamq") == 1
    # A second registration would be refused by the registry (duplicate id) and
    # logged by the plugin as a warning; the provider count alone cannot show it.
    assert [m for m in log_messages if m[1] == plugin_module.LOG_TAG] == []
    plugin.unload()
    process_events()
    assert "hamq" not in provider_ids()


def test_qgis_plugin_loader_leaves_nothing_behind(tmp_path):
    """loadPlugin / startPlugin / unloadPlugin of qgis.utils (QGIS start, Plugin Manager,
    plugin upgrade) three times in a new interpreter: after every unload no hamq module is
    left in sys.modules, and no hamq module or object is alive. A module QGIS did not record
    would stay, keep the old load (parsed cty.dat, translator, settings, the events() hub)
    in memory and be reused by the next load instead of being imported again."""
    script = tmp_path / "plugin_loader.py"
    script.write_text(LOADER_SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    env.pop("QGIS_CUSTOM_CONFIG_PATH", None)  # the session's profile; start_app makes one
    completed = subprocess.run(
        [sys.executable, str(script), str(REPO_ROOT)],
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
        timeout=300,
    )
    stdout = completed.stdout.decode("utf-8", "replace")
    lines = [line for line in stdout.splitlines() if line.startswith(LOADER_RESULT)]
    stderr = completed.stderr.decode("utf-8", "replace")
    assert lines, f"exit status {completed.returncode}\n{stdout[-3000:]}\n{stderr[-3000:]}"
    result = json.loads(lines[-1][len(LOADER_RESULT) :])
    assert "error" not in result, result
    assert result["hooked"], "qgis.utils does not track the plugin imports here"
    assert result["available"]
    assert result["before"] == []
    assert len(result["cycles"]) == 3
    for number, cycle in enumerate(result["cycles"], 1):
        steps = {key: cycle[key] for key in ("loaded", "started", "provider", "unloaded")}
        assert all(steps.values()), (number, steps)
        assert cycle["detached"] == [], number
        assert cycle["left"] == [], number
        assert cycle["alive"] == [], number
        assert cycle["objects"] == [], number
    assert result["problems"] == []


def test_language_change_retranslates_actions(loaded, monkeypatch):
    calls = []

    def fake_tr(text):
        calls.append(text)
        return text.upper()

    monkeypatch.setattr(plugin_module, "tr", fake_tr)
    events().languageChanged.emit("sr_Latn")
    assert loaded.about_action.text() == "ABOUT HAMQ"
    assert "About HamQ" in calls

    monkeypatch.undo()
    loaded.retranslate_ui()
    assert loaded.about_action.text() == "About HamQ"


def test_no_retranslate_after_unload(iface, process_events, monkeypatch):
    plugin = hamq.classFactory(iface)
    plugin.initGui()
    plugin.unload()
    process_events()
    called = []
    monkeypatch.setattr(plugin, "retranslate_ui", lambda: called.append(True))
    events().languageChanged.emit("en")
    assert called == []


def test_add_action_options(loaded, process_events):
    triggered = []
    action = loaded.add_action(
        "grid.svg",
        "Test action",
        triggered.append,
        add_to_menu=False,
        add_to_toolbar=True,
        checkable=True,
        tooltip="Tooltip source",
        object_name="HamQTestAction",
    )
    assert action.objectName() == "HamQTestAction"
    assert action.isCheckable()
    assert action.toolTip() == "Tooltip source"
    assert not action.icon().isNull()
    assert action in loaded.toolbar.actions()
    assert action not in hamq_menu(loaded.iface.mainWindow()).actions()
    action.trigger()
    assert triggered == [True]
    assert action in loaded.actions()


def test_add_action_checked_state(loaded):
    checked = loaded.add_action(None, "Checked action", checkable=True, checked=True)
    assert checked.isCheckable()
    assert checked.isChecked()
    unchecked = loaded.add_action(None, "Unchecked action", checkable=True)
    assert unchecked.isCheckable()
    assert not unchecked.isChecked()
    plain = loaded.add_action(None, "Plain action", checked=True)  # checked needs checkable
    assert not plain.isCheckable()
    assert not plain.isChecked()
    assert plain.icon().isNull()


def test_cleanups_run_last_in_first_out(iface, process_events):
    plugin = hamq.classFactory(iface)
    plugin.initGui()
    order = []
    plugin.add_cleanup(lambda: order.append("first"))
    plugin.add_cleanup(lambda: order.append("second"))

    def failing():
        raise RuntimeError("boom")

    plugin.add_cleanup(failing)  # a failing cleanup must not stop unload()
    plugin.unload()
    process_events()
    assert order == ["second", "first"]
    assert "hamq" not in provider_ids()


def test_dock_widget_is_removed_on_unload(iface, process_events):
    from qgis.PyQt.QtWidgets import QDockWidget

    plugin = hamq.classFactory(iface)
    plugin.initGui()
    dock = QDockWidget("HamQ test dock")
    dock.setObjectName("HamQTestDock")
    plugin.add_dock_widget(dock)
    assert iface.mainWindow().findChild(QDockWidget, "HamQTestDock") is dock
    plugin.unload()
    process_events()
    assert iface.mainWindow().findChild(QDockWidget, "HamQTestDock") is None


def test_about_box(loaded, monkeypatch):
    shown = []

    def fake_about(parent, title, text):
        shown.append((parent, title, text))

    # HamQ's MessageBox names the OK button in the HamQ language (QMessageBox would not).
    monkeypatch.setattr(plugin_module.MessageBox, "about", staticmethod(fake_about))
    loaded.about_action.trigger()
    assert len(shown) == 1
    parent, title, text = shown[0]
    assert parent is loaded.iface.mainWindow()
    assert title == "About HamQ"
    meta = plugin_module.plugin_metadata()
    assert meta["version"] in text
    assert f'href="{meta["repository"]}"' in text
    assert "AD1C" in text
    assert "WSJT" in text
    assert "Hamlib" in text


def test_plugin_metadata():
    meta = plugin_module.plugin_metadata()
    assert meta["name"] == "HamQ"
    assert meta["qgisMinimumVersion"] == "3.34"
    assert meta["supportsQt6"] == "True"
    assert os.path.isfile(os.path.join(plugin_module.PLUGIN_DIR, meta["icon"]))
    assert meta["changelog"].startswith(meta["version"] + ": ")
    # The plugin manager shows 'about': WSJT-X / JTDX and Hamlib are separate programs.
    assert "separate programs" in meta["about"]
    assert "rigctld" in meta["about"] and "rotctld" in meta["about"]
    # 'category' is optional and only Raster, Vector, Database, Mesh or Web (QGIS docs).
    assert meta.get("category", "Web") in ("Raster", "Vector", "Database", "Mesh", "Web")


def test_provider_properties(loaded):
    provider = loaded.provider
    assert provider.id() == "hamq"
    assert provider.name() == "HamQ"
    assert provider.longName()
    assert not provider.icon().isNull()
    assert os.path.isfile(provider.svgIconPath())
    assert len(provider_module.ALGORITHMS) == len(ALGORITHM_IDS)
    assert sorted(alg.id() for alg in provider.algorithms()) == sorted(ALGORITHM_IDS)


class _DummyAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "dummy"

    def displayName(self):
        return "Dummy"

    def createInstance(self):
        return _DummyAlgorithm()

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        return {}


def test_provider_loads_algorithm_classes(loaded, monkeypatch):
    monkeypatch.setattr(provider_module, "ALGORITHMS", [_DummyAlgorithm])
    loaded.provider.refreshAlgorithms()
    assert [alg.id() for alg in loaded.provider.algorithms()] == ["hamq:dummy"]
    assert QgsApplication.processingRegistry().algorithmById("hamq:dummy") is not None
    monkeypatch.undo()
    loaded.provider.refreshAlgorithms()
    assert sorted(alg.id() for alg in loaded.provider.algorithms()) == sorted(ALGORITHM_IDS)


def test_processing_runs_provider_algorithms(loaded, qgis_processing, monkeypatch):
    monkeypatch.setattr(provider_module, "ALGORITHMS", [_DummyAlgorithm])
    loaded.provider.refreshAlgorithms()
    assert qgis_processing.run("hamq:dummy", {}) == {}


def test_toolbox_shows_provider_only_with_algorithms(loaded, monkeypatch):
    """QGIS hides a Processing provider without algorithms in the toolbox.

    M0 registers the provider with an empty ALGORITHMS list: it is in the
    registry, and "HamQ" appears in the Processing Toolbox with the first
    algorithm (M1), not before.
    """
    monkeypatch.setattr(provider_module, "ALGORITHMS", [])
    loaded.provider.refreshAlgorithms()
    assert QgsApplication.processingRegistry().providerById("hamq") is not None
    assert toolbox_children(toolbox_model(), "HamQ") is None

    monkeypatch.setattr(provider_module, "ALGORITHMS", [_DummyAlgorithm])
    loaded.provider.refreshAlgorithms()
    assert toolbox_children(toolbox_model(), "HamQ") == [("Dummy", [])]


class _LabelledAlgorithm(QgsProcessingAlgorithm):
    """Algorithm whose group and parameter label are read from LABELS.

    Stands in for a translated algorithm: real ones return tr() texts from
    group() and initAlgorithm(). The provider keeps the parameters built by
    initAlgorithm() and an open toolbox keeps the group names until
    provider.refreshAlgorithms() re-creates the algorithms.
    """

    LABELS = {"group": "Locators", "parameter": "Locator"}

    def name(self):
        return "labelled"

    def displayName(self):
        return "Labelled"

    def group(self):
        return self.LABELS["group"]

    def groupId(self):
        return "labelled"

    def createInstance(self):
        return _LabelledAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString("LOCATOR", self.LABELS["parameter"]))

    def processAlgorithm(self, parameters, context, feedback):
        return {}


def test_language_change_refreshes_provider_algorithms(loaded, monkeypatch):
    """After a language change the plugin calls provider.refreshAlgorithms() (contract)."""
    monkeypatch.setattr(provider_module, "ALGORITHMS", [_LabelledAlgorithm])
    loaded.provider.refreshAlgorithms()
    parameter = loaded.provider.algorithm("labelled").parameterDefinition("LOCATOR")
    assert parameter.description() == "Locator"
    reloaded = []
    loaded.provider.algorithmsLoaded.connect(lambda: reloaded.append(True))

    monkeypatch.setitem(_LabelledAlgorithm.LABELS, "parameter", "Lokator")
    events().languageChanged.emit("sr_Latn")

    assert reloaded == [True]
    parameter = loaded.provider.algorithm("labelled").parameterDefinition("LOCATOR")
    assert parameter.description() == "Lokator"


def test_open_toolbox_follows_language_change(loaded, monkeypatch):
    monkeypatch.setattr(provider_module, "ALGORITHMS", [_LabelledAlgorithm])
    loaded.provider.refreshAlgorithms()
    model = toolbox_model()  # stays open, like the Processing Toolbox dock
    assert toolbox_children(model, "HamQ") == [("Locators", [("Labelled", [])])]

    monkeypatch.setitem(_LabelledAlgorithm.LABELS, "group", "Lokatori")
    events().languageChanged.emit("sr_Latn")

    assert toolbox_children(model, "HamQ") == [("Lokatori", [("Labelled", [])])]


def test_iface_rejects_unknown_methods(iface):
    # the fixture is a QgisInterface mock with spec (or the fake): typos fail loudly
    with pytest.raises(AttributeError):
        iface.addPluginToHamQMenu("&HamQ", None)


@pytest.mark.parametrize("name", REQUIRED_ICONS)
def test_required_icon_exists(name):
    assert os.path.isfile(icon_path(name))


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(ICONS_DIR) if f.endswith(".svg")))
def test_icon_is_valid_svg(name):
    path = icon_path(name)
    root = ET.parse(path).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert os.path.getsize(path) < 3072
    qtsvg = pytest.importorskip("qgis.PyQt.QtSvg", reason="QtSvg is not installed in this image")
    renderer = qtsvg.QSvgRenderer(path)
    assert renderer.isValid()
    assert not renderer.defaultSize().isEmpty()


# --------------------------------------------------------------------------- INT-01 wiring


def test_menu_toolbar_and_icons(loaded):
    toolbar = loaded.toolbar
    texts = [action.text() for action in toolbar.actions() if action.text()]
    assert texts[: len(TOOLBAR_TEXTS)] == TOOLBAR_TEXTS
    widgets = [toolbar.widgetForAction(action) for action in toolbar.actions()]
    assert loaded.locator_search in widgets
    assert loaded.language_button in widgets
    assert isinstance(loaded.locator_search, LocatorSearchWidget)
    assert isinstance(loaded.language_button, LanguageSwitchButton)
    assert loaded.language_button.text() == "EN"
    assert loaded.language_button.menu() is loaded.language_menu
    for action in loaded.actions():
        assert not action.icon().isNull(), action.text()
    checkable = {action.text() for action in loaded.actions() if action.isCheckable()}
    assert checkable == {
        "Show HamQ panel",
        "Listen to WSJT-X",
        "Point antenna on map",
        "Azimuthal map",
    }
    submenu = next(
        action.menu()
        for action in hamq_menu(loaded.iface.mainWindow()).actions()
        if action.text() == SWITCH_TITLE
    )
    assert isinstance(submenu, QMenu)
    assert [action.text() for action in submenu.actions() if action.text()] == [
        "Auto (QGIS language)",
        "English",
        "Srpski (latinica)",
        "Српски (ћирилица)",
    ]
    assert submenu.actions()[2].isChecked()  # English (after Auto and the separator)


def test_panel_action_follows_the_dock(loaded, process_events):
    main = loaded.iface.mainWindow()
    main.show()
    process_events()
    dock = loaded.controller.dock
    action = loaded.panel_action
    assert dock.isVisible()
    assert action.isChecked()
    action.trigger()  # hide
    assert dock.isHidden()
    assert not action.isChecked()
    action.trigger()  # show again
    assert not dock.isHidden()
    assert action.isChecked()
    dock.close()  # the close button of the panel
    process_events()
    assert not action.isChecked()
    main.hide()


def test_listen_action_starts_and_stops_the_listener(loaded, plugin_env):
    plugin_env.wsjtx_port = free_udp_port()
    controller = loaded.controller
    loaded.listen_action.trigger()
    assert controller.is_listening()
    assert controller.listener.port() == plugin_env.wsjtx_port
    assert loaded.listen_action.isChecked()
    listen_button = controller.dock.findChild(QPushButton, "HamQListenButton")
    assert listen_button.isChecked()
    loaded.listen_action.trigger()
    assert not controller.is_listening()
    assert not loaded.listen_action.isChecked()
    assert not listen_button.isChecked()
    # the panel's button drives the same listener and the action follows
    listen_button.click()
    assert controller.is_listening()
    assert loaded.listen_action.isChecked()
    listen_button.click()
    assert not controller.is_listening()
    assert not loaded.listen_action.isChecked()


def test_listen_action_unchecks_when_the_port_is_taken(loaded, plugin_env):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blocker:
        blocker.bind(("0.0.0.0", 0))  # no SO_REUSEADDR: the port cannot be shared
        plugin_env.wsjtx_port = blocker.getsockname()[1]
        loaded.listen_action.trigger()
        assert not loaded.controller.is_listening()
        assert not loaded.listen_action.isChecked()
        assert any(str(plugin_env.wsjtx_port) in text for text in bar_texts(loaded.iface))


def test_point_action_needs_a_connected_rotator(loaded):
    loaded.point_action.trigger()
    assert not loaded.point_action.isChecked()
    assert loaded.iface.mapCanvas().mapTool() is not loaded.controller.rotator_tool
    assert any("rotator is not connected" in text for text in bar_texts(loaded.iface))


def test_azimuthal_action(loaded, plugin_env, process_events):
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    loaded.azimuthal_action.trigger()  # no locator yet: stays off with a warning
    assert not loaded.azimuthal_action.isChecked()
    assert project.crs().authid() == "EPSG:3857"
    plugin_env.my_grid = "KN04ft"
    loaded.azimuthal_action.trigger()
    assert loaded.azimuthal_action.isChecked()
    assert "aeqd" in project.crs().toProj()
    loaded.unload()  # restores the CRS and removes the helper layer
    process_events()
    assert project.crs().authid() == "EPSG:3857"
    assert project.mapLayersByName("Azimuthal map grid (KN04ft)") == []


def test_processing_actions_open_their_dialogs(loaded, qgis_processing, monkeypatch):
    opened = []
    monkeypatch.setattr(
        qgis_processing, "execAlgorithmDialog", lambda alg, params=None: opened.append(alg) or {}
    )
    for text in ("Import ADIF...", "Maidenhead grid...", "Locator to point..."):
        action_by_text(loaded, text).trigger()
    loaded.recalculate_action.trigger()
    loaded.controller.dock.importRequested.emit()  # the panel's "Import ADIF..." button
    assert opened == [
        "hamq:import_adif",
        "hamq:maidenhead_grid",
        "hamq:locator_to_point",
        "hamq:recalculate",
        "hamq:import_adif",
    ]


def test_download_and_settings_actions(loaded, monkeypatch):
    downloads, dialogs = [], []
    monkeypatch.setattr(loaded.controller.cty_manager, "download", lambda: downloads.append(1))

    def fake_exec(dialog):
        dialogs.append(dialog.windowTitle())
        return compat.DIALOG_REJECTED

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    loaded.cty_action.trigger()
    assert downloads == [1]
    assert "Downloading cty.dat…" in bar_texts(loaded.iface)
    loaded.settings_action.trigger()
    loaded.controller.dock.settingsRequested.emit()
    assert dialogs == ["HamQ settings", "HamQ settings"]


def test_first_run_hints(loaded):
    controller = loaded.controller
    assert sorted(controller.hint_keys()) == ["cty", "grid"]
    texts = bar_texts(loaded.iface)
    assert any("cty.dat" in text for text in texts)
    assert any("QTH locator" in text for text in texts)
    loaded.unload()
    assert not any("cty.dat" in text for text in bar_texts(loaded.iface))


def test_init_gui_failure_leaves_nothing_behind(iface, process_events, monkeypatch):
    iface.mapCanvas()
    process_events()
    before = all_receivers()

    def broken(self):
        raise RuntimeError("start failed")

    monkeypatch.setattr(controller_module.HamQController, "start", broken)
    plugin = hamq.classFactory(iface)
    with pytest.raises(RuntimeError, match="start failed"):
        plugin.initGui()
    process_events()
    assert "hamq" not in provider_ids()
    assert plugin.toolbar is None
    assert plugin.controller is None
    assert hamq_menu(iface.mainWindow()) is None
    assert iface.mainWindow().findChild(QDockWidget, "HamQDock") is None
    assert all_receivers() == before


def test_controller_failure_leaves_nothing_behind(iface, process_events, monkeypatch):
    iface.mapCanvas()
    process_events()
    before = all_receivers()

    def broken(*args, **kwargs):
        raise RuntimeError("no panel")

    monkeypatch.setattr(controller_module, "HamQDock", broken)
    plugin = hamq.classFactory(iface)
    with pytest.raises(RuntimeError, match="no panel"):
        plugin.initGui()
    process_events()
    assert "hamq" not in provider_ids()
    assert all_receivers() == before


def test_language_button_and_menu_switch_the_plugin(loaded):
    from hamq.core.i18n import current_language

    loaded.language_button.click()  # EN -> the last Serbian script (Latin by default)
    assert current_language() == "sr_Latn"
    assert loaded.import_action.text() == "Uvezi ADIF..."
    assert loaded.language_button.text() == "SR"
    loaded.language_menu.action_for("sr_Cyrl").trigger()
    assert loaded.import_action.text() == "Увези ADIF..."
    assert loaded.about_action.text() == "О програму HamQ"
    assert loaded.language_button.text() == "СР"
    loaded.language_menu.action_for("en").trigger()
    assert current_language() == "en"
    assert loaded.import_action.text() == "Import ADIF..."


def test_tooltips_follow_the_language_after_qgis_registered_the_actions(loaded):
    """QGIS registers the main window's actions with its shortcuts manager after it
    started the plugins of the last session, and that sets each tooltip to a fixed
    "<b>tooltip</b>" (for an action without its own tooltip, Qt's tooltip is its text).
    Every tooltip must still follow a language switch."""
    gui = pytest.importorskip("qgis.gui", reason="qgis.gui is not available in this build")
    gui.QgsGui.shortcutsManager().registerAllChildren(loaded.iface.mainWindow())
    assert loaded.log_qso_action.toolTip() == "<b>Log QSO</b>"
    assert loaded.import_action.toolTip() == "<b>Import an ADIF log into the HamQ GeoPackage</b>"
    manager = loaded.controller.language_manager
    for language in (LANG_SR_LATN, LANG_SR_CYRL, LANG_EN):
        assert manager.set_setting(language) == language
        for entry in loaded._actions:
            if entry.tooltip:
                expected = tr(entry.tooltip)
            else:  # Qt's tooltip of an action: its text without "..." and "&"
                expected = tr(entry.text).replace("...", "").replace("&", "").strip()
            assert entry.action.toolTip() == expected, (language, entry.action.objectName())
    assert loaded.log_qso_action.toolTip() == "Log QSO"


def test_recalculate_action_is_named_like_its_algorithm(loaded):
    registry = QgsApplication.processingRegistry()
    manager = loaded.controller.language_manager
    for language in (LANG_SR_LATN, LANG_SR_CYRL, LANG_EN):
        assert manager.set_setting(language) == language
        algorithm = registry.algorithmById("hamq:recalculate")
        assert loaded.recalculate_action.text() == algorithm.displayName() + "...", language
