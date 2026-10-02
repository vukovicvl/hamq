"""End-to-end tests of the wired plugin (INT-01).

The real plugin (``classFactory`` -> ``initGui``) runs against the fake QGIS interface of
``conftest.py``: controller, panel, WSJT-X listener (real UDP datagrams from a Python
socket), Hamlib clients (``fake_hamlib.FakeHamlib`` servers), Processing (the real
``hamq:import_adif``), the rotator map tool (a real ``QgsMapMouseEvent``) and the language
switch. Every test unloads the plugin again; the project and the settings are cleared.
"""

from __future__ import annotations

import functools
import gc
import os
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapMouseEvent, QgsMessageLogViewer
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QPoint, Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QWidget,
)

import hamq
from hamq import controller as controller_module
from hamq.core import geo, maidenhead, wsjtx
from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, latin_to_cyrillic, set_language
from hamq.core.qso import Station, records_to_qsos
from hamq.events import events
from hamq.gui.dock import format_mhz, format_number
from hamq.gui.language import SWITCH_TITLE
from hamq.gui.qso_dialog import QsoDialog
from hamq.gui.settings_dialog import SettingsDialog
from hamq.net.cty_download import CtyManager
from hamq.qgis_io import compat, gpkg, layers
from hamq.settings import HamQSettings

from .fake_hamlib import FakeHamlib, drain_deleted

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WSJTX_LOG = FIXTURES / "adif" / "wsjtx_log.adi"
CTY_DAT = FIXTURES / "cty" / "cty_excerpt.dat"
CTY_CSV = FIXTURES / "cty" / "cty_excerpt.csv"
MY_GRID = "KN04ft"
#: A Logged ADIF message as WSJT-X 2.7 sends it (header + one record).
LIVE_ADIF = (
    "<adif_ver:5>3.1.0<programid:6>WSJT-X<EOH>\n"
    "<call:6>VK2XYZ <gridsquare:4>QF56 <mode:3>FT8 <rst_sent:3>-10 <rst_rcvd:3>-12 "
    "<qso_date:8>20260915 <time_on:6>184500 <qso_date_off:8>20260915 <time_off:6>184612 "
    "<band:3>20m <freq:9>14.075123 <station_callsign:5>YU1QQ <my_gridsquare:6>KN04ft <EOR>"
)
SYDNEY = QgsPointXY(151.2093, -33.8688)
#: Markup from the network: a DX call in a Logged ADIF record and a WSJT-X client id.
EVIL_CALL = "<a href=https://evil.example/x>click me</a>"
EVIL_CLIENT = (
    '<a href="https://evil.example/hamq-update">HamQ: security update required, click here</a>'
)
MENU_SOURCES = [
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
    None,  # the language submenu: SWITCH_TITLE in every language
    "About HamQ",
]


# --------------------------------------------------------------------------- helpers


def wait_until(predicate, timeout=5.0) -> bool:
    """Run the Qt event loop until ``predicate()`` is true; False after ``timeout`` s."""
    deadline = time.monotonic() + timeout
    while True:
        QgsApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.002)


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


def port_is_free(port: int) -> bool:
    """True when ``port`` can be bound exclusively (the listener released it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def send(port: int, *packets: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for packet in packets:
            sock.sendto(packet, ("127.0.0.1", port))


def calls_on_map(path: str) -> list[str]:
    """Calls of the features of the project's QSO layer of ``path`` (empty without it)."""
    qso_layer, _path_layer = layers.find_layers(path)
    if qso_layer is None:
        return []
    return sorted(feature["call"] for feature in qso_layer.getFeatures())


def bar_texts(iface) -> list[str]:
    return [item.text() for item in iface.messageBar().items()]


def hamq_menu(main_window):
    for top in main_window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.menu() is not None and action.text() == "&HamQ":
                return action.menu()
    return None


def dock_text(dock, object_name: str) -> str:
    return dock.findChild(QWidget, object_name).text()


def expected_distance(grid: str) -> str:
    distance = geo.distance_km(*maidenhead.to_latlon(MY_GRID), *maidenhead.to_latlon(grid))
    return format_number(distance, 0)


def both_connected(rig, rotator) -> bool:
    return rig.is_connected() and rotator.is_connected()


def no_problems(log_messages) -> list:
    return [m for m in log_messages if m[2] in (compat.MSG_WARNING, compat.MSG_CRITICAL)]


def links(widget) -> list[str]:
    """Targets of the links in the document of a text widget."""
    found = []
    block = widget.document().begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            char_format = fragments.fragment().charFormat()
            if char_format.isAnchor():
                found.append(char_format.anchorHref())
            fragments += 1
        block = block.next()
    return found


def bar_browsers(iface) -> list:
    """The widgets that show the message bar items: QGIS renders each text as HTML."""
    return [item.findChild(QTextBrowser) for item in iface.messageBar().items()]


def log_panel_views(viewer) -> list:
    """The text views of a Log Messages panel (one tab per tag)."""
    return viewer.findChildren(QPlainTextEdit) + viewer.findChildren(QTextEdit)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _no_leftover_provider():
    yield
    registry = QgsApplication.processingRegistry()
    leftover = registry.providerById("hamq")
    if leftover is not None:
        registry.removeProvider(leftover)


@pytest.fixture
def env(clean_settings, clean_project, tmp_path, monkeypatch):
    """English, my station YU1QQ in KN04ft, the log in tmp_path, a free WSJT-X port and a
    cty.dat cache in tmp_path (empty unless a test copies the fixture files there)."""
    settings = HamQSettings()
    settings.language = LANG_EN
    settings.my_call = "YU1QQ"
    settings.my_grid = MY_GRID
    settings.gpkg_path = str(tmp_path / "log.gpkg")
    settings.wsjtx_addr = "127.0.0.1"
    settings.wsjtx_port = free_udp_port()
    settings.rig_poll_ms = 200
    settings.rot_confirmed = True
    set_language(LANG_EN)
    cty_dir = tmp_path / "cty"
    monkeypatch.setattr(
        controller_module,
        "CtyManager",
        functools.partial(CtyManager, cache_path=str(cty_dir / "cty.dat")),
    )
    yield SimpleNamespace(settings=settings, tmp=tmp_path, cty_dir=cty_dir)
    set_language(LANG_EN)


@pytest.fixture
def load_plugin(env, iface, process_events):
    """``load()`` -> a plugin after ``initGui()``; every plugin is unloaded afterwards."""
    loaded = []

    def load():
        plugin = hamq.classFactory(iface)
        plugin.initGui()
        loaded.append(plugin)
        return plugin

    yield load
    for plugin in loaded:
        plugin.unload()
    process_events()


@pytest.fixture
def daemons(qgis_app):
    """A fake ``rigctld`` and ``rotctld`` on free 127.0.0.1 ports."""
    rig, rot = FakeHamlib("rig"), FakeHamlib("rot")
    rig.start()
    rot.start()
    yield SimpleNamespace(rig=rig, rot=rot)
    rig.stop()
    rot.stop()
    drain_deleted()


def enable_daemons(settings, daemons) -> None:
    settings.rig_enabled = True
    settings.rig_host = "127.0.0.1"
    settings.rig_port = daemons.rig.port
    settings.rot_enabled = True
    settings.rot_host = "127.0.0.1"
    settings.rot_port = daemons.rot.port


def copy_cty(env) -> None:
    env.cty_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CTY_DAT, env.cty_dir / "cty.dat")
    shutil.copyfile(CTY_CSV, env.cty_dir / "cty.csv")


# --------------------------------------------------------------------------- lifecycle


def test_lifecycle_twice_with_everything_running(
    env, load_plugin, daemons, iface, process_events, log_messages
):
    enable_daemons(env.settings, daemons)
    env.settings.wsjtx_autostart = True
    port = env.settings.wsjtx_port
    iface.mapCanvas()
    gc.collect()
    process_events()
    hub = events()
    before = (
        hub.receivers(hub.languageChanged),
        hub.receivers(hub.settingsChanged),
        hub.receivers(hub.dataChanged),
    )
    for _round in range(2):
        plugin = load_plugin()
        controller = plugin.controller
        listener, rig, rotator = controller.listener, controller.rig, controller.rotator
        assert listener.is_running()
        assert plugin.listen_action.isChecked()
        assert wait_until(functools.partial(both_connected, rig, rotator))
        send(port, wsjtx.encode_heartbeat("WSJT-X", version="2.7.0"))
        assert wait_until(listener.is_connected)

        plugin.unload()
        process_events()
        assert sip.isdeleted(listener) and sip.isdeleted(rig) and sip.isdeleted(rotator)
        assert port_is_free(port)
        assert wait_until(lambda: daemons.rig.client_count == 0 and daemons.rot.client_count == 0)
        gc.collect()
        assert (
            hub.receivers(hub.languageChanged),
            hub.receivers(hub.settingsChanged),
            hub.receivers(hub.dataChanged),
        ) == before
    assert no_problems(log_messages) == []


def test_unload_while_the_statistics_are_computed(env, load_plugin, process_events, log_messages):
    plugin = load_plugin()
    controller = plugin.controller
    controller.refresh()
    controller.refresh()
    plugin.unload()
    process_events()
    QgsApplication.taskManager().cancelAll()
    assert wait_until(lambda: not controller_module._RUNNING_TASKS, 5.0)
    assert no_problems(log_messages) == []


# --------------------------------------------------------------------------- import


def test_import_adif_through_processing(env, load_plugin, qgis_processing):
    plugin = load_plugin()
    controller = plugin.controller
    path = env.settings.gpkg_path
    results = qgis_processing.run(
        "hamq:import_adif",
        {"INPUT": str(WSJTX_LOG), "GPKG": path, "MY_GRID": MY_GRID, "USE_CTY": False},
    )
    assert results["IMPORTED"] == 6
    qso_layer, path_layer = layers.find_layers(path)
    assert qso_layer is not None and path_layer is not None
    group = QgsProject.instance().layerTreeRoot().findGroup("HamQ")
    assert group is not None
    assert {child.layerId() for child in group.findLayers()} >= {qso_layer.id(), path_layer.id()}
    assert len(calls_on_map(path)) == 6
    # the import announced the change: the panel shows the statistics of the log
    assert wait_until(lambda: controller.stats is not None and controller.stats.total == 6)
    dock = controller.dock
    assert dock.findChild(QWidget, "HamQTileTotal").value.text() == "6"
    assert dock.findChild(QWidget, "HamQStatsStack").currentIndex() == 1

    again = qgis_processing.run(
        "hamq:import_adif",
        {"INPUT": str(WSJTX_LOG), "GPKG": path, "MY_GRID": MY_GRID, "USE_CTY": False},
    )
    assert (again["IMPORTED"], again["DUPLICATES"]) == (0, 6)
    assert len(calls_on_map(path)) == 6
    assert wait_until(lambda: not controller.is_refreshing())
    assert controller.stats.total == 6


# --------------------------------------------------------------------------- WSJT-X live


def test_live_wsjtx_qso_end_to_end(env, load_plugin, iface, log_messages):
    plugin = load_plugin()
    controller = plugin.controller
    dock = controller.dock
    path = env.settings.gpkg_path
    port = env.settings.wsjtx_port
    plugin.listen_action.trigger()
    assert controller.is_listening()
    assert dock_text(dock, "HamQWsjtxState") == (
        f"Listening on 127.0.0.1:{port}, waiting for WSJT-X"
    )

    send(port, wsjtx.encode_heartbeat("WSJT-X", version="2.7.0"))
    assert wait_until(lambda: dock_text(dock, "HamQWsjtxState") == "Connected: WSJT-X 2.7.0")
    send(port, wsjtx.encode_status("WSJT-X", 14074000, "FT8", "VK2XYZ"))
    assert wait_until(lambda: dock_text(dock, "HamQWsjtxDxCall") == "VK2XYZ")
    assert dock_text(dock, "HamQWsjtxFrequency") == format_mhz(14074000)
    assert dock_text(dock, "HamQWsjtxBand") == "20m"
    assert dock_text(dock, "HamQWsjtxMode") == "FT8"

    assert calls_on_map(path) == []
    started = time.monotonic()
    send(port, wsjtx.encode_logged_adif("WSJT-X", LIVE_ADIF))
    assert wait_until(lambda: calls_on_map(path) == ["VK2XYZ"], 2.0)
    assert time.monotonic() - started < 2.0  # PLAN M5: on the map in under 2 s
    _qso_layer, path_layer = layers.find_layers(path)
    assert path_layer.featureCount() == 1
    feature = next(layers.find_layers(path)[0].getFeatures())
    assert feature["source"] == "wsjtx"
    assert feature["my_gridsquare"] == MY_GRID
    message = f"New QSO: VK2XYZ 20m FT8 ({expected_distance('QF56')} km)"
    assert message in bar_texts(iface)
    assert dock_text(dock, "HamQLastQsoCall") == "VK2XYZ"
    assert wait_until(lambda: controller.stats is not None and controller.stats.total == 1)

    # The same QSO again (another client, so the listener's own repeat filter lets it
    # through): the log keeps one copy and the message bar says so.
    send(port, wsjtx.encode_logged_adif("JTDX", LIVE_ADIF))
    assert wait_until(lambda: "QSO already in the log: VK2XYZ 20m FT8" in bar_texts(iface), 2.0)
    assert calls_on_map(path) == ["VK2XYZ"]
    assert len(gpkg.read_qso_rows(path)) == 1
    assert no_problems(log_messages) == []


def test_live_qso_gets_dxcc_data_from_cty(env, load_plugin):
    copy_cty(env)
    plugin = load_plugin()
    controller = plugin.controller
    assert "cty" not in controller.hint_keys()
    adif = LIVE_ADIF.replace("<gridsquare:4>QF56 ", "")  # position from cty.dat
    assert controller.handle_logged_adif("WSJT-X", adif) == 1
    row = gpkg.read_qso_rows(env.settings.gpkg_path)[0]
    assert row["country"] == "Australia"
    assert row["cont"] == "OC"
    assert row["dxcc"] == 150
    assert row["loc_source"] == "cty"
    assert row["distance_km"] > 10000  # to the centre of the entity


def test_live_qso_with_my_lat_lon_names_their_locator(env, load_plugin):
    """REL-01: a live QSO with MY_LAT/MY_LON and no MY_GRIDSQUARE stores the locator of
    that position (Novi Sad, JN95wg), where its path starts, not my locator (KN04ft)."""
    plugin = load_plugin()
    adif = LIVE_ADIF.replace(
        "<my_gridsquare:6>KN04ft ", "<my_lat:11>N045 16.250 <my_lon:11>E019 52.500 "
    )
    assert plugin.controller.handle_logged_adif("WSJT-X", adif) == 1
    row = gpkg.read_qso_rows(env.settings.gpkg_path)[0]
    assert row["my_gridsquare"] == "JN95wg"
    assert gpkg.STATION_GRID_KEY not in row["adif_extra"]
    assert row["distance_km"] == pytest.approx(
        geo.distance_km(*maidenhead.to_latlon("JN95wg"), *maidenhead.to_latlon("QF56"))
    )


def test_unreadable_wsjtx_qso_is_reported(env, load_plugin, iface):
    plugin = load_plugin()
    controller = plugin.controller
    assert controller.handle_logged_adif("", "<call:5>YU1AB <eor>") == 0  # no date / time
    assert any(
        text.startswith("WSJT-X sent a QSO that could not be read") for text in bar_texts(iface)
    )
    assert controller.handle_logged_adif("JTDX", "garbage") == 0
    assert "JTDX sent a logged QSO without an ADIF record" in bar_texts(iface)
    assert gpkg.read_qso_rows(env.settings.gpkg_path) == []


def test_markup_from_the_network_is_shown_as_text(env, load_plugin, iface):
    """A DX call or a client id from a WSJT-X datagram is shown as the text it is.

    QGIS renders message bar texts as HTML, and so does the Log Messages panel of QGIS
    3.34 to 3.40.6 and 3.42.0 / 3.42.1; a click on a link there opens the browser. Markup
    in a datagram must never become a link."""
    viewer = QgsMessageLogViewer()  # the Log Messages panel; made before the messages
    plugin = load_plugin()
    controller = plugin.controller
    plugin.listen_action.trigger()
    items_before = len(iface.messageBar().items())
    adif = LIVE_ADIF.replace("<call:6>VK2XYZ ", f"<call:{len(EVIL_CALL)}>{EVIL_CALL} ")
    send(env.settings.wsjtx_port, wsjtx.encode_logged_adif("WSJT-X", adif))
    assert wait_until(lambda: len(iface.messageBar().items()) > items_before, 2.0)
    assert controller.handle_logged_adif(EVIL_CLIENT, "garbage") == 0
    try:
        bar = [browser.toPlainText() for browser in bar_browsers(iface)]
        assert [link for browser in bar_browsers(iface) for link in links(browser)] == []
        client_message = f"{EVIL_CLIENT} sent a logged QSO without an ADIF record"
        assert any(text.endswith(client_message) for text in bar), bar
        # every message about the call shows its markup
        about_call = [text for text in bar if "CLICK ME" in text.upper()]
        assert all(EVIL_CALL.upper() in text.upper() for text in about_call), about_call
        if gpkg.read_qso_rows(env.settings.gpkg_path):  # saved: "New QSO: <A HREF=...>..."
            assert any(f"New QSO: {EVIL_CALL.upper()} 20m FT8" in text for text in bar), bar
        views = log_panel_views(viewer)
        assert [link for view in views for link in links(view)] == []
        assert client_message in "\n".join(view.toPlainText() for view in views)
    finally:
        viewer.deleteLater()


def test_qso_that_could_not_be_saved_is_reported_once(env, load_plugin, iface):
    """A live QSO the log cannot take: one message that names the QSO and the cause."""
    plugin = load_plugin()
    controller = plugin.controller
    path = env.settings.gpkg_path
    assert controller.ensure_storage()
    os.chmod(path, 0o444)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("a read-only file is writable for this user (root)")
        assert controller.handle_logged_adif("WSJT-X", LIVE_ADIF) == 0
    finally:
        os.chmod(path, 0o644)
    failed = [text for text in bar_texts(iface) if "VK2XYZ" in text]
    assert len(failed) == 1, failed
    assert failed[0].count("could not be saved") == 1, failed
    assert gpkg.read_qso_rows(path) == []


def test_save_warning_of_the_log_is_shown_as_it_is(env, load_plugin, iface, monkeypatch):
    """insert_qsos's warning for a QSO it could not write already names the QSO and the
    cause: the message bar shows it as it is, not inside a second "could not be saved"."""
    plugin = load_plugin()
    warning = "QSO VK2XYZ 2026-09-15 18:45: could not be saved: disk I/O error"

    def failing_insert(path, qsos, *args, **kwargs):
        return gpkg.InsertResult(failed=len(qsos), warnings=[warning])

    monkeypatch.setattr(gpkg, "insert_qsos", failing_insert)
    assert plugin.controller.handle_logged_adif("WSJT-X", LIVE_ADIF) == 0
    assert [text for text in bar_texts(iface) if "VK2XYZ" in text] == [warning]


# --------------------------------------------------------------------------- language


def test_language_switch_updates_everything(env, load_plugin, iface, process_events):
    env.settings.my_grid = ""  # both first-run hints are shown
    plugin = load_plugin()
    controller = plugin.controller
    assert sorted(controller.hint_keys()) == ["cty", "grid"]
    dock = controller.dock
    qso_layer, path_layer = controller.load_layers()
    path_layer.setName("My paths")  # renamed by the user: HamQ leaves it alone
    settings_dialog = SettingsDialog(
        controller.settings, controller.language_manager, controller.cty_manager, iface.mainWindow()
    )
    qso_dialog = QsoDialog(controller.settings, None, iface.mainWindow())
    settings_dialog.show()
    qso_dialog.show()
    tabs = dock.findChild(QTabWidget, "HamQDockTabs")
    menu = hamq_menu(iface.mainWindow())
    registry = QgsApplication.processingRegistry()
    call_index = qso_layer.fields().indexOf("call")
    button_texts = {LANG_EN: "EN", LANG_SR_LATN: "SR", LANG_SR_CYRL: "СР"}
    serbian = {
        "Import ADIF...": "Uvezi ADIF...",
        "Statistics": "Statistika",
        "Import ADIF": "Uvezi ADIF",
        "ADIF file": "ADIF fajl",
        "Callsign": "Pozivni znak",
        "QSOs": "Veze",
        "Locator, e.g. KN04ft": "Lokator, npr. KN04ft",
        "HamQ settings": "HamQ podešavanja",
        "Log QSO": "Upis veze",
        "Download": "Preuzmi",
    }
    try:
        for language in (LANG_SR_LATN, LANG_SR_CYRL, LANG_EN):
            assert controller.language_manager.set_setting(language) == language
            process_events()

            def shown(source, language=language):
                if language == LANG_EN:
                    return source
                latin = serbian[source]
                return latin if language == LANG_SR_LATN else latin_to_cyrillic(latin)

            assert plugin.import_action.text() == shown("Import ADIF...")
            texts = [action.text() for action in menu.actions()]
            assert texts[11] == SWITCH_TITLE
            assert texts[1] == shown("Import ADIF...")
            assert plugin.language_button.text() == button_texts[language]
            assert plugin.locator_search.line_edit.placeholderText() == shown(
                "Locator, e.g. KN04ft"
            )
            assert tabs.tabText(0) == shown("Statistics")
            algorithm = registry.algorithmById("hamq:import_adif")
            assert algorithm.displayName() == shown("Import ADIF")
            # parameters are built when the provider loads its algorithms: refreshed
            assert algorithm.parameterDefinition("INPUT").description() == shown("ADIF file")
            assert qso_layer.attributeAlias(call_index) == shown("Callsign")
            assert qso_layer.name() == shown("QSOs")
            assert path_layer.name() == "My paths"
            assert settings_dialog.windowTitle() == shown("HamQ settings")
            assert qso_dialog.windowTitle() == shown("Log QSO")
            hint_buttons = [
                button.text()
                for button in iface.messageBar().findChildren(QPushButton, "HamQHintButton")
                if not sip.isdeleted(button)
            ]
            assert shown("Download") in hint_buttons
    finally:
        settings_dialog.close()
        qso_dialog.close()
        settings_dialog.deleteLater()
        qso_dialog.deleteLater()


# --------------------------------------------------------------------------- Hamlib


def test_hamlib_radio_and_rotator_through_the_panel(env, load_plugin, daemons):
    enable_daemons(env.settings, daemons)
    plugin = load_plugin()
    controller = plugin.controller
    dock = controller.dock
    assert wait_until(lambda: dock_text(dock, "HamQRigFrequency") == format_mhz(14074000))
    assert dock_text(dock, "HamQRigState") == "Connected"
    assert wait_until(lambda: "USB" in dock_text(dock, "HamQRigDetails"))
    assert wait_until(lambda: dock_text(dock, "HamQRotatorAzimuth") == "123°")

    # panel -> radio: frequency and a new mode
    dock.findChild(QWidget, "HamQRigFrequencySpin").setValue(7.074)
    combo = dock.findChild(QWidget, "HamQRigModeCombo")
    combo.setCurrentIndex(combo.findText("CW"))
    dock.findChild(QPushButton, "HamQRigSetButton").click()
    assert wait_until(lambda: daemons.rig.freq_hz == 7074000 and daemons.rig.mode == "CW")
    assert "+F 7074000" in daemons.rig.received
    assert "+M CW 0" in daemons.rig.received
    assert wait_until(lambda: dock_text(dock, "HamQRigFrequency") == format_mhz(7074000))

    # the same frequency with the mode unchanged: no mode command (the passband stays)
    daemons.rig.received.clear()
    dock.findChild(QPushButton, "HamQRigSetButton").click()
    assert wait_until(lambda: "+F 7074000" in daemons.rig.received)
    assert not any(line.startswith("+M") for line in daemons.rig.received)

    # panel -> rotator: turn and stop
    dock.findChild(QWidget, "HamQRotatorTargetSpin").setValue(200)
    dock.findChild(QPushButton, "HamQRotatorTurnButton").click()
    assert wait_until(lambda: "+P 200.0 0.0" in daemons.rot.received)
    assert wait_until(lambda: dock_text(dock, "HamQRotatorAzimuth") == "200°")
    assert dock_text(dock, "HamQRotatorTarget") == "Target: 200°"
    dock.findChild(QPushButton, "HamQRotatorStopButton").click()
    assert wait_until(lambda: "+S" in daemons.rot.received)


def test_rotator_map_click_turns_the_antenna(env, load_plugin, daemons, iface, process_events):
    enable_daemons(env.settings, daemons)
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(controller.rotator.is_connected)
    iface.mainWindow().show()  # a hidden canvas keeps its initial size
    canvas = iface.mapCanvas()
    canvas.resize(800, 400)
    process_events()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    canvas.setExtent(QgsRectangle(-180, -90, 180, 90))

    plugin.point_action.trigger()
    assert canvas.mapTool() is controller.rotator_tool
    assert plugin.point_action.isChecked()
    assert dock_button_checked(controller.dock, "HamQPointOnMapButton")

    pixel = canvas.getCoordinateTransform().transform(SYDNEY)
    event = QgsMapMouseEvent(
        canvas,
        QEvent.Type.MouseButtonRelease,
        QPoint(round(pixel.x()), round(pixel.y())),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    clicked = event.mapPoint()
    canvas.mapTool().canvasReleaseEvent(event)
    bearing = geo.bearing_deg(*maidenhead.to_latlon(MY_GRID), clicked.y(), clicked.x())
    assert wait_until(lambda: any(line.startswith("+P ") for line in daemons.rot.received))
    line = next(line for line in daemons.rot.received if line.startswith("+P "))
    azimuth, elevation = (float(part) for part in line[3:].split())
    assert azimuth == pytest.approx(bearing, abs=0.01)
    assert azimuth == pytest.approx(91.0, abs=1.5)  # Sydney from Belgrade
    assert elevation == 0.0
    assert controller.rotator_tool.beam_geometry() is not None

    # the rotator goes away: the tool is released and the action unchecked
    daemons.rot.stop()
    assert wait_until(lambda: not controller.rotator.is_connected())
    assert canvas.mapTool() is not controller.rotator_tool
    assert not plugin.point_action.isChecked()
    assert not dock_button_checked(controller.dock, "HamQPointOnMapButton")


def dock_button_checked(dock, object_name: str) -> bool:
    return dock.findChild(QPushButton, object_name).isChecked()


def test_rotator_map_click_asks_the_first_time(env, load_plugin, daemons, iface, monkeypatch):
    from hamq.gui import rotator_tool

    enable_daemons(env.settings, daemons)
    env.settings.rot_confirmed = False
    questions = []

    def answer(*args, **kwargs):
        questions.append(args[2])
        return compat.MSGBOX_YES

    monkeypatch.setattr(rotator_tool.QMessageBox, "question", answer)
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(controller.rotator.is_connected)
    assert controller.set_point_on_map(True)
    controller.rotator_tool.aim_at(SYDNEY, QgsCoordinateReferenceSystem("EPSG:4326"))
    assert len(questions) == 1
    assert env.settings.rot_confirmed
    assert wait_until(lambda: any(line.startswith("+P ") for line in daemons.rot.received))


def test_another_map_tool_ends_pointing(env, load_plugin, daemons, iface):
    from qgis.gui import QgsMapToolPan

    enable_daemons(env.settings, daemons)
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(controller.rotator.is_connected)
    canvas = iface.mapCanvas()
    pan = QgsMapToolPan(canvas)
    canvas.setMapTool(pan)
    plugin.point_action.trigger()
    assert canvas.mapTool() is controller.rotator_tool
    plugin.point_action.trigger()  # off again: the previous tool comes back
    assert canvas.mapTool() is pan
    assert not plugin.point_action.isChecked()
    plugin.point_action.trigger()
    canvas.setMapTool(pan)  # the user picks another tool
    assert not controller.is_pointing_on_map()
    assert not plugin.point_action.isChecked()
    canvas.unsetMapTool(pan)


def test_turn_outside_the_rotator_range_is_refused(env, load_plugin, daemons, iface):
    enable_daemons(env.settings, daemons)
    env.settings.rot_min_az = 0.0
    env.settings.rot_max_az = 180.0
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(controller.rotator.is_connected)
    assert controller.turn_rotator(270.0) is None
    assert any("cannot turn to azimuth 270°" in text for text in bar_texts(iface))
    assert controller.turn_rotator(90.0) == 90.0
    assert wait_until(lambda: "+P 90.0 0.0" in daemons.rot.received)


# --------------------------------------------------------------------------- settings


def test_settings_change_restarts_listener_and_clients(env, load_plugin, daemons, iface):
    plugin = load_plugin()
    controller = plugin.controller
    dock = controller.dock
    first = env.settings.wsjtx_port
    plugin.listen_action.trigger()
    assert controller.listener.port() == first

    second = free_udp_port()
    env.settings.wsjtx_port = second
    events().settingsChanged.emit()
    assert controller.is_listening()
    assert controller.listener.port() == second
    assert plugin.listen_action.isChecked()
    assert dock_text(dock, "HamQWsjtxState") == (
        f"Listening on 127.0.0.1:{second}, waiting for WSJT-X"
    )
    assert port_is_free(first)
    send(second, wsjtx.encode_heartbeat("WSJT-X", version="2.7.0"))
    assert wait_until(lambda: dock_text(dock, "HamQWsjtxState") == "Connected: WSJT-X 2.7.0")

    # an unrelated change keeps the listener as it is (a restart would drop the client)
    lost = []
    controller.listener.connectionChanged.connect(lost.append)
    env.settings.my_call = "YU1ZZ"
    events().settingsChanged.emit()
    assert controller.listener.is_connected()
    assert lost == []

    # Hamlib follows its settings
    assert dock_text(dock, "HamQRigState") == "Radio control is off. Turn it on in Settings."
    enable_daemons(env.settings, daemons)
    events().settingsChanged.emit()
    assert wait_until(lambda: dock_text(dock, "HamQRigState") == "Connected")
    assert wait_until(controller.rotator.is_connected)
    env.settings.rig_enabled = False
    events().settingsChanged.emit()
    assert not controller.rig.is_running()
    assert dock_text(dock, "HamQRigState") == "Radio control is off. Turn it on in Settings."

    # the statistics follow the GeoPackage
    other = str(env.tmp / "other.gpkg")
    qsos, _warnings = records_to_qsos(
        [
            {
                "CALL": "DL1XYZ",
                "QSO_DATE": "20260901",
                "TIME_ON": "1200",
                "BAND": "20m",
                "MODE": "CW",
            }
        ],
        station=Station(grid=MY_GRID),
    )
    gpkg.insert_qsos(other, qsos)
    env.settings.gpkg_path = other
    events().settingsChanged.emit()
    assert wait_until(lambda: controller.stats is not None and controller.stats.total == 1)


# --------------------------------------------------------------------------- manual QSO


def test_manual_qso_prefilled_from_the_rig(env, load_plugin, daemons, iface, monkeypatch):
    enable_daemons(env.settings, daemons)
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(lambda: controller.rig.state().get("mode") == "USB")
    seen = {}

    def fake_exec(dialog):
        seen["freq"] = dialog.findChild(QWidget, "HamQQsoFrequency").text()
        seen["band"] = dialog.findChild(QWidget, "HamQQsoBand").currentText()
        seen["mode"] = dialog.findChild(QWidget, "HamQQsoMode").currentText()
        dialog.findChild(QWidget, "HamQQsoCall").setText("DL1XYZ")
        dialog.findChild(QWidget, "HamQQsoGrid").setText("JO62qm")
        assert dialog.validation_errors() == []
        return compat.DIALOG_ACCEPTED

    monkeypatch.setattr(QsoDialog, "exec", fake_exec)
    plugin.log_qso_action.trigger()
    assert seen == {"freq": "14.074", "band": "20m", "mode": "SSB"}
    path = env.settings.gpkg_path
    assert calls_on_map(path) == ["DL1XYZ"]
    row = gpkg.read_qso_rows(path)[0]
    assert (row["source"], row["mode"], row["band"], row["freq_mhz"]) == (
        "manual",
        "SSB",
        "20m",
        14.074,
    )
    assert f"New QSO: DL1XYZ 20m SSB ({expected_distance('JO62qm')} km)" in bar_texts(iface)

    # the panel's "Log QSO..." button: the same dialog, here canceled
    monkeypatch.setattr(QsoDialog, "exec", lambda dialog: compat.DIALOG_REJECTED)
    controller.dock.findChild(QPushButton, "HamQLogQsoButton").click()
    assert calls_on_map(path) == ["DL1XYZ"]


# --------------------------------------------------------------------------- first run


def test_first_run_hints_and_their_buttons(env, load_plugin, iface, monkeypatch):
    env.settings.my_grid = ""
    plugin = load_plugin()
    controller = plugin.controller
    assert sorted(controller.hint_keys()) == ["cty", "grid"]
    downloads, dialogs = [], []
    monkeypatch.setattr(controller.cty_manager, "download", lambda: downloads.append(1))

    def fake_exec(dialog):
        dialogs.append(dialog)
        env.settings.my_grid = MY_GRID  # the user set the locator and pressed OK
        events().settingsChanged.emit()
        return compat.DIALOG_ACCEPTED

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    buttons = {
        button.text(): button
        for button in iface.messageBar().findChildren(QPushButton, "HamQHintButton")
    }
    buttons["Download"].click()
    assert downloads == [1]
    assert "cty" not in controller.hint_keys()
    buttons["Settings..."].click()
    assert len(dialogs) == 1
    assert controller.hint_keys() == []

    # the end of a download is shown in the message bar
    controller.cty_manager.downloadFinished.emit(True, "cty.dat downloaded, entities: 346")
    assert "cty.dat downloaded, entities: 346" in bar_texts(iface)


def test_unusable_geopackage_is_reported_once(env, load_plugin, iface):
    broken = env.tmp / "not_a_geopackage.gpkg"
    broken.write_text("this is not a GeoPackage", encoding="utf-8")
    env.settings.gpkg_path = str(broken)
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(lambda: not controller.is_refreshing())
    controller.refresh()
    assert wait_until(lambda: not controller.is_refreshing())
    warnings = [text for text in bar_texts(iface) if text.startswith("The statistics could not")]
    assert len(warnings) == 1


# --------------------------------------------------------------------------- statistics


def test_refresh_requests_are_merged(env, load_plugin):
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(lambda: not controller.is_refreshing())
    shown = []
    controller.statsChanged.connect(shown.append)
    for _ in range(5):
        controller.refresh()
    assert wait_until(lambda: not controller.is_refreshing())
    assert len(shown) == 2  # the running refresh, then one for the four requests after it


def test_data_changed_of_another_file_is_ignored(env, load_plugin):
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(lambda: not controller.is_refreshing())
    events().dataChanged.emit(str(env.tmp / "somebody_elses.gpkg"))
    assert not controller.is_refreshing()
    events().dataChanged.emit(env.settings.gpkg_path)
    assert controller.is_refreshing()


@pytest.fixture(scope="module")
def big_log(tmp_path_factory, qgis_app):
    """A GeoPackage with 10 000 QSOs (about 3 s to build)."""
    path = str(tmp_path_factory.mktemp("big") / "big.gpkg")
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bands, modes = ("20m", "40m", "15m", "10m"), ("FT8", "CW", "SSB", "FT4")
    records = []
    for index in range(10_000):
        when = start + timedelta(minutes=7 * index)
        lat, lon = -60 + (index * 7) % 130, -179 + (index * 13) % 358
        records.append(
            {
                "CALL": f"YU{index % 9 + 1}A{chr(65 + index % 26)}{chr(65 + (index // 26) % 26)}",
                "QSO_DATE": when.strftime("%Y%m%d"),
                "TIME_ON": when.strftime("%H%M%S"),
                "BAND": bands[index % 4],
                "MODE": modes[index % 3],
                "GRIDSQUARE": maidenhead.to_locator(lat, lon, 4),
                "DXCC": str(1 + index % 300),
                "CONT": "EU",
            }
        )
    qsos, _warnings = records_to_qsos(records, station=Station(grid=MY_GRID), source="test")
    assert gpkg.insert_qsos(path, qsos).inserted == 10_000
    return path


@pytest.mark.slow
def test_statistics_of_10000_qsos_refresh_quickly(env, load_plugin, big_log):
    env.settings.gpkg_path = big_log
    plugin = load_plugin()
    controller = plugin.controller
    assert wait_until(lambda: controller.stats is not None and controller.stats.total == 10_000)

    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    shown = []
    controller.statsChanged.connect(lambda stats: shown.append(time.perf_counter()))
    timer.start()
    started = time.perf_counter()
    controller.refresh()
    assert wait_until(lambda: bool(shown), 5.0)
    timer.stop()
    elapsed = shown[0] - started
    gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:])]
    assert elapsed < 0.3, f"refresh took {elapsed * 1000:.0f} ms"
    # the work runs in a QgsTask: the user interface keeps running meanwhile
    assert gaps and max(gaps) < 0.1, f"longest event loop gap {max(gaps) * 1000:.0f} ms"
    assert controller.dock.findChild(QWidget, "HamQTileTotal").value.text() == "10 000"


def test_several_records_and_duplicates(env, load_plugin, iface):
    plugin = load_plugin()
    controller = plugin.controller
    records = [
        {
            "CALL": "DL1XYZ",
            "QSO_DATE": "20260901",
            "TIME_ON": "1200",
            "BAND": "20m",
            "MODE": "CW",
            "GRIDSQUARE": "JO62",
        },
        {
            "CALL": "W1XYZ",
            "QSO_DATE": "20260901",
            "TIME_ON": "1300",
            "BAND": "20m",
            "MODE": "SSB",
            "GRIDSQUARE": "FN42",
        },
    ]
    assert controller.save_records(records, "test") == 2
    assert "QSOs saved: 2, duplicates: 0" in bar_texts(iface)
    assert controller.save_records(records, "test") == 0
    assert "QSOs saved: 0, duplicates: 2" in bar_texts(iface)
    assert calls_on_map(env.settings.gpkg_path) == ["DL1XYZ", "W1XYZ"]
    stats = controller.refresh_now()
    assert stats.total == 2
    assert controller.stats is stats


def test_live_qso_while_the_layers_are_edited(env, load_plugin, iface):
    plugin = load_plugin()
    controller = plugin.controller
    qso_layer, _path_layer = controller.load_layers()
    assert qso_layer.startEditing()
    try:
        assert controller.handle_logged_adif("WSJT-X", LIVE_ADIF) == 1
        assert any("edit mode" in text for text in bar_texts(iface))
    finally:
        qso_layer.rollBack()
    assert [row["call"] for row in gpkg.read_qso_rows(env.settings.gpkg_path)] == ["VK2XYZ"]
