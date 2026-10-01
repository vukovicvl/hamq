"""HamQ settings dialog: station, storage, WSJT-X, Hamlib radio and rotator, language, DXCC data.

:class:`SettingsDialog` shows the values of :class:`hamq.settings.HamQSettings`.
OK validates every field (the problems are listed at the bottom of the dialog and
the first invalid field gets the focus), saves, applies the language through the
:class:`hamq.gui.language.LanguageManager` and emits ``events().settingsChanged``
once. Cancel changes nothing. Open it with ``exec()``.

The dialog retranslates itself while it is open. It is connected to ``events()``
and to the cty.dat manager only while it is shown; :meth:`SettingsDialog.cleanup`
disconnects (it also runs when the dialog closes) and is safe to call twice.
"""

from __future__ import annotations

import contextlib
import functools
import ipaddress
import os
import re
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from qgis.core import QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import maidenhead
from ..core.i18n import LANG_AUTO, LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, language_name, tr, tr_noop
from ..events import events
from ..qgis_io.compat import (
    DIALOG_CANCEL,
    DIALOG_OK,
    FILE_DIALOG_DONT_CONFIRM_OVERWRITE,
    MSG_CRITICAL,
    TEXT_PLAIN,
)
from ..settings import HamQSettings, default_gpkg_path
from .dock import DecimalSpinBox, format_number

if TYPE_CHECKING:
    from .language import LanguageManager

__all__ = ["AZIMUTH_PRESETS", "LANGUAGE_CHOICES", "SettingsDialog"]

LOG_TAG = "HamQ"
#: Language choices of the dialog, in order (texts from ``core.i18n.language_name``).
LANGUAGE_CHOICES: tuple[str, ...] = (LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL)
#: Rotator azimuth range presets ``(label, min, max)``; the labels are numbers only.
AZIMUTH_PRESETS: tuple[tuple[str, float, float], ...] = (
    ("0° … 360°", 0.0, 360.0),
    ("0° … 450°", 0.0, 450.0),
    ("−180° … 180°", -180.0, 180.0),
)
_CUSTOM = tr_noop("Custom")
_MAX_AZIMUTH_SPAN = 720.0
_CALL_RE = re.compile(r"[A-Z0-9/]+")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ERROR_STYLE = "color: #c62828;"
# WSJT-X menu path, shown in English as in WSJT-X itself (a placeholder, never translated).
_WSJTX_MENU = "File > Settings > Reporting > UDP Server"


def _log_error(where: str, exc: BaseException) -> None:
    message = tr("Unexpected error in {slot}: {error}").format(slot=where, error=exc)
    QgsMessageLog.logMessage(f"{message}\n{traceback.format_exc()}", LOG_TAG, MSG_CRITICAL)


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Qt slot: an exception is logged instead of escaping into Qt."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except Exception as exc:  # a slot must never raise into Qt
            _log_error(method.__qualname__, exc)
            return None

    return wrapper


def _normalized_grid(text: str) -> str | None:
    """``maidenhead.normalize(text)`` or ``None`` when ``text`` is not a locator."""
    try:
        return maidenhead.normalize(text)
    except ValueError:
        return None


def _valid_host(host: str) -> bool:
    return bool(host) and not any(char.isspace() for char in host)


def _same_host(first: str, second: str) -> bool:
    a, b = first.strip().lower(), second.strip().lower()
    return a == b or (a in _LOOPBACK_HOSTS and b in _LOOPBACK_HOSTS)


class SettingsDialog(QDialog):
    """Edit the HamQ settings; OK validates, saves and emits ``events().settingsChanged``.

    ``settings`` is a :class:`HamQSettings`, ``language_manager`` the plugin's
    ``LanguageManager`` (the language choice is applied through it on OK; with
    ``None`` it is only stored), ``cty_manager`` the ``CtyManager`` for
    "Download now" (the button is disabled without one).
    """

    def __init__(
        self,
        settings: HamQSettings,
        language_manager: LanguageManager | None,
        cty_manager: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HamQSettingsDialog")
        self.setMinimumWidth(480)
        self._settings = settings
        self._language_manager = language_manager
        self._cty_manager = cty_manager
        self._connections: list[tuple[Any, Callable[..., Any]]] = []
        self._reset_confirmation = False
        self._syncing_preset = False
        self._downloading = False
        self._cty_message = ""
        self._problems: list[tuple[QWidget | None, str]] = []
        self._build_ui()
        self.load()
        self.retranslate()

    # ------------------------------------------------------------------ building

    def _build_ui(self) -> None:
        self.tabs = QTabWidget(self)
        self.station_page = QWidget()
        self.wsjtx_page = QWidget()
        self.radio_page = QWidget()
        self.general_page = QWidget()
        self._build_station_page(self.station_page)
        self._build_wsjtx_page(self.wsjtx_page)
        self._build_radio_page(self.radio_page)
        self._build_general_page(self.general_page)
        for page in (self.station_page, self.wsjtx_page, self.radio_page, self.general_page):
            self.tabs.addTab(page, "")

        self.error_label = QLabel(self)
        self.error_label.setObjectName("HamQSettingsErrors")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(_ERROR_STYLE)
        self.error_label.hide()

        self.button_box = QDialogButtonBox(DIALOG_OK | DIALOG_CANCEL, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.error_label)
        layout.addWidget(self.button_box)
        # The problems and the cty.dat status repeat typed text and server messages:
        # no label renders markup (QLabel's default is AutoText).
        for label in self.findChildren(QLabel):
            label.setTextFormat(TEXT_PLAIN)

    def _build_station_page(self, page: QWidget) -> None:
        self.station_group = QGroupBox(page)
        form = QFormLayout(self.station_group)
        self.call_label = QLabel(self.station_group)
        self.call_edit = QLineEdit(self.station_group)
        self.call_edit.setObjectName("HamQMyCall")
        self.call_edit.setMaxLength(20)
        self.call_edit.textEdited.connect(self._on_call_edited)
        form.addRow(self.call_label, self.call_edit)
        self.grid_label = QLabel(self.station_group)
        self.grid_edit = QLineEdit(self.station_group)
        self.grid_edit.setObjectName("HamQMyGrid")
        self.grid_edit.setMaxLength(12)
        self.grid_edit.textChanged.connect(self._on_grid_changed)
        form.addRow(self.grid_label, self.grid_edit)
        self.grid_status = QLabel(self.station_group)
        self.grid_status.setWordWrap(True)
        form.addRow("", self.grid_status)

        self.storage_group = QGroupBox(page)
        storage = QVBoxLayout(self.storage_group)
        self.gpkg_label = QLabel(self.storage_group)
        storage.addWidget(self.gpkg_label)
        row = QHBoxLayout()
        self.gpkg_edit = QLineEdit(self.storage_group)
        self.gpkg_edit.setObjectName("HamQGpkgPath")
        self.gpkg_browse_button = QPushButton(self.storage_group)
        self.gpkg_browse_button.clicked.connect(self._on_browse_clicked)
        self.gpkg_default_button = QPushButton(self.storage_group)
        self.gpkg_default_button.clicked.connect(self._on_default_clicked)
        row.addWidget(self.gpkg_edit, 1)
        row.addWidget(self.gpkg_browse_button)
        row.addWidget(self.gpkg_default_button)
        storage.addLayout(row)

        layout = QVBoxLayout(page)
        layout.addWidget(self.station_group)
        layout.addWidget(self.storage_group)
        layout.addStretch(1)

    def _build_wsjtx_page(self, page: QWidget) -> None:
        self.wsjtx_group = QGroupBox(page)
        form = QFormLayout(self.wsjtx_group)
        self.wsjtx_addr_label = QLabel(self.wsjtx_group)
        self.wsjtx_addr_edit = QLineEdit(self.wsjtx_group)
        self.wsjtx_addr_edit.setObjectName("HamQWsjtxAddress")
        form.addRow(self.wsjtx_addr_label, self.wsjtx_addr_edit)
        self.wsjtx_port_label = QLabel(self.wsjtx_group)
        self.wsjtx_port_spin = QSpinBox(self.wsjtx_group)
        self.wsjtx_port_spin.setObjectName("HamQWsjtxPort")
        self.wsjtx_port_spin.setRange(1, 65535)
        form.addRow(self.wsjtx_port_label, self.wsjtx_port_spin)
        self.wsjtx_autostart_check = QCheckBox(self.wsjtx_group)
        form.addRow(self.wsjtx_autostart_check)
        self.wsjtx_hint = QLabel(self.wsjtx_group)
        self.wsjtx_hint.setWordWrap(True)
        form.addRow(self.wsjtx_hint)

        layout = QVBoxLayout(page)
        layout.addWidget(self.wsjtx_group)
        layout.addStretch(1)

    def _build_radio_page(self, page: QWidget) -> None:
        self.rig_group = QGroupBox(page)
        rig = QFormLayout(self.rig_group)
        self.rig_enabled_check = QCheckBox(self.rig_group)
        rig.addRow(self.rig_enabled_check)
        self.rig_host_label = QLabel(self.rig_group)
        self.rig_host_edit = QLineEdit(self.rig_group)
        self.rig_host_edit.setObjectName("HamQRigHost")
        rig.addRow(self.rig_host_label, self.rig_host_edit)
        self.rig_port_label = QLabel(self.rig_group)
        self.rig_port_spin = QSpinBox(self.rig_group)
        self.rig_port_spin.setObjectName("HamQRigPort")
        self.rig_port_spin.setRange(1, 65535)
        rig.addRow(self.rig_port_label, self.rig_port_spin)
        self.rig_poll_label = QLabel(self.rig_group)
        self.rig_poll_spin = QSpinBox(self.rig_group)
        self.rig_poll_spin.setObjectName("HamQRigPoll")
        self.rig_poll_spin.setRange(100, 60000)
        self.rig_poll_spin.setSingleStep(100)
        self.rig_poll_spin.setSuffix(" ms")
        rig.addRow(self.rig_poll_label, self.rig_poll_spin)

        self.rot_group = QGroupBox(page)
        rot = QFormLayout(self.rot_group)
        self.rot_enabled_check = QCheckBox(self.rot_group)
        rot.addRow(self.rot_enabled_check)
        self.rot_host_label = QLabel(self.rot_group)
        self.rot_host_edit = QLineEdit(self.rot_group)
        self.rot_host_edit.setObjectName("HamQRotHost")
        rot.addRow(self.rot_host_label, self.rot_host_edit)
        self.rot_port_label = QLabel(self.rot_group)
        self.rot_port_spin = QSpinBox(self.rot_group)
        self.rot_port_spin.setObjectName("HamQRotPort")
        self.rot_port_spin.setRange(1, 65535)
        rot.addRow(self.rot_port_label, self.rot_port_spin)
        self.rot_preset_label = QLabel(self.rot_group)
        self.rot_preset_combo = QComboBox(self.rot_group)
        for label, low, high in AZIMUTH_PRESETS:
            self.rot_preset_combo.addItem(label, (low, high))
        self.rot_preset_combo.addItem("", None)  # "Custom", text set by retranslate()
        self.rot_preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        rot.addRow(self.rot_preset_label, self.rot_preset_combo)
        self.rot_min_label = QLabel(self.rot_group)
        self.rot_min_spin = self._azimuth_spin(-360.0, 360.0)
        self.rot_min_spin.setObjectName("HamQRotMinAz")
        rot.addRow(self.rot_min_label, self.rot_min_spin)
        self.rot_max_label = QLabel(self.rot_group)
        self.rot_max_spin = self._azimuth_spin(-360.0, 720.0)
        self.rot_max_spin.setObjectName("HamQRotMaxAz")
        rot.addRow(self.rot_max_label, self.rot_max_spin)
        self.rot_reset_button = QPushButton(self.rot_group)
        self.rot_reset_button.clicked.connect(self._on_reset_clicked)
        rot.addRow(self.rot_reset_button)
        self.rot_reset_status = QLabel(self.rot_group)
        self.rot_reset_status.setWordWrap(True)
        rot.addRow(self.rot_reset_status)

        self.radio_hint = QLabel(page)
        self.radio_hint.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.addWidget(self.radio_hint)
        layout.addWidget(self.rig_group)
        layout.addWidget(self.rot_group)
        layout.addStretch(1)

    def _azimuth_spin(self, low: float, high: float) -> QDoubleSpinBox:
        # the HamQ decimal separator; "36.5" and "36,5" are both 36.5 in every language
        spin = DecimalSpinBox(self.rot_group)
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setSingleStep(5.0)
        spin.setSuffix("°")
        spin.valueChanged.connect(self._on_azimuth_changed)
        return spin

    def _build_general_page(self, page: QWidget) -> None:
        self.language_group = QGroupBox(page)
        form = QFormLayout(self.language_group)
        self.language_label = QLabel(self.language_group)
        self.language_combo = QComboBox(self.language_group)
        self.language_combo.setObjectName("HamQLanguageChoice")
        for code in LANGUAGE_CHOICES:
            self.language_combo.addItem(language_name(code), code)
        form.addRow(self.language_label, self.language_combo)
        self.language_hint = QLabel(self.language_group)
        self.language_hint.setWordWrap(True)
        form.addRow(self.language_hint)

        self.cty_group = QGroupBox(page)
        cty = QVBoxLayout(self.cty_group)
        self.cty_date_label = QLabel(self.cty_group)
        self.cty_date_label.setWordWrap(True)
        cty.addWidget(self.cty_date_label)
        row = QHBoxLayout()
        self.cty_download_button = QPushButton(self.cty_group)
        self.cty_download_button.clicked.connect(self._on_download_clicked)
        self.cty_status_label = QLabel(self.cty_group)
        self.cty_status_label.setWordWrap(True)
        row.addWidget(self.cty_download_button)
        row.addWidget(self.cty_status_label, 1)
        cty.addLayout(row)

        layout = QVBoxLayout(page)
        layout.addWidget(self.language_group)
        layout.addWidget(self.cty_group)
        layout.addStretch(1)

    # ------------------------------------------------------------------ texts

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    def retranslate(self) -> None:
        """Set every text in the current HamQ language (also while the dialog is open)."""
        self.setWindowTitle(self.tr("HamQ settings"))
        self.tabs.setTabText(self.tabs.indexOf(self.station_page), self.tr("Station"))
        self.tabs.setTabText(self.tabs.indexOf(self.wsjtx_page), "WSJT-X")
        self.tabs.setTabText(self.tabs.indexOf(self.radio_page), self.tr("Radio and rotator"))
        self.tabs.setTabText(self.tabs.indexOf(self.general_page), self.tr("General"))

        self.station_group.setTitle(self.tr("Station"))
        self.call_label.setText(self.tr("My callsign"))
        self.call_edit.setPlaceholderText(self.tr("e.g. YU1AB"))
        self.grid_label.setText(self.tr("My QTH locator"))
        self.grid_edit.setPlaceholderText(self.tr("e.g. KN04ft"))
        self.storage_group.setTitle(self.tr("Storage"))
        self.gpkg_label.setText(self.tr("GeoPackage with the QSO log"))
        self.gpkg_browse_button.setText(self.tr("Browse…"))
        self.gpkg_default_button.setText(self.tr("Use default"))

        self.wsjtx_group.setTitle("WSJT-X")
        self.wsjtx_addr_label.setText(self.tr("UDP address"))
        self.wsjtx_port_label.setText(self.tr("UDP port"))
        self.wsjtx_autostart_check.setText(self.tr("Start listening when QGIS starts"))
        self.wsjtx_hint.setText(
            self.tr(
                "WSJT-X sends to 127.0.0.1, port 2237, unless set otherwise in {menu}. "
                "To receive WSJT-X in several programs at once (JTAlert, GridTracker, HamQ), "
                "set the same multicast address, e.g. 224.0.0.1, there and here."
            ).format(menu=_WSJTX_MENU)
        )

        self.radio_hint.setText(
            self.tr(
                "HamQ connects over TCP to the Hamlib daemons rigctld (radio) and rotctld "
                "(rotator); start them first."
            )
        )
        self.rig_group.setTitle(self.tr("Radio (rigctld)"))
        self.rig_enabled_check.setText(self.tr("Connect to rigctld"))
        self.rig_host_label.setText(self.tr("Address"))
        self.rig_port_label.setText(self.tr("Port"))
        self.rig_poll_label.setText(self.tr("Poll interval"))
        self.rot_group.setTitle(self.tr("Rotator (rotctld)"))
        self.rot_enabled_check.setText(self.tr("Connect to rotctld"))
        self.rot_host_label.setText(self.tr("Address"))
        self.rot_port_label.setText(self.tr("Port"))
        self.rot_preset_label.setText(self.tr("Azimuth range"))
        self.rot_preset_combo.setItemText(len(AZIMUTH_PRESETS), tr(_CUSTOM))
        self.rot_min_label.setText(self.tr("Minimum azimuth"))
        self.rot_max_label.setText(self.tr("Maximum azimuth"))
        self.rot_min_spin.refresh_text()  # the decimal separator of the new language
        self.rot_max_spin.refresh_text()
        self.rot_reset_button.setText(
            self.tr("Ask again before turning the antenna from a map click")
        )

        self.language_group.setTitle(self.tr("Language"))
        self.language_label.setText(self.tr("Interface language"))
        for index in range(self.language_combo.count()):
            self.language_combo.setItemText(
                index, language_name(self.language_combo.itemData(index))
            )
        self.language_hint.setText(
            self.tr("The language changes when you save the settings, without a restart.")
        )
        self.cty_group.setTitle(self.tr("DXCC data (cty.dat)"))
        self.cty_download_button.setText(self.tr("Download now"))

        ok_button = self.button_box.button(DIALOG_OK)
        if ok_button is not None:
            ok_button.setText(self.tr("OK"))
        cancel_button = self.button_box.button(DIALOG_CANCEL)
        if cancel_button is not None:
            cancel_button.setText(self.tr("Cancel"))

        self._update_grid_status()
        self._update_reset_status()
        self._update_cty_status()
        if self._problems:  # shown problems follow the language; the focus stays
            self._show_problems(self._validate(), focus=False)

    # ------------------------------------------------------------------ load / save

    def load(self) -> None:
        """Fill the fields from the settings (discarding unsaved edits)."""
        settings = self._settings
        self.call_edit.setText(settings.my_call)
        self.grid_edit.setText(settings.my_grid)
        self.gpkg_edit.setText(settings.gpkg_path)
        self.wsjtx_addr_edit.setText(settings.wsjtx_addr)
        self.wsjtx_port_spin.setValue(settings.wsjtx_port)
        self.wsjtx_autostart_check.setChecked(settings.wsjtx_autostart)
        self.rig_enabled_check.setChecked(settings.rig_enabled)
        self.rig_host_edit.setText(settings.rig_host)
        self.rig_port_spin.setValue(settings.rig_port)
        self.rig_poll_spin.setValue(settings.rig_poll_ms)
        self.rot_enabled_check.setChecked(settings.rot_enabled)
        self.rot_host_edit.setText(settings.rot_host)
        self.rot_port_spin.setValue(settings.rot_port)
        self.rot_min_spin.setValue(settings.rot_min_az)
        self.rot_max_spin.setValue(settings.rot_max_az)
        self._sync_preset()
        setting = (
            self._language_manager.setting()
            if self._language_manager is not None
            else settings.language
        )
        index = self.language_combo.findData(setting)
        self.language_combo.setCurrentIndex(max(index, 0))
        self._reset_confirmation = False
        self._show_problems([])
        self._update_grid_status()
        self._update_reset_status()
        self._update_cty_status()

    def selected_language(self) -> str:
        """The language choice in the dialog: ``auto``, ``en``, ``sr_Latn`` or ``sr_Cyrl``."""
        return self.language_combo.currentData() or LANG_AUTO

    def validate(self) -> list[str]:
        """Messages about invalid fields (translated); empty when everything can be saved."""
        return [message for _widget, message in self._validate()]

    def _validate(self) -> list[tuple[QWidget | None, str]]:
        problems: list[tuple[QWidget | None, str]] = []
        call = self.call_edit.text().strip().upper()
        if call and not _CALL_RE.fullmatch(call):
            problems.append(
                (
                    self.call_edit,
                    self.tr("Callsign {call}: use only letters, digits and /.").format(call=call),
                )
            )
        grid = self.grid_edit.text().strip()
        if grid and _normalized_grid(grid) is None:
            problems.append(
                (
                    self.grid_edit,
                    self.tr(
                        "QTH locator {locator} is not a valid Maidenhead locator (e.g. KN04ft)."
                    ).format(locator=grid),
                )
            )
        problems.extend(self._gpkg_problems())
        address_problem = self._wsjtx_address_problem(self.wsjtx_addr_edit.text().strip())
        if address_problem:
            problems.append((self.wsjtx_addr_edit, address_problem))
        rig_host = self.rig_host_edit.text().strip()
        if not _valid_host(rig_host):
            problems.append(
                (self.rig_host_edit, self.tr("Enter a valid rigctld address (e.g. 127.0.0.1)."))
            )
        rot_host = self.rot_host_edit.text().strip()
        if not _valid_host(rot_host):
            problems.append(
                (self.rot_host_edit, self.tr("Enter a valid rotctld address (e.g. 127.0.0.1)."))
            )
        if (
            self.rig_enabled_check.isChecked()
            and self.rot_enabled_check.isChecked()
            and _same_host(rig_host, rot_host)
            and self.rig_port_spin.value() == self.rot_port_spin.value()
        ):
            problems.append(
                (
                    self.rot_port_spin,
                    self.tr("rigctld and rotctld cannot use the same port on the same computer."),
                )
            )
        low, high = self.rot_min_spin.value(), self.rot_max_spin.value()
        if high <= low:
            problems.append(
                (
                    self.rot_max_spin,
                    self.tr("The maximum azimuth must be greater than the minimum azimuth."),
                )
            )
        elif high - low > _MAX_AZIMUTH_SPAN:
            problems.append(
                (self.rot_max_spin, self.tr("The azimuth range cannot be wider than 720°."))
            )
        return problems

    def _wsjtx_address_problem(self, address: str) -> str:
        """Why the WSJT-X listener cannot use ``address``; ``""`` when it can.

        The listener (``net.wsjtx_listener``) accepts an IPv4 address, unicast or
        multicast (224.0.0.0/4), and ``localhost``; WSJT-X itself sends over IPv4.
        """
        if not address:
            return self.tr("Enter the WSJT-X UDP address.")
        if address.lower() == "localhost":
            return ""
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return self.tr(
                "WSJT-X address {address} is not an IPv4 address (e.g. 127.0.0.1 or 224.0.0.1)."
            ).format(address=address)
        if parsed.version != 4:
            return self.tr(
                "WSJT-X address {address} is an IPv6 address; use an IPv4 address "
                "(e.g. 127.0.0.1 or 224.0.0.1)."
            ).format(address=address)
        return ""

    def _gpkg_problems(self) -> list[tuple[QWidget | None, str]]:
        path = os.path.expanduser(self.gpkg_edit.text().strip())
        if not path:
            return [(self.gpkg_edit, self.tr("Choose the GeoPackage file."))]
        if not os.path.isabs(path):
            return [
                (
                    self.gpkg_edit,
                    self.tr("Enter the full path of the GeoPackage file: {path}").format(path=path),
                )
            ]
        folder = os.path.dirname(os.path.normpath(path))
        if not os.path.isdir(folder):
            return [
                (
                    self.gpkg_edit,
                    self.tr("The folder {folder} does not exist.").format(folder=folder),
                )
            ]
        if os.path.isdir(path):
            return [
                (
                    self.gpkg_edit,
                    self.tr("{path} is a folder; choose a GeoPackage file.").format(path=path),
                )
            ]
        return []

    def save(self) -> bool:
        """Validate and save; on success apply the language and emit ``settingsChanged``.

        Returns False (and shows the problems) when a field is invalid; nothing is
        saved then.
        """
        problems = self._validate()
        self._show_problems(problems)
        if problems:
            return False
        settings = self._settings
        values: dict[str, Any] = {
            "my_call": self.call_edit.text().strip().upper(),
            "my_grid": _normalized_grid(self.grid_edit.text().strip()) or "",
            "gpkg_path": os.path.normpath(os.path.expanduser(self.gpkg_edit.text().strip())),
            "wsjtx_addr": self.wsjtx_addr_edit.text().strip(),
            "wsjtx_port": self.wsjtx_port_spin.value(),
            "wsjtx_autostart": self.wsjtx_autostart_check.isChecked(),
            "rig_enabled": self.rig_enabled_check.isChecked(),
            "rig_host": self.rig_host_edit.text().strip(),
            "rig_port": self.rig_port_spin.value(),
            "rig_poll_ms": self.rig_poll_spin.value(),
            "rot_enabled": self.rot_enabled_check.isChecked(),
            "rot_host": self.rot_host_edit.text().strip(),
            "rot_port": self.rot_port_spin.value(),
            "rot_min_az": self.rot_min_spin.value(),
            "rot_max_az": self.rot_max_spin.value(),
        }
        if self._reset_confirmation:
            values["rot_confirmed"] = False
        language = self.selected_language()
        if self._language_manager is None:
            values["language"] = language
        previous = {name: getattr(settings, name) for name in values}
        try:
            for name, value in values.items():
                setattr(settings, name, value)
        except ValueError as exc:  # validated above; a setter may still refuse a value
            for name, value in previous.items():  # all or nothing
                with contextlib.suppress(ValueError):
                    setattr(settings, name, value)
            self._show_problems(
                [(None, self.tr("The settings could not be saved: {error}").format(error=exc))]
            )
            return False
        self._reset_confirmation = False
        if self._language_manager is not None:
            try:
                self._language_manager.set_setting(language)
            except Exception as exc:  # the other settings are saved: still announce them
                _log_error("SettingsDialog.save", exc)
        events().settingsChanged.emit()
        return True

    # ------------------------------------------------------------------ dialog

    def accept(self) -> None:
        """OK: validate and save; the dialog stays open while a field is invalid."""
        try:
            saved = self.save()
        except Exception as exc:  # a slot must never raise into Qt
            _log_error("SettingsDialog.accept", exc)
            self._show_problems(
                [(None, self.tr("The settings could not be saved: {error}").format(error=exc))]
            )
            return
        if saved:
            super().accept()

    def done(self, result: int) -> None:
        """Close the dialog and disconnect it."""
        try:
            self.cleanup()
        except Exception as exc:  # closing must work whatever happens
            _log_error("SettingsDialog.done", exc)
        super().done(result)

    def showEvent(self, event: Any) -> None:
        """Connect to language changes and cty.dat downloads while the dialog is shown."""
        try:
            self._connect_signals()
            self.retranslate()
        except Exception as exc:  # a Qt event handler must never raise into Qt
            _log_error("SettingsDialog.showEvent", exc)
        super().showEvent(event)

    def cleanup(self) -> None:
        """Disconnect from ``events()`` and the cty.dat manager. Safe to call twice.

        A download still running goes on; the dialog no longer waits for its end (shown
        again, it asks the manager whether one is running).
        """
        while self._connections:
            signal, slot = self._connections.pop()
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._downloading = False

    def _connect_signals(self) -> None:
        if self._connections:
            return
        pairs: list[tuple[Any, Callable[..., Any]]] = [
            (events().languageChanged, self._on_language_changed)
        ]
        finished = getattr(self._cty_manager, "downloadFinished", None)
        if finished is not None:
            pairs.append((finished, self._on_download_finished))
        for signal, slot in pairs:
            signal.connect(slot)
            self._connections.append((signal, slot))

    # ------------------------------------------------------------------ feedback

    def _show_problems(
        self, problems: list[tuple[QWidget | None, str]], focus: bool = True
    ) -> None:
        """List ``problems`` under the tabs; with ``focus`` show the first invalid field."""
        self._problems = problems
        if not problems:
            self.error_label.clear()
            self.error_label.hide()
            return
        self.error_label.setText("\n".join(message for _widget, message in problems))
        self.error_label.show()
        widget = next((widget for widget, _message in problems if widget is not None), None)
        if focus and widget is not None:
            for index in range(self.tabs.count()):
                page = self.tabs.widget(index)
                if page is not None and page.isAncestorOf(widget):
                    self.tabs.setCurrentIndex(index)
                    break
            widget.setFocus()

    def _update_grid_status(self) -> None:
        text = self.grid_edit.text().strip()
        if not text:
            self.grid_status.setStyleSheet("")
            self.grid_status.setText(
                self.tr("Not set: distances, bearings and the azimuthal map need it.")
            )
            return
        locator = _normalized_grid(text)
        if locator is None:
            self.grid_status.setStyleSheet(_ERROR_STYLE)
            self.grid_status.setText(
                self.tr("Not a valid Maidenhead locator: use 2, 4, 6 or 8 characters, e.g. KN04ft.")
            )
            return
        lat, lon = maidenhead.to_latlon(locator)
        self.grid_status.setStyleSheet("")
        self.grid_status.setText(
            self.tr("Valid: {locator}, center {lat}°, {lon}°").format(
                locator=locator, lat=format_number(lat, 4), lon=format_number(lon, 4)
            )
        )

    def _update_reset_status(self) -> None:
        confirmed = self._settings.rot_confirmed and not self._reset_confirmation
        self.rot_reset_button.setEnabled(confirmed)
        if confirmed:
            self.rot_reset_status.setText(self.tr("A map click turns the antenna without asking."))
        else:
            self.rot_reset_status.setText(
                self.tr("The first map click asks before turning the antenna.")
            )

    def _update_cty_status(self) -> None:
        downloaded = self._settings.cty_downloaded
        if downloaded:
            self.cty_date_label.setText(self.tr("Downloaded: {date}").format(date=downloaded))
        else:
            self.cty_date_label.setText(self.tr("Not downloaded yet"))
        available = self._cty_manager is not None
        downloading = self._is_downloading()
        self.cty_download_button.setEnabled(available and not downloading)
        if downloading:
            self.cty_status_label.setText(self.tr("Downloading…"))
        else:
            self.cty_status_label.setText(self._cty_message)

    def _is_downloading(self) -> bool:
        """True while a cty.dat download runs, also one started elsewhere (dock, start-up)."""
        check = getattr(self._cty_manager, "is_downloading", None)
        if callable(check):
            try:
                return bool(check())
            except Exception as exc:  # a broken manager must not break the dialog
                _log_error("SettingsDialog._is_downloading", exc)
        return self._downloading

    def _sync_preset(self) -> None:
        """Select the preset that matches the minimum and maximum azimuth, else Custom."""
        low, high = self.rot_min_spin.value(), self.rot_max_spin.value()
        index = len(AZIMUTH_PRESETS)
        for position, (_label, preset_low, preset_high) in enumerate(AZIMUTH_PRESETS):
            if abs(low - preset_low) < 0.05 and abs(high - preset_high) < 0.05:
                index = position
                break
        self._syncing_preset = True
        try:
            self.rot_preset_combo.setCurrentIndex(index)
        finally:
            self._syncing_preset = False

    # ------------------------------------------------------------------ slots

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()

    @_guarded
    def _on_call_edited(self, text: str) -> None:
        upper = text.upper()
        if upper != text:
            position = self.call_edit.cursorPosition()
            self.call_edit.setText(upper)
            self.call_edit.setCursorPosition(position)

    @_guarded
    def _on_grid_changed(self, _text: str) -> None:
        self._update_grid_status()

    @_guarded
    def _on_browse_clicked(self, _checked: bool = False) -> None:
        current = self.gpkg_edit.text().strip() or default_gpkg_path()
        path = self._ask_gpkg_path(current)
        if path:
            if not path.lower().endswith(".gpkg"):
                path += ".gpkg"
            self.gpkg_edit.setText(os.path.normpath(path))

    def _ask_gpkg_path(self, current: str) -> str:
        """Ask for the GeoPackage file (an existing one is fine: the log is appended to)."""
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("GeoPackage with the QSO log"),
            current,
            # the pattern is a placeholder: Cyrillic transliteration would change "*.gpkg"
            self.tr("GeoPackage files ({pattern})").format(pattern="*.gpkg"),
            options=FILE_DIALOG_DONT_CONFIRM_OVERWRITE,
        )
        return path or ""

    @_guarded
    def _on_default_clicked(self, _checked: bool = False) -> None:
        self.gpkg_edit.setText(default_gpkg_path())

    @_guarded
    def _on_preset_changed(self, *_args: Any) -> None:
        if self._syncing_preset:
            return
        data = self.rot_preset_combo.currentData()
        if not data:  # Custom: keep the values
            return
        low, high = data
        self.rot_min_spin.setValue(low)
        self.rot_max_spin.setValue(high)

    @_guarded
    def _on_azimuth_changed(self, _value: float) -> None:
        self._sync_preset()

    @_guarded
    def _on_reset_clicked(self, _checked: bool = False) -> None:
        self._reset_confirmation = True
        self._update_reset_status()

    @_guarded
    def _on_download_clicked(self, _checked: bool = False) -> None:
        if self._cty_manager is None or self._is_downloading():
            return
        self._connect_signals()
        self._downloading = True
        self._cty_message = ""
        try:
            # may report the end at once: _on_download_finished runs before this returns
            self._cty_manager.download()
        except Exception as exc:
            self._downloading = False
            self._cty_message = self.tr("Download failed: {error}").format(error=exc)
            _log_error("SettingsDialog._on_download_clicked", exc)
        self._update_cty_status()

    @_guarded
    def _on_download_finished(self, ok: bool, message: str) -> None:
        self._downloading = False
        if message:
            self._cty_message = message
        elif ok:
            self._cty_message = self.tr("cty.dat was downloaded.")
        else:
            self._cty_message = self.tr("cty.dat could not be downloaded.")
        self._update_cty_status()
