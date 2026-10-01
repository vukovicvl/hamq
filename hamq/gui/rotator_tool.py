"""Map tool that turns the antenna rotator toward a clicked point.

A left click on the map is converted to EPSG:4326, the great-circle bearing and
distance from my QTH are computed with :mod:`hamq.core.geo`, the bearing is mapped
into the rotator's range with :func:`hamq.core.hamlib.rotator_target` and the result
is handed to the ``on_target`` callback, which sends it to the rotator. The
great-circle path from my QTH to the point is drawn as a "beam line" rubber band
until the tool is deactivated or the map is right-clicked. With the long path on
(``get_long_path``) the antenna turns the other way round (bearing + 180 degrees)
and the beam line follows the long path.

The first time (``HamQSettings.rot_confirmed`` is false) the user is asked to
confirm through the ``confirm`` callback; after a "yes" later clicks turn the
rotator at once. Problems (no QTH, a point outside the rotator range, ...) are
reported through ``notify`` as translated messages and nothing is sent.

Call :meth:`RotatorMapTool.cleanup` before unloading: it removes the rubber band
from the canvas and unsets the tool. It is safe to call twice.
"""

from __future__ import annotations

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
)
from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QMessageBox

from ..core import geo, hamlib
from ..core.i18n import tr, tr_noop
from ..qgis_io import compat
from .dock import format_azimuth, format_bearing, format_distance, format_number

__all__ = ["RotatorMapTool", "long_path_parts"]

LOG_TAG = "HamQ"
#: Clicks closer to my QTH than this (km) have no meaningful bearing.
MIN_DISTANCE_KM = 0.01
_BEAM_COLOR = QColor(232, 89, 12, 220)
_BEAM_WIDTH = 3
_MSG_ERROR = tr_noop("Unexpected error in the rotator map tool: {error}")
# Long-path waypoints are joined when their parts meet closer than this (degrees).
_JOIN_EPS = 1e-9


def _log_error() -> None:
    QgsMessageLog.logMessage(
        tr(_MSG_ERROR).format(error=traceback.format_exc()), LOG_TAG, compat.MSG_CRITICAL
    )


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def long_path_parts(
    lat1: float, lon1: float, lat2: float, lon2: float, step_km: float = 100.0
) -> list[list[tuple[float, float]]]:
    """Points along the long great-circle path from point 1 to point 2.

    The long path leaves point 1 in the opposite direction of the short path (bearing
    + 180 degrees) and covers the rest of the great circle (about 40030 km minus the
    short path). Same format as :func:`hamq.core.geo.great_circle`: a list of parts of
    ``(lat, lon)`` points, split at the antimeridian, vertices at most ``step_km``
    apart. ``[]`` when the points are (nearly) the same or antipodal: the great circle
    is then not defined.
    """
    short = geo.distance_km(lat1, lon1, lat2, lon2)
    if not short >= MIN_DISTANCE_KM or geo.is_antipodal(lat1, lon1, lat2, lon2):
        return []
    distance, bearing = geo.long_path(short, geo.bearing_deg(lat1, lon1, lat2, lon2))
    # Thirds of the long path are shorter than half the circumference, so the short
    # great circle between two waypoints runs along the long path.
    waypoints = [(lat1, lon1)]
    waypoints += [geo.destination(lat1, lon1, bearing, distance * k / 3.0) for k in (1, 2)]
    waypoints.append((lat2, lon2))
    parts: list[list[tuple[float, float]]] = []
    for start, end in zip(waypoints, waypoints[1:]):
        for index, part in enumerate(geo.great_circle(*start, *end, step_km)):
            joined = (
                index == 0
                and parts
                and abs(parts[-1][-1][0] - part[0][0]) < _JOIN_EPS
                and abs(parts[-1][-1][1] - part[0][1]) < _JOIN_EPS
            )
            if joined:
                parts[-1].extend(part[1:])
            else:
                parts.append(list(part))
    return parts


class RotatorMapTool(QgsMapToolEmitPoint):
    """Click on the map -> bearing from my QTH -> rotator azimuth -> ``on_target``.

    ``get_station_latlon()`` returns my QTH as ``(lat, lon)`` or ``None`` (e.g.
    ``lambda: settings.station().latlon()``). ``get_rotator_range()`` returns the
    rotator's ``(min_az, max_az)`` in degrees. ``on_target(azimuth, bearing,
    distance_km, point)`` receives the azimuth to command (inside the range; for
    ranges wider than 360 degrees the equivalent closest to the current position),
    the compass bearing in [0, 360), the great-circle distance and the clicked point
    as a ``QgsPointXY`` in EPSG:4326 (x = longitude). With the long path, bearing and
    distance are those of the long path.

    ``confirm(message) -> bool`` asks before the first turn (default: a
    ``QMessageBox.question``). Optional keyword arguments: ``notify(message)`` shows a
    translated problem (default: the QGIS message bar when available, and the HamQ
    log), ``get_current_az()`` returns the rotator position or ``None``,
    ``get_long_path()`` returns ``True`` to turn to the long path (e.g.
    ``dock.is_long_path``), ``settings`` is the :class:`hamq.settings.HamQSettings`
    holding ``rot_confirmed`` (created when omitted), ``step_km`` is the spacing of the
    beam line vertices.

    Like every ``QgsMapToolEmitPoint`` the tool also emits ``canvasClicked(point,
    button)`` for each click (``point`` in the canvas CRS).
    """

    def __init__(
        self,
        canvas: QgsMapCanvas,
        get_station_latlon: Callable[[], tuple[float, float] | None],
        get_rotator_range: Callable[[], tuple[float, float]],
        on_target: Callable[[float, float, float, QgsPointXY], Any],
        confirm: Callable[[str], bool] | None = None,
        *,
        notify: Callable[[str], Any] | None = None,
        get_current_az: Callable[[], float | None] | None = None,
        get_long_path: Callable[[], bool] | None = None,
        settings: Any = None,
        step_km: float = 100.0,
    ) -> None:
        step = float(step_km)
        if not (math.isfinite(step) and step > 0.0):
            raise ValueError(f"step_km must be a positive number, got {step_km!r}")
        super().__init__(canvas)
        self._canvas = canvas
        self._get_station_latlon = get_station_latlon
        self._get_rotator_range = get_rotator_range
        self._on_target = on_target
        self._confirm = confirm if confirm is not None else self._ask
        self._notify = notify if notify is not None else self._show_message
        self._get_current_az = get_current_az
        self._get_long_path = get_long_path
        self._settings = settings
        self._step_km = step
        self._rubber_band: QgsRubberBand | None = None
        #: ``(azimuth, bearing, distance_km, point)`` of the last target sent, or ``None``.
        self.last_target: tuple[float, float, float, QgsPointXY] | None = None
        self.setCursor(QCursor(compat.CURSOR_CROSS))

    # ------------------------------------------------------------------ Qt / QGIS overrides

    def canvasReleaseEvent(self, event: Any) -> None:  # noqa: N802 (QGIS override)
        """Left click: aim at the point. Right click: remove the beam line."""
        try:
            button = event.button()
            point = event.mapPoint()
            self.canvasClicked.emit(point, button)
            if button == Qt.MouseButton.LeftButton:
                self.aim_at(point)
            elif button == Qt.MouseButton.RightButton:
                self.clear_beam()
        except Exception:
            _log_error()

    def deactivate(self) -> None:
        """Remove the beam line when another map tool takes over."""
        try:
            self.clear_beam()
        except Exception:
            _log_error()
        super().deactivate()

    # ------------------------------------------------------------------ aiming

    def aim_at(
        self, point: QgsPointXY, crs: QgsCoordinateReferenceSystem | None = None
    ) -> float | None:
        """Turn toward ``point`` (in ``crs``, default the canvas CRS).

        Returns the azimuth handed to ``on_target``, or ``None`` when nothing was sent
        (the reason was passed to ``notify``, or the user declined the confirmation).
        """
        station = self._station()
        if station is None:
            self._fail(tr("Set your QTH locator in Settings to turn the antenna from the map."))
            return None
        target = self._to_wgs84(point, crs)
        if target is None:
            self._fail(tr("The clicked point could not be converted to latitude and longitude."))
            return None
        lat1, lon1 = station
        lat2, lon2 = target.y(), target.x()
        distance = geo.distance_km(lat1, lon1, lat2, lon2)
        if distance < MIN_DISTANCE_KM:
            self._fail(tr("This point is at your QTH; click farther away."))
            return None
        if geo.is_antipodal(lat1, lon1, lat2, lon2):
            self._fail(
                tr("This point is at the antipode of your QTH: every direction leads there.")
            )
            return None
        bearing = geo.bearing_deg(lat1, lon1, lat2, lon2)
        long_path = self._long_path()
        if long_path:
            distance, bearing = geo.long_path(distance, bearing)

        limits = self._range()
        if limits is None:
            return None
        min_az, max_az = limits
        azimuth = hamlib.rotator_target(bearing, min_az, max_az, self._current_az())
        if azimuth is None:
            self._fail(
                tr(
                    "The rotator cannot turn to azimuth {azimuth}: its range is "
                    "{minimum}° to {maximum}°."
                ).format(
                    azimuth=format_bearing(bearing),
                    minimum=format_number(min_az, 0),
                    maximum=format_number(max_az, 0),
                )
            )
            return None

        self._draw_beam(station, (lat2, lon2), long_path)
        if not self._confirmed(azimuth, distance, long_path):
            self.clear_beam()
            return None
        self.last_target = (azimuth, bearing, distance, target)
        try:
            self._on_target(azimuth, bearing, distance, target)
        except Exception:
            _log_error()
        return azimuth

    def _fail(self, message: str) -> None:
        self.clear_beam()
        self._notify(message)

    def _station(self) -> tuple[float, float] | None:
        latlon = self._get_station_latlon()
        try:
            lat, lon = (_finite(value) for value in latlon)
        except (TypeError, ValueError):  # None, or not a (lat, lon) pair
            return None
        if lat is None or lon is None or not -90.0 <= lat <= 90.0:
            return None
        return lat, lon

    def _range(self) -> tuple[float, float] | None:
        limits = self._get_rotator_range()
        values = tuple(limits) if isinstance(limits, (tuple, list)) else ()
        numbers = tuple(_finite(value) for value in values)
        if len(numbers) == 2 and None not in numbers and numbers[0] < numbers[1]:
            return numbers[0], numbers[1]
        shown = [format_number(value, 0) if value is not None else "?" for value in numbers[:2]]
        shown += ["?"] * (2 - len(shown))
        self._fail(
            tr(
                "The rotator range {minimum}° to {maximum}° is not valid; check it in Settings."
            ).format(minimum=shown[0], maximum=shown[1])
        )
        return None

    def _current_az(self) -> float | None:
        if self._get_current_az is None:
            return None
        return _finite(self._get_current_az())

    def _long_path(self) -> bool:
        return bool(self._get_long_path()) if self._get_long_path is not None else False

    def _confirmed(self, azimuth: float, distance_km: float, long_path: bool) -> bool:
        settings = self._settings
        if settings is None:
            from ..settings import HamQSettings

            settings = self._settings = HamQSettings()
        if settings.rot_confirmed:
            return True
        if long_path:
            text = tr(
                "Turn the antenna to azimuth {azimuth} (long path, {distance})? HamQ asks "
                "only this first time; later clicks on the map turn the rotator right away."
            )
        else:
            text = tr(
                "Turn the antenna to azimuth {azimuth} ({distance})? HamQ asks only this "
                "first time; later clicks on the map turn the rotator right away."
            )
        message = text.format(
            azimuth=format_azimuth(azimuth), distance=format_distance(distance_km)
        )
        if not self._confirm(message):
            return False
        settings.rot_confirmed = True
        return True

    # ------------------------------------------------------------------ coordinates

    def _canvas_crs(self) -> QgsCoordinateReferenceSystem:
        crs = self._canvas.mapSettings().destinationCrs()
        return crs if crs.isValid() else QgsCoordinateReferenceSystem("EPSG:4326")

    def _transform(
        self, source: QgsCoordinateReferenceSystem, destination: QgsCoordinateReferenceSystem
    ) -> QgsCoordinateTransform:
        return QgsCoordinateTransform(source, destination, QgsProject.instance().transformContext())

    def _to_wgs84(
        self, point: QgsPointXY, crs: QgsCoordinateReferenceSystem | None
    ) -> QgsPointXY | None:
        source = crs if crs is not None and crs.isValid() else self._canvas_crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        try:
            result = QgsPointXY(point)
            if source != wgs84:
                result = self._transform(source, wgs84).transform(result)
        except QgsCsException:
            return None
        lon, lat = _finite(result.x()), _finite(result.y())
        if lon is None or lat is None or not -90.0 <= lat <= 90.0:
            return None
        if not -180.0 <= lon <= 180.0:  # a click beyond the edge of a world map
            lon = math.remainder(lon, 360.0)
        return QgsPointXY(lon, lat)

    # ------------------------------------------------------------------ beam line

    def _draw_beam(
        self, start: tuple[float, float], end: tuple[float, float], long_path: bool = False
    ) -> None:
        try:
            if long_path:
                parts = long_path_parts(start[0], start[1], end[0], end[1], self._step_km)
            else:
                parts = geo.great_circle(start[0], start[1], end[0], end[1], self._step_km)
        except ValueError:  # e.g. a step_km far too small: no beam line, still turn
            _log_error()
            parts = []
        transform = self._transform(QgsCoordinateReferenceSystem("EPSG:4326"), self._canvas_crs())
        lines = []
        for part in parts:
            line = []
            for lat, lon in part:
                try:
                    xy = transform.transform(QgsPointXY(lon, lat))
                except QgsCsException:
                    continue
                if _finite(xy.x()) is not None and _finite(xy.y()) is not None:
                    line.append(xy)
            if len(line) >= 2:
                lines.append(line)
        if not lines:
            self.clear_beam()
            return
        band = self._beam()
        band.setToGeometry(QgsGeometry.fromMultiPolylineXY(lines))
        band.show()

    def _beam(self) -> QgsRubberBand:
        if self._rubber_band is None:
            band = QgsRubberBand(self._canvas, compat.GEOMETRY_LINE)
            band.setColor(_BEAM_COLOR)
            band.setWidth(_BEAM_WIDTH)
            self._rubber_band = band
        return self._rubber_band

    def beam_geometry(self) -> QgsGeometry | None:
        """The beam line in canvas coordinates (a MultiLineString), ``None`` when none is shown."""
        band = self._rubber_band
        if band is None:
            return None
        try:
            geometry = band.asGeometry()
        except RuntimeError:  # the canvas (and the band with it) is gone
            self._rubber_band = None
            return None
        return None if geometry.isEmpty() else geometry

    def clear_beam(self) -> None:
        """Remove the beam line from the map (the rubber band is kept for the next click)."""
        if self._rubber_band is not None:
            try:
                self._rubber_band.reset(compat.GEOMETRY_LINE)
            except RuntimeError:  # the canvas (and the band with it) is gone
                self._rubber_band = None

    # ------------------------------------------------------------------ teardown and defaults

    def cleanup(self) -> None:
        """Unset the tool and remove the rubber band from the canvas. Safe to call twice."""
        try:
            if self._canvas.mapTool() is self:
                self._canvas.unsetMapTool(self)
        except RuntimeError:
            pass
        band, self._rubber_band = self._rubber_band, None
        if band is not None:
            try:
                band.reset(compat.GEOMETRY_LINE)
                scene = self._canvas.scene()
                if scene is not None:
                    scene.removeItem(band)
            except RuntimeError:
                pass

    def _ask(self, message: str) -> bool:
        answer = QMessageBox.question(
            self._canvas.window(),
            tr("Turn the rotator"),
            message,
            compat.MSGBOX_YES | compat.MSGBOX_NO,
            compat.MSGBOX_NO,
        )
        return answer == compat.MSGBOX_YES

    def _show_message(self, message: str) -> None:
        QgsMessageLog.logMessage(message, LOG_TAG, compat.MSG_INFO)
        try:
            from qgis.utils import iface
        except ImportError:  # pragma: no cover - qgis.utils ships with QGIS
            return
        if iface is not None:
            iface.messageBar().pushMessage("HamQ", message, compat.MSG_WARNING, 6)
