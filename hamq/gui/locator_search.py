"""Toolbar locator search: type ``KN04ft``, press Enter, the map centers on the cell.

:class:`LocatorSearchWidget` holds a line edit (up to 10 characters: a 10-character
locator is cut to 8 as ``core.maidenhead.normalize`` does). Enter normalizes the
text, centers the map canvas on the cell and zooms so that the cell and its
neighbours are visible (the cell bounds are transformed from EPSG:4326 to the canvas
CRS, which QGIS keeps equal to the project CRS), and highlights the cell with a rubber
band for about three seconds. Invalid input turns the line edit red and shows a warning
in the message bar. The field is as wide as its placeholder in the current language (at
least a 10-character locator), so it never stretches across a wide toolbar.

Call :meth:`LocatorSearchWidget.cleanup` before the plugin unloads; it removes the
rubber band, stops the timer and disconnects from ``events()``. Safe to call twice.
"""

from __future__ import annotations

import contextlib
import functools
import math
import traceback
from collections.abc import Callable
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QHBoxLayout, QLineEdit, QStyle, QWidget

from ..core import maidenhead
from ..core.i18n import tr
from ..events import events
from ..qgis_io.compat import GEOMETRY_POLYGON, MSG_CRITICAL, MSG_WARNING, SIZE_FIXED
from . import get_icon

__all__ = ["HIGHLIGHT_MS", "MAX_LENGTH", "LocatorSearchWidget", "latitude_limit", "view_rectangle"]

LOG_TAG = "HamQ"
#: How long the found cell stays highlighted (milliseconds).
HIGHLIGHT_MS = 3000
#: Longest accepted input: a 10-character locator is cut to 8.
MAX_LENGTH = 10
#: Seconds the "not a valid locator" warning stays in the message bar.
MESSAGE_SECONDS = 5
_WGS84 = "EPSG:4326"
_MERCATOR_MAX_LAT = 85.0511287798066  # Web Mercator: the map is a square
_EDGE_POINTS = 16  # points per cell edge of the highlight: cell edges curve in most CRSs
_ERROR_STYLE = "QLineEdit { color: #c62828; border: 1px solid #c62828; padding: 1px 2px; }"
_WIDEST_LOCATOR = "KN04ft12ab"  # the field always fits the longest accepted input
_BAND_COLOR = QColor(230, 81, 0)
_BAND_FILL = QColor(230, 81, 0, 60)


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


def view_rectangle(locator: str) -> tuple[float, float, float, float]:
    """``(lat_min, lon_min, lat_max, lon_max)`` to show for a locator: the cell and one
    cell on every side, clamped to the world (a field shows 60 x 30 degrees, an
    extended square 90 x 45 seconds). Raises ValueError for an invalid locator."""
    lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
    dlat, dlon = lat_max - lat_min, lon_max - lon_min
    return (
        max(lat_min - dlat, -90.0),
        max(lon_min - dlon, -180.0),
        min(lat_max + dlat, 90.0),
        min(lon_max + dlon, 180.0),
    )


def latitude_limit(crs: QgsCoordinateReferenceSystem) -> tuple[float, float] | None:
    """``(south, north)`` latitude limits of a Mercator CRS, None for other projections.

    Mercator cannot show the poles: near them it gives huge coordinates (Web Mercator
    stops at 85.06 degrees). The limits are the CRS's area of use, 85.0511 without
    one. Other projections are not limited: their formulas stay valid far outside the
    area of use (a Transverse Mercator zone shows a neighbouring country well), so a
    locator there is shown where it is.
    """
    if crs.projectionAcronym() != "merc":
        return None
    bounds = crs.bounds()
    if bounds.isNull() or not bounds.isFinite() or bounds.height() <= 0:
        return -_MERCATOR_MAX_LAT, _MERCATOR_MAX_LAT
    south, north = bounds.yMinimum(), bounds.yMaximum()
    return max(south, -_MERCATOR_MAX_LAT), min(north, _MERCATOR_MAX_LAT)


def _limit_rect(
    rect: QgsRectangle, limits: tuple[float, float] | None, keep_size: bool
) -> QgsRectangle | None:
    """``rect`` (degrees) with its latitudes inside ``limits``.

    A rectangle beyond a limit is moved inside when ``keep_size`` (the view of a polar
    cell shows the nearest part of the map), else cut; None when nothing is left.
    """
    if limits is None:
        return rect
    south, north = limits
    y0, y1 = rect.yMinimum(), rect.yMaximum()
    if keep_size and (y1 <= south or y0 >= north):
        height = min(y1 - y0, north - south)
        y0, y1 = (south, south + height) if y1 <= south else (north - height, north)
    else:
        y0, y1 = max(y0, south), min(y1, north)
    if y1 <= y0:
        return None
    return QgsRectangle(rect.xMinimum(), y0, rect.xMaximum(), y1)


def _finite_point(point: QgsPointXY) -> bool:
    return math.isfinite(point.x()) and math.isfinite(point.y())


class LocatorSearchWidget(QWidget):
    """Toolbar widget: a Maidenhead locator plus Enter centers the map on the cell."""

    #: Emitted with the normalized locator after the map moved to it.
    locatorFound = pyqtSignal(str)

    def __init__(self, iface: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HamQLocatorSearch")
        self._iface = iface
        self._rubber_band: Any = None
        self._band_canvas: Any = None  # the canvas that owns the rubber band
        self._error = False
        #: Highlight duration in milliseconds (tests shorten it).
        self.highlight_ms = HIGHLIGHT_MS

        self.line_edit = QLineEdit(self)
        self.line_edit.setObjectName("HamQLocatorEdit")
        self.line_edit.setMaxLength(MAX_LENGTH)
        self.line_edit.setClearButtonEnabled(True)
        self.line_edit.addAction(get_icon("locator.svg"), QLineEdit.ActionPosition.LeadingPosition)
        # A toolbar stretches an expanding widget over the whole row: the field gets a
        # fixed width instead (set for each language by retranslate()).
        self.setSizePolicy(SIZE_FIXED, SIZE_FIXED)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.line_edit)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_highlight_timeout)
        self.line_edit.returnPressed.connect(self._on_return_pressed)
        self.line_edit.textEdited.connect(self._on_text_edited)
        self._connected = False
        events().languageChanged.connect(self._on_language_changed)
        self._connected = True
        self.retranslate()

    # ------------------------------------------------------------------ public

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    def retranslate(self) -> None:
        """Set the placeholder and tooltip in the current HamQ language."""
        self.line_edit.setPlaceholderText(self.tr("Locator, e.g. KN04ft"))
        self.line_edit.setToolTip(
            self.tr(
                "Maidenhead locator (2, 4, 6 or 8 characters, e.g. KN04ft): "
                "press Enter to center the map on it"
            )
        )
        self._fit_width()

    def _fit_width(self) -> None:
        """Make the field as wide as its placeholder or a 10-character locator, plus the
        locator icon and the clear button."""
        edit = self.line_edit
        metrics = edit.fontMetrics()
        text = max(
            metrics.horizontalAdvance(edit.placeholderText()),
            metrics.horizontalAdvance(_WIDEST_LOCATOR),
        )
        style = edit.style()
        icon = style.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, edit)
        frame = style.pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, edit)
        action = icon + 6 + icon // 4  # what QLineEdit gives an action: icon, padding, gap
        edit.setFixedWidth(text + 2 * action + 2 * frame + 16)

    def has_error(self) -> bool:
        """True while the line edit shows invalid input."""
        return self._error

    def is_highlighting(self) -> bool:
        """True while the found cell is highlighted on the map."""
        return self._rubber_band is not None

    def highlight_geometry(self) -> QgsGeometry | None:
        """Geometry of the highlight in the canvas CRS, None when nothing is highlighted."""
        if self._rubber_band is None:
            return None
        return self._rubber_band.asGeometry()

    def search(self, text: str | None = None) -> bool:
        """Center the map on a locator (the line edit text by default).

        Returns True when the map moved. Invalid input shows an error and returns
        False; empty input only clears a shown error.
        """
        raw = (self.line_edit.text() if text is None else text).strip()
        if not raw:
            self._set_error(False)
            return False
        try:
            locator = maidenhead.normalize(raw)
        except ValueError:
            self._set_error(True)
            self._warn(
                self.tr(
                    "{text} is not a valid Maidenhead locator: use 2, 4, 6 or 8 characters, "
                    "e.g. KN04ft."
                ).format(text=raw)
            )
            return False
        self._set_error(False)
        if self.line_edit.text() != locator:
            self.line_edit.setText(locator)
        canvas = self._iface.mapCanvas()
        if canvas is None:
            return False
        if not self._show(canvas, locator):
            self._warn(
                self.tr("The map cannot show {locator} in the current projection.").format(
                    locator=locator
                )
            )
            return False
        self.locatorFound.emit(locator)
        return True

    def clear_highlight(self) -> None:
        """Remove the highlight from the map now and delete the rubber band."""
        with contextlib.suppress(RuntimeError):  # the widget is being destroyed
            self._timer.stop()
        band, self._rubber_band = self._rubber_band, None
        canvas, self._band_canvas = self._band_canvas, None
        if band is None:
            return
        # A rubber band belongs to C++ (Python does not delete it) and a deleted canvas
        # deletes its items without telling Python: touch the band only while its canvas
        # lives, then delete it (its destructor takes it off the scene).
        if canvas is None or sip.isdeleted(canvas) or sip.isdeleted(band):
            return
        with contextlib.suppress(RuntimeError):
            scene = band.scene()
            if scene is not None:
                scene.removeItem(band)
            sip.delete(band)

    def cleanup(self) -> None:
        """Remove the highlight, stop the timer, disconnect from ``events()``. Safe twice."""
        self.clear_highlight()
        if self._connected:
            self._connected = False
            with contextlib.suppress(TypeError, RuntimeError):
                events().languageChanged.disconnect(self._on_language_changed)

    # ------------------------------------------------------------------ map

    def _show(self, canvas: Any, locator: str) -> bool:
        crs = canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            crs = QgsProject.instance().crs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem(_WGS84)
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(_WGS84), crs, QgsProject.instance()
        )
        limits = latitude_limit(crs)
        lat_min, lon_min, lat_max, lon_max = view_rectangle(locator)
        view = _limit_rect(QgsRectangle(lon_min, lat_min, lon_max, lat_max), limits, True)
        lat, lon = maidenhead.to_latlon(locator)
        if limits is not None:  # a polar cell in Web Mercator: as close as the map goes
            lat = min(max(lat, limits[0]), limits[1])
        center = QgsPointXY(lon, lat)
        if view is None:
            return False
        try:
            extent = transform.transformBoundingBox(view)
            map_center = transform.transform(center)
        except QgsCsException:
            return False
        if extent.isNull() or extent.isEmpty() or not extent.isFinite():
            return False
        if not _finite_point(map_center):
            return False
        canvas.setExtent(extent)
        canvas.setCenter(map_center)
        canvas.refresh()
        self._highlight(canvas, transform, locator, limits)
        return True

    def _highlight(
        self,
        canvas: Any,
        transform: QgsCoordinateTransform,
        locator: str,
        limits: tuple[float, float] | None,
    ) -> None:
        from qgis.gui import QgsRubberBand  # lazily: importing this module needs no qgis.gui

        self.clear_highlight()
        geometry = self._cell_geometry(transform, locator, limits)
        if geometry is None:
            return
        band = QgsRubberBand(canvas, GEOMETRY_POLYGON)
        band.setStrokeColor(_BAND_COLOR)
        band.setFillColor(_BAND_FILL)
        band.setWidth(2)
        band.setToGeometry(geometry)  # already in the canvas CRS
        band.show()
        self._rubber_band = band
        self._band_canvas = canvas
        self._timer.start(max(int(self.highlight_ms), 0))

    @staticmethod
    def _cell_geometry(
        transform: QgsCoordinateTransform, locator: str, limits: tuple[float, float] | None
    ) -> QgsGeometry | None:
        """The cell as a polygon in the canvas CRS, edges densified; None if it fails
        (or when the cell lies wholly beyond the latitude limits of a Mercator map)."""
        lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
        cell = _limit_rect(QgsRectangle(lon_min, lat_min, lon_max, lat_max), limits, False)
        if cell is None:
            return None
        x0, y0, x1, y1 = cell.xMinimum(), cell.yMinimum(), cell.xMaximum(), cell.yMaximum()
        ring: list[QgsPointXY] = []
        for step in range(_EDGE_POINTS):  # south edge west -> east, then around
            ring.append(QgsPointXY(x0 + (x1 - x0) * step / _EDGE_POINTS, y0))
        for step in range(_EDGE_POINTS):
            ring.append(QgsPointXY(x1, y0 + (y1 - y0) * step / _EDGE_POINTS))
        for step in range(_EDGE_POINTS):
            ring.append(QgsPointXY(x1 - (x1 - x0) * step / _EDGE_POINTS, y1))
        for step in range(_EDGE_POINTS):
            ring.append(QgsPointXY(x0, y1 - (y1 - y0) * step / _EDGE_POINTS))
        ring.append(QgsPointXY(x0, y0))
        try:
            points = [transform.transform(point) for point in ring]
        except QgsCsException:
            return None
        if not all(_finite_point(point) for point in points):
            return None
        return QgsGeometry.fromPolygonXY([points])

    # ------------------------------------------------------------------ feedback

    def _set_error(self, error: bool) -> None:
        if error == self._error:
            return
        self._error = error
        self.line_edit.setStyleSheet(_ERROR_STYLE if error else "")

    def _warn(self, text: str) -> None:
        bar = self._iface.messageBar()
        if bar is not None:
            bar.pushMessage("HamQ", text, MSG_WARNING, MESSAGE_SECONDS)

    # ------------------------------------------------------------------ slots

    @_guarded
    def _on_return_pressed(self) -> None:
        self.search()

    @_guarded
    def _on_text_edited(self, _text: str) -> None:
        self._set_error(False)

    @_guarded
    def _on_highlight_timeout(self) -> None:
        self.clear_highlight()

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()
