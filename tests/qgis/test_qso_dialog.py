"""hamq.gui.qso_dialog.QsoDialog: prefill from the radio, band derivation, record, validation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
)

from hamq.core import i18n
from hamq.events import events
from hamq.gui.qso_dialog import QsoDialog
from hamq.qgis_io.compat import DIALOG_ACCEPTED, DIALOG_CANCEL, DIALOG_SAVE, MSG_CRITICAL

USB_20M = {"freq_hz": 14074000, "mode": "USB", "passband": 2400}


@pytest.fixture
def settings():
    return SimpleNamespace(my_call="YU1AB", my_grid="KN04ft")


@pytest.fixture
def language():
    def switch(code: str) -> None:
        i18n.set_language(code)
        events().languageChanged.emit(code)

    switch("en")
    yield switch
    switch("en")


@pytest.fixture
def make_dialog(qgis_app, settings, process_events, language):
    made = []

    def factory(rig_state=None, **kwargs):
        dialog = QsoDialog(kwargs.pop("settings", settings), rig_state, **kwargs)
        made.append(dialog)
        return dialog

    yield factory
    for dialog in made:
        dialog.cleanup()
        dialog.deleteLater()
    process_events()


def field(dialog, cls, name):
    widget = dialog.findChild(cls, name)
    assert widget is not None, name
    return widget


def edit(dialog, name: str, text: str) -> None:
    """Type ``text`` into a line edit as the user would (setText + textEdited)."""
    line = field(dialog, QLineEdit, name)
    line.setText(text)
    line.textEdited.emit(text)


def combo(dialog, name: str) -> QComboBox:
    return field(dialog, QComboBox, name)


def set_when(dialog, year, month, day, hour, minute) -> None:
    field(dialog, QDateTimeEdit, "HamQQsoDateTime").setDateTime(
        QDateTime(QDate(year, month, day), QTime(hour, minute), QTimeZone.utc())
    )


def errors_shown(dialog) -> str:
    label = field(dialog, QLabel, "HamQQsoErrors")
    return label.text() if label.isVisibleTo(dialog) else ""


def test_prefill_from_rig_in_usb(make_dialog):
    dialog = make_dialog(USB_20M)
    assert field(dialog, QLineEdit, "HamQQsoFrequency").text() == "14.074"
    assert combo(dialog, "HamQQsoBand").currentText() == "20m"
    assert combo(dialog, "HamQQsoMode").currentText() == "SSB"
    assert combo(dialog, "HamQQsoSubmode").currentText() == ""
    assert field(dialog, QLineEdit, "HamQQsoRstSent").text() == "59"
    assert field(dialog, QLineEdit, "HamQQsoRstRcvd").text() == "59"
    assert field(dialog, QLabel, "HamQQsoModeHint").isHidden()


@pytest.mark.parametrize(
    ("state", "freq", "band", "mode", "submode", "rst"),
    [
        ({"freq_hz": 7012500, "mode": "CWR", "passband": 500}, "7.0125", "40m", "CW", "", "599"),
        ({"freq_hz": 145500000, "mode": "FM", "passband": 15000}, "145.5", "2m", "FM", "", "59"),
        ({"freq_hz": 3790000, "mode": "LSB", "passband": 2400}, "3.79", "80m", "SSB", "", "59"),
        (
            {"freq_hz": 14080000, "mode": "RTTYR", "passband": 500},
            "14.08",
            "20m",
            "RTTY",
            "",
            "599",
        ),
        ({"freq_hz": 7180000, "mode": "AM", "passband": 6000}, "7.18", "40m", "AM", "", "59"),
        (
            {"freq_hz": 430100000, "mode": "C4FM", "passband": 0},
            "430.1",
            "70cm",
            "DIGITALVOICE",
            "C4FM",
            "59",
        ),
    ],
)
def test_prefill_maps_the_rig_mode(make_dialog, state, freq, band, mode, submode, rst):
    dialog = make_dialog(state)
    assert field(dialog, QLineEdit, "HamQQsoFrequency").text() == freq
    assert combo(dialog, "HamQQsoBand").currentText() == band
    assert combo(dialog, "HamQQsoMode").currentText() == mode
    assert combo(dialog, "HamQQsoSubmode").currentText() == submode
    assert field(dialog, QLineEdit, "HamQQsoRstSent").text() == rst


def test_data_mode_lets_the_operator_choose(make_dialog):
    dialog = make_dialog({"freq_hz": 14074000, "mode": "PKTUSB", "passband": 3000})
    mode = combo(dialog, "HamQQsoMode")
    hint = field(dialog, QLabel, "HamQQsoModeHint")
    assert mode.currentText() == ""
    assert not hint.isHidden()
    assert hint.text() == (
        "The radio is in a data mode (PKTUSB): choose the mode, for example FT8 or PSK31."
    )
    assert field(dialog, QLineEdit, "HamQQsoRstSent").text() == ""
    assert combo(dialog, "HamQQsoBand").currentText() == "20m"
    mode.setCurrentText("FT8")
    assert hint.isHidden()
    assert field(dialog, QLineEdit, "HamQQsoRstSent").text() == "-10"
    assert field(dialog, QLineEdit, "HamQQsoRstRcvd").text() == "-10"


def test_without_rig(make_dialog):
    dialog = make_dialog()
    assert field(dialog, QLineEdit, "HamQQsoFrequency").text() == ""
    assert combo(dialog, "HamQQsoBand").currentText() == ""
    assert combo(dialog, "HamQQsoMode").currentText() == ""
    assert field(dialog, QLabel, "HamQQsoModeHint").isHidden()


@pytest.mark.parametrize(
    "state",
    [
        {"freq_hz": None, "mode": None, "passband": None},
        {"freq_hz": 0, "mode": "BOGUS"},
        {"freq_hz": "14074000", "mode": 5},
        {"freq_hz": float("inf")},
        {"freq_hz": float("nan")},
        {"freq_hz": True},
        {},
    ],
)
def test_unknown_rig_values_are_ignored(make_dialog, state, log_messages):
    dialog = make_dialog(state)
    assert field(dialog, QLineEdit, "HamQQsoFrequency").text() == ""
    assert combo(dialog, "HamQQsoMode").currentText() == ""
    assert not [m for m in log_messages if m[2] == MSG_CRITICAL]


def test_default_time_is_now_utc(make_dialog):
    dialog = make_dialog()
    when = dialog.qso_datetime()
    now = datetime.now(timezone.utc)
    assert when.tzinfo == timezone.utc
    assert now - timedelta(minutes=2) <= when <= now
    assert when.second == 0
    set_when(dialog, 2020, 1, 1, 0, 0)
    field(dialog, QPushButton, "HamQQsoNowButton").click()
    assert dialog.qso_datetime() >= now - timedelta(minutes=2)


def test_band_follows_the_frequency(make_dialog):
    dialog = make_dialog(USB_20M)
    band = combo(dialog, "HamQQsoBand")
    edit(dialog, "HamQQsoFrequency", "7,074")
    assert band.currentText() == "40m"
    edit(dialog, "HamQQsoFrequency", "50.313")
    assert band.currentText() == "6m"
    # outside every band: the band stays as it was, saving reports the mismatch
    edit(dialog, "HamQQsoFrequency", "14.5")
    assert band.currentText() == "6m"
    # the band stays editable
    band.setCurrentText("2m")
    assert band.currentText() == "2m"


def test_callsign_is_uppercased_while_typing(make_dialog):
    dialog = make_dialog()
    edit(dialog, "HamQQsoCall", "yu1ab/p")
    assert field(dialog, QLineEdit, "HamQQsoCall").text() == "YU1AB/P"
    assert dialog.callsign() == "YU1AB/P"


def test_callsign_field_accepts_pasted_spaces_and_rejects_other_characters(make_dialog):
    dialog = make_dialog()
    validator = field(dialog, QLineEdit, "HamQQsoCall").validator()
    acceptable = validator.validate(" yu1ab ", 0)[0]
    assert acceptable == validator.validate("YU1AB", 0)[0]
    assert validator.validate("YU1AB!", 0)[0] != acceptable
    assert validator.validate("YU 1AB", 0)[0] != acceptable
    edit(dialog, "HamQQsoCall", " yu1ab ")
    assert dialog.callsign() == "YU1AB"
    assert dialog.validation_errors()[:1] == ["Choose the band."]


def test_microwave_rig_frequency(make_dialog):
    dialog = make_dialog({"freq_hz": 10368100000, "mode": "USB", "passband": 2400})
    assert field(dialog, QLineEdit, "HamQQsoFrequency").text() == "10368.1"
    assert combo(dialog, "HamQQsoBand").currentText() == "3cm"
    edit(dialog, "HamQQsoCall", "OE3ABC")
    assert dialog.record()["FREQ"] == "10368.1"


def test_record_content(make_dialog):
    dialog = make_dialog(USB_20M)
    edit(dialog, "HamQQsoCall", "dl1xyz")
    set_when(dialog, 2026, 9, 30, 18, 45)
    edit(dialog, "HamQQsoGrid", "jo62QM")
    edit(dialog, "HamQQsoName", "  Hans ")
    edit(dialog, "HamQQsoComment", "Nice signal")
    edit(dialog, "HamQQsoRstRcvd", "57")
    assert dialog.validation_errors() == []
    assert dialog.record() == {
        "QSO_DATE": "20260930",
        "TIME_ON": "184500",
        "CALL": "DL1XYZ",
        "FREQ": "14.074",
        "BAND": "20m",
        "MODE": "SSB",
        "RST_SENT": "59",
        "RST_RCVD": "57",
        "GRIDSQUARE": "JO62qm",
        "NAME": "Hans",
        "COMMENT": "Nice signal",
        "MY_GRIDSQUARE": "KN04ft",
        "STATION_CALLSIGN": "YU1AB",
    }


def test_record_leaves_out_empty_fields(make_dialog):
    dialog = make_dialog(settings=SimpleNamespace(my_call="", my_grid="not a locator"))
    edit(dialog, "HamQQsoCall", "YU1AB")
    set_when(dialog, 2026, 1, 2, 3, 4)
    combo(dialog, "HamQQsoBand").setCurrentText("40m")
    combo(dialog, "HamQQsoMode").setCurrentText("CW")
    field(dialog, QLineEdit, "HamQQsoRstSent").setText("")
    field(dialog, QLineEdit, "HamQQsoRstRcvd").setText("")
    assert dialog.record() == {
        "QSO_DATE": "20260102",
        "TIME_ON": "030400",
        "CALL": "YU1AB",
        "BAND": "40m",
        "MODE": "CW",
    }


def test_mode_choices_become_adif_mode_and_submode(make_dialog):
    dialog = make_dialog(USB_20M)
    mode, submode = combo(dialog, "HamQQsoMode"), combo(dialog, "HamQQsoSubmode")
    rst = field(dialog, QLineEdit, "HamQQsoRstSent")
    submode.setCurrentText("USB")  # explicit SSB submode
    assert dialog.record()["SUBMODE"] == "USB"
    mode.setCurrentText("FT4")
    assert submode.currentText() == ""  # USB does not belong to MFSK
    assert [submode.itemText(i) for i in range(submode.count())][:2] == ["FT4", "FST4"]
    record = dialog.record()
    assert (record["MODE"], record["SUBMODE"]) == ("MFSK", "FT4")
    assert rst.text() == "-10"
    mode.setCurrentText("MFSK")
    submode.setCurrentText("Q65")
    record = dialog.record()
    assert (record["MODE"], record["SUBMODE"]) == ("MFSK", "Q65")
    assert rst.text() == "-10"
    mode.setCurrentText("psk31")
    record = dialog.record()
    assert (record["MODE"], record["SUBMODE"]) == ("PSK", "PSK31")
    assert rst.text() == "599"


def test_rst_typed_by_the_user_is_kept(make_dialog):
    dialog = make_dialog(USB_20M)
    edit(dialog, "HamQQsoRstSent", "57")
    combo(dialog, "HamQQsoMode").setCurrentText("CW")
    assert field(dialog, QLineEdit, "HamQQsoRstSent").text() == "57"
    assert field(dialog, QLineEdit, "HamQQsoRstRcvd").text() == "599"


def test_validation_messages(make_dialog):
    dialog = make_dialog()
    assert dialog.validation_errors() == [
        "Enter the callsign.",
        "Choose the band.",
        "Choose the mode.",
    ]
    edit(dialog, "HamQQsoCall", "YU1AB//P")
    edit(dialog, "HamQQsoFrequency", "fourteen")
    combo(dialog, "HamQQsoMode").setCurrentText("SSB")
    edit(dialog, "HamQQsoGrid", "ZZ99")
    assert dialog.validation_errors() == [
        "YU1AB//P is not a valid callsign.",
        "The frequency must be a number in MHz, for example 14.074.",
        "Choose the band.",
        "ZZ99 is not a valid Maidenhead locator.",
    ]
    edit(dialog, "HamQQsoCall", "ABC")  # no digit: not a callsign
    edit(dialog, "HamQQsoFrequency", "14,5")
    combo(dialog, "HamQQsoBand").setCurrentText("20m")
    edit(dialog, "HamQQsoGrid", "KN04ft")
    assert dialog.validation_errors() == [
        "ABC is not a valid callsign.",
        "14.5 MHz is outside the 20m band.",
    ]
    edit(dialog, "HamQQsoCall", "4U1UN")
    edit(dialog, "HamQQsoFrequency", "14.2")
    assert dialog.validation_errors() == []


def test_save_only_closes_a_valid_entry(make_dialog):
    dialog = make_dialog(USB_20M)
    buttons = field(dialog, QDialogButtonBox, "HamQQsoButtons")
    buttons.button(DIALOG_SAVE).click()
    assert dialog.result() != DIALOG_ACCEPTED
    assert errors_shown(dialog) == "Enter the callsign."
    edit(dialog, "HamQQsoCall", "YU1AB")
    buttons.button(DIALOG_SAVE).click()
    assert dialog.result() == DIALOG_ACCEPTED
    assert errors_shown(dialog) == ""


def test_translated_texts_and_validation(make_dialog, language, log_messages):
    dialog = make_dialog({"freq_hz": 14074000, "mode": "PKTUSB", "passband": 3000})
    buttons = field(dialog, QDialogButtonBox, "HamQQsoButtons")
    assert dialog.windowTitle() == "Log QSO"
    assert buttons.button(DIALOG_SAVE).text() == "Save"
    buttons.button(DIALOG_SAVE).click()
    language("sr_Latn")
    assert dialog.windowTitle() == "Upis veze"
    assert buttons.button(DIALOG_SAVE).text() == "Sačuvaj"
    assert buttons.button(DIALOG_CANCEL).text() == "Otkaži"
    assert field(dialog, QPushButton, "HamQQsoNowButton").text() == "Sada"
    captions = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Pozivni znak" in captions
    assert "Datum i vreme (UTC)" in captions
    assert (
        field(dialog, QLabel, "HamQQsoModeHint")
        .text()
        .startswith("Radio je u režimu za prenos podataka (PKTUSB)")
    )
    # the visible problems follow the language too
    assert errors_shown(dialog) == "Unesite pozivni znak.\nIzaberite vrstu rada."
    assert field(dialog, QLineEdit, "HamQQsoFrequency").placeholderText() == "14,074"
    language("sr_Cyrl")
    assert dialog.windowTitle() == "Упис везе"
    assert errors_shown(dialog) == "Унесите позивни знак.\nИзаберите врсту рада."
    assert not [m for m in log_messages if m[2] == MSG_CRITICAL]


def test_closing_disconnects_from_language_changes(make_dialog, language):
    dialog = make_dialog()
    before = events().receivers(events().languageChanged)
    dialog.reject()
    assert events().receivers(events().languageChanged) == before - 1
    dialog.cleanup()  # again: nothing left to do
    language("sr_Latn")
    assert dialog.windowTitle() == "Log QSO"


def test_record_is_ready_for_record_to_qso(make_dialog):
    qso = pytest.importorskip("hamq.core.qso", reason="core/qso.py is not written yet")
    dialog = make_dialog(USB_20M)
    edit(dialog, "HamQQsoCall", "VK2ABC")
    set_when(dialog, 2026, 9, 30, 18, 45)
    edit(dialog, "HamQQsoGrid", "QF56")
    result, warnings = qso.record_to_qso(
        dialog.record(), station=qso.Station(call="YU1AB", grid="KN04ft"), source="manual"
    )
    assert result is not None, warnings
    assert result.call == "VK2ABC"
    assert result.qso_datetime == datetime(2026, 9, 30, 18, 45, tzinfo=timezone.utc)
    assert result.band == "20m"
    assert result.mode == "SSB"
    assert result.freq_mhz == pytest.approx(14.074)
    assert result.gridsquare == "QF56"
    assert result.my_gridsquare == "KN04ft"
    assert result.distance_km == pytest.approx(15676, rel=0.01)
    assert (result.rst_sent, result.rst_rcvd) == ("59", "59")
    assert result.dedup_key == "VK2ABC|202609301845|20m|SSB"
    # fields without a column of their own are kept in adif_extra
    edit(dialog, "HamQQsoName", "Bruce")
    edit(dialog, "HamQQsoComment", "Long path, 5 and 9")
    result, warnings = qso.record_to_qso(dialog.record(), source="manual")
    assert result is not None, warnings
    assert json.loads(result.adif_extra) == {
        "COMMENT": "Long path, 5 and 9",
        "NAME": "Bruce",
        "STATION_CALLSIGN": "YU1AB",
    }


def test_ft4_record_dedups_like_wsjtx(make_dialog):
    """FT4 from the dialog (MODE=MFSK SUBMODE=FT4) gets the same key as MODE=FT4."""
    qso = pytest.importorskip("hamq.core.qso", reason="core/qso.py is not written yet")
    dialog = make_dialog({"freq_hz": 14080000, "mode": "PKTUSB", "passband": 3000})
    edit(dialog, "HamQQsoCall", "DL1XYZ")
    set_when(dialog, 2026, 9, 30, 18, 45)
    combo(dialog, "HamQQsoMode").setCurrentText("FT4")
    record = dialog.record()
    assert (record["MODE"], record["SUBMODE"], record["RST_SENT"]) == ("MFSK", "FT4", "-10")
    ours, _ = qso.record_to_qso(record, source="manual")
    theirs, _ = qso.record_to_qso(
        {
            "CALL": "DL1XYZ",
            "QSO_DATE": "20260930",
            "TIME_ON": "184512",
            "BAND": "20m",
            "MODE": "FT4",
        },
        source="wsjtx",
    )
    assert ours.dedup_key == theirs.dedup_key


def test_problems_never_squeeze_the_form(make_dialog, process_events, language):
    """Wrapped texts that appear after the dialog is shown make it taller."""
    dialog = make_dialog({"freq_hz": 14074000, "mode": "PKTUSB", "passband": 3000})
    dialog.show()
    process_events()
    height = dialog.height()
    field(dialog, QDialogButtonBox, "HamQQsoButtons").button(DIALOG_SAVE).click()
    process_events()
    layout = dialog.layout()
    assert dialog.height() > height
    assert dialog.height() >= layout.totalHeightForWidth(dialog.width())
    hint = field(dialog, QLabel, "HamQQsoModeHint")
    assert hint.height() >= hint.heightForWidth(hint.width())
    errors = field(dialog, QLabel, "HamQQsoErrors")
    assert errors.height() >= errors.heightForWidth(errors.width())
    rst = field(dialog, QLineEdit, "HamQQsoRstSent")
    assert hint.geometry().bottom() < rst.mapTo(dialog, rst.rect().topLeft()).y()
    language("sr_Latn")  # longer texts
    process_events()
    assert dialog.height() >= layout.totalHeightForWidth(dialog.width())
    dialog.reject()


def test_deleting_the_dialog_without_closing_disconnects(qgis_app, settings, process_events):
    before = events().receivers(events().languageChanged)
    dialog = QsoDialog(settings)
    assert events().receivers(events().languageChanged) == before + 1
    dialog.deleteLater()
    process_events()
    assert events().receivers(events().languageChanged) == before
    events().languageChanged.emit("en")  # nothing left to call
