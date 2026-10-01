"""Default styles of the HamQ layers: QSO points and paths coloured by band, the locator grid.

:func:`apply_default_style` styles a layer of one of the :data:`STYLE_KINDS`:

``qso``
    Points coloured by ``band`` (:data:`BAND_COLORS`) with a dark outline.
``qso_path``
    Semi-transparent lines coloured by ``band``.
``grid``
    Maidenhead grid cells: no fill, a thin grey outline and the locator as a label. Each
    label is shown only from the map scale on where it fits in its cell
    (:data:`GRID_LABEL_SCALES`), so one style serves fields, squares and subsquares.

The style is loaded from ``resources/styles/<kind>.qml`` when that file exists and builds
in code otherwise. The ``.qml`` files are generated from the code by
``scripts/make_styles.py`` on the oldest supported QGIS (3.34), so they load on every
version; only symbology and labels are loaded (field aliases, forms and the like of the
layer are never touched). The legend label of bands without a colour of their own is
translated when the style is applied and again by :func:`retranslate_style`; a legend
that shows the layer (the Layers panel) is told to read it again.

Band colours
------------
The 15 common bands get distinct colours taken from colour-blind-safe palettes (Okabe-Ito,
Paul Tol's bright / muted / vibrant / medium-contrast sets, IBM). The set was chosen with
the CIEDE2000 colour difference under normal vision and simulated protanopia,
deuteranopia and tritanopia (Machado et al. 2009, full severity): every two band colours
(and the grey of the other bands) stay at least 6.3 apart in all four; the busy bands
(160 m to 10 m without 60 m, and 6 m) and grey at least 8. Every colour is mid-light (CIELAB L* 42-72) with a
contrast of at least 2:1 against white, so lines stay visible on light base maps and
points keep their hue under the dark outline. Other bands are neutral grey.
"""

from __future__ import annotations

import os
from typing import Any

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsMessageLog,
    QgsPalLayerSettings,
    QgsProperty,
    QgsPropertyCollection,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from ..core.bands import BAND_ORDER
from ..core.i18n import tr, tr_noop
from . import compat

__all__ = [
    "BAND_COLORS",
    "GRID_LABEL_FIELDS",
    "GRID_LABEL_SCALES",
    "OTHER_BAND_COLOR",
    "STYLES_DIR",
    "STYLE_KINDS",
    "apply_default_style",
    "build_default_style",
    "retranslate_style",
    "style_categories",
    "style_path",
]

#: Colour of each common band (``#rrggbb``), in frequency order. See the module docstring.
BAND_COLORS: dict[str, str] = {
    "160m": "#8C564B",  # brown
    "80m": "#AA3377",  # purple
    "60m": "#999933",  # olive
    "40m": "#0072B2",  # blue
    "30m": "#33BBEE",  # cyan
    "20m": "#CC3311",  # red
    "17m": "#DDAA33",  # gold
    "15m": "#228833",  # green
    "12m": "#CC6677",  # rose
    "10m": "#648FFF",  # periwinkle
    "6m": "#EE8866",  # salmon
    "4m": "#009988",  # teal
    "2m": "#785EF0",  # violet
    "70cm": "#CC79A7",  # pink
    "23cm": "#AA4499",  # magenta
}
#: Colour of every other band and of QSOs without a band.
OTHER_BAND_COLOR = "#9E9E9E"
#: Legend label of the "all other values" category (translated when applied).
OTHER_BANDS_LABEL = tr_noop("Other bands")

#: Layer kinds :func:`apply_default_style` knows.
STYLE_KINDS = ("qso", "qso_path", "grid")
#: Folder with the generated ``<kind>.qml`` files.
STYLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "styles"
)
#: Fields that may hold the locator of a grid cell, in order of preference.
GRID_LABEL_FIELDS = ("locator", "grid", "gridsquare", "name")
#: Largest map scale denominator at which a grid label is shown, by locator length: a
#: label must fit into its cell (a square is about 157 x 111 km at 45 degrees north).
GRID_LABEL_SCALES = {2: 200_000_000, 4: 15_000_000, 6: 500_000, 8: 40_000}

_BAND_FIELD = "band"
_OTHER_LABEL_PROPERTY = "hamq/other_bands_label"  # legend label HamQ set, for retranslation
_POINT_SIZE_MM = "2.4"
_POINT_OUTLINE = "#232323"
_POINT_OUTLINE_MM = "0.2"
_LINE_WIDTH_MM = "0.4"
_LINE_OPACITY = 0.6
_GRID_OUTLINE = "#5A5A5A"
_GRID_OUTLINE_MM = "0.2"
_GRID_LABEL_SIZE_PT = 8.0
_GRID_LABEL_COLOR = "#303030"
_LOG_TAG = "HamQ"


def style_path(kind: str) -> str:
    """Path of the generated style file of ``kind`` (it may not exist)."""
    _check_kind(kind)
    return os.path.join(STYLES_DIR, f"{kind}.qml")


def style_categories() -> Any:
    """Style categories a default style sets: symbology and labels."""
    return compat.STYLE_CATEGORY_SYMBOLOGY | compat.STYLE_CATEGORY_LABELING


def apply_default_style(layer: QgsVectorLayer, kind: str) -> None:
    """Give ``layer`` the default HamQ style of ``kind`` (``qso``, ``qso_path`` or ``grid``).

    Loads ``resources/styles/<kind>.qml`` when it exists and fits the layer (the grid
    style labels a ``locator`` field), else builds the style in code (a grid layer is then
    labelled with the first of :data:`GRID_LABEL_FIELDS` it has). Only symbology and
    labels change. Raises ``ValueError`` for an unknown kind or a layer whose geometry
    type does not match it (programmer errors).
    """
    _check_kind(kind)
    _check_geometry(layer, kind)
    loaded = False
    qml = style_path(kind)
    if os.path.isfile(qml) and _qml_fits(layer, kind):
        message, ok = layer.loadNamedStyle(qml, categories=style_categories())
        loaded = bool(ok) and layer.renderer() is not None
        if not loaded:
            QgsMessageLog.logMessage(
                tr(
                    "The style {path} could not be loaded, the built-in style is used: {error}"
                ).format(path=qml, error=message),
                _LOG_TAG,
                compat.MSG_WARNING,
            )
    if not loaded:
        build_default_style(layer, kind)
    _translate_other_label(layer, force=True)
    layer.triggerRepaint()


def build_default_style(layer: QgsVectorLayer, kind: str) -> None:
    """Build the default style of ``kind`` in code and set it on ``layer``.

    :func:`apply_default_style` uses it when no ``.qml`` file fits;
    ``scripts/make_styles.py`` generates the ``.qml`` files from it.
    """
    _check_kind(kind)
    if kind == "grid":
        symbol = QgsFillSymbol.createSimple(
            {
                "style": "no",
                "color": "0,0,0,0",
                "outline_color": _GRID_OUTLINE,
                "outline_style": "solid",
                "outline_width": _GRID_OUTLINE_MM,
                "outline_width_unit": "MM",
            }
        )
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        label_field = _grid_label_field(layer)
        if label_field is None:
            layer.setLabelsEnabled(False)
        else:
            layer.setLabeling(QgsVectorLayerSimpleLabeling(_grid_label_settings(label_field)))
            layer.setLabelsEnabled(True)
        return
    layer.setRenderer(_band_renderer(kind))
    layer.setLabelsEnabled(False)


def retranslate_style(layer: QgsVectorLayer) -> None:
    """Translate the "Other bands" legend label of a HamQ band style again.

    A label the user changed since HamQ set it is left alone. When the label changes, the
    layer emits ``legendChanged``, so the Layers panel shows the new text at once.
    """
    _translate_other_label(layer, force=False)


# --- band renderers ---------------------------------------------------------------------------


def _band_renderer(kind: str) -> QgsCategorizedSymbolRenderer:
    """Categorized renderer on ``band``: one category per :data:`BAND_COLORS` entry in
    frequency order, then "all other values" (also NULL) in grey."""
    order = {band: index for index, band in enumerate(BAND_ORDER)}
    bands = sorted(BAND_COLORS, key=lambda band: order.get(band, len(order)))
    categories = [
        QgsRendererCategory(band, _symbol(kind, BAND_COLORS[band]), band) for band in bands
    ]
    categories.append(
        QgsRendererCategory(None, _symbol(kind, OTHER_BAND_COLOR), tr(OTHER_BANDS_LABEL))
    )
    return QgsCategorizedSymbolRenderer(_BAND_FIELD, categories)


def _symbol(kind: str, color: str) -> Any:
    if kind == "qso":
        return QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": color,
                "outline_color": _POINT_OUTLINE,
                "outline_style": "solid",
                "outline_width": _POINT_OUTLINE_MM,
                "outline_width_unit": "MM",
                "size": _POINT_SIZE_MM,
                "size_unit": "MM",
            }
        )
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": color,
            "line_style": "solid",
            "line_width": _LINE_WIDTH_MM,
            "line_width_unit": "MM",
            "capstyle": "round",
            "joinstyle": "round",
        }
    )
    symbol.setOpacity(_LINE_OPACITY)
    return symbol


def _translate_other_label(layer: QgsVectorLayer, *, force: bool) -> None:
    """Set the label of the "all other values" category of a band renderer to the
    translation of :data:`OTHER_BANDS_LABEL`; without ``force`` only when it still is the
    label HamQ set last time.

    The renderer is changed in place, which QGIS does not notice: a legend (the Layers
    panel, a layout) keeps showing the labels it read. ``legendChanged`` makes it read them
    again, once per layer and only when the text really changed (rebuilding the legend of
    the 16 band categories costs a noticeable moment of GUI time on QGIS 3.x).
    """
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return
    if renderer.classAttribute() != _BAND_FIELD:
        return
    previous = layer.customProperty(_OTHER_LABEL_PROPERTY)
    previous = previous if isinstance(previous, str) else None
    label = tr(OTHER_BANDS_LABEL)
    changed = False
    for index, category in enumerate(renderer.categories()):
        value = category.value()
        if not _is_null(value) and value != "":
            continue
        if force or category.label() == previous:
            if category.label() != label:
                renderer.updateCategoryLabel(index, label)
                changed = True
            layer.setCustomProperty(_OTHER_LABEL_PROPERTY, label)
        break
    if changed:
        layer.legendChanged.emit()


# --- grid --------------------------------------------------------------------------------------


def _grid_label_field(layer: QgsVectorLayer) -> str | None:
    fields = layer.fields()
    for name in GRID_LABEL_FIELDS:
        index = fields.lookupField(name)
        if index >= 0:
            return fields.at(index).name()
    return None


def _grid_label_settings(field_name: str) -> QgsPalLayerSettings:
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.7)
    buffer.setColor(QColor(255, 255, 255))
    buffer.setOpacity(0.8)
    text_format = QgsTextFormat()
    text_format.setSize(_GRID_LABEL_SIZE_PT)
    text_format.setColor(QColor(_GRID_LABEL_COLOR))
    text_format.setBuffer(buffer)
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.isExpression = False
    settings.placement = compat.LABEL_PLACEMENT_OVER_POINT
    settings.setFormat(text_format)
    properties = QgsPropertyCollection("labeling")  # a new collection: no aliasing
    properties.setProperty(
        compat.LABEL_PROPERTY_SHOW, QgsProperty.fromExpression(_grid_show_expression(field_name))
    )
    settings.setDataDefinedProperties(properties)
    return settings


def _grid_show_expression(field_name: str) -> str:
    """``@map_scale <= CASE WHEN length("locator") <= 2 THEN ... END``: show a label only
    from the scale on where it fits into its cell; longer locators need a larger scale.
    (QGIS expressions only know the searched ``CASE WHEN condition``.)"""
    quoted = '"' + field_name.replace('"', '""') + '"'
    lengths = sorted(GRID_LABEL_SCALES.items())
    cases = " ".join(
        f"WHEN length({quoted}) <= {length} THEN {scale}" for length, scale in lengths[:-1]
    )
    return f"@map_scale <= CASE {cases} ELSE {lengths[-1][1]} END"


# --- checks ------------------------------------------------------------------------------------


def _check_kind(kind: str) -> None:
    if kind not in STYLE_KINDS:
        raise ValueError(f"unknown style kind {kind!r}; expected one of {STYLE_KINDS}")


_GEOMETRY_OF_KIND = {
    "qso": compat.GEOMETRY_POINT,
    "qso_path": compat.GEOMETRY_LINE,
    "grid": compat.GEOMETRY_POLYGON,
}


def _check_geometry(layer: QgsVectorLayer, kind: str) -> None:
    if not isinstance(layer, QgsVectorLayer):
        raise ValueError(f"expected a QgsVectorLayer, got {type(layer).__name__}")
    expected = _GEOMETRY_OF_KIND[kind]
    if layer.geometryType() != expected:
        raise ValueError(
            f"a {kind!r} style needs a {expected!r} layer, {layer.name()!r} has "
            f"{layer.geometryType()!r}"
        )


def _qml_fits(layer: QgsVectorLayer, kind: str) -> bool:
    """The generated grid style labels the ``locator`` field; the band styles always fit."""
    if kind != "grid":
        return True
    return layer.fields().lookupField(GRID_LABEL_FIELDS[0]) >= 0


def _is_null(value: object) -> bool:
    """NULL attribute value: ``None`` on QGIS 4, a null ``QVariant`` on QGIS 3."""
    if value is None:
        return True
    return type(value).__name__ == "QVariant" and bool(value.isNull())  # type: ignore[attr-defined]
