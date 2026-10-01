"""hamq.gui.dock.HamQDock: statistics, WSJT-X and radio tabs, signals, retranslation."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone
from qgis.PyQt.QtGui import QValidator
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QToolButton,
    QWidget,
)

from hamq.core import i18n, wsjtx
from hamq.core.stats import QsoStats, compute_stats
from hamq.events import events
from hamq.gui import dock as dock_module
from hamq.gui.dock import (
    HamQDock,
    decimal_separator,
    format_azimuth,
    format_bearing,
    format_distance,
    format_mhz,
    format_number,
    format_utc,
)
from hamq.qgis_io.compat import MSG_CRITICAL, MSG_WARNING

UTC = timezone.utc
NBSP = "\u00a0"
DASH = "\u2014"

ROWS = [
    {
        "call": "YU1AB",
        "qso_datetime": datetime(2024, 1, 2, 12, 34, tzinfo=UTC),
        "band": "20m",
        "mode": "FT8",
        "gridsquare": "KN04",
        "dxcc": 296,
        "country": "Serbia",
        "cont": "EU",
        "distance_km": 12.5,
    },
    {
        "call": "DL1XYZ",
        "qso_datetime": datetime(2024, 3, 5, 8, 0, tzinfo=UTC),
        "band": "20m",
        "mode": "MFSK",
        "submode": "FT4",
        "gridsquare": "JO62qm",
        "dxcc": 230,
        "country": "Fed. Rep. of Germany",
        "cont": "EU",
        "distance_km": 1000.0,
    },
    {
        "call": "VK2ABC",
        "qso_datetime": datetime(2025, 6, 1, 21, 15, tzinfo=UTC),
        "band": "15m",
        "mode": "SSB",
        "gridsquare": "QF56",
        "dxcc": 150,
        "country": "Australia",
        "cont": "OC",
        "distance_km": 15676.2,
    },
    {
        "call": "K1ABC",
        "qso_datetime": datetime(2025, 2, 1, 3, 0, tzinfo=UTC),
        "band": "40m",
        "mode": "CW",
        "gridsquare": "FN42",
        "dxcc": 291,
        "country": "United States of America",
        "cont": "NA",
        "distance_km": 7000.0,
    },
    {
        "call": "YU1AB",
        "qso_datetime": datetime(2026, 9, 30, 18, 45, tzinfo=UTC),
        "band": "40m",
        "mode": "FT8",
        "gridsquare": "KN04",
        "dxcc": 296,
        "country": "Serbia",
        "cont": "EU",
        "distance_km": 12.5,
    },
    {"call": "XX9XX"},  # nothing known: "?" rows
]


class Spy:
    """Records the arguments of every emission of a signal."""

    def __init__(self, signal):
        self.calls = []
        signal.connect(self._slot)

    def _slot(self, *args):
        self.calls.append(args)


@pytest.fixture
def stats() -> QsoStats:
    return compute_stats(ROWS)


@pytest.fixture
def language():
    """Switch the HamQ language like the language manager does; English afterwards."""

    def switch(code: str) -> None:
        i18n.set_language(code)
        events().languageChanged.emit(code)

    switch("en")
    yield switch
    switch("en")


@pytest.fixture
def station():
    return SimpleNamespace(my_call="YU1AB", my_grid="KN04ft")


@pytest.fixture
def dock(qgis_app, station, process_events, language):
    widget = HamQDock(settings=station)
    widget.resize(360, 700)
    yield widget
    widget.cleanup()
    widget.deleteLater()
    process_events()


def child(parent: QWidget, cls, name: str):
    widget = parent.findChild(cls, name)
    assert widget is not None, name
    return widget


def label(dock: HamQDock, name: str) -> str:
    return child(dock, QLabel, name).text()


def table_rows(table: QTableWidget) -> list[tuple[str, ...]]:
    return [
        tuple(table.item(row, column).text() for column in range(table.columnCount()))
        for row in range(table.rowCount())
    ]


def no_problems(messages) -> None:
    problems = [m for m in messages if m[1] == "HamQ" and m[2] in (MSG_WARNING, MSG_CRITICAL)]
    assert not problems, problems


# --------------------------------------------------------------------------- structure


def test_object_name_and_tabs(dock):
    assert dock.objectName() == "HamQDock"
    assert dock.windowTitle() == "HamQ"
    tabs = child(dock, QTabWidget, "HamQDockTabs")
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Statistics", "WSJT-X", "Radio"]
    assert dock.current_tab() == HamQDock.TAB_STATISTICS
    dock.show_tab(HamQDock.TAB_RADIO)
    assert tabs.currentIndex() == HamQDock.TAB_RADIO


def test_created_without_settings_reads_hamq_settings(qgis_app, clean_settings, process_events):
    from hamq.settings import HamQSettings

    HamQSettings().my_call = "yu7xyz"
    HamQSettings().my_grid = "kn05aa"
    widget = HamQDock()
    try:
        assert widget.findChild(QLabel, "HamQStationLabel").text() == "YU7XYZ · KN05aa"
    finally:
        widget.cleanup()
        widget.deleteLater()
        process_events()


# --------------------------------------------------------------------------- statistics


def test_empty_state_for_none(dock, log_messages):
    spy = Spy(dock.importRequested)
    dock.set_stats(None)
    assert child(dock, QStackedWidget, "HamQStatsStack").currentIndex() == 0
    assert label(dock, "HamQEmptyTitle") == "No QSOs yet"
    button = child(dock, QPushButton, "HamQImportButton")
    assert button.text() == "Import ADIF..."
    button.click()
    assert spy.calls == [()]
    no_problems(log_messages)


def test_empty_state_for_a_log_without_qsos(dock, stats):
    dock.set_stats(stats)
    assert child(dock, QStackedWidget, "HamQStatsStack").currentIndex() == 1
    dock.set_stats(compute_stats([]))
    assert child(dock, QStackedWidget, "HamQStatsStack").currentIndex() == 0


def test_realistic_stats(dock, stats, log_messages):
    dock.set_stats(stats)
    assert child(dock, QStackedWidget, "HamQStatsStack").currentIndex() == 1
    assert label(dock, "HamQTileTotalCaption") == "Total QSOs"
    assert label(dock, "HamQTileTotalValue") == "6"
    assert label(dock, "HamQTileDxccCaption") == "DXCC entities"
    assert label(dock, "HamQTileDxccValue") == "4"
    assert label(dock, "HamQTileCallsCaption") == "Unique callsigns"
    assert label(dock, "HamQTileCallsValue") == "5"
    assert label(dock, "HamQTileGridsCaption") == "Grid squares"
    assert label(dock, "HamQTileGridsValue") == "4"
    assert label(dock, "HamQTileLongestCaption") == "Longest QSO"
    assert label(dock, "HamQTileLongestValue") == "VK2ABC"
    assert label(dock, "HamQTileLongestDetail") == f"15{NBSP}676{NBSP}km · Australia"
    assert (
        dock.findChild(QWidget, "HamQTileLongest").toolTip() == "15m · SSB · 2025-06-01 21:15 UTC"
    )
    assert label(dock, "HamQFirstQso") == "First QSO: 2024-01-02 12:34 UTC"
    assert label(dock, "HamQLastQso") == "Last QSO: 2026-09-30 18:45 UTC"

    continents = child(dock, QTableWidget, "HamQContinentTable")
    assert [continents.horizontalHeaderItem(i).text() for i in range(3)] == [
        "Continent",
        "QSOs",
        "Share",
    ]
    assert table_rows(continents) == [
        ("Europe (EU)", "3", "50.0%"),
        ("North America (NA)", "1", "16.7%"),
        ("Oceania (OC)", "1", "16.7%"),
        ("Unknown", "1", "16.7%"),
    ]
    bands = child(dock, QTableWidget, "HamQBandTable")
    assert bands.horizontalHeaderItem(0).text() == "Band"
    assert table_rows(bands) == [
        ("40m", "2", "33.3%"),
        ("20m", "2", "33.3%"),
        ("15m", "1", "16.7%"),
        ("Unknown", "1", "16.7%"),
    ]
    modes = child(dock, QTableWidget, "HamQModeTable")
    assert modes.horizontalHeaderItem(0).text() == "Mode"
    # core.stats sorts modes by count, then name ("?" before letters): the panel puts
    # the unknown row last, as for continents and bands
    assert list(stats.by_mode) == ["FT8", "?", "CW", "FT4", "SSB"]
    assert table_rows(modes) == [
        ("FT8", "2", "33.3%"),
        ("CW", "1", "16.7%"),
        ("FT4", "1", "16.7%"),
        ("SSB", "1", "16.7%"),
        ("Unknown", "1", "16.7%"),
    ]
    # every row is visible without scrolling inside the table
    for table in (continents, bands, modes):
        rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
        assert table.height() >= rows + table.horizontalHeader().sizeHint().height()
    no_problems(log_messages)


def test_stats_without_longest_or_times(dock):
    dock.set_stats(compute_stats([{"call": "YU1AB", "band": "20m", "mode": "FT8"}]))
    assert label(dock, "HamQTileTotalValue") == "1"
    assert label(dock, "HamQTileLongestValue") == DASH
    assert label(dock, "HamQFirstQso") == f"First QSO: {DASH}"
    assert table_rows(child(dock, QTableWidget, "HamQContinentTable")) == [
        ("Unknown", "1", "100.0%")
    ]


def test_large_numbers_are_grouped(dock):
    stats = compute_stats([{"call": f"K{i}AA", "band": "20m"} for i in range(12345)])
    dock.set_stats(stats)
    assert label(dock, "HamQTileTotalValue") == f"12{NBSP}345"


def test_station_header(dock, station, process_events):
    assert label(dock, "HamQStationLabel") == "YU1AB · KN04ft"
    assert child(dock, QLabel, "HamQStationHint").isHidden()
    dock.set_station("", "")
    assert child(dock, QLabel, "HamQStationLabel").isHidden()
    hint = child(dock, QLabel, "HamQStationHint")
    assert not hint.isHidden()
    assert hint.text() == "Set your callsign and QTH locator in Settings."
    dock.set_station("yu1ab", "")
    assert label(dock, "HamQStationLabel") == "YU1AB"
    assert not hint.isHidden()
    # the settings are read again when they are saved
    station.my_call, station.my_grid = "YT1X", "KN05bb"
    events().settingsChanged.emit()
    assert label(dock, "HamQStationLabel") == "YT1X · KN05bb"
    assert hint.isHidden()


def test_station_hint_for_an_invalid_locator(dock):
    hint = child(dock, QLabel, "HamQStationHint")
    dock.set_station("YU1AB", "not a locator")
    assert label(dock, "HamQStationLabel") == "YU1AB · not a locator"
    assert not hint.isHidden()  # no distances without a valid QTH locator
    dock.set_station("YU1AB", "KN04")
    assert hint.isHidden()


def test_toolbar_buttons_emit(dock):
    refresh, imports, settings = (
        Spy(dock.refreshRequested),
        Spy(dock.importRequested),
        Spy(dock.settingsRequested),
    )
    child(dock, QToolButton, "HamQRefreshButton").click()
    child(dock, QToolButton, "HamQImportToolButton").click()
    child(dock, QToolButton, "HamQSettingsButton").click()
    child(dock, QToolButton, "HamQRadioSettingsButton").click()
    assert (len(refresh.calls), len(imports.calls), len(settings.calls)) == (1, 1, 2)
    assert child(dock, QToolButton, "HamQRefreshButton").toolTip() == "Refresh the statistics"


# --------------------------------------------------------------------------- WSJT-X


def test_wsjtx_listening_states(dock, log_messages):
    toggles = Spy(dock.listenToggled)
    button = child(dock, QPushButton, "HamQListenButton")
    led = dock.findChild(QLabel, "HamQWsjtxLed")
    assert label(dock, "HamQWsjtxState") == "Not listening"
    assert button.text() == "Start listening"
    assert not button.isChecked()
    assert led.state() == "off"

    dock.set_listening(True, "127.0.0.1", 2237)
    assert label(dock, "HamQWsjtxState") == "Listening on 127.0.0.1:2237, waiting for WSJT-X"
    assert button.isChecked()
    assert button.text() == "Stop listening"
    assert led.state() == "wait"

    dock.set_wsjtx_connected(True, "WSJT-X", "2.7.0")
    assert label(dock, "HamQWsjtxState") == "Connected: WSJT-X 2.7.0"
    assert led.state() == "on"
    dock.set_wsjtx_connected(False)
    assert label(dock, "HamQWsjtxState") == "Listening on 127.0.0.1:2237, waiting for WSJT-X"
    dock.set_wsjtx_connected(True)  # client and version are remembered
    assert label(dock, "HamQWsjtxState") == "Connected: WSJT-X 2.7.0"

    dock.set_listening(False)
    assert label(dock, "HamQWsjtxState") == "Not listening"
    assert not button.isChecked()
    assert button.text() == "Start listening"
    assert led.state() == "off"
    assert toggles.calls == []  # setters never emit
    no_problems(log_messages)


def test_wsjtx_ipv6_and_unknown_client(dock):
    dock.set_listening(True, "::1", 2238)
    assert label(dock, "HamQWsjtxState") == "Listening on [::1]:2238, waiting for WSJT-X"
    dock.set_wsjtx_connected(True, "", "")
    assert label(dock, "HamQWsjtxState") == "Connected: WSJT-X"


def test_listening_port_and_address_are_tolerant(dock, log_messages):
    dock.set_listening(True, "224.0.0.1", "2237")  # numeric text, multicast group
    assert label(dock, "HamQWsjtxState") == "Listening on 224.0.0.1:2237, waiting for WSJT-X"
    dock.set_listening(True, None, None)
    assert label(dock, "HamQWsjtxState") == "Listening on 0.0.0.0:0, waiting for WSJT-X"
    no_problems(log_messages)


def test_wsjtx_error(dock, log_messages):
    error = child(dock, QLabel, "HamQWsjtxError")
    led = dock.findChild(QLabel, "HamQWsjtxLed")
    assert error.isHidden()
    message = "UDP port 2237 is already in use, probably by another program"
    dock.set_wsjtx_error(message)  # the listener could not start
    assert (error.text(), error.isHidden()) == (message, False)
    assert led.state() == "error"
    assert label(dock, "HamQWsjtxState") == "Not listening"
    dock.set_listening(True, "224.0.0.1", 2237)  # started after all
    assert error.isHidden()
    assert led.state() == "wait"
    dock.set_wsjtx_error("socket error")  # a problem while listening
    assert (error.text(), error.isHidden()) == ("socket error", False)
    dock.set_wsjtx_error(None)
    assert error.isHidden()
    no_problems(log_messages)


def test_rig_and_rotator_errors(dock, log_messages):
    rig_error = child(dock, QLabel, "HamQRigError")
    rot_error = child(dock, QLabel, "HamQRotatorError")
    dock.set_rig_error("ignored while radio control is off")
    dock.set_rotator_error("ignored while rotator control is off")
    assert rig_error.isHidden()
    dock.set_rig_enabled(True)
    dock.set_rotator_enabled(True)
    assert rig_error.isHidden() and rot_error.isHidden()  # nothing stale comes back
    dock.set_rotator_enabled(False)
    dock.set_rig_error("Connection refused")
    assert (rig_error.text(), rig_error.isHidden()) == ("Connection refused", False)
    dock.set_rig_connected(True)
    assert rig_error.isHidden()
    dock.set_rig_error("Feature not available")  # a failed set command
    assert not rig_error.isHidden()
    child(dock, QPushButton, "HamQRigSetButton").click()  # a new command
    assert rig_error.isHidden()
    dock.set_rig_error("again")
    dock.set_rig_enabled(False)
    assert rig_error.isHidden()

    dock.set_rotator_enabled(True)
    dock.set_rotator_error("Connection refused")
    assert not rot_error.isHidden()
    dock.set_rotator_connected(True)
    assert rot_error.isHidden()
    for clear in (
        child(dock, QPushButton, "HamQRotatorTurnButton").click,
        child(dock, QPushButton, "HamQRotatorStopButton").click,
        lambda: dock.set_rotator_target(90.0),
        lambda: dock.set_rotator_error(""),
        lambda: dock.set_rotator_enabled(False),
    ):
        dock.set_rotator_enabled(True)
        dock.set_rotator_error("Limit exceeded")
        assert (rot_error.text(), rot_error.isHidden()) == ("Limit exceeded", False)
        clear()
        assert rot_error.isHidden()
    no_problems(log_messages)


def test_listen_button_emits(dock):
    toggles = Spy(dock.listenToggled)
    button = child(dock, QPushButton, "HamQListenButton")
    button.click()
    assert toggles.calls == [(True,)]
    assert button.text() == "Stop listening"
    # the controller could not start the listener
    dock.set_listening(False)
    assert not button.isChecked()
    button.click()
    dock.set_listening(True, "127.0.0.1", 2237)
    button.click()
    assert toggles.calls == [(True,), (True,), (False,)]


def test_wsjtx_status(dock, log_messages):
    for name in ("HamQWsjtxFrequency", "HamQWsjtxBand", "HamQWsjtxMode", "HamQWsjtxDxCall"):
        assert label(dock, name) == DASH
    status = wsjtx.decode(wsjtx.encode_status("WSJT-X", 14074000, "FT8", "YU1AB"))
    dock.set_wsjtx_status(status)
    assert label(dock, "HamQWsjtxFrequency") == f"14.074000{NBSP}MHz"
    assert label(dock, "HamQWsjtxBand") == "20m"
    assert label(dock, "HamQWsjtxMode") == "FT8"
    assert label(dock, "HamQWsjtxDxCall") == "YU1AB"
    # WSJT-X without a rig (0 Hz) and null strings
    dock.set_wsjtx_status({"type": "status", "freq_hz": 0, "mode": None, "dx_call": None})
    for name in ("HamQWsjtxFrequency", "HamQWsjtxBand", "HamQWsjtxMode", "HamQWsjtxDxCall"):
        assert label(dock, name) == DASH
    dock.set_wsjtx_status({"freq_hz": 50313000, "mode": "FT8"})
    assert label(dock, "HamQWsjtxBand") == "6m"
    dock.set_wsjtx_status(None)
    assert label(dock, "HamQWsjtxFrequency") == DASH
    assert child(dock, QGroupBox, "HamQWsjtxStatusGroup").title() == "Last status"
    no_problems(log_messages)


def test_status_client_is_used_when_connected(dock):
    dock.set_listening(True, "127.0.0.1", 2237)
    dock.set_wsjtx_status({"client": "JTDX", "freq_hz": 7074000, "mode": "FT8"})
    dock.set_wsjtx_connected(True)
    assert label(dock, "HamQWsjtxState") == "Connected: JTDX"


def test_last_qso_from_adif_record(dock, log_messages):
    assert label(dock, "HamQLastQsoCall") == "No QSO logged yet"
    dock.set_last_qso(
        {
            "CALL": "yu1ab",
            "QSO_DATE": "20260930",
            "TIME_ON": "184512",
            "FREQ": "14.075123",
            "MODE": "MFSK",
            "SUBMODE": "FT4",
            "GRIDSQUARE": "KN04",
        }
    )
    assert label(dock, "HamQLastQsoCall") == "YU1AB"
    assert label(dock, "HamQLastQsoDetail") == "20m · FT4 · 2026-09-30 18:45 UTC · KN04"
    no_problems(log_messages)


def test_last_qso_from_qso_attributes(dock):
    dock.set_last_qso(
        {
            "call": "VK2ABC",
            "qso_datetime": QDateTime(QDate(2025, 6, 1), QTime(21, 15), QTimeZone.utc()),
            "band": "15m",
            "mode": "SSB",
            "submode": None,
            "gridsquare": "QF56",
            "distance_km": 15676.2,
            "country": "Australia",
        }
    )
    assert label(dock, "HamQLastQsoCall") == "VK2ABC"
    assert label(dock, "HamQLastQsoDetail") == (
        f"15m · SSB · 2025-06-01 21:15 UTC · QF56 · 15{NBSP}676{NBSP}km · Australia"
    )
    dock.set_last_qso(None)
    assert label(dock, "HamQLastQsoCall") == "No QSO logged yet"


# --------------------------------------------------------------------------- radio


def test_rig_disabled_look(dock, log_messages):
    state = child(dock, QLabel, "HamQRigState")
    assert state.text() == "Radio control is off. Turn it on in Settings."
    assert dock.findChild(QLabel, "HamQRigLed").state() == "disabled"
    assert not child(dock, QPushButton, "HamQRigSetButton").isEnabled()
    assert not child(dock, QLabel, "HamQRigFrequency").isEnabled()
    assert not child(dock, QLabel, "HamQRigFrequencyUnit").isEnabled()
    assert label(dock, "HamQRigFrequency") == DASH
    dock.set_rig_enabled(True)
    assert state.text() == "Not connected"
    assert dock.findChild(QLabel, "HamQRigLed").state() == "error"
    assert child(dock, QLabel, "HamQRigFrequency").isEnabled()
    assert not child(dock, QPushButton, "HamQRigSetButton").isEnabled()
    dock.set_rig_connected(True)
    assert child(dock, QLabel, "HamQRigFrequencyUnit").isEnabled()
    no_problems(log_messages)


def test_rig_state_and_set(dock, log_messages):
    sets = Spy(dock.rigSetRequested)
    dock.set_rig_enabled(True)
    dock.set_rig_connected(True)
    dock.set_rig_state({"freq_hz": 14074000, "mode": "USB", "passband": 2400})
    assert label(dock, "HamQRigState") == "Connected"
    assert dock.findChild(QLabel, "HamQRigLed").state() == "on"
    assert label(dock, "HamQRigFrequency") == f"14.074000{NBSP}MHz"
    assert label(dock, "HamQRigDetails") == f"20m · USB · 2400{NBSP}Hz"
    spin = child(dock, QDoubleSpinBox, "HamQRigFrequencySpin")
    combo = child(dock, QComboBox, "HamQRigModeCombo")
    assert spin.isEnabled() and combo.isEnabled()
    assert spin.value() == pytest.approx(14.074)
    assert combo.currentText() == "USB"
    set_button = child(dock, QPushButton, "HamQRigSetButton")
    assert set_button.text() == "Set"

    # the controls follow the radio ...
    dock.set_rig_state({"freq_hz": 7074000, "mode": "PKTUSB", "passband": 3000})
    assert spin.value() == pytest.approx(7.074)
    assert combo.currentText() == "PKTUSB"
    # ... until the user edits them
    spin.setValue(3.573)
    combo.setCurrentIndex(combo.findText("CW"))
    dock.set_rig_state({"freq_hz": 7075000, "mode": "PKTUSB", "passband": 3000})
    assert spin.value() == pytest.approx(3.573)
    assert combo.currentText() == "CW"
    assert label(dock, "HamQRigFrequency") == f"7.075000{NBSP}MHz"
    set_button.click()
    assert sets.calls == [(3573000, "CW")]
    # after "Set" they follow the radio again
    dock.set_rig_state({"freq_hz": 3573000, "mode": "CW", "passband": 500})
    dock.set_rig_state({"freq_hz": 3574500, "mode": "CW", "passband": 500})
    assert spin.value() == pytest.approx(3.5745)
    no_problems(log_messages)


def test_rig_unknown_values_and_disconnect(dock, log_messages):
    dock.set_rig_enabled(True)
    dock.set_rig_connected(True)
    dock.set_rig_state({"freq_hz": None, "mode": None, "passband": None})
    assert label(dock, "HamQRigFrequency") == DASH
    assert label(dock, "HamQRigDetails") == f"{DASH} · {DASH}"
    dock.set_rig_state({"freq_hz": 145000000, "mode": "FM", "passband": 15000})
    assert label(dock, "HamQRigDetails") == f"2m · FM · 15{NBSP}000{NBSP}Hz"
    dock.set_rig_connected(False)
    assert label(dock, "HamQRigState") == "Not connected"
    assert label(dock, "HamQRigFrequency") == DASH  # nothing stale is shown
    assert not child(dock, QPushButton, "HamQRigSetButton").isEnabled()
    dock.set_rig_connected(True)  # reconnected: no old frequency until the radio reports
    assert label(dock, "HamQRigFrequency") == DASH
    dock.set_rig_state(None)
    no_problems(log_messages)


@pytest.mark.parametrize(
    ("mhz", "hz", "band"),
    [(2400.1, 2400100000, "13cm"), (10368.1, 10368100000, "3cm"), (1296.2, 1296200000, "23cm")],
)
def test_microwave_frequencies_are_not_truncated(dock, mhz, hz, band):
    """rigSetRequested carries a 64-bit Hz value: a C++ int would wrap above 2147 MHz."""
    sets = Spy(dock.rigSetRequested)
    dock.set_rig_enabled(True)
    dock.set_rig_connected(True)
    dock.set_rig_state({"freq_hz": hz, "mode": "USB", "passband": 2400})
    assert label(dock, "HamQRigDetails").startswith(f"{band} · USB")
    assert child(dock, QDoubleSpinBox, "HamQRigFrequencySpin").value() == pytest.approx(mhz)
    child(dock, QPushButton, "HamQRigSetButton").click()
    assert sets.calls == [(hz, "USB")]


def test_frequency_spin_box_accepts_point_and_comma(dock):
    spin = child(dock, QDoubleSpinBox, "HamQRigFrequencySpin")
    for text in ("7,074", "7.074", " 7.074 "):
        spin.lineEdit().setText(text)
        spin.interpretText()
        assert spin.value() == pytest.approx(7.074), text
    acceptable = QValidator.State.Acceptable
    assert spin.validate("14,074", 0)[0] == acceptable
    assert spin.validate("14.074", 0)[0] == acceptable
    assert spin.validate("14.07.4", 0)[0] == QValidator.State.Invalid
    assert spin.validate("14.0741234", 0)[0] == QValidator.State.Invalid  # 7 decimals
    assert spin.validate("abc", 0)[0] == QValidator.State.Invalid
    assert spin.validate("", 0)[0] == QValidator.State.Intermediate
    assert spin.validate("0", 0)[0] == QValidator.State.Intermediate
    assert spin.validate("9999999", 0)[0] == QValidator.State.Invalid
    spin.setValue(7.074)
    assert spin.valueFromText("nan") == pytest.approx(7.074)  # keeps the value
    assert spin.valueFromText("x") == pytest.approx(7.074)


def test_frequency_spin_box_never_raises_into_qt(dock, monkeypatch, log_messages):
    """textFromValue / validate are C++ virtuals: errors are logged, never raised."""

    def broken(*args, **kwargs):
        raise RuntimeError("formatter broke")

    spin = child(dock, QDoubleSpinBox, "HamQRigFrequencySpin")
    monkeypatch.setattr(dock_module, "format_number", broken)
    assert spin.textFromValue(14.074) == "14.074000"
    monkeypatch.setattr(spin, "_validate", broken)
    assert spin.validate("14", 0)[0] == QValidator.State.Invalid
    critical = [m for m in log_messages if m[1] == "HamQ" and m[2] == MSG_CRITICAL]
    assert len(critical) == 2
    assert all("formatter broke" in m[0] for m in critical)


def test_rotator_disabled_look(dock):
    assert label(dock, "HamQRotatorState") == "Rotator control is off. Turn it on in Settings."
    assert dock.findChild(QLabel, "HamQRotatorLed").state() == "disabled"
    for cls, name in (
        (QPushButton, "HamQRotatorTurnButton"),
        (QPushButton, "HamQRotatorStopButton"),
        (QPushButton, "HamQPointOnMapButton"),
        (QSpinBox, "HamQRotatorTargetSpin"),
        (QCheckBox, "HamQLongPathCheck"),
        (QLabel, "HamQRotatorAzimuth"),
        (QLabel, "HamQRotatorTarget"),
        (QLabel, "HamQRotatorElevation"),
    ):
        assert not child(dock, cls, name).isEnabled(), name
    assert not child(dock, QWidget, "HamQCompass").isEnabled()
    dock.set_rotator_enabled(True)  # configured but not connected: readouts, no controls
    assert child(dock, QLabel, "HamQRotatorTarget").isEnabled()
    assert not child(dock, QPushButton, "HamQRotatorTurnButton").isEnabled()


def test_rotator_position_target_and_buttons(dock, log_messages):
    turns, stops, points = (
        Spy(dock.rotatorTurnRequested),
        Spy(dock.rotatorStopRequested),
        Spy(dock.pointOnMapToggled),
    )
    dock.set_rotator_enabled(True)
    assert label(dock, "HamQRotatorState") == "Not connected"
    dock.set_rotator_connected(True)
    assert label(dock, "HamQRotatorState") == "Connected"
    assert dock.findChild(QLabel, "HamQRotatorLed").state() == "on"
    compass = child(dock, QWidget, "HamQCompass")

    dock.set_rotator_position(123.4, 0.0)
    assert label(dock, "HamQRotatorAzimuth") == "123°"
    assert compass.heading() == pytest.approx(123.4)
    assert child(dock, QLabel, "HamQRotatorElevation").isHidden()
    dock.set_rotator_position(400.0, 12.0)  # 0..450 rotator in the overlap
    assert label(dock, "HamQRotatorAzimuth") == "40° (400°)"
    assert compass.heading() == pytest.approx(40.0)
    assert label(dock, "HamQRotatorElevation") == "Elevation: 12°"
    assert not child(dock, QLabel, "HamQRotatorElevation").isHidden()
    assert label(dock, "HamQRotatorTarget") == f"Target: {DASH}"
    dock.set_rotator_target(271.0)
    assert label(dock, "HamQRotatorTarget") == "Target: 271°"
    assert compass.target() == pytest.approx(271.0)
    dock.set_rotator_target(None)
    assert compass.target() is None

    spin = child(dock, QSpinBox, "HamQRotatorTargetSpin")
    long_path = child(dock, QCheckBox, "HamQLongPathCheck")
    turn = child(dock, QPushButton, "HamQRotatorTurnButton")
    spin.setValue(90)
    turn.click()
    assert not dock.is_long_path()
    long_path.setChecked(True)
    assert dock.is_long_path()  # the rotator map tool asks for it through get_long_path
    turn.click()
    spin.setValue(300)
    turn.click()
    assert turns.calls == [(90.0,), (270.0,), (120.0,)]
    child(dock, QPushButton, "HamQRotatorStopButton").click()
    assert stops.calls == [()]

    point = child(dock, QPushButton, "HamQPointOnMapButton")
    point.click()
    assert point.isChecked()
    assert points.calls == [(True,)]
    point.click()
    assert points.calls == [(True,), (False,)]
    dock.set_point_on_map_checked(True)  # the map tool was activated elsewhere
    assert point.isChecked()
    assert points.calls == [(True,), (False,)]  # no emission from the setter
    no_problems(log_messages)


def test_point_on_map_is_released_when_the_rotator_goes_away(dock):
    points = Spy(dock.pointOnMapToggled)
    dock.set_rotator_enabled(True)
    dock.set_rotator_connected(True)
    point = child(dock, QPushButton, "HamQPointOnMapButton")
    point.click()
    dock.set_rotator_connected(False)
    assert not point.isChecked()
    assert not point.isEnabled()
    assert points.calls == [(True,), (False,)]
    dock.set_rotator_connected(False)  # nothing more to release
    dock.set_rotator_position(90.0, 0.0)
    dock.set_rotator_connected(True)
    assert label(dock, "HamQRotatorAzimuth") == "90°"
    point.click()
    dock.set_rotator_enabled(False)
    assert points.calls == [(True,), (False,), (True,), (False,)]
    assert label(dock, "HamQRotatorAzimuth") == DASH
    dock.set_rotator_enabled(True)
    dock.set_rotator_connected(False)
    dock.set_rotator_connected(True)  # reconnected: no stale heading
    assert label(dock, "HamQRotatorAzimuth") == DASH


def test_log_qso_button(dock):
    spy = Spy(dock.logQsoRequested)
    button = child(dock, QPushButton, "HamQLogQsoButton")
    assert button.text() == "Log QSO..."
    button.click()
    assert spy.calls == [()]


# --------------------------------------------------------------------------- translation


def test_retranslate_serbian_latin_and_cyrillic(dock, stats, language, log_messages):
    dock.set_stats(stats)
    dock.set_listening(True, "127.0.0.1", 2237)
    dock.set_rig_enabled(True)
    dock.set_rig_connected(True)
    dock.set_rig_state({"freq_hz": 14074000, "mode": "USB", "passband": 2400})
    dock.set_rotator_enabled(True)
    dock.set_rotator_connected(True)
    dock.set_rotator_target(271.0)
    tabs = child(dock, QTabWidget, "HamQDockTabs")
    continents = child(dock, QTableWidget, "HamQContinentTable")

    language("sr_Latn")
    assert [tabs.tabText(i) for i in range(3)] == ["Statistika", "WSJT-X", "Radio"]
    assert label(dock, "HamQTileTotalCaption") == "Ukupno veza"
    assert continents.horizontalHeaderItem(0).text() == "Kontinent"
    assert table_rows(continents)[0] == ("Evropa (EU)", "3", "50,0%")
    assert label(dock, "HamQWsjtxState") == "Sluša se na 127.0.0.1:2237, čeka se WSJT-X"
    assert child(dock, QPushButton, "HamQListenButton").text() == "Zaustavi slušanje"
    assert label(dock, "HamQRigState") == "Povezan"
    assert label(dock, "HamQRigFrequency") == f"14,074000{NBSP}MHz"
    assert child(dock, QDoubleSpinBox, "HamQRigFrequencySpin").text() == "14,074000"
    assert label(dock, "HamQRotatorTarget") == "Cilj: 271°"
    assert child(dock, QPushButton, "HamQLogQsoButton").text() == "Upiši vezu..."
    assert decimal_separator() == ","

    language("sr_Cyrl")
    assert tabs.tabText(0) == "Статистика"
    assert label(dock, "HamQTileTotalCaption") == "Укупно веза"
    assert table_rows(continents)[0] == ("Европа (EU)", "3", "50,0%")
    assert label(dock, "HamQWsjtxState") == "Слуша се на 127.0.0.1:2237, чека се WSJT-X"
    assert child(dock, QPushButton, "HamQRotatorTurnButton").text() == "Окрени"

    language("en")
    assert tabs.tabText(0) == "Statistics"
    assert label(dock, "HamQRigFrequency") == f"14.074000{NBSP}MHz"
    assert child(dock, QDoubleSpinBox, "HamQRigFrequencySpin").text() == "14.074000"
    no_problems(log_messages)


def test_every_visible_text_changes_in_serbian(dock, stats, language):
    """No English text is left behind after switching to Serbian (Latin)."""
    dock.set_stats(stats)
    dock.set_rig_enabled(True)
    dock.set_rotator_enabled(True)

    def texts() -> dict[str, str]:
        found = {}
        for widget in dock.findChildren(QWidget):
            text = getattr(widget, "text", None)
            if isinstance(widget, (QLabel, QPushButton, QToolButton, QCheckBox)) and text():
                found[f"{widget.objectName()}:{text()}"] = text()
            if isinstance(widget, QGroupBox) and widget.title():
                found[f"{widget.objectName()}:{widget.title()}"] = widget.title()
            if widget.toolTip():
                found[f"{widget.objectName()}:tip:{widget.toolTip()}"] = widget.toolTip()
        return found

    english = texts()
    language("sr_Latn")
    serbian = set(texts().values())
    # texts that are the same in both languages (names, numbers, units, codes)
    same = {
        "HamQ",
        "MHz",
        "Radio",
        "YU1AB · KN04ft",
        "VK2ABC",
        "6",
        "4",
        "5",
        DASH,
        f"{DASH} · {DASH}",
        "Rotator",
        # log data: the longest QSO's distance, country, band, mode and time
        f"15{NBSP}676{NBSP}km · Australia",
        "15m · SSB · 2025-06-01 21:15 UTC",
    }
    leftovers = sorted(
        key for key, value in english.items() if value in serbian and value not in same
    )
    assert leftovers == []


def test_cleanup_disconnects_and_is_idempotent(qgis_app, station, language, process_events):
    widget = HamQDock(settings=station)
    tabs = widget.findChild(QTabWidget, "HamQDockTabs")
    receivers_before = events().receivers(events().languageChanged)
    widget.cleanup()
    widget.cleanup()
    assert events().receivers(events().languageChanged) < receivers_before
    language("sr_Latn")
    assert tabs.tabText(0) == "Statistics"  # no longer follows the language
    widget.deleteLater()
    process_events()


def test_deleting_the_dock_without_cleanup_disconnects(qgis_app, station, process_events):
    """Safety net: a deleted dock never receives (or keeps) HamQ events."""
    language_before = events().receivers(events().languageChanged)
    settings_before = events().receivers(events().settingsChanged)
    widget = HamQDock(settings=station)
    assert events().receivers(events().languageChanged) == language_before + 1
    widget.deleteLater()
    process_events()
    assert events().receivers(events().languageChanged) == language_before
    assert events().receivers(events().settingsChanged) == settings_before
    events().languageChanged.emit("en")  # nothing left to call
    events().settingsChanged.emit()


# --------------------------------------------------------------------------- robustness


def test_errors_in_setters_are_logged_not_raised(dock, stats, monkeypatch, log_messages):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dock_module, "format_number", broken)
    dock.set_stats(stats)  # must not raise
    critical = [m for m in log_messages if m[1] == "HamQ" and m[2] == MSG_CRITICAL]
    assert critical
    assert "Unexpected error in the HamQ panel" in critical[0][0]
    assert "boom" in critical[0][0]


def test_compass_and_dock_paint(dock, log_messages):
    dock.set_rotator_enabled(True)
    dock.set_rotator_connected(True)
    dock.set_rotator_position(123.0, 0.0)
    dock.set_rotator_target(271.0)
    dock.show_tab(HamQDock.TAB_RADIO)
    compass = child(dock, QWidget, "HamQCompass")
    compass.resize(140, 140)
    assert not compass.grab().isNull()
    dock.set_rotator_enabled(False)
    assert not compass.grab().isNull()
    assert not dock.grab().isNull()
    no_problems(log_messages)


# --------------------------------------------------------------------------- formatting


def test_format_helpers(language):
    assert format_number(6) == "6"
    assert format_number(1234) == "1234"
    assert format_number(12345) == f"12{NBSP}345"
    assert format_number(1234567.891, 2) == f"1{NBSP}234{NBSP}567.89"
    assert format_number(-0.0001, 2) == "0.00"
    assert format_number(-3.5, 1) == "-3.5"
    assert format_number(float("nan")) == DASH
    assert format_number("x") == DASH
    assert format_mhz(14074000) == f"14.074000{NBSP}MHz"
    assert format_mhz(0) == DASH
    assert format_mhz(None) == DASH
    assert format_distance(15676.2) == f"15{NBSP}676{NBSP}km"
    assert format_distance(None) == DASH
    assert format_azimuth(91.3) == "91°"
    assert format_azimuth(359.6) == "0° (360°)"
    assert format_azimuth(-90) == "270° (-90°)"
    assert format_azimuth(None) == DASH
    assert format_bearing(359.7) == "0°"  # a compass bearing never shows a raw value
    assert format_bearing(91.4) == "91°"
    assert format_bearing(-90) == "270°"
    assert format_bearing(float("nan")) == DASH
    when = datetime(2026, 9, 30, 18, 45, 12, tzinfo=UTC)
    assert format_utc(when) == "2026-09-30 18:45 UTC"
    assert format_utc(when.replace(tzinfo=None)) == "2026-09-30 18:45 UTC"
    assert format_utc("2026-09-30T18:45:00Z") == "2026-09-30 18:45 UTC"
    assert format_utc(QDateTime(QDate(2026, 9, 30), QTime(18, 45), QTimeZone.utc())) == (
        "2026-09-30 18:45 UTC"
    )
    assert format_utc(None) == DASH
    assert format_utc("not a date") == DASH
    language("sr_Latn")
    assert format_number(1234567.891, 2) == f"1{NBSP}234{NBSP}567,89"
    assert format_mhz(7074000) == f"7,074000{NBSP}MHz"
