"""HamQ dock panel: log statistics, WSJT-X status, radio and rotator control.

:class:`HamQDock` only shows state and reports what the user asks for; it holds no
business logic. The controller feeds it through the ``set_*`` methods and reacts
to its signals:

========================  ============================================================
Signal                    Meaning
========================  ============================================================
``refreshRequested``      recompute the statistics
``importRequested``       import an ADIF file (toolbar button or the empty state)
``settingsRequested``     open the HamQ settings
``listenToggled(bool)``   start (True) / stop (False) the WSJT-X UDP listener
``rigSetRequested``       ``(hz, mode)``: tune the radio; ``mode`` is a Hamlib mode name.
                          ``hz`` is a Python int (declared ``qint64``: a C++ ``int`` would
                          wrap frequencies above 2147 MHz, e.g. 13 cm and 3 cm)
``rotatorTurnRequested``  ``(bearing)``: compass bearing in [0, 360), long path already
                          applied; map it with ``core.hamlib.rotator_target`` before
                          sending it to the rotator
``rotatorStopRequested``  stop the rotator
``pointOnMapToggled``     ``(bool)``: turn the rotator map tool on / off. Also emitted with
                          ``False`` when the dock turns "Point on map" off itself because
                          the rotator was disconnected or disabled
``logQsoRequested``       open the manual QSO dialog
========================  ============================================================

Setters never emit these signals, except ``pointOnMapToggled(False)`` as described.
Every text is translated with :func:`hamq.core.i18n.tr` and re-applied from the last
known state by :meth:`HamQDock.retranslate`, which runs on
``events().languageChanged``. Numbers use the decimal separator of the HamQ language
(``14.074000`` / ``14,074000``). Every label shows plain text and tooltips are escaped:
callsigns, countries and client names come from log files and the network and must
never be rendered as markup. Call :meth:`HamQDock.cleanup` before deleting the dock
(deleting it also drops its connections to ``events()``, as a safety net).
"""

from __future__ import annotations

import functools
import html
import math
import traceback
from collections.abc import Callable, Mapping
from typing import Any

from qgis.core import QgsMessageLog
from qgis.PyQt.QtCore import QLocale, QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPalette, QPen, QTextOption, QValidator
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import hamlib, maidenhead
from ..core.adif import parse_freq, parse_qso_datetime
from ..core.bands import band_from_freq
from ..core.i18n import LANG_SR_CYRL, LANG_SR_LATN, current_language, is_serbian, tr, tr_noop
from ..core.modes import display_mode
from ..events import events
from ..qgis_io import compat
from ..qgis_io.fields import from_qdatetime
from . import get_icon

__all__ = [
    "DecimalSpinBox",
    "HamQDock",
    "decimal_separator",
    "format_azimuth",
    "format_bearing",
    "format_distance",
    "format_mhz",
    "format_number",
    "format_utc",
    "hamq_locale",
]

LOG_TAG = "HamQ"
DASH = "\u2014"  # shown for unknown values
_NBSP = "\u00a0"

# Continent names for the "By continent" table (keys of QsoStats.by_continent).
_CONTINENT_NAMES = {
    "EU": tr_noop("Europe"),
    "AS": tr_noop("Asia"),
    "AF": tr_noop("Africa"),
    "NA": tr_noop("North America"),
    "SA": tr_noop("South America"),
    "OC": tr_noop("Oceania"),
    "AN": tr_noop("Antarctica"),
}
_UNKNOWN_KEY = "?"  # core.stats key for a missing continent, band or mode
_UNKNOWN = tr_noop("Unknown")
# Compass rose letters (Serbian: S, I, J, Z).
_COMPASS_LETTERS = (
    (0.0, tr_noop("N")),
    (90.0, tr_noop("E")),
    (180.0, tr_noop("S")),
    (270.0, tr_noop("W")),
)
_MSG_ERROR = tr_noop("Unexpected error in the HamQ panel: {error}")

# LED colours: not configured / idle, waiting, connected, error.
_LED_COLORS = {
    "disabled": "#c9c9c9",
    "off": "#8f8f8f",
    "wait": "#e8a200",
    "on": "#2f9e44",
    "error": "#d64545",
}
_HEADING_COLOR = QColor("#e8590c")
_TARGET_COLOR = QColor("#1c7ed6")


# --------------------------------------------------------------------------- formatting


def decimal_separator() -> str:
    """Decimal separator of the HamQ interface language: ``","`` in Serbian, else ``"."``."""
    return "," if is_serbian(current_language()) else "."


def hamq_locale() -> QLocale:
    """``QLocale`` of the HamQ interface language, for Qt widgets that format by locale.

    Serbian (Latin or Cyrillic script, Serbia) in Serbian; otherwise the neutral ``C``
    locale: English names, a decimal point and weeks from Monday. The system locale is
    never used, so e.g. a calendar names its months in the HamQ language.
    """
    language = current_language()
    if language == LANG_SR_LATN:
        return QLocale("sr_Latn_RS")
    if language == LANG_SR_CYRL:
        return QLocale("sr_Cyrl_RS")
    return QLocale.c()


def format_number(value: float, decimals: int = 0, *, group: bool = True) -> str:
    """``value`` with ``decimals`` decimals in the HamQ language, e.g. ``12,5`` in Serbian.

    With ``group`` numbers of five or more digits get a no-break space between
    thousands (``15 676``), which reads the same in English and Serbian. ``DASH``
    for values that are not finite numbers.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return DASH
    if not math.isfinite(number):
        return DASH
    text = f"{abs(number):.{max(0, int(decimals))}f}"
    whole, _, fraction = text.partition(".")
    if group and len(whole) > 4:
        whole = f"{int(whole):,}".replace(",", _NBSP)
    negative = number < 0 and any(char not in "0." for char in text)
    result = ("-" if negative else "") + whole
    if fraction:
        result += decimal_separator() + fraction
    return result


def format_mhz(freq_hz: object) -> str:
    """Frequency in Hz as MHz with 6 decimals: ``14074000`` -> ``"14.074000 MHz"``.

    ``DASH`` for ``None``, zero, negative or non-numeric values.
    """
    hz = _positive_number(freq_hz)
    if hz is None:
        return DASH
    return format_number(hz / 1e6, 6, group=False) + _NBSP + "MHz"


def format_distance(distance_km: object) -> str:
    """Distance in whole km: ``15676.2`` -> ``"15 676 km"``; ``DASH`` when unknown."""
    km = _number(distance_km)
    if km is None or km < 0:
        return DASH
    return format_number(km, 0) + _NBSP + "km"


def format_azimuth(azimuth: object) -> str:
    """Azimuth rounded to a whole degree: ``91.3`` -> ``"91°"``.

    A rotator position outside 0..359 (overlap ranges such as 0..450, or -180..180)
    also shows the raw value: ``400`` -> ``"40° (400°)"``. ``DASH`` when unknown.
    """
    value = _number(azimuth)
    if value is None:
        return DASH
    whole = round(value)
    compass = whole % 360
    text = f"{compass}°"
    if whole != compass:
        text += f" ({whole}°)"
    return text


def format_bearing(bearing: object) -> str:
    """Compass bearing rounded to a whole degree in 0..359: ``359.7`` -> ``"0°"``.

    Unlike :func:`format_azimuth` it never shows a raw rotator value. ``DASH`` when
    unknown.
    """
    value = _number(bearing)
    if value is None:
        return DASH
    return f"{round(value) % 360}°"


def format_utc(value: object) -> str:
    """A time as ``"2026-09-30 18:45 UTC"``.

    Accepts ``datetime`` (naive means UTC), ``QDateTime`` and ISO 8601 text;
    ``DASH`` for anything else.
    """
    try:
        when = from_qdatetime(value)
    except (TypeError, ValueError, OverflowError):
        when = None
    if when is None:
        return DASH
    return f"{when.year:04d}-{when.month:02d}-{when.day:02d} {when.hour:02d}:{when.minute:02d} UTC"


def _number(value: object) -> float | None:
    """A finite float from an int / float / numeric text, else ``None`` (bool too)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _positive_number(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _text(value: object) -> str:
    """Stripped text of a string value; ``""`` for anything else (None, NULL, numbers)."""
    return value.strip() if isinstance(value, str) else ""


def _host(address: str) -> str:
    """``address`` for ``host:port`` display; IPv6 addresses get brackets."""
    return f"[{address}]" if ":" in address and not address.startswith("[") else address


def _tooltip(text: str) -> str:
    """``text`` for ``setToolTip``, shown exactly as it is.

    Qt renders a tooltip that looks like markup as rich text and cannot be told
    otherwise, so text with ``<`` or ``&`` (e.g. from a log file or a UDP datagram) is
    escaped inside ``<qt>``; other text is returned unchanged.
    """
    if "<" not in text and "&" not in text:
        return text
    return "<qt>" + html.escape(text, quote=False).replace("\n", "<br>") + "</qt>"


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
    """Run a slot or setter so that no exception escapes into Qt; errors are logged."""

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any) -> Any:
        try:
            return method(self, *args)
        except Exception:
            _log_error()
            return None

    return wrapper


# --------------------------------------------------------------------------- small widgets


class _Led(QLabel):
    """A small round status light."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFixedSize(12, 12)
        self._state = ""
        self.set_state("off")

    def state(self) -> str:
        """Current state: ``disabled``, ``off``, ``wait``, ``on`` or ``error``."""
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        color = _LED_COLORS.get(state, _LED_COLORS["off"])
        self.setStyleSheet(
            f"background-color: {color}; border: 1px solid rgba(0, 0, 0, 90); border-radius: 6px;"
        )


class _Tile(QFrame):
    """A statistics tile: a caption, a large value and an optional detail line."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(1)
        self.caption = QLabel(self)
        self.caption.setObjectName(object_name + "Caption")
        self.caption.setWordWrap(True)
        self.value = QLabel(self)
        self.value.setObjectName(object_name + "Value")
        font = QFont(self.value.font())
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * 1.5)
        self.value.setFont(font)
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail = QLabel(self)
        self.detail.setObjectName(object_name + "Detail")
        self.detail.setWordWrap(True)
        self.detail.hide()
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)


class _CompassWidget(QWidget):
    """Compass rose with the rotator heading (solid needle) and the target (dashed)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HamQCompass")
        self._heading: float | None = None
        self._target: float | None = None
        self.setMinimumSize(96, 96)
        self.setSizePolicy(compat.SIZE_PREFERRED, compat.SIZE_PREFERRED)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(132, 132)

    def heading(self) -> float | None:
        """Current heading in [0, 360) or ``None``."""
        return self._heading

    def target(self) -> float | None:
        """Target azimuth in [0, 360) or ``None``."""
        return self._target

    def set_heading(self, azimuth: float | None) -> None:
        self._heading = None if azimuth is None else float(azimuth) % 360.0
        self.update()

    def set_target(self, azimuth: float | None) -> None:
        self._target = None if azimuth is None else float(azimuth) % 360.0
        self.update()

    @staticmethod
    def _point(center: QPointF, radius: float, azimuth: float) -> QPointF:
        angle = math.radians(azimuth)
        return QPointF(center.x() + radius * math.sin(angle), center.y() - radius * math.cos(angle))

    @_guarded
    def paintEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        side = min(self.width(), self.height())
        if side < 24:
            return
        painter = QPainter(self)
        try:
            self._paint(painter, side)
        finally:
            painter.end()

    def _paint(self, painter: QPainter, side: int) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        palette = self.palette()
        foreground = palette.color(group, QPalette.ColorRole.WindowText)
        background = palette.color(group, QPalette.ColorRole.Base)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = side / 2.0 - 2.0

        painter.setPen(QPen(foreground, 1.2))
        painter.setBrush(background)
        painter.drawEllipse(center, radius, radius)
        for azimuth in range(0, 360, 30):
            inner = radius * (0.80 if azimuth % 90 == 0 else 0.88)
            painter.drawLine(
                self._point(center, radius, azimuth), self._point(center, inner, azimuth)
            )

        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(8, int(radius / 5)))
        painter.setFont(font)
        box = max(12.0, radius / 3)
        for azimuth, letter in _COMPASS_LETTERS:
            spot = self._point(center, radius * 0.60, azimuth)
            rect = QRectF(spot.x() - box / 2, spot.y() - box / 2, box, box)
            painter.drawText(rect, tr(letter), QTextOption(compat.ALIGN_CENTER))

        enabled = self.isEnabled()
        if self._target is not None:
            pen = QPen(_TARGET_COLOR if enabled else foreground, 2.0)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            end = self._point(center, radius * 0.92, self._target)
            painter.drawLine(center, end)
            painter.setBrush(_TARGET_COLOR if enabled else foreground)
            painter.drawEllipse(end, 3.0, 3.0)
        if self._heading is not None:
            pen = QPen(_HEADING_COLOR if enabled else foreground, 3.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(center, self._point(center, radius * 0.86, self._heading))
        painter.setPen(QPen(foreground, 1.0))
        painter.setBrush(foreground)
        painter.drawEllipse(center, 3.0, 3.0)


class DecimalSpinBox(QDoubleSpinBox):
    """``QDoubleSpinBox`` that shows the HamQ decimal separator and accepts ``.`` and ``,``.

    Neither character is ever read as a thousands separator, so ``36.5`` and ``36,5``
    both mean 36.5 whatever the system locale (Qt's own spin box takes ``36.5`` for 365
    under a Serbian or German locale). A leading ``-`` is accepted when the minimum is
    negative; the prefix and the suffix (e.g. ``°``) may be typed or left out. Call
    :meth:`refresh_text` after a language change.
    """

    # The overrides below are C++ virtuals: an exception must never escape into Qt.

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt override)
        try:
            return format_number(value, self.decimals(), group=False)
        except Exception:
            _log_error()
            return f"{value:.{max(0, self.decimals())}f}"

    def valueFromText(self, text: str) -> float:  # noqa: N802 (Qt override)
        try:
            number = float(self._number_text(text).replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            return self.value()
        return number if math.isfinite(number) else self.value()

    def validate(self, text: str, pos: int) -> tuple[Any, str, int]:
        try:
            return self._validate(text, pos)
        except Exception:  # a C++ virtual: never let an exception escape into Qt
            _log_error()
            return QValidator.State.Invalid, text, pos

    def fixup(self, text: str) -> str:
        """Keep the text: Qt's fixup deletes the locale's thousands separator (36.5 -> 365)."""
        return text

    def _number_text(self, text: str) -> str:
        """``text`` without the prefix, the suffix and the spaces around them."""
        body = text.strip()
        prefix, suffix = self.prefix().strip(), self.suffix().strip()
        if prefix and body.startswith(prefix):
            body = body[len(prefix) :]
        if suffix and body.endswith(suffix):
            body = body[: -len(suffix)]
        return body.strip()

    def _validate(self, text: str, pos: int) -> tuple[Any, str, int]:
        candidate = self._number_text(text).replace(",", ".")
        negative = candidate.startswith("-")
        if negative:
            if self.minimum() >= 0:
                return QValidator.State.Invalid, text, pos
            candidate = candidate[1:]
        if candidate in ("", "."):
            return QValidator.State.Intermediate, text, pos
        whole, _, fraction = candidate.partition(".")
        if (
            any(char not in "0123456789" for char in whole + fraction)
            or len(fraction) > self.decimals()
        ):
            return QValidator.State.Invalid, text, pos
        value = -float(candidate) if negative else float(candidate)
        # More digits move a number away from zero: past the limit on that side it can
        # never become valid again, on the other side it still can.
        if value > self.maximum():
            state = QValidator.State.Invalid if value > 0 else QValidator.State.Intermediate
            return state, text, pos
        if value < self.minimum():
            state = QValidator.State.Invalid if value < 0 else QValidator.State.Intermediate
            return state, text, pos
        return QValidator.State.Acceptable, text, pos

    def refresh_text(self) -> None:
        """Show the value again, e.g. with the decimal separator of a new language."""
        self.setPrefix(self.prefix())  # re-renders the text, keeps the value


class _FrequencySpinBox(DecimalSpinBox):
    """MHz spin box with six decimals: ``14.074`` and ``14,074`` both mean 14.074 MHz."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDecimals(6)
        self.setRange(0.001, 999999.0)
        self.setSingleStep(0.001)
        self.setValue(14.074)
        self.setAlignment(compat.ALIGN_RIGHT | compat.ALIGN_VCENTER)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


def _bold(label: QLabel, scale: float = 1.0) -> QLabel:
    font = QFont(label.font())
    font.setBold(True)
    if scale != 1.0 and font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * scale)
    label.setFont(font)
    return label


def _error_label(object_name: str) -> QLabel:
    """A hidden, word-wrapped label for a problem reported by the listener or a client."""
    label = QLabel()
    label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setStyleSheet(f"color: {_LED_COLORS['error']};")
    label.hide()
    return label


def _show_error(label: QLabel, message: str) -> None:
    label.setText(message)
    label.setVisible(bool(message))


def _make_table(object_name: str) -> QTableWidget:
    table = QTableWidget(0, 3)
    table.setObjectName(object_name)
    table.setEditTriggers(compat.EDIT_NO_TRIGGERS)
    table.setSelectionMode(compat.SELECTION_NONE)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setWordWrap(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, compat.HEADER_STRETCH)
    header.setSectionResizeMode(1, compat.HEADER_RESIZE_TO_CONTENTS)
    header.setSectionResizeMode(2, compat.HEADER_RESIZE_TO_CONTENTS)
    header.setHighlightSections(False)
    row_height = table.fontMetrics().height() + 6
    table.verticalHeader().setMinimumSectionSize(row_height)
    table.verticalHeader().setDefaultSectionSize(row_height)
    return table


def _fit_table(table: QTableWidget) -> None:
    """Make ``table`` exactly as high as its rows, so the tab scrolls instead of it."""
    rows = sum(table.rowHeight(row) for row in range(table.rowCount()))
    header = table.horizontalHeader().sizeHint().height()
    table.setFixedHeight(header + rows + 2 * table.frameWidth() + 2)


# --------------------------------------------------------------------------- the dock


class HamQDock(QDockWidget):
    """The HamQ panel with the tabs Statistics, WSJT-X and Radio (objectName ``HamQDock``).

    ``settings`` (a :class:`hamq.settings.HamQSettings`, created when omitted) is
    only read for my callsign and locator in the Statistics header, again whenever
    ``events().settingsChanged`` fires; :meth:`set_station` overrides it.
    """

    refreshRequested = pyqtSignal()
    importRequested = pyqtSignal()
    settingsRequested = pyqtSignal()
    listenToggled = pyqtSignal(bool)
    #: ``(hz, mode)``; ``qint64`` because a 32-bit ``int`` wraps above 2147483647 Hz.
    rigSetRequested = pyqtSignal("qint64", str)
    rotatorTurnRequested = pyqtSignal(float)
    rotatorStopRequested = pyqtSignal()
    pointOnMapToggled = pyqtSignal(bool)
    logQsoRequested = pyqtSignal()

    TAB_STATISTICS, TAB_WSJTX, TAB_RADIO = 0, 1, 2

    def __init__(self, parent: QWidget | None = None, settings: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("HamQDock")
        self.setAllowedAreas(compat.DOCK_ALL)
        self._settings = settings
        # Last known state; retranslate() renders everything from it.
        self._station = ("", "")
        self._stats: Any = None
        self._listening = False
        self._listen_address = ""
        self._listen_port = 0
        self._wsjtx_connected = False
        self._wsjtx_client = ""
        self._wsjtx_version = ""
        self._wsjtx_status: dict[str, Any] | None = None
        self._last_qso: dict[str, Any] | None = None
        self._wsjtx_error_text = ""  # problems reported by the listener / the clients,
        self._rig_error_text = ""  # already translated by them
        self._rot_error_text = ""
        self._rig_enabled = False
        self._rig_connected = False
        self._rig_state: dict[str, Any] | None = None
        self._rot_enabled = False
        self._rot_connected = False
        self._rot_position: tuple[float, float] | None = None
        self._rot_target: float | None = None
        self._syncing = False  # rig controls are being filled from the rig state
        self._rig_controls_dirty = False  # the user edited the rig controls
        self._connections: list[tuple[Any, Callable[..., Any]]] = []

        self._build_ui()
        self._connect_event(events().languageChanged, self._on_language_changed)
        self._connect_event(events().settingsChanged, self._on_settings_changed)
        # Safety net when the dock is deleted without cleanup(): the lambda holds the
        # list, not the dock, so it still works while the dock is being destroyed.
        connections = self._connections
        self.destroyed.connect(lambda *_args: _disconnect_all(connections))
        self._read_station()
        self.retranslate()

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    # ------------------------------------------------------------------ building

    def _build_ui(self) -> None:
        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("HamQDockTabs")
        self._tabs.addTab(self._build_stats_tab(), get_icon("panel.svg"), "")
        self._tabs.addTab(_scroll(self._build_wsjtx_tab()), get_icon("wsjtx.svg"), "WSJT-X")
        self._tabs.addTab(_scroll(self._build_radio_tab()), get_icon("radio.svg"), "")
        self.setWidget(self._tabs)
        # Callsigns, countries, modes and client names come from log files and the
        # network: no label may render them as markup (QLabel's default is AutoText).
        for label in self.findChildren(QLabel):
            label.setTextFormat(compat.TEXT_PLAIN)

    def _tool_button(self, icon: str, object_name: str, slot: Callable[..., Any]) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setIcon(get_icon(icon))
        button.setAutoRaise(True)
        button.clicked.connect(slot)
        return button

    def _build_stats_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HamQStatsTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        self._station_label = _bold(QLabel(), 1.15)
        self._station_label.setObjectName("HamQStationLabel")
        self._station_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self._station_label, 1)
        self._refresh_button = self._tool_button(
            "refresh.svg", "HamQRefreshButton", self._on_refresh_clicked
        )
        self._import_tool_button = self._tool_button(
            "import_adif.svg", "HamQImportToolButton", self._on_import_clicked
        )
        self._settings_button = self._tool_button(
            "settings.svg", "HamQSettingsButton", self._on_settings_clicked
        )
        for button in (self._refresh_button, self._import_tool_button, self._settings_button):
            header.addWidget(button)
        layout.addLayout(header)
        self._station_hint = QLabel()
        self._station_hint.setObjectName("HamQStationHint")
        self._station_hint.setWordWrap(True)
        layout.addWidget(self._station_hint)

        self._stats_stack = QStackedWidget()
        self._stats_stack.setObjectName("HamQStatsStack")
        self._stats_stack.addWidget(self._build_empty_page())
        self._stats_stack.addWidget(_scroll(self._build_stats_page()))
        layout.addWidget(self._stats_stack, 1)
        return page

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HamQStatsEmpty")
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self._empty_title = _bold(QLabel(), 1.2)
        self._empty_title.setObjectName("HamQEmptyTitle")
        self._empty_title.setAlignment(compat.ALIGN_CENTER)
        self._empty_text = QLabel()
        self._empty_text.setObjectName("HamQEmptyText")
        self._empty_text.setAlignment(compat.ALIGN_CENTER)
        self._empty_text.setWordWrap(True)
        self._empty_import_button = QPushButton(get_icon("import_adif.svg"), "")
        self._empty_import_button.setObjectName("HamQImportButton")
        self._empty_import_button.clicked.connect(self._on_import_clicked)
        layout.addWidget(self._empty_title)
        layout.addWidget(self._empty_text)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._empty_import_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HamQStatsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        tiles = QGridLayout()
        tiles.setSpacing(6)
        self._tile_total = _Tile("HamQTileTotal")
        self._tile_dxcc = _Tile("HamQTileDxcc")
        self._tile_calls = _Tile("HamQTileCalls")
        self._tile_grids = _Tile("HamQTileGrids")
        self._tile_longest = _Tile("HamQTileLongest")
        self._tile_longest.detail.show()
        tiles.addWidget(self._tile_total, 0, 0)
        tiles.addWidget(self._tile_dxcc, 0, 1)
        tiles.addWidget(self._tile_calls, 1, 0)
        tiles.addWidget(self._tile_grids, 1, 1)
        tiles.addWidget(self._tile_longest, 2, 0, 1, 2)
        layout.addLayout(tiles)

        self._first_label = QLabel()
        self._first_label.setObjectName("HamQFirstQso")
        self._last_label = QLabel()
        self._last_label.setObjectName("HamQLastQso")
        layout.addWidget(self._first_label)
        layout.addWidget(self._last_label)

        self._continent_title = _bold(QLabel())
        self._continent_table = _make_table("HamQContinentTable")
        self._band_title = _bold(QLabel())
        self._band_table = _make_table("HamQBandTable")
        self._mode_title = _bold(QLabel())
        self._mode_table = _make_table("HamQModeTable")
        for title, table in (
            (self._continent_title, self._continent_table),
            (self._band_title, self._band_table),
            (self._mode_title, self._mode_table),
        ):
            layout.addSpacing(4)
            layout.addWidget(title)
            layout.addWidget(table)
        layout.addStretch(1)
        return page

    def _build_wsjtx_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HamQWsjtxTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        self._listen_button = QPushButton(get_icon("wsjtx.svg"), "")
        self._listen_button.setObjectName("HamQListenButton")
        self._listen_button.setCheckable(True)
        self._listen_button.clicked.connect(self._on_listen_clicked)
        layout.addWidget(self._listen_button)

        state = QHBoxLayout()
        self._wsjtx_led = _Led("HamQWsjtxLed")
        self._wsjtx_state = QLabel()
        self._wsjtx_state.setObjectName("HamQWsjtxState")
        self._wsjtx_state.setWordWrap(True)
        state.addWidget(self._wsjtx_led)
        state.addWidget(self._wsjtx_state, 1)
        layout.addLayout(state)
        self._wsjtx_error = _error_label("HamQWsjtxError")
        layout.addWidget(self._wsjtx_error)

        self._status_group = QGroupBox()
        self._status_group.setObjectName("HamQWsjtxStatusGroup")
        form = QFormLayout(self._status_group)
        self._status_freq_caption, self._status_freq = QLabel(), QLabel()
        self._status_band_caption, self._status_band = QLabel(), QLabel()
        self._status_mode_caption, self._status_mode = QLabel(), QLabel()
        self._status_dx_caption, self._status_dx = QLabel(), QLabel()
        for caption, value, name in (
            (self._status_freq_caption, self._status_freq, "HamQWsjtxFrequency"),
            (self._status_band_caption, self._status_band, "HamQWsjtxBand"),
            (self._status_mode_caption, self._status_mode, "HamQWsjtxMode"),
            (self._status_dx_caption, self._status_dx, "HamQWsjtxDxCall"),
        ):
            value.setObjectName(name)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(caption, value)
        layout.addWidget(self._status_group)

        self._last_qso_group = QGroupBox()
        self._last_qso_group.setObjectName("HamQWsjtxLastQsoGroup")
        box = QVBoxLayout(self._last_qso_group)
        self._last_qso_call = _bold(QLabel(), 1.2)
        self._last_qso_call.setObjectName("HamQLastQsoCall")
        self._last_qso_call.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._last_qso_detail = QLabel()
        self._last_qso_detail.setObjectName("HamQLastQsoDetail")
        self._last_qso_detail.setWordWrap(True)
        box.addWidget(self._last_qso_call)
        box.addWidget(self._last_qso_detail)
        layout.addWidget(self._last_qso_group)
        layout.addStretch(1)
        return page

    def _build_radio_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HamQRadioTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        # --- radio (rig)
        self._rig_group = QGroupBox()
        self._rig_group.setObjectName("HamQRigGroup")
        rig = QVBoxLayout(self._rig_group)
        state = QHBoxLayout()
        self._rig_led = _Led("HamQRigLed")
        self._rig_state_label = QLabel()
        self._rig_state_label.setObjectName("HamQRigState")
        self._rig_state_label.setWordWrap(True)
        state.addWidget(self._rig_led)
        state.addWidget(self._rig_state_label, 1)
        rig.addLayout(state)
        self._rig_error = _error_label("HamQRigError")
        rig.addWidget(self._rig_error)
        self._rig_freq_label = _bold(QLabel(), 1.8)
        self._rig_freq_label.setObjectName("HamQRigFrequency")
        self._rig_freq_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._rig_details = QLabel()
        self._rig_details.setObjectName("HamQRigDetails")
        rig.addWidget(self._rig_freq_label)
        rig.addWidget(self._rig_details)
        controls = QGridLayout()
        self._rig_freq_caption = QLabel()
        self._rig_freq_spin = _FrequencySpinBox()
        self._rig_freq_spin.setObjectName("HamQRigFrequencySpin")
        self._rig_freq_spin.valueChanged.connect(self._on_rig_control_edited)
        self._rig_mode_caption = QLabel()
        self._rig_mode_combo = QComboBox()
        self._rig_mode_combo.setObjectName("HamQRigModeCombo")
        self._rig_mode_combo.addItems(list(hamlib.MODES))
        self._rig_mode_combo.currentIndexChanged.connect(self._on_rig_control_edited)
        self._rig_set_button = QPushButton()
        self._rig_set_button.setObjectName("HamQRigSetButton")
        self._rig_set_button.clicked.connect(self._on_rig_set_clicked)
        self._rig_mhz_label = QLabel("MHz")  # a unit symbol, the same in every language
        self._rig_mhz_label.setObjectName("HamQRigFrequencyUnit")
        controls.addWidget(self._rig_freq_caption, 0, 0)
        controls.addWidget(self._rig_freq_spin, 0, 1)
        controls.addWidget(self._rig_mhz_label, 0, 2)
        controls.addWidget(self._rig_mode_caption, 1, 0)
        controls.addWidget(self._rig_mode_combo, 1, 1)
        controls.addWidget(self._rig_set_button, 1, 2)
        controls.setColumnStretch(1, 1)
        rig.addLayout(controls)
        layout.addWidget(self._rig_group)

        # --- rotator
        self._rot_group = QGroupBox()
        self._rot_group.setObjectName("HamQRotatorGroup")
        rot = QVBoxLayout(self._rot_group)
        state = QHBoxLayout()
        self._rot_led = _Led("HamQRotatorLed")
        self._rot_state_label = QLabel()
        self._rot_state_label.setObjectName("HamQRotatorState")
        self._rot_state_label.setWordWrap(True)
        state.addWidget(self._rot_led)
        state.addWidget(self._rot_state_label, 1)
        rot.addLayout(state)
        self._rot_error = _error_label("HamQRotatorError")
        rot.addWidget(self._rot_error)
        view = QHBoxLayout()
        self._compass = _CompassWidget()
        view.addWidget(self._compass)
        readout = QVBoxLayout()
        self._rot_az_caption = QLabel()
        self._rot_az_label = _bold(QLabel(), 1.8)
        self._rot_az_label.setObjectName("HamQRotatorAzimuth")
        self._rot_el_label = QLabel()
        self._rot_el_label.setObjectName("HamQRotatorElevation")
        self._rot_target_label = QLabel()
        self._rot_target_label.setObjectName("HamQRotatorTarget")
        readout.addStretch(1)
        for widget in (
            self._rot_az_caption,
            self._rot_az_label,
            self._rot_el_label,
            self._rot_target_label,
        ):
            readout.addWidget(widget)
        readout.addStretch(1)
        view.addLayout(readout, 1)
        rot.addLayout(view)
        turn = QHBoxLayout()
        self._rot_target_spin = QSpinBox()
        self._rot_target_spin.setObjectName("HamQRotatorTargetSpin")
        self._rot_target_spin.setRange(0, 359)
        self._rot_target_spin.setWrapping(True)
        self._rot_target_spin.setSuffix("°")
        self._rot_turn_button = QPushButton(get_icon("rotator.svg"), "")
        self._rot_turn_button.setObjectName("HamQRotatorTurnButton")
        self._rot_turn_button.clicked.connect(self._on_turn_clicked)
        self._rot_stop_button = QPushButton()
        self._rot_stop_button.setObjectName("HamQRotatorStopButton")
        self._rot_stop_button.clicked.connect(self._on_stop_clicked)
        turn.addWidget(self._rot_target_spin, 1)
        turn.addWidget(self._rot_turn_button)
        turn.addWidget(self._rot_stop_button)
        rot.addLayout(turn)
        options = QHBoxLayout()
        self._long_path_check = QCheckBox()
        self._long_path_check.setObjectName("HamQLongPathCheck")
        self._point_button = QPushButton()
        self._point_button.setObjectName("HamQPointOnMapButton")
        self._point_button.setCheckable(True)
        self._point_button.clicked.connect(self._on_point_on_map_clicked)
        options.addWidget(self._long_path_check)
        options.addStretch(1)
        options.addWidget(self._point_button)
        rot.addLayout(options)
        layout.addWidget(self._rot_group)

        bottom = QHBoxLayout()
        self._log_qso_button = QPushButton()
        self._log_qso_button.setObjectName("HamQLogQsoButton")
        self._log_qso_button.clicked.connect(self._on_log_qso_clicked)
        self._radio_settings_button = self._tool_button(
            "settings.svg", "HamQRadioSettingsButton", self._on_settings_clicked
        )
        bottom.addWidget(self._log_qso_button, 1)
        bottom.addWidget(self._radio_settings_button)
        layout.addLayout(bottom)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ events and teardown

    def _connect_event(self, signal: Any, slot: Callable[..., Any]) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    def cleanup(self) -> None:
        """Disconnect from the HamQ events. Safe to call more than once."""
        _disconnect_all(self._connections)

    def _read_station(self) -> None:
        settings = self._settings
        if settings is None:
            from ..settings import HamQSettings

            settings = self._settings = HamQSettings()
        call = _text(getattr(settings, "my_call", "")).upper()
        self._station = (call, _text(getattr(settings, "my_grid", "")))

    @_guarded
    def _on_language_changed(self, _language: str = "") -> None:
        self.retranslate()

    @_guarded
    def _on_settings_changed(self) -> None:
        self._read_station()
        self._render_station()

    # ------------------------------------------------------------------ user actions

    @_guarded
    def _on_refresh_clicked(self, _checked: bool = False) -> None:
        self.refreshRequested.emit()

    @_guarded
    def _on_import_clicked(self, _checked: bool = False) -> None:
        self.importRequested.emit()

    @_guarded
    def _on_settings_clicked(self, _checked: bool = False) -> None:
        self.settingsRequested.emit()

    @_guarded
    def _on_listen_clicked(self, checked: bool = False) -> None:
        self._render_listen_button()
        self.listenToggled.emit(bool(checked))

    @_guarded
    def _on_rig_control_edited(self, *_args: Any) -> None:
        if not self._syncing:
            self._rig_controls_dirty = True

    @_guarded
    def _on_rig_set_clicked(self, _checked: bool = False) -> None:
        self._rig_freq_spin.interpretText()
        hz = int(round(self._rig_freq_spin.value() * 1e6))
        mode = self._rig_mode_combo.currentText()
        self._rig_controls_dirty = False  # follow the radio again after this command
        if self._rig_error_text:  # a new command: an earlier problem is over
            self._rig_error_text = ""
            self._render_rig()
        self.rigSetRequested.emit(hz, mode)

    @_guarded
    def _on_turn_clicked(self, _checked: bool = False) -> None:
        self._rot_target_spin.interpretText()
        bearing = float(self._rot_target_spin.value())
        if self._long_path_check.isChecked():
            bearing = (bearing + 180.0) % 360.0
        self._clear_rotator_error()
        self.rotatorTurnRequested.emit(bearing)

    @_guarded
    def _on_stop_clicked(self, _checked: bool = False) -> None:
        self._clear_rotator_error()
        self.rotatorStopRequested.emit()

    def _clear_rotator_error(self) -> None:
        if self._rot_error_text:  # a new command: an earlier problem is over
            self._rot_error_text = ""
            self._render_rotator()

    @_guarded
    def _on_point_on_map_clicked(self, checked: bool = False) -> None:
        self.pointOnMapToggled.emit(bool(checked))

    @_guarded
    def _on_log_qso_clicked(self, _checked: bool = False) -> None:
        self.logQsoRequested.emit()

    # ------------------------------------------------------------------ setters: statistics

    @_guarded
    def set_station(self, call: str, grid: str) -> None:
        """Show my callsign and locator in the Statistics header."""
        self._station = (_text(call).upper(), _text(grid))
        self._render_station()

    @_guarded
    def set_stats(self, stats: Any) -> None:
        """Show a :class:`hamq.core.stats.QsoStats`; ``None`` (or no QSOs) shows the empty state."""
        self._stats = stats
        self._render_stats()

    # ------------------------------------------------------------------ setters: WSJT-X

    @_guarded
    def set_listening(self, listening: bool, address: str = "", port: int = 0) -> None:
        """The UDP listener runs (on ``address:port``) or not. Does not emit ``listenToggled``."""
        self._listening = bool(listening)
        self._listen_address = _text(address)
        number = _number(port)
        self._listen_port = int(number) if number is not None and 0 < number < 65536 else 0
        if self._listening:
            self._wsjtx_error_text = ""  # started: an earlier problem is over
        else:
            self._wsjtx_connected = False
        self._listen_button.setChecked(self._listening)
        self._render_wsjtx_state()

    @_guarded
    def set_wsjtx_error(self, message: str | None) -> None:
        """Show a problem of the UDP listener (``WsjtxListener.errorOccurred``, translated).

        ``None`` or ``""`` hides it; ``set_listening(True, ...)`` clears it too.
        """
        self._wsjtx_error_text = _text(message)
        self._render_wsjtx_state()

    @_guarded
    def set_wsjtx_connected(
        self, connected: bool, client: str | None = None, version: str | None = None
    ) -> None:
        """A WSJT-X client sends heartbeats (``client`` id and ``version``) or went away.

        ``None`` keeps the client name / version known from earlier messages.
        """
        self._wsjtx_connected = bool(connected)
        if client is not None:
            self._wsjtx_client = _text(client)
        if version is not None:
            self._wsjtx_version = _text(version)
        self._render_wsjtx_state()

    @_guarded
    def set_wsjtx_status(self, status: Mapping[str, Any] | None) -> None:
        """Show a decoded WSJT-X Status message (``core.wsjtx.decode``); ``None`` clears it."""
        self._wsjtx_status = dict(status) if isinstance(status, Mapping) else None
        if self._wsjtx_status and _text(self._wsjtx_status.get("client")):
            self._wsjtx_client = _text(self._wsjtx_status.get("client"))
        self._render_wsjtx_status()
        self._render_wsjtx_state()

    @_guarded
    def set_last_qso(self, qso: Mapping[str, Any] | None) -> None:
        """Show the last logged QSO; ``None`` clears it.

        ``qso`` is an ADIF record (``CALL``, ``QSO_DATE``, ``TIME_ON``, ``BAND``,
        ``FREQ``, ``MODE``, ``SUBMODE``, ``GRIDSQUARE``) or ``Qso.attributes()``
        (``call``, ``qso_datetime``, ``band``, ``freq_mhz``, ``mode``, ``submode``,
        ``gridsquare``, ``distance_km``, ``country``); key case does not matter.
        """
        self._last_qso = dict(qso) if isinstance(qso, Mapping) else None
        self._render_last_qso()

    # ------------------------------------------------------------------ setters: radio

    @_guarded
    def set_rig_enabled(self, enabled: bool) -> None:
        """Radio control is configured (``True``) or shown greyed out as "off" (``False``)."""
        self._rig_enabled = bool(enabled)
        if not self._rig_enabled:
            self._rig_error_text = ""
        self._render_rig()

    @_guarded
    def set_rig_connected(self, connected: bool) -> None:
        """``rigctld`` answers (``True``) or not."""
        self._rig_connected = bool(connected)
        if self._rig_connected:
            self._rig_error_text = ""  # (re)connected: an earlier problem is over
        else:
            self._rig_controls_dirty = False
            self._rig_state = None  # never show a stale frequency after a reconnect
        self._render_rig()

    @_guarded
    def set_rig_error(self, message: str | None) -> None:
        """Show a problem of the radio connection (``RigClient.errorOccurred``, translated).

        ``None`` or ``""`` hides it; ignored while radio control is off. It is also
        cleared when the radio connects, when radio control is turned off and when the
        user presses "Set".
        """
        self._rig_error_text = _text(message) if self._rig_enabled else ""
        self._render_rig()

    @_guarded
    def set_rig_state(self, state: Mapping[str, Any] | None) -> None:
        """Show ``{"freq_hz", "mode", "passband"}`` from the rig (``None`` values = unknown).

        The set controls follow the radio until the user edits them, and again after
        "Set".
        """
        self._rig_state = dict(state) if isinstance(state, Mapping) else None
        self._render_rig()

    @_guarded
    def set_rotator_enabled(self, enabled: bool) -> None:
        """Rotator control is configured (``True``) or shown greyed out as "off" (``False``)."""
        self._rot_enabled = bool(enabled)
        if not self._rot_enabled:
            self._rot_error_text = ""
        self._render_rotator()
        self._release_point_on_map()

    @_guarded
    def set_rotator_connected(self, connected: bool) -> None:
        """``rotctld`` answers (``True``) or not."""
        self._rot_connected = bool(connected)
        if self._rot_connected:
            self._rot_error_text = ""  # (re)connected: an earlier problem is over
        else:
            self._rot_position = None  # never show a stale heading after a reconnect
        self._render_rotator()
        self._release_point_on_map()

    @_guarded
    def set_rotator_error(self, message: str | None) -> None:
        """Show a problem of the rotator connection (``RotatorClient.errorOccurred``).

        ``None`` or ``""`` hides it; ignored while rotator control is off. It is also
        cleared when the rotator connects, when rotator control is turned off, on "Turn"
        and "Stop" and when a new target is set.
        """
        self._rot_error_text = _text(message) if self._rot_enabled else ""
        self._render_rotator()

    @_guarded
    def set_rotator_position(self, azimuth: float | None, elevation: float | None = 0.0) -> None:
        """Current rotator position in degrees (as ``rotctld`` reports it); ``None`` = unknown."""
        az = _number(azimuth)
        el = _number(elevation)
        self._rot_position = None if az is None else (az, 0.0 if el is None else el)
        self._render_rotator()

    @_guarded
    def set_rotator_target(self, azimuth: float | None) -> None:
        """Azimuth the rotator was sent to (compass marker and label); ``None`` clears it."""
        self._rot_target = _number(azimuth)
        if self._rot_target is not None:
            self._rot_error_text = ""  # a new command: an earlier problem is over
        self._render_rotator()

    @_guarded
    def set_point_on_map_checked(self, checked: bool) -> None:
        """Show the "Point on map" button pressed or not; does not emit ``pointOnMapToggled``."""
        self._point_button.setChecked(bool(checked))

    def _release_point_on_map(self) -> None:
        if self._point_button.isChecked() and not (self._rot_enabled and self._rot_connected):
            self._point_button.setChecked(False)
            self.pointOnMapToggled.emit(False)

    # ------------------------------------------------------------------ translation

    @_guarded
    def retranslate(self) -> None:
        """Apply every text again in the current HamQ language."""
        self.setWindowTitle("HamQ")
        self._tabs.setTabText(self.TAB_STATISTICS, tr("Statistics"))
        self._tabs.setTabText(self.TAB_WSJTX, "WSJT-X")
        self._tabs.setTabText(self.TAB_RADIO, tr("Radio"))

        # statistics
        for button, text, tooltip in (
            (self._refresh_button, tr("Refresh"), tr("Refresh the statistics")),
            (self._import_tool_button, tr("Import ADIF..."), tr("Import an ADIF log")),
            (self._settings_button, tr("Settings..."), tr("HamQ settings")),
            (self._radio_settings_button, tr("Settings..."), tr("HamQ settings")),
        ):
            button.setText(text)
            button.setToolTip(tooltip)
        self._station_hint.setText(tr("Set your callsign and QTH locator in Settings."))
        self._empty_title.setText(tr("No QSOs yet"))
        self._empty_text.setText(tr("Import an ADIF log to see your statistics here."))
        self._empty_import_button.setText(tr("Import ADIF..."))
        self._tile_total.caption.setText(tr("Total QSOs"))
        self._tile_dxcc.caption.setText(tr("DXCC entities"))
        self._tile_calls.caption.setText(tr("Unique callsigns"))
        self._tile_grids.caption.setText(tr("Grid squares"))
        self._tile_longest.caption.setText(tr("Longest QSO"))
        self._continent_title.setText(tr("By continent"))
        self._band_title.setText(tr("By band"))
        self._mode_title.setText(tr("By mode"))
        count, share = tr("QSOs"), tr("Share")
        self._continent_table.setHorizontalHeaderLabels([tr("Continent"), count, share])
        self._band_table.setHorizontalHeaderLabels([tr("Band"), count, share])
        self._mode_table.setHorizontalHeaderLabels([tr("Mode"), count, share])

        # WSJT-X
        self._listen_button.setToolTip(tr("Receive QSOs and status from WSJT-X or JTDX over UDP"))
        self._status_group.setTitle(tr("Last status"))
        self._status_freq_caption.setText(tr("Frequency"))
        self._status_band_caption.setText(tr("Band"))
        self._status_mode_caption.setText(tr("Mode"))
        self._status_dx_caption.setText(tr("DX call"))
        self._last_qso_group.setTitle(tr("Last logged QSO"))

        # radio
        self._rig_group.setTitle(tr("Radio"))
        self._rig_freq_caption.setText(tr("Frequency"))
        self._rig_mode_caption.setText(tr("Mode"))
        self._rig_set_button.setText(tr("Set"))
        self._rig_set_button.setToolTip(tr("Set the radio to this frequency and mode"))
        self._rig_freq_spin.refresh_text()
        self._rot_group.setTitle(tr("Rotator"))
        self._rot_az_caption.setText(tr("Azimuth"))
        self._rot_turn_button.setText(tr("Turn"))
        self._rot_turn_button.setToolTip(tr("Turn the antenna to this azimuth"))
        self._rot_stop_button.setText(tr("Stop"))
        self._rot_stop_button.setToolTip(tr("Stop the rotator"))
        self._long_path_check.setText(tr("Long path"))
        self._long_path_check.setToolTip(
            tr("Turn to the long path (azimuth + 180°), also when pointing on the map")
        )
        self._point_button.setText(tr("Point on map"))
        self._point_button.setToolTip(tr("Click on the map to turn the antenna toward that point"))
        self._log_qso_button.setText(tr("Log QSO..."))
        self._log_qso_button.setToolTip(
            tr("Enter a QSO by hand; frequency and mode come from the radio")
        )

        self._render_station()
        self._render_stats()
        self._render_wsjtx_state()
        self._render_wsjtx_status()
        self._render_last_qso()
        self._render_rig()
        self._render_rotator()
        self._compass.update()

    # ------------------------------------------------------------------ rendering

    def _render_station(self) -> None:
        call, grid = self._station
        parts = [part for part in (call, grid) if part]
        self._station_label.setText(" · ".join(parts))
        self._station_label.setVisible(bool(parts))
        # also when the stored locator is not a locator: no distances without my QTH
        self._station_hint.setVisible(not (call and maidenhead.is_valid(grid)))

    def _render_stats(self) -> None:
        stats = self._stats
        total = getattr(stats, "total", 0) if stats is not None else 0
        if not isinstance(total, int) or total <= 0:
            self._stats_stack.setCurrentIndex(0)
            return
        self._stats_stack.setCurrentIndex(1)
        self._tile_total.value.setText(format_number(total))
        self._tile_dxcc.value.setText(format_number(getattr(stats, "dxcc_count", 0)))
        self._tile_calls.value.setText(format_number(getattr(stats, "unique_calls", 0)))
        self._tile_grids.value.setText(format_number(getattr(stats, "grid_count", 0)))

        longest = getattr(stats, "longest", None)
        if longest is None:
            self._tile_longest.value.setText(DASH)
            self._tile_longest.detail.setText("")
            self._tile_longest.setToolTip("")
        else:
            self._tile_longest.value.setText(_text(getattr(longest, "call", "")) or DASH)
            detail = [format_distance(getattr(longest, "distance_km", None))]
            country = _text(getattr(longest, "country", None))
            if country:
                detail.append(country)
            self._tile_longest.detail.setText(" · ".join(detail))
            extra = [
                _text(getattr(longest, "band", None)),
                _text(getattr(longest, "mode", None)),
            ]
            when = getattr(longest, "qso_datetime", None)
            if when is not None:
                extra.append(format_utc(when))
            self._tile_longest.setToolTip(_tooltip(" · ".join(part for part in extra if part)))

        self._first_label.setText(
            tr("First QSO: {when}").format(when=format_utc(getattr(stats, "first_qso", None)))
        )
        self._last_label.setText(
            tr("Last QSO: {when}").format(when=format_utc(getattr(stats, "last_qso", None)))
        )
        self._fill_table(
            self._continent_table,
            [
                (self._continent_name(key), count)
                for key, count in _items(getattr(stats, "by_continent", None))
            ],
            total,
        )
        self._fill_table(
            self._band_table,
            [
                (self._key_name(key), count)
                for key, count in _items(getattr(stats, "by_band", None))
            ],
            total,
        )
        self._fill_table(
            self._mode_table,
            [
                (self._key_name(key), count)
                for key, count in _items(getattr(stats, "by_mode", None))
            ],
            total,
        )

    @staticmethod
    def _key_name(key: str) -> str:
        return tr(_UNKNOWN) if key == _UNKNOWN_KEY else key

    @staticmethod
    def _continent_name(key: str) -> str:
        name = _CONTINENT_NAMES.get(key)
        if name is not None:
            return f"{tr(name)} ({key})"
        return HamQDock._key_name(key)

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[tuple[str, int]], total: int) -> None:
        table.setRowCount(len(rows))
        for row, (name, count) in enumerate(rows):
            share = format_number(100.0 * count / total, 1) + "%" if total > 0 else DASH
            cells = (QTableWidgetItem(name), QTableWidgetItem(format_number(count)))
            cells += (QTableWidgetItem(share),)
            for column, item in enumerate(cells):
                if column:
                    item.setTextAlignment(compat.ALIGN_RIGHT | compat.ALIGN_VCENTER)
                table.setItem(row, column, item)
        _fit_table(table)

    def _render_listen_button(self) -> None:
        checked = self._listen_button.isChecked()
        self._listen_button.setText(tr("Stop listening") if checked else tr("Start listening"))

    def _render_wsjtx_state(self) -> None:
        self._render_listen_button()
        if not self._listening:
            self._wsjtx_led.set_state("error" if self._wsjtx_error_text else "off")
            text = tr("Not listening")
        elif self._wsjtx_connected:
            self._wsjtx_led.set_state("on")
            text = (
                tr("Connected: {client} {version}")
                .format(client=self._wsjtx_client or "WSJT-X", version=self._wsjtx_version)
                .strip()
            )
        else:
            self._wsjtx_led.set_state("wait")
            text = tr("Listening on {address}:{port}, waiting for WSJT-X").format(
                address=_host(self._listen_address or "0.0.0.0"), port=self._listen_port
            )
        self._wsjtx_state.setText(text)
        self._wsjtx_led.setToolTip(_tooltip(text))
        _show_error(self._wsjtx_error, self._wsjtx_error_text)

    def _render_wsjtx_status(self) -> None:
        status = self._wsjtx_status or {}
        hz = _positive_number(status.get("freq_hz"))
        self._status_freq.setText(format_mhz(hz))
        self._status_band.setText((band_from_freq(hz / 1e6) if hz else None) or DASH)
        self._status_mode.setText(_text(status.get("mode")) or DASH)
        self._status_dx.setText(_text(status.get("dx_call")) or DASH)

    def _render_last_qso(self) -> None:
        qso = self._last_qso
        if not qso:
            self._last_qso_call.setText(tr("No QSO logged yet"))
            self._last_qso_detail.setText("")
            self._last_qso_detail.hide()
            return
        fields = {str(key).lower(): value for key, value in qso.items()}
        freq = _positive_number(fields.get("freq_mhz"))
        if freq is None:
            freq = parse_freq(fields.get("freq"))
        band = _text(fields.get("band")) or (band_from_freq(freq) if freq else "") or ""
        mode = display_mode(_text(fields.get("mode")), _text(fields.get("submode")))
        when = fields.get("qso_datetime")
        if when is None or format_utc(when) == DASH:
            when = parse_qso_datetime(
                _text(fields.get("qso_date")) or None, _text(fields.get("time_on")) or None
            )
        parts = [
            band,
            mode,
            format_utc(when) if when is not None else "",
            _text(fields.get("gridsquare")),
        ]
        distance = _number(fields.get("distance_km"))
        if distance is not None:
            parts.append(format_distance(distance))
        country = _text(fields.get("country"))
        if country:
            parts.append(country)
        self._last_qso_call.setText(_text(fields.get("call")).upper() or DASH)
        self._last_qso_detail.setText(" · ".join(part for part in parts if part and part != DASH))
        self._last_qso_detail.show()

    def _render_rig(self) -> None:
        enabled = self._rig_enabled
        connected = enabled and self._rig_connected
        if not enabled:
            self._rig_led.set_state("disabled")
            text = tr("Radio control is off. Turn it on in Settings.")
        elif connected:
            self._rig_led.set_state("on")
            text = tr("Connected")
        else:
            self._rig_led.set_state("error")
            text = tr("Not connected")
        self._rig_state_label.setText(text)
        self._rig_led.setToolTip(_tooltip(text))
        _show_error(self._rig_error, self._rig_error_text if enabled else "")

        state = (self._rig_state or {}) if connected else {}
        hz = _positive_number(state.get("freq_hz"))
        self._rig_freq_label.setText(format_mhz(hz))
        mode = _text(state.get("mode"))
        passband = _number(state.get("passband"))
        details = [
            (band_from_freq(hz / 1e6) if hz else None) or DASH,
            mode or DASH,
        ]
        if passband is not None and passband > 0:
            details.append(format_number(passband) + _NBSP + "Hz")
        self._rig_details.setText(" · ".join(details))
        for widget in (self._rig_freq_label, self._rig_details):
            widget.setEnabled(enabled)
        for widget in (
            self._rig_freq_caption,
            self._rig_freq_spin,
            self._rig_mhz_label,
            self._rig_mode_caption,
            self._rig_mode_combo,
            self._rig_set_button,
        ):
            widget.setEnabled(connected)
        if connected and not self._rig_controls_dirty:
            self._sync_rig_controls(hz, mode)

    def _sync_rig_controls(self, hz: float | None, mode: str) -> None:
        """Fill the set controls from the radio without marking them as edited."""
        self._syncing = True
        try:
            if hz is not None and not self._rig_freq_spin.hasFocus():
                self._rig_freq_spin.setValue(hz / 1e6)
            index = self._rig_mode_combo.findText(mode.upper()) if mode else -1
            if index >= 0 and not self._rig_mode_combo.hasFocus():
                self._rig_mode_combo.setCurrentIndex(index)
        finally:
            self._syncing = False

    def _render_rotator(self) -> None:
        enabled = self._rot_enabled
        connected = enabled and self._rot_connected
        if not enabled:
            self._rot_led.set_state("disabled")
            text = tr("Rotator control is off. Turn it on in Settings.")
        elif connected:
            self._rot_led.set_state("on")
            text = tr("Connected")
        else:
            self._rot_led.set_state("error")
            text = tr("Not connected")
        self._rot_state_label.setText(text)
        self._rot_led.setToolTip(_tooltip(text))
        _show_error(self._rot_error, self._rot_error_text if enabled else "")

        position = self._rot_position if connected else None
        azimuth = position[0] if position else None
        elevation = position[1] if position else 0.0
        self._rot_az_label.setText(format_azimuth(azimuth))
        self._rot_el_label.setText(
            tr("Elevation: {elevation}").format(elevation=format_number(elevation, 0) + "°")
        )
        self._rot_el_label.setVisible(abs(elevation) >= 0.5)
        target = self._rot_target if enabled else None
        self._rot_target_label.setText(
            tr("Target: {azimuth}").format(azimuth=format_azimuth(target))
        )
        self._compass.set_heading(azimuth)
        self._compass.set_target(target)
        for widget in (
            self._compass,
            self._rot_az_caption,
            self._rot_az_label,
            self._rot_el_label,
            self._rot_target_label,
        ):
            widget.setEnabled(enabled)
        for widget in (
            self._rot_target_spin,
            self._rot_turn_button,
            self._rot_stop_button,
            self._long_path_check,
            self._point_button,
        ):
            widget.setEnabled(connected)

    # ------------------------------------------------------------------ for tests / controller

    def current_tab(self) -> int:
        """Index of the visible tab (``TAB_STATISTICS``, ``TAB_WSJTX`` or ``TAB_RADIO``)."""
        return self._tabs.currentIndex()

    def show_tab(self, index: int) -> None:
        """Show the tab ``index`` (``TAB_STATISTICS``, ``TAB_WSJTX`` or ``TAB_RADIO``)."""
        self._tabs.setCurrentIndex(index)

    def is_long_path(self) -> bool:
        """True while "Long path" is checked.

        "Turn" applies it itself; pass this method as ``get_long_path`` to
        :class:`hamq.gui.rotator_tool.RotatorMapTool` so that map clicks follow it too.
        """
        return self._long_path_check.isChecked()


def _items(counts: object) -> list[tuple[str, int]]:
    """``(key, count)`` pairs of a ``QsoStats.by_*`` mapping in its order, ``"?"`` last.

    ``core.stats`` sorts modes by count, so its unknown row can be anywhere there; the
    panel always lists it after the known continents, bands and modes.
    """
    if not isinstance(counts, Mapping):
        return []
    items = [
        (str(key), count)
        for key, count in counts.items()
        if isinstance(count, int) and not isinstance(count, bool)
    ]
    known = [item for item in items if item[0] != _UNKNOWN_KEY]
    return known + [item for item in items if item[0] == _UNKNOWN_KEY]
