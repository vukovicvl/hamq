"""Processing algorithm ``hamq:maidenhead_grid``: the Maidenhead grid as polygons.

The ``EXTENT`` is converted to EPSG:4326 with the transform context of the run and
clamped to the world (:func:`extent_to_wgs84`), then grown outward to whole cells
of the chosen ``LEVEL`` (field, square, subsquare or extended square). Each cell
becomes a polygon (EPSG:4326) with the text field ``locator``. Grids larger than
``core.maidenhead.MAX_GRID_CELLS`` are refused with a message that suggests a smaller
extent or a coarser level. A grid layer that Processing loads into the project gets
the HamQ grid style (``qgis_io.styles``, locator labels where they fit).

Extents in other CRSs are converted point by point, not with
``QgsCoordinateTransform.transformBoundingBox``, whose result differs between QGIS
versions: QGIS 3.34 returns a box with ``xMinimum > xMaximum`` for a world-wide Web
Mercator extent, and no version tells an extent across the antimeridian from one
around the world without the crossover flag. Here longitudes are followed along the
edges of the extent, so:

- an extent across the antimeridian (a map centered on the Pacific) gives two parts,
  west and east of 180 degrees, and one that wraps around the world gives all
  longitudes (as QGIS 3.44 and 4.x transform it);
- an extent around a pole (a polar map) covers all longitudes up to the pole;
- points that cannot be transformed (beyond the antipode of an azimuthal map) are
  skipped; the poles are tested directly then.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Iterator
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsRectangle,
    QgsVectorLayer,
)

from ..core import maidenhead
from ..core.i18n import tr, tr_noop
from ..qgis_io import compat, styles
from ..qgis_io.fields import make_fields
from .common import GROUP_MAIDENHEAD, HamQAlgorithm, log, split_tags

__all__ = [
    "GRID_FIELDS",
    "LEVELS",
    "LEVEL_NAMES",
    "MaidenheadGridAlgorithm",
    "extent_to_wgs84",
    "grid_styler",
]

#: Locator lengths of the ``LEVEL`` choices, in order.
LEVELS = (
    maidenhead.LEVEL_FIELD,
    maidenhead.LEVEL_SQUARE,
    maidenhead.LEVEL_SUBSQUARE,
    maidenhead.LEVEL_EXTENDED,
)
#: Names of the ``LEVEL`` choices (translated when the parameter is created).
LEVEL_NAMES = (
    tr_noop("Field (2 characters, 20° x 10°)"),
    tr_noop("Square (4 characters, 2° x 1°)"),
    tr_noop("Subsquare (6 characters, 5' x 2.5')"),
    tr_noop('Extended square (8 characters, 30" x 15")'),
)
#: Fields of the grid cells as ``(name, kind)``.
GRID_FIELDS: tuple[tuple[str, str], ...] = (("locator", "text"),)

_WGS84 = "EPSG:4326"
_EDGE_SAMPLES = 64  # points per edge of a projected extent
_INTERIOR_SAMPLES = 9  # points per axis inside it (for the latitude range)
_NEAR_POLE = 89.9  # latitude of the points around a pole that must be inside with it
_BATCH = 2000  # features per addFeatures call

Part = tuple[float, float, float, float]  # (lat_min, lon_min, lat_max, lon_max)


# --- extent -------------------------------------------------------------------------------------


def extent_to_wgs84(
    rect: QgsRectangle, crs: QgsCoordinateReferenceSystem, transform_context: Any
) -> list[Part]:
    """``rect`` (in ``crs``) as EPSG:4326 parts ``(lat_min, lon_min, lat_max, lon_max)``.

    - EPSG:4326, a geographic CRS or no CRS (an extent typed without one in a project
      without a CRS): the values are degrees and are only clamped to the world.
    - Other CRSs: see the module documentation. The result is clamped to the world;
      an extent across the antimeridian gives two parts.

    Returns ``[]`` when the extent lies wholly outside the world or none of its points
    can be transformed.
    """
    xmin, ymin = rect.xMinimum(), rect.yMinimum()
    xmax, ymax = rect.xMaximum(), rect.yMaximum()
    if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)):
        return []
    geographic = not crs.isValid() or crs.isGeographic()
    if geographic:
        if xmin > 180.0 or xmax < -180.0 or ymin > 90.0 or ymax < -90.0:
            return []
        xmin, xmax = max(xmin, -180.0), min(xmax, 180.0)
        ymin, ymax = max(ymin, -90.0), min(ymax, 90.0)
        if not crs.isValid() or crs.authid() == _WGS84:
            return [(ymin, xmin, ymax, xmax)]

    wgs84 = QgsCoordinateReferenceSystem(_WGS84)
    to_wgs84 = QgsCoordinateTransform(crs, wgs84, transform_context)
    ring = [_transformed(to_wgs84, x, y) for x, y in _ring(xmin, ymin, xmax, ymax)]
    inner = [_transformed(to_wgs84, x, y) for x, y in _interior(xmin, ymin, xmax, ymax)]
    valid = [point for point in ring + inner if point is not None]
    if not valid:
        return []
    lat_min = max(min(lat for _, lat in valid), -90.0)
    lat_max = min(max(lat for _, lat in valid), 90.0)

    lon_range: tuple[float, float] | None = None
    poles = _poles_inside(crs, wgs84, transform_context, xmin, ymin, xmax, ymax)
    if poles:
        pass  # all longitudes meet at a pole
    elif all(point is not None for point in ring):
        winding, low, high = _unwrap([point[0] for point in ring if point is not None])
        if abs(winding) > 180.0:  # the edges go once around a pole close to an edge
            mean = sum(lat for _, lat in valid) / len(valid)
            poles = {90.0 if mean >= 0.0 else -90.0}
        else:
            lon_range = (low, high)
    else:
        lon_range = _covering_arc([lon for lon, _ in valid])
    if 90.0 in poles:
        lat_max = 90.0
    if -90.0 in poles:
        lat_min = -90.0
    if lon_range is None:
        lon_range = (-180.0, 180.0)
    return _split(lat_min, lat_max, *lon_range)


def _transformed(
    transform: QgsCoordinateTransform, x: float, y: float
) -> tuple[float, float] | None:
    """``(x, y)`` transformed; ``None`` when it fails or gives no finite position."""
    try:
        point = transform.transform(QgsPointXY(x, y))
    except QgsCsException:
        return None
    px, py = point.x(), point.y()
    if not (math.isfinite(px) and math.isfinite(py)):
        return None
    return px, py


def _ring(xmin: float, ymin: float, xmax: float, ymax: float) -> list[tuple[float, float]]:
    """Points along the edges, in order around the rectangle (back to the first one)."""
    n = _EDGE_SAMPLES
    dx, dy = (xmax - xmin) / n, (ymax - ymin) / n
    bottom = [(xmin + i * dx, ymin) for i in range(n)]
    right = [(xmax, ymin + i * dy) for i in range(n)]
    top = [(xmax - i * dx, ymax) for i in range(n)]
    left = [(xmin, ymax - i * dy) for i in range(n)]
    return bottom + right + top + left


def _interior(xmin: float, ymin: float, xmax: float, ymax: float) -> Iterator[tuple[float, float]]:
    n = _INTERIOR_SAMPLES
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            yield xmin + (xmax - xmin) * i / (n + 1), ymin + (ymax - ymin) * j / (n + 1)


def _unwrap(lons: list[float]) -> tuple[float, float, float]:
    """Follow the longitudes around the closed ring: ``(winding, lowest, highest)``.

    Each step is taken the short way (at most 180 degrees), so crossing the antimeridian
    continues past 180 instead of jumping back. ``winding`` is the total change around
    the ring: about +-360 when the ring goes around a pole, about 0 otherwise.
    """
    current = low = high = lons[0]
    previous = lons[0]
    winding = 0.0
    for lon in [*lons[1:], lons[0]]:
        step = (lon - previous + 180.0) % 360.0 - 180.0
        previous = lon
        current += step
        winding += step
        low, high = min(low, current), max(high, current)
    return winding, low, high


def _covering_arc(lons: Iterable[float]) -> tuple[float, float]:
    """The shortest range of longitudes ``(start, end)`` that holds all ``lons``; ``end``
    may exceed 180 (the range then crosses the antimeridian)."""
    ordered = sorted(((lon + 180.0) % 360.0) - 180.0 for lon in lons)
    if len(ordered) == 1:
        return ordered[0], ordered[0]
    # the largest gap between neighbours (the last one wraps around) is left out
    gaps = [(ordered[i + 1] - ordered[i], i + 1) for i in range(len(ordered) - 1)]
    gaps.append((ordered[0] + 360.0 - ordered[-1], 0))
    _, start = max(gaps)
    first = ordered[start]
    last = ordered[start - 1] if start else ordered[-1]
    return first, last if last >= first else last + 360.0


def _poles_inside(
    crs: QgsCoordinateReferenceSystem,
    wgs84: QgsCoordinateReferenceSystem,
    transform_context: Any,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
) -> set[float]:
    """The latitudes (90, -90) of the poles that lie inside the extent.

    A pole counts when it and four points around it (latitude 89.9, every 90 degrees of
    longitude) are inside: in a cylindrical projection the pole is a line, and a point of
    that line inside the extent does not make every longitude part of it.
    """
    from_wgs84 = QgsCoordinateTransform(wgs84, crs, transform_context)
    found = set()
    for pole in (90.0, -90.0):
        near = math.copysign(_NEAR_POLE, pole)
        points = [(0.0, pole)] + [(lon, near) for lon in (-180.0, -90.0, 0.0, 90.0)]
        inside = True
        for lon, lat in points:
            point = _transformed(from_wgs84, lon, lat)
            if point is None or not (xmin <= point[0] <= xmax and ymin <= point[1] <= ymax):
                inside = False
                break
        if inside:
            found.add(pole)
    return found


def _split(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> list[Part]:
    """Parts of the extent in ``[-180, 180]``; a range past 180 is split at the antimeridian."""
    if lon_max - lon_min >= 360.0:
        return [(lat_min, -180.0, lat_max, 180.0)]
    shift = math.floor((lon_min + 180.0) / 360.0) * 360.0
    lon_min, lon_max = lon_min - shift, lon_max - shift
    if lon_max <= 180.0:
        return [(lat_min, lon_min, lat_max, lon_max)]
    return [(lat_min, lon_min, lat_max, 180.0), (lat_min, -180.0, lat_max, lon_max - 360.0)]


# --- style of the loaded layer ---------------------------------------------------------------


class _GridStyler(QgsProcessingLayerPostProcessorInterface):
    """Gives a grid layer loaded by Processing the default HamQ grid style."""

    def postProcessLayer(self, layer: Any, context: Any, feedback: Any) -> None:
        try:
            if isinstance(layer, QgsVectorLayer) and layer.fields().indexOf("locator") >= 0:
                styles.apply_default_style(layer, "grid")
        except Exception as exc:  # called by Processing: never let an exception through
            log(tr("The grid style could not be applied: {error}").format(error=exc))


_styler: _GridStyler | None = None
_styler_lock = threading.Lock()


def grid_styler() -> QgsProcessingLayerPostProcessorInterface:
    """The post-processor that styles loaded grid layers (one for the whole session).

    ``QgsProcessingAlgorithm.run()`` works on a copy of the algorithm that is deleted
    before Processing loads the result layer, and the context keeps only a pointer to
    the post-processor: it must not belong to the algorithm object.
    """
    global _styler
    with _styler_lock:
        if _styler is None:
            _styler = _GridStyler()
        return _styler


# --- algorithm ----------------------------------------------------------------------------------


class MaidenheadGridAlgorithm(HamQAlgorithm):
    """Maidenhead grid cells (polygons) for an extent and a level."""

    EXTENT = "EXTENT"
    LEVEL = "LEVEL"
    OUTPUT = "OUTPUT"
    ICON = "grid.svg"
    GROUP_ID = GROUP_MAIDENHEAD

    def name(self) -> str:
        return "maidenhead_grid"

    def displayName(self) -> str:
        return tr("Generate Maidenhead grid")

    def shortHelpString(self) -> str:
        return tr(
            "Generates the Maidenhead locator grid as polygons for an extent and a level: "
            "field (2 characters, 20° x 10°), square (4 characters, 2° x 1°), subsquare "
            "(6 characters, 5' x 2.5') or extended square (8 characters, 30\" x 15\"). The "
            "extent is converted to EPSG:4326 and grown to whole cells; an extent across "
            "the antimeridian is split there. Each cell has its locator in the field "
            "{field}. At most {limit} cells are generated: for a larger grid choose a "
            "smaller extent or a coarser level."
        ).format(field="locator", limit=maidenhead.MAX_GRID_CELLS)

    def tags(self) -> list[str]:
        return split_tags(tr("Maidenhead, locator, grid, QTH, square, ham radio"))

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, tr("Extent")))
        self.addParameter(
            QgsProcessingParameterEnum(
                self.LEVEL,
                tr("Level"),
                options=[tr(name) for name in LEVEL_NAMES],
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, tr("Maidenhead grid"), type=compat.SOURCE_VECTOR_POLYGON
            )
        )

    def processAlgorithm(
        self, parameters: dict[str, Any], context: Any, feedback: Any
    ) -> dict[str, Any]:
        self._dest_id = None
        index = self.parameterAsEnum(parameters, self.LEVEL, context)
        if not 0 <= index < len(LEVELS):
            raise QgsProcessingException(tr("Unknown grid level {level}").format(level=index))
        level = LEVELS[index]
        rect = self.parameterAsExtent(parameters, self.EXTENT, context)
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        parts = extent_to_wgs84(rect, crs, context.transformContext())
        count = sum(maidenhead.count_cells(*part, level) for part in parts)
        if not count:
            raise QgsProcessingException(
                tr("The extent lies outside the world or cannot be converted to EPSG:4326")
            )
        if count > maidenhead.MAX_GRID_CELLS:
            raise QgsProcessingException(
                tr(
                    "The grid would have {count} cells, more than the limit of {limit}. "
                    "Choose a smaller extent or a coarser level."
                ).format(count=count, limit=maidenhead.MAX_GRID_CELLS)
            )
        for lat_min, lon_min, lat_max, lon_max in parts:
            feedback.pushInfo(
                tr(
                    "Extent in EPSG:4326: longitude {west} to {east}, latitude {south} to {north}"
                ).format(
                    west=_degrees(lon_min),
                    east=_degrees(lon_max),
                    south=_degrees(lat_min),
                    north=_degrees(lat_max),
                )
            )
        feedback.pushInfo(tr("Grid cells: {count}").format(count=count))

        fields = make_fields(GRID_FIELDS)
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            compat.WKB_POLYGON,
            QgsCoordinateReferenceSystem(_WGS84),
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        written = 0
        batch: list[QgsFeature] = []
        for part in parts:
            for locator, (south, west, north, east) in maidenhead.iter_cells(*part, level):
                feature = QgsFeature(fields)
                feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(west, south, east, north)))
                feature.setAttributes([locator])
                batch.append(feature)
                if len(batch) >= _BATCH:
                    if feedback.isCanceled():
                        return {self.OUTPUT: dest_id}
                    written += self._write(sink, batch, parameters)
                    batch = []
                    feedback.setProgress(100.0 * written / count)
        if batch and not feedback.isCanceled():
            written += self._write(sink, batch, parameters)
        feedback.setProgress(100.0)
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def postProcessAlgorithm(self, context: Any, feedback: Any) -> dict[str, Any]:
        dest_id = getattr(self, "_dest_id", None)
        if dest_id and context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(grid_styler())
        return {}

    def _write(self, sink: Any, batch: list[QgsFeature], parameters: dict[str, Any]) -> int:
        if not sink.addFeatures(batch, compat.SINK_FAST_INSERT):
            raise QgsProcessingException(self.writeFeatureError(sink, parameters, self.OUTPUT))
        return len(batch)


def _degrees(value: float) -> str:
    """``12.5`` -> ``'12.5°'`` (at most 6 decimals, no trailing zeros)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return ("0" if text == "-0" else text) + "°"
