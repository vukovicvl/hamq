"""Azimuthal equidistant map centred on my QTH (the ham radio "great circle map").

:meth:`AzimuthalMap.enable` remembers the project CRS (and the canvas extent), sets
the project CRS to ``core.geo.aeqd_proj`` of the centre of my locator (straight
lines through the centre are great circles, distances from it are true, map units
are km) and adds a helper memory layer to the layer tree group "HamQ": distance
rings every 2500 km up to 20 000 km labelled "2500 km" ..., and azimuth lines every
30 degrees labelled "0°" ... . :meth:`AzimuthalMap.disable` restores the CRS and
the extent and removes the helper layer (and the "HamQ" group when it created the
group and nothing else is in it).

The map follows the project: when the project is cleared or another project is
opened the azimuthal map is simply off (nothing is restored into the new project);
when the user picks another project CRS, that CRS stays and the helper layer is
removed. A new locator saved in the settings re-centres an enabled map.
:attr:`AzimuthalMap.enabledChanged` reports every change, e.g. to keep a checkable
action in sync. :meth:`AzimuthalMap.cleanup` disables the map and disconnects; it
is safe to call twice.

The helper is a memory layer that HamQ can always make again, so it carries the custom
property ``skipMemoryLayersCheck``: QGIS then does not warn that "temporary scratch
layers" will be lost when QGIS quits or another project is opened. A project saved
while the map is on keeps the helper layer but not its features; the helper also
carries the map's state (:data:`STATE_PROPERTY`: locator, the CRS and view to restore),
so opening that project switches the map on again: the rings and lines are drawn
again and switching off restores the CRS and view from before the map.
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import traceback
from collections.abc import Callable, Iterator
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsMemoryProviderUtils,
    QgsMessageLog,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtGui import QColor

from ..core import maidenhead
from ..core.geo import aeqd_proj
from ..core.i18n import tr
from ..events import events
from ..qgis_io.compat import LABEL_PLACEMENT_LINE, MSG_CRITICAL, MSG_WARNING, WKB_LINESTRING
from ..qgis_io.fields import make_fields
from ..settings import HamQSettings

__all__ = [
    "AZIMUTH_KIND",
    "AZIMUTH_STEP_DEG",
    "GROUP_NAME",
    "RING_KIND",
    "RING_MAX_KM",
    "RING_STEP_KM",
    "STATE_PROPERTY",
    "AzimuthalMap",
    "azimuths",
    "ring_distances",
]

LOG_TAG = "HamQ"
#: Layer tree group of the HamQ layers (a product name: not translated).
GROUP_NAME = "HamQ"
#: Distance between the distance rings, km.
RING_STEP_KM = 2500
#: Largest distance ring, km (the antipode is about 20 000 km away).
RING_MAX_KM = 20000
#: Angle between the azimuth lines, degrees.
AZIMUTH_STEP_DEG = 30
#: Values of the ``kind`` field of the helper layer.
RING_KIND, AZIMUTH_KIND = "ring", "azimuth"
#: Fields of the helper layer.
HELPER_FIELDS = (("kind", "text"), ("value", "real"), ("label", "text"))
#: Seconds the "set a valid locator" warning stays in the message bar.
MESSAGE_SECONDS = 8
#: Custom property of the helper layer: JSON ``{"locator", "restore_crs",
#: "restore_extent", "created_group"}``, saved with a project, read when it is opened.
STATE_PROPERTY = "hamq/azimuthal"
#: Custom property QGIS reads before closing a project: no "scratch layers will be
#: lost" question for a memory layer that has it (HamQ makes the helper again).
SKIP_MEMORY_CHECK = "skipMemoryLayersCheck"
_RING_VERTICES = 360  # one vertex per degree: a smooth circle at every scale
_LINE_COLOR = "110,110,110,200"
_LABEL_COLOR = QColor(60, 60, 60)
_MARGIN = 1.05  # the world disc plus 5 %


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


def ring_distances() -> list[int]:
    """Distances of the rings in km: 2500, 5000, ..., 20000."""
    return list(range(RING_STEP_KM, RING_MAX_KM + 1, RING_STEP_KM))


def azimuths() -> list[int]:
    """Azimuths of the lines in degrees: 0, 30, ..., 330."""
    return list(range(0, 360, AZIMUTH_STEP_DEG))


def _polar(distance_km: float, azimuth_deg: float) -> QgsPointXY:
    """Point at ``distance_km`` and ``azimuth_deg`` from the centre, in aeqd km (north = +y)."""
    angle = math.radians(azimuth_deg)
    return QgsPointXY(distance_km * math.sin(angle), distance_km * math.cos(angle))


def _ring(distance_km: float) -> list[QgsPointXY]:
    points = [_polar(distance_km, 360.0 * i / _RING_VERTICES) for i in range(_RING_VERTICES)]
    points.append(QgsPointXY(points[0]))
    return points


def _helper_features(layer: QgsVectorLayer) -> Iterator[QgsFeature]:
    fields = layer.fields()
    for distance in ring_distances():
        feature = QgsFeature(fields)
        feature.setGeometry(QgsGeometry.fromPolylineXY(_ring(distance)))
        # numbers and the unit symbol read the same in every HamQ language
        feature.setAttributes([RING_KIND, float(distance), f"{distance} km"])
        yield feature
    for azimuth in azimuths():
        feature = QgsFeature(fields)
        line = [QgsPointXY(0.0, 0.0), _polar(RING_MAX_KM, azimuth)]
        feature.setGeometry(QgsGeometry.fromPolylineXY(line))
        feature.setAttributes([AZIMUTH_KIND, float(azimuth), f"{azimuth}°"])
        yield feature


def _style(layer: QgsVectorLayer) -> None:
    symbol = QgsLineSymbol.createSimple(
        {"line_color": _LINE_COLOR, "line_width": "0.26", "line_style": "dash"}
    )
    if symbol is not None:
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor(255, 255, 255))
    text_format = QgsTextFormat()
    text_format.setSize(8)
    text_format.setColor(_LABEL_COLOR)
    text_format.setBuffer(buffer)
    labels = QgsPalLayerSettings()
    labels.fieldName = "label"
    labels.placement = LABEL_PLACEMENT_LINE
    labels.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(labels))
    layer.setLabelsEnabled(True)


def _crs_text(crs: QgsCoordinateReferenceSystem | None) -> str:
    """A CRS for the saved state: the id of a registered CRS (``EPSG:3857``), else its
    WKT (a user CRS id means nothing on another computer); ``""`` for no CRS."""
    if crs is None or not crs.isValid():
        return ""
    authid = crs.authid()
    if authid and not authid.upper().startswith("USER:"):
        return authid
    return crs.toWkt()


def _crs_from_text(text: object) -> QgsCoordinateReferenceSystem | None:
    """Inverse of :func:`_crs_text`; an invalid CRS for ``""``, None when unreadable."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return QgsCoordinateReferenceSystem()  # the project had no CRS
    if "[" in text:
        crs = QgsCoordinateReferenceSystem.fromWkt(text)
    else:
        crs = QgsCoordinateReferenceSystem(text)
    return crs if crs.isValid() else None


def _state_of(layer: QgsVectorLayer) -> dict[str, Any]:
    """The JSON state (:data:`STATE_PROPERTY`) of a saved helper, ``{}`` when unreadable."""
    try:
        state = json.loads(layer.customProperty(STATE_PROPERTY))
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _extent_list(extent: QgsRectangle | None) -> list[float] | None:
    if extent is None or extent.isEmpty() or not extent.isFinite():
        return None
    return [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]


def _extent_from_list(values: object) -> QgsRectangle | None:
    if not isinstance(values, list) or len(values) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    numbers = [float(value) for value in values]
    if not all(math.isfinite(number) for number in numbers):
        return None
    extent = QgsRectangle(*numbers)
    return None if extent.isEmpty() else extent


class AzimuthalMap(QObject):
    """Switch the project to an azimuthal equidistant map centred on my QTH and back."""

    #: The map was switched on (True) or off (False), by a call or by the project.
    enabledChanged = pyqtSignal(bool)

    def __init__(
        self, iface: Any, settings: HamQSettings | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._iface = iface
        self._settings = settings if settings is not None else HamQSettings()
        self._enabled = False
        self._crs: QgsCoordinateReferenceSystem | None = None
        self._grid = ""
        self._saved_crs: QgsCoordinateReferenceSystem | None = None
        self._saved_canvas_crs: QgsCoordinateReferenceSystem | None = None
        self._saved_extent: QgsRectangle | None = None
        self._layer_id: str | None = None
        self._created_group = False
        self._changing_crs = False
        self._connections: list[tuple[Any, Callable[..., Any]]] = []
        project = QgsProject.instance()
        for signal, slot in (
            (project.cleared, self._on_project_cleared),
            (project.readProject, self._on_project_read),
            (project.crsChanged, self._on_crs_changed),
            (events().settingsChanged, self._on_settings_changed),
            (events().languageChanged, self._on_language_changed),
        ):
            signal.connect(slot)
            self._connections.append((signal, slot))

    # ------------------------------------------------------------------ public

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    def is_enabled(self) -> bool:
        """True while the project shows the azimuthal map."""
        return self._enabled

    def center_locator(self) -> str:
        """Locator the map is centred on, ``""`` when the map is off."""
        return self._grid

    def crs(self) -> QgsCoordinateReferenceSystem | None:
        """The azimuthal CRS in use, None when the map is off."""
        return QgsCoordinateReferenceSystem(self._crs) if self._crs is not None else None

    def helper_layer(self) -> QgsVectorLayer | None:
        """The helper layer (rings and azimuth lines), None when it is not in the project."""
        if self._layer_id is None:
            return None
        layer = QgsProject.instance().mapLayer(self._layer_id)
        return layer if isinstance(layer, QgsVectorLayer) else None

    def enable(self) -> bool:
        """Show the azimuthal map centred on my locator; True on success.

        Without a valid ``my_grid`` a translated warning goes to the message bar and
        nothing changes (False). While the map is on, calling this again re-centres
        it on the current locator.
        """
        was_enabled = self._enabled
        try:
            return self._enable()
        except Exception as exc:  # never leave the project half switched
            _log_error("AzimuthalMap.enable", exc)
            if not was_enabled:
                try:
                    self._restore()
                except Exception as restore_exc:  # restored what could be; the map is off
                    _log_error("AzimuthalMap.enable", restore_exc)
                    self._forget()
            return False

    def disable(self) -> None:
        """Restore the previous CRS and extent and remove the helper layer. Safe twice."""
        if not self._enabled:
            return
        try:
            self._restore()
        except Exception as exc:
            _log_error("AzimuthalMap.disable", exc)
            self._forget()
        self.enabledChanged.emit(False)

    @_guarded
    def set_enabled(self, enabled: bool) -> bool:
        """Switch on or off (a slot for a checkable action); returns the new state.

        When the map cannot be switched on (no valid locator), :attr:`enabledChanged`
        reports the unchanged state anyway, so that a checkable action connected to it
        unchecks itself again.
        """
        wanted = bool(enabled)
        if wanted:
            self.enable()
        else:
            self.disable()
        if self._enabled != wanted:
            self.enabledChanged.emit(self._enabled)
        return self._enabled

    def retranslate(self) -> None:
        """Rename the helper layer in the current HamQ language."""
        layer = self.helper_layer()
        if layer is not None:
            layer.setName(self._layer_name(self._grid))

    def cleanup(self) -> None:
        """Disable the map and disconnect from the project and ``events()``. Safe twice."""
        self.disable()
        while self._connections:
            signal, slot = self._connections.pop()
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)

    # ------------------------------------------------------------------ switching

    def _center(self) -> tuple[float, float, str] | None:
        """``(lat, lon, locator)`` of my locator's cell centre, None without a valid locator."""
        try:
            locator = maidenhead.normalize(self._settings.my_grid)
        except ValueError:
            return None
        lat, lon = maidenhead.to_latlon(locator)
        return lat, lon, locator

    def _enable(self) -> bool:
        center = self._center()
        if center is None:
            self._warn(
                self.tr("Set a valid QTH locator in the HamQ settings to use the azimuthal map.")
            )
            return False
        lat, lon, locator = center
        crs = QgsCoordinateReferenceSystem.fromProj(aeqd_proj(lat, lon))
        if not crs.isValid():
            self._warn(
                self.tr("The azimuthal projection for {locator} could not be created.").format(
                    locator=locator
                )
            )
            return False
        was_enabled = self._enabled
        project = QgsProject.instance()
        canvas = self._iface.mapCanvas()
        if not was_enabled:
            self._saved_crs = QgsCoordinateReferenceSystem(project.crs())
            if canvas is not None:
                self._saved_canvas_crs = QgsCoordinateReferenceSystem(
                    canvas.mapSettings().destinationCrs()
                )
                self._saved_extent = QgsRectangle(canvas.extent())
        self._remove_layer()
        self._set_crs(crs, canvas)
        self._crs = crs
        self._grid = locator
        self._enabled = True
        self._add_layer(self._build_layer(crs, locator))
        if canvas is not None:
            radius = RING_MAX_KM * _MARGIN
            canvas.setExtent(QgsRectangle(-radius, -radius, radius, radius))
            canvas.refresh()
        if not was_enabled:
            self.enabledChanged.emit(True)
        return True

    def _set_crs(self, crs: QgsCoordinateReferenceSystem, canvas: Any) -> None:
        """Set the project CRS (and the canvas CRS, which QGIS itself keeps equal)."""
        self._changing_crs = True
        try:
            if crs.isValid() or QgsProject.instance().crs().isValid():
                QgsProject.instance().setCrs(crs)
            if canvas is not None and crs.isValid():
                if canvas.mapSettings().destinationCrs() != crs:
                    canvas.setDestinationCrs(crs)
        finally:
            self._changing_crs = False

    def _restore(self) -> None:
        """Forget the state, remove the helper layer, restore the saved CRS and extent.

        The CRS is restored even when removing the layer fails (the error is raised after).
        """
        saved_crs = self._saved_crs
        canvas_crs = self._saved_canvas_crs
        extent = self._saved_extent
        self._forget()
        try:
            self._remove_layer()
        finally:
            self._restore_view(saved_crs, canvas_crs, extent)

    def _restore_view(
        self,
        saved_crs: QgsCoordinateReferenceSystem | None,
        canvas_crs: QgsCoordinateReferenceSystem | None,
        extent: QgsRectangle | None,
    ) -> None:
        if saved_crs is not None:
            self._set_crs(saved_crs, None)
        canvas = self._iface.mapCanvas()
        if canvas is None:
            return
        if canvas_crs is not None and canvas_crs.isValid():
            self._changing_crs = True
            try:
                if canvas.mapSettings().destinationCrs() != canvas_crs:
                    canvas.setDestinationCrs(canvas_crs)
            finally:
                self._changing_crs = False
        if extent is not None and not extent.isEmpty() and extent.isFinite():
            canvas.setExtent(extent)
        canvas.refresh()

    def _forget(self) -> None:
        """Drop the state without touching the project (it was cleared or changed by the user)."""
        self._enabled = False
        self._crs = None
        self._grid = ""
        self._saved_crs = None
        self._saved_canvas_crs = None
        self._saved_extent = None

    # ------------------------------------------------------------------ helper layer

    def _layer_name(self, locator: str) -> str:
        return self.tr("Azimuthal map grid ({locator})").format(locator=locator)

    def _build_layer(self, crs: QgsCoordinateReferenceSystem, locator: str) -> QgsVectorLayer:
        layer = QgsMemoryProviderUtils.createMemoryLayer(
            self._layer_name(locator), make_fields(HELPER_FIELDS), WKB_LINESTRING, crs
        )
        if layer is None or not layer.isValid():
            raise RuntimeError("the helper memory layer could not be created")
        layer.dataProvider().addFeatures(list(_helper_features(layer)))
        layer.updateExtents()
        _style(layer)
        layer.setCustomProperty(SKIP_MEMORY_CHECK, 1)
        return layer

    def _add_layer(self, layer: QgsVectorLayer) -> None:
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME)
        if group is None:
            group = root.insertGroup(0, GROUP_NAME)
            self._created_group = True
        layer.setCustomProperty(STATE_PROPERTY, self._state_text())
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        self._layer_id = layer.id()

    def _state_text(self) -> str:
        """The map's state for :data:`STATE_PROPERTY` (saved with a project)."""
        state = {
            "locator": self._grid,
            "restore_crs": _crs_text(self._saved_crs),
            "restore_extent": _extent_list(self._saved_extent),
            "created_group": self._created_group,
        }
        return json.dumps(state, sort_keys=True)

    def _saved_state(self, layer: QgsVectorLayer) -> dict[str, Any] | None:
        """The state of a helper read from a project, None when the map cannot go on.

        The map goes on only when the project CRS is still the azimuthal CRS of the
        saved locator and the layer has the helper's fields.
        """
        state = _state_of(layer)
        try:
            locator = maidenhead.normalize(str(state.get("locator", "")))
        except ValueError:
            return None
        if [field.name() for field in layer.fields()] != [name for name, _ in HELPER_FIELDS]:
            return None
        crs = QgsCoordinateReferenceSystem.fromProj(aeqd_proj(*maidenhead.to_latlon(locator)))
        if not crs.isValid() or QgsProject.instance().crs() != crs:
            return None
        return {
            "locator": locator,
            "crs": crs,
            "restore_crs": _crs_from_text(state.get("restore_crs")),
            "restore_extent": _extent_from_list(state.get("restore_extent")),
            "created_group": state.get("created_group") is True,
        }

    def _take_saved_map(self) -> None:
        """Switch the map on again in a project that was saved with it.

        A project keeps a memory layer's fields but not its features: the helper is
        filled again. Other saved helpers that are empty (left over, e.g. the CRS was
        changed in a QGIS without HamQ) are removed; layers with features are kept.
        """
        project = QgsProject.instance()
        helpers = [
            layer
            for layer in project.mapLayers().values()
            if isinstance(layer, QgsVectorLayer)
            and layer.providerType() == "memory"
            and layer.customProperty(STATE_PROPERTY) is not None
            and layer.id() != self._layer_id
        ]
        taken: tuple[QgsVectorLayer, dict[str, Any]] | None = None
        if not self._enabled:
            for layer in helpers:
                state = self._saved_state(layer)
                if state is not None:
                    taken = (layer, state)
                    break
        created_group = False
        for layer in helpers:
            if (taken is None or layer is not taken[0]) and layer.featureCount() == 0:
                created_group |= _state_of(layer).get("created_group") is True
                project.removeMapLayer(layer.id())
        if created_group:
            self._remove_group_if_empty()
        if taken is None:
            return
        layer, state = taken
        if layer.featureCount() == 0:
            layer.dataProvider().addFeatures(list(_helper_features(layer)))
            layer.updateExtents()
        layer.setCustomProperty(SKIP_MEMORY_CHECK, 1)
        group = project.layerTreeRoot().findGroup(GROUP_NAME)
        restore_crs = state["restore_crs"]
        self._layer_id = layer.id()
        self._crs = state["crs"]
        self._grid = state["locator"]
        self._saved_crs = restore_crs
        self._saved_canvas_crs = (
            QgsCoordinateReferenceSystem(restore_crs) if restore_crs is not None else None
        )
        self._saved_extent = state["restore_extent"]
        self._created_group = state["created_group"] and (
            group is not None and group.findLayer(layer.id()) is not None
        )
        self._enabled = True
        layer.setName(self._layer_name(self._grid))  # maybe saved in another language
        self.enabledChanged.emit(True)

    def _remove_layer(self) -> None:
        layer_id, self._layer_id = self._layer_id, None
        project = QgsProject.instance()
        if layer_id is not None and project.mapLayer(layer_id) is not None:
            project.removeMapLayer(layer_id)
        if self._created_group:
            self._created_group = False
            self._remove_group_if_empty()

    @staticmethod
    def _remove_group_if_empty() -> None:
        group = QgsProject.instance().layerTreeRoot().findGroup(GROUP_NAME)
        if group is not None and not group.children():
            parent = group.parent()
            if parent is not None:
                parent.removeChildNode(group)

    def _warn(self, text: str) -> None:
        bar = self._iface.messageBar()
        if bar is not None:
            bar.pushMessage("HamQ", text, MSG_WARNING, MESSAGE_SECONDS)

    # ------------------------------------------------------------------ slots

    @_guarded
    def _on_project_cleared(self) -> None:
        # the layers are gone and a new project is starting: restore nothing into it
        was_enabled = self._enabled
        self._layer_id = None
        self._created_group = False
        self._forget()
        if was_enabled:
            self.enabledChanged.emit(False)

    @_guarded
    def _on_project_read(self, *_args: Any) -> None:
        self._take_saved_map()

    @_guarded
    def _on_crs_changed(self) -> None:
        if self._changing_crs or not self._enabled:
            return
        if self._crs is not None and QgsProject.instance().crs() == self._crs:
            return
        # the user picked another CRS: keep it and end the azimuthal map
        self._remove_layer()
        self._forget()
        self.enabledChanged.emit(False)

    @_guarded
    def _on_settings_changed(self) -> None:
        if not self._enabled:
            return
        center = self._center()
        if center is not None and center[2] != self._grid:
            self.enable()

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()
