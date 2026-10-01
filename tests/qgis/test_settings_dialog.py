"""hamq.gui.settings_dialog.SettingsDialog: load, validate, save, cancel, retranslate, cty.dat."""

from __future__ import annotations

import os

import pytest
from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QLocale, QObject, QTimer, pyqtSignal

from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, current_language, set_language
from hamq.events import events
from hamq.gui import language
from hamq.gui.language import LanguageManager
from hamq.gui.settings_dialog import AZIMUTH_PRESETS, LANGUAGE_CHOICES, SettingsDialog
from hamq.qgis_io import compat
from hamq.settings import HamQSettings, default_gpkg_path


class FakeCtyManager(QObject):
    """The part of ``net.cty_download.CtyManager`` the dialog uses."""

    downloadFinished = pyqtSignal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0

    def download(self) -> None:
        self.downloads += 1

    def is_available(self) -> bool:
        return False

    def database(self):
        raise AssertionError("the dialog must not parse cty.dat in the UI thread")


class Recorder:
    def __init__(self, signal):
        self.values: list = []
        self._signal = signal
        signal.connect(self._record)

    def _record(self, *args):
        self.values.append(args)

    def close(self):
        self._signal.disconnect(self._record)


@pytest.fixture(autouse=True)
def environment(clean_settings, monkeypatch, log_messages):
    """English QGIS, no locale override; English again afterwards; no slot errors."""
    for key in ("locale/overrideFlag", "locale/userLocale"):
        QgsSettings().remove(key)
    monkeypatch.setattr(language, "_system_locale", lambda: QLocale("en_US"))
    monkeypatch.setattr(language, "_qgis_translation", lambda: "")
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)
    errors = [m for m, tag, level in log_messages if tag == "HamQ" and level == compat.MSG_CRITICAL]
    assert errors == []


@pytest.fixture
def settings():
    return HamQSettings()


@pytest.fixture
def manager(settings):
    manager = LanguageManager(settings)
    manager.apply_from_settings()
    yield manager
    manager.cleanup()


@pytest.fixture
def cty():
    return FakeCtyManager()


@pytest.fixture
def make_dialog(iface, settings, manager, cty, process_events):
    dialogs = []

    def make(**kwargs):
        options = {"cty_manager": cty}
        options.update(kwargs)
        dialog = SettingsDialog(
            settings,
            options.pop("language_manager", manager),
            options["cty_manager"],
            iface.mainWindow(),
        )
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.cleanup()
        dialog.deleteLater()
    process_events()


@pytest.fixture
def saved():
    recorder = Recorder(events().settingsChanged)
    yield recorder
    recorder.close()


def store_everything(settings, tmp_path):
    settings.my_call = "yu1ab"
    settings.my_grid = "kn04FT"
    settings.gpkg_path = str(tmp_path / "log.gpkg")
    settings.wsjtx_addr = "224.0.0.1"
    settings.wsjtx_port = 2238
    settings.wsjtx_autostart = True
    settings.language = "sr_Cyrl"
    settings.rig_enabled = True
    settings.rig_host = "192.168.1.20"
    settings.rig_port = 4534
    settings.rig_poll_ms = 500
    settings.rot_enabled = True
    settings.rot_host = "rotator.local"
    settings.rot_port = 4535
    settings.rot_min_az = -180.0
    settings.rot_max_az = 180.0
    settings.rot_confirmed = True
    settings.cty_downloaded = "2026-09-29"


def test_loads_every_field(make_dialog, settings, tmp_path):
    store_everything(settings, tmp_path)
    dialog = make_dialog(language_manager=None)
    assert dialog.call_edit.text() == "YU1AB"
    assert dialog.grid_edit.text() == "KN04ft"
    assert dialog.gpkg_edit.text() == str(tmp_path / "log.gpkg")
    assert dialog.wsjtx_addr_edit.text() == "224.0.0.1"
    assert dialog.wsjtx_port_spin.value() == 2238
    assert dialog.wsjtx_autostart_check.isChecked()
    assert dialog.rig_enabled_check.isChecked()
    assert dialog.rig_host_edit.text() == "192.168.1.20"
    assert dialog.rig_port_spin.value() == 4534
    assert dialog.rig_poll_spin.value() == 500
    assert dialog.rot_enabled_check.isChecked()
    assert dialog.rot_host_edit.text() == "rotator.local"
    assert dialog.rot_port_spin.value() == 4535
    assert dialog.rot_min_spin.value() == -180.0
    assert dialog.rot_max_spin.value() == 180.0
    assert dialog.rot_preset_combo.currentIndex() == 2  # -180 ... 180
    assert dialog.selected_language() == LANG_SR_CYRL
    assert dialog.rot_reset_button.isEnabled()
    assert "2026-09-29" in dialog.cty_date_label.text()
    assert dialog.validate() == []


def test_defaults(make_dialog):
    dialog = make_dialog()
    assert dialog.call_edit.text() == ""
    assert dialog.grid_edit.text() == ""
    assert dialog.gpkg_edit.text() == default_gpkg_path()
    assert dialog.wsjtx_addr_edit.text() == "127.0.0.1"
    assert dialog.wsjtx_port_spin.value() == 2237
    assert dialog.rig_port_spin.value() == 4532
    assert dialog.rot_port_spin.value() == 4533
    assert dialog.rot_preset_combo.currentIndex() == 0  # 0 ... 360
    assert dialog.selected_language() == "auto"
    assert [dialog.language_combo.itemData(i) for i in range(4)] == list(LANGUAGE_CHOICES)
    assert not dialog.rot_reset_button.isEnabled()
    assert dialog.cty_date_label.text() == "Not downloaded yet"
    assert dialog.validate() == []


def test_ok_saves_every_field_and_emits_once(make_dialog, settings, tmp_path, saved):
    language_events = Recorder(events().languageChanged)
    try:
        store_everything(settings, tmp_path)
        settings.language = "auto"
        dialog = make_dialog()
        dialog.call_edit.setText(" yu7xyz/p ")
        dialog.grid_edit.setText(" jn95WG04 ")
        dialog.gpkg_edit.setText(str(tmp_path / "other.gpkg"))
        dialog.wsjtx_addr_edit.setText(" 127.0.0.1 ")
        dialog.wsjtx_port_spin.setValue(2240)
        dialog.wsjtx_autostart_check.setChecked(False)
        dialog.rig_enabled_check.setChecked(False)
        dialog.rig_host_edit.setText("127.0.0.1")
        dialog.rig_port_spin.setValue(14532)
        dialog.rig_poll_spin.setValue(2000)
        dialog.rot_enabled_check.setChecked(False)
        dialog.rot_host_edit.setText(" 10.0.0.5 ")
        dialog.rot_port_spin.setValue(14533)
        dialog.rot_min_spin.setValue(0.0)
        dialog.rot_max_spin.setValue(450.0)
        dialog.rot_reset_button.click()
        dialog.language_combo.setCurrentIndex(LANGUAGE_CHOICES.index(LANG_SR_LATN))
        assert current_language() == LANG_EN  # applied on OK only

        dialog.show()
        dialog.accept()
        assert dialog.result() == compat.DIALOG_ACCEPTED
        assert not dialog.isVisible()

        assert settings.my_call == "YU7XYZ/P"
        assert settings.my_grid == "JN95wg04"
        assert settings.gpkg_path == str(tmp_path / "other.gpkg")
        assert settings.wsjtx_addr == "127.0.0.1"
        assert settings.wsjtx_port == 2240
        assert settings.wsjtx_autostart is False
        assert settings.rig_enabled is False
        assert settings.rig_host == "127.0.0.1"
        assert settings.rig_port == 14532
        assert settings.rig_poll_ms == 2000
        assert settings.rot_enabled is False
        assert settings.rot_host == "10.0.0.5"
        assert settings.rot_port == 14533
        assert settings.rot_min_az == 0.0
        assert settings.rot_max_az == 450.0
        assert settings.rot_confirmed is False
        assert settings.language == LANG_SR_LATN
        assert settings.cty_downloaded == "2026-09-29"  # not an input
        assert current_language() == LANG_SR_LATN
        assert saved.values == [()]
        assert language_events.values == [(LANG_SR_LATN,)]
    finally:
        language_events.close()


def test_cancel_changes_nothing(make_dialog, settings, tmp_path, saved):
    store_everything(settings, tmp_path)
    before = {
        name: getattr(settings, name) for name in dir(HamQSettings) if not name.startswith("_")
    }
    before.pop("station")
    dialog = make_dialog()
    dialog.call_edit.setText("YT1XX")
    dialog.grid_edit.setText("JN58td")
    dialog.wsjtx_port_spin.setValue(2299)
    dialog.rig_enabled_check.setChecked(False)
    dialog.rot_reset_button.click()
    dialog.language_combo.setCurrentIndex(0)
    dialog.show()
    dialog.reject()
    assert dialog.result() == compat.DIALOG_REJECTED
    after = {name: getattr(settings, name) for name in before}
    assert after == before
    assert saved.values == []
    assert current_language() == LANG_EN


def test_exec_accept_and_reject(make_dialog, settings, saved):
    dialog = make_dialog()
    dialog.call_edit.setText("YU1AB")
    QTimer.singleShot(0, dialog.reject)
    assert dialog.exec() == compat.DIALOG_REJECTED
    assert settings.my_call == ""
    QTimer.singleShot(0, dialog.accept)
    assert dialog.exec() == compat.DIALOG_ACCEPTED
    assert settings.my_call == "YU1AB"
    assert saved.values == [()]


@pytest.mark.parametrize("grid", ["KN0", "ZZ99", "KN04ftx", "KN04 ft", "12AB"])
def test_invalid_locator_is_rejected(make_dialog, settings, saved, grid):
    dialog = make_dialog()
    dialog.show()
    dialog.tabs.setCurrentWidget(dialog.general_page)
    dialog.grid_edit.setText(grid)
    dialog.accept()
    assert dialog.isVisible()  # stays open
    assert dialog.result() != compat.DIALOG_ACCEPTED
    assert dialog.error_label.isVisibleTo(dialog)
    assert grid.strip() in dialog.error_label.text()
    assert dialog.tabs.currentWidget() is dialog.station_page
    assert settings.my_grid == ""
    assert saved.values == []
    dialog.grid_edit.setText("KN04")
    dialog.accept()
    assert not dialog.isVisible()
    assert settings.my_grid == "KN04"


def test_live_locator_feedback(make_dialog):
    dialog = make_dialog()
    dialog.grid_edit.setText("kn04ft")
    assert "KN04ft" in dialog.grid_status.text()
    assert "44.8125" in dialog.grid_status.text()
    assert dialog.grid_status.styleSheet() == ""
    dialog.grid_edit.setText("KN04f")
    assert "Not a valid Maidenhead locator" in dialog.grid_status.text()
    assert dialog.grid_status.styleSheet() != ""
    dialog.grid_edit.setText("KN04ft12ab")  # 10 characters: cut to 8
    assert "KN04ft12" in dialog.grid_status.text()
    dialog.grid_edit.setText("")
    assert dialog.grid_status.text().startswith("Not set")


def test_callsign_is_uppercased_while_typing(make_dialog):
    qtest = pytest.importorskip("qgis.PyQt.QtTest")
    dialog = make_dialog()
    qtest.QTest.keyClicks(dialog.call_edit, "yu1abc/p")
    assert dialog.call_edit.text() == "YU1ABC/P"
    dialog.call_edit.setText("YU1 AB")
    assert any("YU1 AB" in problem for problem in dialog.validate())


def test_ports_are_limited_and_must_not_clash(make_dialog):
    dialog = make_dialog()
    for spin in (dialog.wsjtx_port_spin, dialog.rig_port_spin, dialog.rot_port_spin):
        assert (spin.minimum(), spin.maximum()) == (1, 65535)
        spin.setValue(0)
        assert spin.value() == 1
        spin.setValue(70000)
        assert spin.value() == 65535
    dialog.rig_enabled_check.setChecked(True)
    dialog.rot_enabled_check.setChecked(True)
    dialog.rig_host_edit.setText("localhost")
    dialog.rot_host_edit.setText("127.0.0.1")
    dialog.rig_port_spin.setValue(4532)
    dialog.rot_port_spin.setValue(4532)
    problems = dialog.validate()
    assert problems == ["rigctld and rotctld cannot use the same port on the same computer."]
    dialog.rot_host_edit.setText("192.168.1.30")  # another computer
    assert dialog.validate() == []
    dialog.rot_host_edit.setText("127.0.0.1")
    dialog.rot_enabled_check.setChecked(False)  # only one daemon is used
    assert dialog.validate() == []


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("wsjtx_addr_edit", "wsjtx.local", "not an IPv4 address"),
        ("wsjtx_addr_edit", "256.1.1.1", "not an IPv4 address"),
        ("wsjtx_addr_edit", "::1", "is an IPv6 address"),  # the listener is IPv4 only
        ("wsjtx_addr_edit", "ff02::1", "is an IPv6 address"),
        ("wsjtx_addr_edit", "  ", "Enter the WSJT-X UDP address"),
        ("rig_host_edit", "", "rigctld address"),
        ("rot_host_edit", "my host", "rotctld address"),
        ("gpkg_edit", "", "Choose the GeoPackage file"),
        ("gpkg_edit", "relative/log.gpkg", "full path"),
    ],
)
def test_invalid_fields(make_dialog, field, value, fragment):
    dialog = make_dialog()
    getattr(dialog, field).setText(value)
    problems = dialog.validate()
    assert len(problems) == 1
    assert fragment in problems[0]


def test_gpkg_folder_checks(make_dialog, tmp_path):
    dialog = make_dialog()
    dialog.gpkg_edit.setText(str(tmp_path / "missing" / "log.gpkg"))
    assert "does not exist" in dialog.validate()[0]
    dialog.gpkg_edit.setText(str(tmp_path))
    assert "is a folder" in dialog.validate()[0]
    dialog.gpkg_edit.setText(str(tmp_path / "log.gpkg"))
    assert dialog.validate() == []


def test_valid_wsjtx_addresses(make_dialog):
    # what net.wsjtx_listener accepts: IPv4 unicast or multicast, and localhost
    dialog = make_dialog()
    for address in ("127.0.0.1", "224.0.0.1", "0.0.0.0", "239.255.0.1", "localhost", "LocalHost"):
        dialog.wsjtx_addr_edit.setText(address)
        assert dialog.validate() == [], address


class RefusingSettings(HamQSettings):
    """Settings whose ``rot_port`` setter refuses every value."""

    def __setattr__(self, name, value):
        if name == "rot_port":
            raise ValueError("refused for the test")
        super().__setattr__(name, value)


def test_a_refused_value_saves_nothing(iface, settings, tmp_path, saved, process_events):
    store_everything(settings, tmp_path)
    before = {name: getattr(settings, name) for name in ("my_call", "my_grid", "wsjtx_port")}
    dialog = SettingsDialog(RefusingSettings(), None, None, iface.mainWindow())
    try:
        dialog.call_edit.setText("YT1XX")
        dialog.grid_edit.setText("JN58td")
        dialog.wsjtx_port_spin.setValue(2299)
        dialog.show()
        dialog.accept()
        assert dialog.isVisible()  # stays open
        assert "refused for the test" in dialog.error_label.text()
        after = {name: getattr(settings, name) for name in before}
        assert after == before  # saved before rot_port, then rolled back
        assert saved.values == []
    finally:
        dialog.cleanup()
        dialog.deleteLater()
        process_events()


def test_gpkg_buttons(make_dialog, monkeypatch, tmp_path):
    dialog = make_dialog()
    dialog.gpkg_edit.setText("")
    dialog.gpkg_default_button.click()
    assert dialog.gpkg_edit.text() == default_gpkg_path()
    asked = []

    def ask(current):
        asked.append(current)
        return str(tmp_path / "chosen")

    monkeypatch.setattr(dialog, "_ask_gpkg_path", ask)
    dialog.gpkg_browse_button.click()
    assert asked == [default_gpkg_path()]
    assert dialog.gpkg_edit.text() == str(tmp_path / "chosen.gpkg")
    monkeypatch.setattr(dialog, "_ask_gpkg_path", lambda current: "")  # canceled
    dialog.gpkg_browse_button.click()
    assert dialog.gpkg_edit.text() == str(tmp_path / "chosen.gpkg")


def test_rotator_presets(make_dialog):
    dialog = make_dialog()
    assert dialog.rot_preset_combo.count() == len(AZIMUTH_PRESETS) + 1
    assert dialog.rot_preset_combo.itemText(len(AZIMUTH_PRESETS)) == "Custom"
    dialog.rot_preset_combo.setCurrentIndex(1)
    assert (dialog.rot_min_spin.value(), dialog.rot_max_spin.value()) == (0.0, 450.0)
    dialog.rot_preset_combo.setCurrentIndex(2)
    assert (dialog.rot_min_spin.value(), dialog.rot_max_spin.value()) == (-180.0, 180.0)
    dialog.rot_max_spin.setValue(170.0)
    assert dialog.rot_preset_combo.currentIndex() == len(AZIMUTH_PRESETS)  # Custom
    dialog.rot_preset_combo.setCurrentIndex(len(AZIMUTH_PRESETS))  # Custom keeps the values
    assert (dialog.rot_min_spin.value(), dialog.rot_max_spin.value()) == (-180.0, 170.0)
    dialog.rot_min_spin.setValue(0.0)
    dialog.rot_max_spin.setValue(360.0)
    assert dialog.rot_preset_combo.currentIndex() == 0
    dialog.rot_max_spin.setValue(0.0)
    assert dialog.validate() == ["The maximum azimuth must be greater than the minimum azimuth."]
    dialog.rot_min_spin.setValue(-360.0)
    dialog.rot_max_spin.setValue(720.0)
    assert dialog.validate() == ["The azimuth range cannot be wider than 720°."]


def test_reset_map_click_confirmation(make_dialog, settings):
    settings.rot_confirmed = True
    dialog = make_dialog()
    assert dialog.rot_reset_button.isEnabled()
    assert dialog.rot_reset_status.text() == "A map click turns the antenna without asking."
    dialog.rot_reset_button.click()
    assert not dialog.rot_reset_button.isEnabled()
    assert dialog.rot_reset_status.text() == "The first map click asks before turning the antenna."
    assert settings.rot_confirmed is True  # only on OK
    dialog.reject()
    assert settings.rot_confirmed is True
    dialog.load()
    dialog.rot_reset_button.click()
    dialog.accept()
    assert settings.rot_confirmed is False


def test_download_now(make_dialog, cty, settings):
    dialog = make_dialog()
    dialog.show()
    assert dialog.cty_download_button.isEnabled()
    dialog.cty_download_button.click()
    assert cty.downloads == 1
    assert dialog.cty_status_label.text() == "Downloading…"
    assert not dialog.cty_download_button.isEnabled()
    settings.cty_downloaded = "2026-09-30"  # the manager stores the date first
    cty.downloadFinished.emit(True, "cty.dat downloaded, entities: 346")
    assert dialog.cty_status_label.text() == "cty.dat downloaded, entities: 346"
    assert dialog.cty_download_button.isEnabled()
    assert dialog.cty_date_label.text() == "Downloaded: 2026-09-30"
    dialog.cty_download_button.click()
    cty.downloadFinished.emit(False, "")
    assert dialog.cty_status_label.text() == "cty.dat could not be downloaded."
    assert cty.downloads == 2


class BusyCtyManager(FakeCtyManager):
    """A manager that also tells whether a download runs, as ``CtyManager`` does."""

    def __init__(self) -> None:
        super().__init__()
        self.busy = False

    def is_downloading(self) -> bool:
        return self.busy

    def download(self) -> None:
        super().download()
        self.busy = True


class FailingCtyManager(FakeCtyManager):
    """A manager that reports the end of a download before ``download()`` returns."""

    def download(self) -> None:
        super().download()
        self.downloadFinished.emit(False, "cty.dat download failed: no network.")


def test_download_started_elsewhere(make_dialog):
    cty = BusyCtyManager()
    cty.busy = True  # e.g. started by the panel or at start-up
    dialog = make_dialog(cty_manager=cty)
    dialog.show()
    assert not dialog.cty_download_button.isEnabled()
    assert dialog.cty_status_label.text() == "Downloading…"
    cty.busy = False
    cty.downloadFinished.emit(True, "cty.dat downloaded, entities: 346")
    assert dialog.cty_download_button.isEnabled()
    assert dialog.cty_status_label.text() == "cty.dat downloaded, entities: 346"
    dialog.cty_download_button.click()
    assert cty.downloads == 1
    assert dialog.cty_status_label.text() == "Downloading…"
    assert not dialog.cty_download_button.isEnabled()


def test_download_that_fails_at_once(make_dialog):
    dialog = make_dialog(cty_manager=FailingCtyManager())
    dialog.show()
    dialog.cty_download_button.click()
    assert dialog.cty_status_label.text() == "cty.dat download failed: no network."
    assert dialog.cty_download_button.isEnabled()


def test_reopened_dialog_is_not_stuck_downloading(make_dialog, cty):
    dialog = make_dialog()
    dialog.show()
    dialog.cty_download_button.click()
    assert dialog.cty_status_label.text() == "Downloading…"
    dialog.reject()  # closed: disconnected, the end of the download is not seen
    cty.downloadFinished.emit(True, "cty.dat downloaded, entities: 346")
    dialog.show()
    assert dialog.cty_download_button.isEnabled()
    assert dialog.cty_status_label.text() != "Downloading…"
    dialog.reject()


def answer_file_dialog(choice: str) -> dict:
    """Answer the next QFileDialog: Save ``choice``, or Cancel when it is empty.

    Never leaves a modal dialog open (that would hang the test): on an error, or when
    the dialog is still open a second after Save, it is canceled and the problem noted.
    """
    from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox, QFileDialog

    state: dict = {"dont_confirm": None, "errors": []}

    def find():
        windows = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
        return next((w for w in windows if isinstance(w, QFileDialog)), None)

    def give_up():
        file_dialog = find()
        if file_dialog is not None:
            state["errors"].append("the file dialog did not close")
            file_dialog.reject()

    def answer(attempt=0):
        file_dialog = find()
        if file_dialog is None:
            if attempt < 500:
                QTimer.singleShot(10, lambda: answer(attempt + 1))
            return
        try:
            option = compat.FILE_DIALOG_DONT_CONFIRM_OVERWRITE
            state["dont_confirm"] = file_dialog.testOption(option)
            if choice:
                file_dialog.selectFile(choice)
                # QFileDialog.accept() is protected: press Save like a user
                file_dialog.findChild(QDialogButtonBox).button(compat.DIALOG_SAVE).click()
                QTimer.singleShot(1000, give_up)
            else:
                file_dialog.reject()
        except Exception as exc:
            state["errors"].append(repr(exc))
            file_dialog.reject()

    QTimer.singleShot(0, answer)
    return state


def test_browse_opens_a_real_file_dialog(make_dialog, tmp_path):
    # the static QFileDialog call with options= must work on PyQt5 and PyQt6
    from qgis.PyQt.QtWidgets import QApplication

    if QApplication.platformName() != "offscreen":  # a native dialog cannot be answered
        pytest.skip("needs the Qt file dialog of the offscreen platform")
    dialog = make_dialog()
    canceled = answer_file_dialog("")
    assert dialog._ask_gpkg_path(str(tmp_path / "log.gpkg")) == ""
    assert canceled == {"dont_confirm": True, "errors": []}
    chosen = str(tmp_path / "chosen.gpkg")
    saved = answer_file_dialog(chosen)
    assert os.path.normpath(dialog._ask_gpkg_path(str(tmp_path / "log.gpkg"))) == chosen
    # an existing log is picked without an overwrite question
    assert saved == {"dont_confirm": True, "errors": []}


def test_download_without_manager(make_dialog):
    dialog = make_dialog(cty_manager=None)
    dialog.show()
    assert not dialog.cty_download_button.isEnabled()
    dialog.cty_download_button.click()  # disabled: nothing happens
    assert dialog.cty_status_label.text() == ""


def test_works_without_a_language_manager(make_dialog, settings, saved):
    dialog = make_dialog(language_manager=None)
    dialog.language_combo.setCurrentIndex(LANGUAGE_CHOICES.index(LANG_SR_CYRL))
    dialog.accept()
    assert settings.language == LANG_SR_CYRL
    assert saved.values == [()]


def test_retranslates_while_open(make_dialog, manager):
    dialog = make_dialog()
    dialog.show()
    assert dialog.windowTitle() == "HamQ settings"
    manager.set_setting("sr_Latn")
    assert dialog.windowTitle() == "HamQ podešavanja"
    assert dialog.tabs.tabText(0) == "Stanica"
    assert dialog.language_combo.itemText(0) == "Automatski (jezik QGIS-a)"
    assert dialog.language_combo.itemText(3) == "Српски (ћирилица)"
    assert dialog.rot_preset_combo.itemText(len(AZIMUTH_PRESETS)) == "Prilagođeno"
    assert dialog.button_box.button(compat.DIALOG_OK).text() == "U redu"
    dialog.grid_edit.setText("XX")
    dialog.accept()  # the problem is shown in Serbian and re-shown in Cyrillic
    assert "nije ispravan" in dialog.error_label.text()
    assert dialog.tabs.currentWidget() is dialog.station_page
    dialog.tabs.setCurrentWidget(dialog.radio_page)
    manager.set_setting("sr_Cyrl")
    assert dialog.windowTitle() == "HamQ подешавања"
    assert "није исправан" in dialog.error_label.text()
    assert dialog.tabs.currentWidget() is dialog.radio_page  # a language change keeps the tab
    assert "rigctld" in dialog.radio_hint.text() and "Hamlib" in dialog.radio_hint.text()
    assert dialog.language_combo.itemText(0) == "Аутоматски (језик QGIS-а)"
    dialog.reject()
    manager.set_setting("en")
    assert dialog.windowTitle() == "HamQ подешавања"  # closed: disconnected


def test_connected_only_while_shown(make_dialog, cty):
    obj = events()
    before = obj.receivers(obj.languageChanged)
    dialog = make_dialog()
    assert obj.receivers(obj.languageChanged) == before
    dialog.show()
    assert obj.receivers(obj.languageChanged) == before + 1
    dialog.reject()
    assert obj.receivers(obj.languageChanged) == before
    dialog.show()
    assert obj.receivers(obj.languageChanged) == before + 1
    dialog.cleanup()
    dialog.cleanup()
    assert obj.receivers(obj.languageChanged) == before
    dialog.hide()


def test_saving_uses_the_expanded_path(make_dialog, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dialog = make_dialog()
    dialog.gpkg_edit.setText("~/log.gpkg")
    assert dialog.validate() == []
    dialog.accept()
    assert settings.gpkg_path == os.path.join(str(tmp_path), "log.gpkg")
