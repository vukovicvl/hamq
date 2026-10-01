"""Manual QSO entry dialog.

:class:`QsoDialog` collects one QSO and returns it from :meth:`QsoDialog.record` as
an ADIF-like record (``{"CALL": "YU1AB", "QSO_DATE": "20260930", ...}``), ready for
:func:`hamq.core.qso.record_to_qso`; the caller stores it like any other QSO.

When a radio state is given (``{"freq_hz", "mode", "passband"}`` from
``RigClient.stateChanged``) the frequency, band, mode and signal reports are filled
in from it: ``USB`` / ``LSB`` give SSB, ``CW`` / ``CWR`` CW, ``FM`` / ``WFM`` FM and so
on (:func:`hamq.core.rigmode.adif_mode`). In a data mode (``PKTUSB``, ...) only the
operator knows the mode (FT8, PSK31, ...), so the mode is left empty with a hint.

The date and time are UTC and default to now. The band follows the frequency while
the frequency is edited; a band that does not contain the frequency is reported
when saving. Save only closes the dialog when :meth:`QsoDialog.validation_errors`
is empty; the translated problems are shown in the dialog otherwise.
"""

from __future__ import annotations

import functools
import math
import re
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from qgis.core import QgsMessageLog
from qgis.PyQt.QtCore import QDate, QDateTime, QRegularExpression, Qt, QTime, QTimeZone
from qgis.PyQt.QtGui import QRegularExpressionValidator
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import maidenhead, rigmode
from ..core.adif import parse_freq
from ..core.bands import BAND_ORDER, band_from_freq
from ..core.i18n import tr, tr_noop
from ..events import events
from ..qgis_io import compat
from ..qgis_io.fields import from_qdatetime
from . import get_icon
from .dock import decimal_separator, format_number, hamq_locale

__all__ = ["QsoDialog"]

LOG_TAG = "HamQ"
_MSG_ERROR = tr_noop("Unexpected error in the QSO dialog: {error}")
# Letters, digits and "/" between them; at least one letter and one digit (ITU calls).
_CALL_RE = re.compile(r"[A-Z0-9]+(?:/[A-Z0-9]+)*")
# What the callsign field accepts while typing or pasting (spaces around are stripped).
_CALL_CHARS = QRegularExpression(r"\s*[A-Za-z0-9/]{0,24}\s*")
_EARLIEST = QDate(1930, 1, 1)  # ADIF's earliest QSO_DATE


def _log_error() -> None:
    QgsMessageLog.logMessage(
        tr(_MSG_ERROR).format(error=traceback.format_exc()), LOG_TAG, compat.MSG_CRITICAL
    )


def _disconnect_all(connections: list[tuple[Any, Callable[..., Any]]]) -> None:
    """Disconnect and forget every ``(signal, slot)`` pair in ``connections``."""
    while connections:
        signal, slot = connections.pop()
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):  # already disconnected / sender deleted
            pass


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a slot so that no exception escapes into Qt; errors are logged."""

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any) -> Any:
        try:
            return method(self, *args)
        except Exception:
            _log_error()
            return None

    return wrapper


def _mhz(freq_mhz: float) -> str:
    """``14.074`` -> ``"14.074"`` (up to 6 decimals, no trailing zeros), HamQ separator."""
    text = format_number(freq_mhz, 6, group=False)
    if decimal_separator() in text:
        text = text.rstrip("0").rstrip(decimal_separator())
    return text


def _adif_freq(freq_mhz: float) -> str:
    """ADIF FREQ: MHz with a decimal point, up to 6 decimals: ``"14.074"``."""
    text = f"{freq_mhz:.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _utc_now_minute() -> QDateTime:
    now = QDateTime.currentDateTimeUtc()
    time = now.time()
    return QDateTime(now.date(), QTime(time.hour(), time.minute()), QTimeZone.utc())


class QsoDialog(QDialog):
    """Enter one QSO by hand; :meth:`record` returns it as an ADIF-like record.

    ``settings`` is a :class:`hamq.settings.HamQSettings` (``my_grid`` becomes
    ``MY_GRIDSQUARE``, ``my_call`` ``STATION_CALLSIGN``), ``rig_state`` the last
    radio state or ``None``.
    """

    def __init__(
        self,
        settings: Any,
        rig_state: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HamQQsoDialog")
        self.setWindowIcon(get_icon("hamq.svg"))
        self._settings = settings
        self._rig_state = dict(rig_state) if isinstance(rig_state, Mapping) else None
        self._rig_mode = ""  # the radio's mode when it is a data mode (shown in the hint)
        self._rst_default = ""  # RST the fields hold until the user changes them
        self._connections: list[tuple[Any, Callable[..., Any]]] = []
        self._build_ui()
        self._prefill()
        self._connect_event(events().languageChanged, self._on_language_changed)
        # Safety net when the dialog is deleted without done() / cleanup(): the lambda
        # holds the list, not the dialog, so it still works during destruction.
        connections = self._connections
        self.destroyed.connect(lambda *_args: _disconnect_all(connections))
        self.retranslate()

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    # ------------------------------------------------------------------ building

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._captions: dict[str, QLabel] = {}

        def row(key: str, field: Any) -> None:
            caption = QLabel()
            self._captions[key] = caption
            if isinstance(field, QWidget):
                caption.setBuddy(field)
                form.addRow(caption, field)
            else:
                form.addRow(caption, field)

        self._call_edit = QLineEdit()
        self._call_edit.setObjectName("HamQQsoCall")
        self._call_edit.setValidator(QRegularExpressionValidator(_CALL_CHARS, self._call_edit))
        self._call_edit.textEdited.connect(self._on_call_edited)
        row("call", self._call_edit)

        when = QHBoxLayout()
        self._when_edit = QDateTimeEdit()
        self._when_edit.setObjectName("HamQQsoDateTime")
        if hasattr(self._when_edit, "setTimeZone"):  # Qt >= 6.7
            self._when_edit.setTimeZone(QTimeZone.utc())
        else:
            self._when_edit.setTimeSpec(Qt.TimeSpec.UTC)
        self._when_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._when_edit.setCalendarPopup(True)
        self._when_edit.setMinimumDate(_EARLIEST)
        self._now_button = QPushButton()
        self._now_button.setObjectName("HamQQsoNowButton")
        self._now_button.clicked.connect(self._on_now_clicked)
        when.addWidget(self._when_edit, 1)
        when.addWidget(self._now_button)
        row("when", when)

        self._freq_edit = QLineEdit()
        self._freq_edit.setObjectName("HamQQsoFrequency")
        self._freq_edit.textEdited.connect(self._on_freq_edited)
        row("freq", self._freq_edit)

        self._band_combo = QComboBox()
        self._band_combo.setObjectName("HamQQsoBand")
        self._band_combo.addItems([""] + list(BAND_ORDER))
        row("band", self._band_combo)

        self._mode_combo = QComboBox()
        self._mode_combo.setObjectName("HamQQsoMode")
        self._mode_combo.setEditable(True)
        self._mode_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._mode_combo.addItems(list(rigmode.MODE_CHOICES))
        self._mode_combo.setCurrentIndex(-1)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        row("mode", self._mode_combo)

        self._submode_combo = QComboBox()
        self._submode_combo.setObjectName("HamQQsoSubmode")
        self._submode_combo.setEditable(True)
        self._submode_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._submode_combo.currentTextChanged.connect(self._on_submode_changed)
        row("submode", self._submode_combo)

        self._mode_hint = QLabel()
        self._mode_hint.setObjectName("HamQQsoModeHint")
        self._mode_hint.setWordWrap(True)
        self._mode_hint.hide()
        form.addRow(self._mode_hint)

        self._rst_sent_edit = QLineEdit()
        self._rst_sent_edit.setObjectName("HamQQsoRstSent")
        self._rst_sent_edit.setMaxLength(8)
        row("rst_sent", self._rst_sent_edit)
        self._rst_rcvd_edit = QLineEdit()
        self._rst_rcvd_edit.setObjectName("HamQQsoRstRcvd")
        self._rst_rcvd_edit.setMaxLength(8)
        row("rst_rcvd", self._rst_rcvd_edit)

        self._grid_edit = QLineEdit()
        self._grid_edit.setObjectName("HamQQsoGrid")
        self._grid_edit.setMaxLength(10)
        self._grid_edit.setPlaceholderText("KN04ft")
        row("grid", self._grid_edit)
        self._name_edit = QLineEdit()
        self._name_edit.setObjectName("HamQQsoName")
        row("name", self._name_edit)
        self._comment_edit = QLineEdit()
        self._comment_edit.setObjectName("HamQQsoComment")
        row("comment", self._comment_edit)
        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setObjectName("HamQQsoErrors")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #c92a2a;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._buttons = QDialogButtonBox(compat.DIALOG_SAVE | compat.DIALOG_CANCEL)
        self._buttons.setObjectName("HamQQsoButtons")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        # The problems repeat what was typed: no label renders markup (default AutoText).
        for label in self.findChildren(QLabel):
            label.setTextFormat(compat.TEXT_PLAIN)

    def _prefill(self) -> None:
        self._when_edit.setDateTime(_utc_now_minute())
        state = self._rig_state or {}
        hz = state.get("freq_hz")
        if isinstance(hz, (int, float)) and not isinstance(hz, bool) and 0 < hz < math.inf:
            self._freq_edit.setText(_mhz(hz / 1e6))
            self._follow_frequency()
        rig_mode = state.get("mode")
        mapped = rigmode.adif_mode(rig_mode)
        if mapped is not None:
            mode, submode = mapped
            self._mode_combo.setCurrentText(mode)
            self._submode_combo.setCurrentText(submode)
        elif rigmode.is_data_mode(rig_mode):
            self._rig_mode = rigmode.normalize_rig_mode(rig_mode)
            self._mode_hint.show()
        self._apply_rst_default()

    # ------------------------------------------------------------------ events and teardown

    def _connect_event(self, signal: Any, slot: Callable[..., Any]) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    def cleanup(self) -> None:
        """Disconnect from the HamQ events. Safe to call more than once."""
        _disconnect_all(self._connections)

    def done(self, result: int) -> None:
        """Close the dialog and stop following language changes."""
        self.cleanup()
        super().done(result)

    @_guarded
    def _on_language_changed(self, _language: str = "") -> None:
        self.retranslate()

    # ------------------------------------------------------------------ slots

    @_guarded
    def _on_call_edited(self, text: str) -> None:
        upper = text.upper()
        if upper != text:
            position = self._call_edit.cursorPosition()
            self._call_edit.setText(upper)
            self._call_edit.setCursorPosition(position)

    @_guarded
    def _on_now_clicked(self, _checked: bool = False) -> None:
        self._when_edit.setDateTime(_utc_now_minute())

    @_guarded
    def _on_freq_edited(self, _text: str) -> None:
        self._follow_frequency()

    @_guarded
    def _on_mode_changed(self, _text: str) -> None:
        mode, _implied = rigmode.split_mode(self._mode_combo.currentText())
        submodes = rigmode.SUBMODES.get(mode, ())
        current = self._submode_combo.currentText().strip().upper()
        self._submode_combo.blockSignals(True)
        try:
            self._submode_combo.clear()
            self._submode_combo.addItems(list(submodes))
            self._submode_combo.setCurrentText(current if current in submodes else "")
        finally:
            self._submode_combo.blockSignals(False)
        if mode:
            self._mode_hint.hide()
        self._apply_rst_default()

    @_guarded
    def _on_submode_changed(self, _text: str) -> None:
        self._apply_rst_default()

    def _follow_frequency(self) -> None:
        """Select the band of the entered frequency (the band stays editable)."""
        freq = parse_freq(self._freq_edit.text())
        band = band_from_freq(freq) if freq is not None else None
        if band:
            self._band_combo.setCurrentText(band)

    def _apply_rst_default(self) -> None:
        """Give both reports the default of the mode unless the user typed their own."""
        new = rigmode.default_rst(self._mode_combo.currentText(), self._submode_combo.currentText())
        for edit in (self._rst_sent_edit, self._rst_rcvd_edit):
            if edit.text().strip() in ("", self._rst_default):
                edit.setText(new)
        self._rst_default = new

    @_guarded
    def accept(self) -> None:
        """Save: close only when the entry is valid, otherwise show what is wrong."""
        errors = self.validation_errors()
        self._show_errors(errors)
        if not errors:
            super().accept()

    def _show_errors(self, errors: list[str]) -> None:
        self._error_label.setText("\n".join(errors))
        self._error_label.setVisible(bool(errors))
        self._grow_to_fit()

    def _grow_to_fit(self) -> None:
        """Make the shown dialog tall enough for wrapped texts that appeared or grew.

        Qt fits a window to its content only when it is shown; a word-wrapped label
        that appears later (the problems, a longer translation) would otherwise be
        squeezed over the rows below it. The dialog never shrinks here.
        """
        layout = self.layout()
        if layout is None or not self.isVisible():
            return
        layout.activate()
        width = self.width()
        if layout.hasHeightForWidth():
            needed = layout.totalHeightForWidth(width)
        else:
            needed = layout.totalSizeHint().height()
        if needed > self.height():
            self.resize(width, needed)

    # ------------------------------------------------------------------ values

    def callsign(self) -> str:
        """The entered callsign, uppercase and stripped."""
        return self._call_edit.text().strip().upper()

    def qso_datetime(self) -> datetime | None:
        """The entered UTC time as an aware ``datetime``."""
        return from_qdatetime(self._when_edit.dateTime())

    def _mode_pair(self) -> tuple[str, str]:
        mode, implied = rigmode.split_mode(self._mode_combo.currentText())
        submode = self._submode_combo.currentText().strip().upper()
        return mode, submode or implied

    def validation_errors(self) -> list[str]:
        """Translated problems of the entry; empty when it can be saved."""
        errors = []
        call = self.callsign()
        if not call:
            errors.append(tr("Enter the callsign."))
        elif not (
            _CALL_RE.fullmatch(call)
            and any(char.isdigit() for char in call)
            and any(char.isalpha() for char in call)
        ):
            errors.append(tr("{call} is not a valid callsign.").format(call=call))
        freq_text = self._freq_edit.text().strip()
        freq = parse_freq(freq_text) if freq_text else None
        if freq_text and freq is None:
            errors.append(tr("The frequency must be a number in MHz, for example 14.074."))
        band = self._band_combo.currentText()
        if not band:
            errors.append(tr("Choose the band."))
        elif freq is not None and band_from_freq(freq) != band:
            errors.append(
                tr("{frequency} MHz is outside the {band} band.").format(
                    frequency=_mhz(freq), band=band
                )
            )
        if not self._mode_pair()[0]:
            errors.append(tr("Choose the mode."))
        grid = self._grid_edit.text().strip()
        if grid:
            try:
                maidenhead.normalize(grid)
            except ValueError:
                errors.append(tr("{grid} is not a valid Maidenhead locator.").format(grid=grid))
        return errors

    def record(self) -> dict[str, str]:
        """The QSO as an ADIF-like record; empty fields are left out.

        Keys: ``QSO_DATE``, ``TIME_ON`` (UTC), ``CALL``, ``FREQ`` (MHz), ``BAND``,
        ``MODE``, ``SUBMODE``, ``RST_SENT``, ``RST_RCVD``, ``GRIDSQUARE`` (normalized),
        ``NAME``, ``COMMENT``, ``MY_GRIDSQUARE`` (my locator from the settings, when
        valid) and ``STATION_CALLSIGN`` (my callsign, when set).
        """
        when = self._when_edit.dateTime().toUTC()
        mode, submode = self._mode_pair()
        freq = parse_freq(self._freq_edit.text().strip())
        grid = self._grid_edit.text().strip()
        try:
            grid = maidenhead.normalize(grid) if grid else ""
        except ValueError:
            pass  # kept as typed; validation_errors() reports it
        my_grid = str(getattr(self._settings, "my_grid", "") or "").strip()
        my_call = str(getattr(self._settings, "my_call", "") or "").strip().upper()
        fields = {
            "QSO_DATE": when.date().toString("yyyyMMdd"),
            "TIME_ON": when.time().toString("HHmmss"),
            "CALL": self.callsign(),
            "FREQ": _adif_freq(freq) if freq is not None else "",
            "BAND": self._band_combo.currentText(),
            "MODE": mode,
            "SUBMODE": submode,
            "RST_SENT": self._rst_sent_edit.text().strip(),
            "RST_RCVD": self._rst_rcvd_edit.text().strip(),
            "GRIDSQUARE": grid,
            "NAME": self._name_edit.text().strip(),
            "COMMENT": self._comment_edit.text().strip(),
            "MY_GRIDSQUARE": my_grid if maidenhead.is_valid(my_grid) else "",
            "STATION_CALLSIGN": my_call,
        }
        return {key: value for key, value in fields.items() if value}

    # ------------------------------------------------------------------ translation

    @_guarded
    def retranslate(self) -> None:
        """Apply every text again in the current HamQ language."""
        self.setWindowTitle(tr("Log QSO"))
        captions = {
            "call": tr("Callsign"),
            "when": tr("Date and time (UTC)"),
            "freq": tr("Frequency (MHz)"),
            "band": tr("Band"),
            "mode": tr("Mode"),
            "submode": tr("Submode"),
            "rst_sent": tr("RST sent"),
            "rst_rcvd": tr("RST received"),
            "grid": tr("Locator"),
            "name": tr("Name"),
            "comment": tr("Comment"),
        }
        for key, text in captions.items():
            self._captions[key].setText(text)
        self._now_button.setText(tr("Now"))
        self._now_button.setToolTip(tr("Set the current UTC time"))
        # the date popup names months and days in the HamQ language, not the system one
        locale = hamq_locale()
        self._when_edit.setLocale(locale)
        calendar = self._when_edit.calendarWidget()
        if calendar is not None:
            calendar.setLocale(locale)
        self._freq_edit.setPlaceholderText(format_number(14.074, 3))
        self._mode_hint.setText(
            tr(
                "The radio is in a data mode ({mode}): choose the mode, for example FT8 or PSK31."
            ).format(mode=self._rig_mode or "PKT")
        )
        self._buttons.button(compat.DIALOG_SAVE).setText(tr("Save"))
        self._buttons.button(compat.DIALOG_CANCEL).setText(tr("Cancel"))
        if self._error_label.isVisibleTo(self):
            self._show_errors(self.validation_errors())
        self._grow_to_fit()  # a translation can be longer than the text it replaces
