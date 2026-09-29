"""Plugin lifecycle: classFactory -> initGui -> unload, twice (reload bug check)."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import pytest
from qgis.core import QgsApplication, QgsProcessingAlgorithm
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QToolBar

import hamq
from hamq import plugin as plugin_module
from hamq.events import events
from hamq.gui import ICONS_DIR, icon_path
from hamq.processing import provider as provider_module

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


def test_load_and_unload_twice(iface, process_events):
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


def test_unload_is_idempotent(iface, process_events):
    plugin = hamq.classFactory(iface)
    plugin.initGui()
    plugin.unload()
    plugin.unload()
    process_events()
    assert "hamq" not in provider_ids()


def test_init_processing_only_then_unload(iface, process_events):
    """qgis_process calls initProcessing() without initGui()."""
    plugin = hamq.classFactory(iface)
    plugin.initProcessing()
    plugin.initProcessing()  # idempotent
    assert provider_ids().count("hamq") == 1
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
