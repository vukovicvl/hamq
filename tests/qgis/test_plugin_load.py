"""Plugin lifecycle: classFactory -> initGui -> unload, twice (reload bug check)."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import pytest
from qgis.core import QgsApplication, QgsProcessingAlgorithm, QgsProcessingParameterString
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QModelIndex, Qt
from qgis.PyQt.QtWidgets import QToolBar

import hamq
from hamq import plugin as plugin_module
from hamq.events import events
from hamq.gui import ICONS_DIR, icon_path
from hamq.processing import provider as provider_module
from hamq.qgis_io import compat

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
    receivers_before = language_receivers()
    for _round in range(2):
        plugin = hamq.classFactory(iface)
        plugin.initGui()

        assert provider_ids().count("hamq") == 1
        provider = QgsApplication.processingRegistry().providerById("hamq")
        assert provider is not None
        assert provider.name() == "HamQ"

        toolbar = iface.mainWindow().findChild(QToolBar, plugin_module.TOOLBAR_OBJECT_NAME)
        assert toolbar is not None
        assert toolbar is plugin.toolbar
        assert toolbar.windowTitle() == "HamQ"

        menu = hamq_menu(iface.mainWindow())
        assert menu is not None
        assert [action.text() for action in menu.actions()] == ["About HamQ"]
        assert plugin.about_action in toolbar.actions()
        assert language_receivers() == receivers_before + 1

        actions = plugin.actions()
        assert actions == [plugin.about_action]

        plugin.unload()
        process_events()

        assert "hamq" not in provider_ids()
        assert plugin.provider is None
        assert plugin.toolbar is None
        assert iface.mainWindow().findChild(QToolBar, plugin_module.TOOLBAR_OBJECT_NAME) is None
        assert sip.isdeleted(toolbar)
        assert hamq_menu(iface.mainWindow()) is None
        assert all(sip.isdeleted(action) for action in actions)
        assert plugin.actions() == []
        assert language_receivers() == receivers_before

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

    monkeypatch.setattr(plugin_module.QMessageBox, "about", fake_about)
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


def test_plugin_metadata():
    meta = plugin_module.plugin_metadata()
    assert meta["name"] == "HamQ"
    assert meta["qgisMinimumVersion"] == "3.34"
    assert meta["supportsQt6"] == "True"
    assert os.path.isfile(os.path.join(plugin_module.PLUGIN_DIR, meta["icon"]))


def test_provider_properties(loaded):
    provider = loaded.provider
    assert provider.id() == "hamq"
    assert provider.name() == "HamQ"
    assert provider.longName()
    assert not provider.icon().isNull()
    assert os.path.isfile(provider.svgIconPath())
    assert provider_module.ALGORITHMS == []
    assert provider.algorithms() == []


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
    assert loaded.provider.algorithms() == []


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
