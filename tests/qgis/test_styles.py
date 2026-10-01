"""hamq.qgis_io.styles: band colours, the default styles built in code and the .qml files."""

from __future__ import annotations

import itertools
import math
import os
import re

import pytest
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextScope,
    QgsFeature,
    QgsLayerTreeModel,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsRenderContext,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, Qt

from hamq.core.bands import BAND_ORDER
from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, set_language
from hamq.qgis_io import compat, styles

COMMON_BANDS = (
    "160m",
    "80m",
    "60m",
    "40m",
    "30m",
    "20m",
    "17m",
    "15m",
    "12m",
    "10m",
    "6m",
    "4m",
    "2m",
    "70cm",
    "23cm",
)
BUSY_BANDS = ("160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m")
URIS = {
    "qso": "Point?crs=EPSG:4326&field=band:string",
    "qso_path": "MultiLineString?crs=EPSG:4326&field=band:string",
    "grid": "Polygon?crs=EPSG:4326&field=locator:string",
}


@pytest.fixture(autouse=True)
def english():
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)


def memory_layer(kind: str, uri: str | None = None) -> QgsVectorLayer:
    layer = QgsVectorLayer(uri or URIS[kind], kind, "memory")
    assert layer.isValid()
    return layer


def is_else_value(value) -> bool:
    """The "all other values" category: NULL (None / null QVariant) or an empty string."""
    if value is None or value == "":
        return True
    return type(value).__name__ == "QVariant" and value.isNull()


def color_of(symbol) -> str:
    return symbol.color().name().upper()


def symbol_for(layer: QgsVectorLayer, value) -> object:
    renderer = layer.renderer()
    context = QgsRenderContext()
    renderer.startRender(context, layer.fields())
    try:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([value])
        symbol = renderer.symbolForFeature(feature, context)
        return None if symbol is None else color_of(symbol)
    finally:
        renderer.stopRender(context)


def category_summary(layer: QgsVectorLayer) -> list[tuple[str, str, str]]:
    renderer = layer.renderer()
    assert isinstance(renderer, QgsCategorizedSymbolRenderer)
    return [
        ("" if is_else_value(c.value()) else c.value(), color_of(c.symbol()), c.label())
        for c in renderer.categories()
    ]


# --- band colours ---------------------------------------------------------------------------


def test_band_colors_cover_the_common_bands():
    assert tuple(styles.BAND_COLORS) == COMMON_BANDS
    assert set(COMMON_BANDS) <= set(BAND_ORDER)
    values = [*styles.BAND_COLORS.values(), styles.OTHER_BAND_COLOR]
    assert all(re.fullmatch(r"#[0-9A-F]{6}", value) for value in values)
    assert len(set(values)) == len(values)
    red, green, blue = (int(styles.OTHER_BAND_COLOR[i : i + 2], 16) for i in (1, 3, 5))
    assert red == green == blue  # neutral grey


# Colour vision deficiency simulation (Machado, Oliveira and Fernandes 2009, severity 1.0,
# linear RGB) and the CIEDE2000 colour difference: the check behind the palette.
_CVD = {
    "normal": None,
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _lab(color: str, vision: str) -> tuple[float, float, float]:
    rgb = [_linear(int(color[i : i + 2], 16) / 255.0) for i in (1, 3, 5)]
    matrix = _CVD[vision]
    if matrix is not None:
        rgb = [min(1.0, max(0.0, sum(m * c for m, c in zip(row, rgb)))) for row in matrix]
    x = 0.4124564 * rgb[0] + 0.3575761 * rgb[1] + 0.1804375 * rgb[2]
    y = 0.2126729 * rgb[0] + 0.7151522 * rgb[1] + 0.0721750 * rgb[2]
    z = 0.0193339 * rgb[0] + 0.1191920 * rgb[1] + 0.9503041 * rgb[2]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _ciede2000(lab1, lab2) -> float:
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean**7 / (c_mean**7 + 25**7)))
    a1, a2 = (1 + g) * a1, (1 + g) * a2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    h1 = math.degrees(math.atan2(b1, a1)) % 360
    h2 = math.degrees(math.atan2(b2, a2)) % 360
    dh = 0.0 if c1 * c2 == 0 else ((h2 - h1 + 180) % 360) - 180
    d_l, d_c = l2 - l1, c2 - c1
    d_h = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh / 2))
    l_mean, c_mean = (l1 + l2) / 2, (c1 + c2) / 2
    if c1 * c2 == 0:
        h_mean = h1 + h2
    elif abs(h1 - h2) <= 180:
        h_mean = (h1 + h2) / 2
    else:
        h_mean = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_mean - 30))
        + 0.24 * math.cos(math.radians(2 * h_mean))
        + 0.32 * math.cos(math.radians(3 * h_mean + 6))
        - 0.20 * math.cos(math.radians(4 * h_mean - 63))
    )
    s_l = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    s_c = 1 + 0.045 * c_mean
    s_h = 1 + 0.015 * c_mean * t
    r_t = (
        -math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2))))
        * 2
        * math.sqrt(c_mean**7 / (c_mean**7 + 25**7))
    )
    return math.sqrt(
        (d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2 + r_t * (d_c / s_c) * (d_h / s_h)
    )


def _worst_difference(first: str, second: str) -> float:
    return min(_ciede2000(_lab(first, v), _lab(second, v)) for v in _CVD)


def test_ciede2000_reference_pair():
    # Sharma, Wu and Dalal (2005), test data pair 1
    assert _ciede2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(
        2.0425, abs=1e-4
    )


def test_band_colors_stay_apart_for_colour_blind_viewers():
    colors = {**styles.BAND_COLORS, "other": styles.OTHER_BAND_COLOR}
    worst = min(
        (_worst_difference(c1, c2), b1, b2)
        for (b1, c1), (b2, c2) in itertools.combinations(colors.items(), 2)
    )
    assert worst[0] >= 6.0, worst
    busy = {band: colors[band] for band in (*BUSY_BANDS, "other")}
    worst_busy = min(
        (_worst_difference(c1, c2), b1, b2)
        for (b1, c1), (b2, c2) in itertools.combinations(busy.items(), 2)
    )
    assert worst_busy[0] >= 8.0, worst_busy


def test_band_colors_are_visible_on_white():
    def luminance(color: str) -> float:
        r, g, b = (_linear(int(color[i : i + 2], 16) / 255.0) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for band, color in styles.BAND_COLORS.items():
        assert 1.05 / (luminance(color) + 0.05) >= 2.0, band


# --- styles built in code -------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["qso", "qso_path"])
def test_band_style_in_code(kind):
    layer = memory_layer(kind)
    styles.build_default_style(layer, kind)
    renderer = layer.renderer()
    assert isinstance(renderer, QgsCategorizedSymbolRenderer)
    assert renderer.classAttribute() == "band"
    expected = [(band, styles.BAND_COLORS[band].upper(), band) for band in COMMON_BANDS]
    expected.append(("", styles.OTHER_BAND_COLOR.upper(), "Other bands"))
    assert category_summary(layer) == expected
    assert not layer.labelsEnabled()
    categories = renderer.categories()  # keeps the category, which owns the symbol, alive
    symbol = categories[0].symbol()
    if kind == "qso":
        assert isinstance(symbol, QgsMarkerSymbol)
        assert symbol.size() == pytest.approx(2.4)
        assert symbol.symbolLayer(0).strokeColor().name().upper() == "#232323"
    else:
        assert isinstance(symbol, QgsLineSymbol)
        assert 0.0 < symbol.opacity() < 1.0
        assert symbol.width() == pytest.approx(0.4)


@pytest.mark.parametrize("kind", ["qso", "qso_path"])
def test_band_style_colours_features(kind):
    layer = memory_layer(kind)
    styles.build_default_style(layer, kind)
    assert symbol_for(layer, "20m") == styles.BAND_COLORS["20m"].upper()
    assert symbol_for(layer, "70cm") == styles.BAND_COLORS["70cm"].upper()
    grey = styles.OTHER_BAND_COLOR.upper()
    assert symbol_for(layer, "2190m") == grey  # a band without a colour of its own
    assert symbol_for(layer, "33cm") == grey
    assert symbol_for(layer, None) == grey  # no band
    assert symbol_for(layer, "") == grey


def test_grid_style_in_code():
    layer = memory_layer("grid")
    styles.build_default_style(layer, "grid")
    renderer = layer.renderer()
    assert isinstance(renderer, QgsSingleSymbolRenderer)
    fill = renderer.symbol().symbolLayer(0)
    assert fill.brushStyle() == compat.BRUSH_NONE
    assert fill.strokeWidth() == pytest.approx(0.2)
    assert layer.labelsEnabled()
    settings = layer.labeling().settings()
    assert settings.fieldName == "locator"
    assert not settings.isExpression
    assert settings.placement == compat.LABEL_PLACEMENT_OVER_POINT
    show = settings.dataDefinedProperties().property(compat.LABEL_PROPERTY_SHOW)
    assert "@map_scale" in show.expressionString()


@pytest.mark.parametrize(
    ("locator", "scale", "shown"),
    [
        ("KN", 150_000_000, True),
        ("KN04", 10_000_000, True),
        ("KN04", 20_000_000, False),
        ("KN04ft", 400_000, True),
        ("KN04ft", 1_000_000, False),
        ("KN04ft12", 25_000, True),
        ("KN04ft12", 100_000, False),
    ],
)
def test_grid_labels_show_where_they_fit(locator, scale, shown):
    layer = memory_layer("grid")
    styles.build_default_style(layer, "grid")
    settings = layer.labeling().settings()  # a copy: keep it alive while using its parts
    show = settings.dataDefinedProperties().property(compat.LABEL_PROPERTY_SHOW)
    expression = QgsExpression(show.expressionString())
    assert not expression.hasParserError(), expression.parserErrorString()
    context = QgsExpressionContext()
    scope = QgsExpressionContextScope()
    scope.setVariable("map_scale", scale)
    context.appendScope(scope)
    feature = QgsFeature(layer.fields())
    feature.setAttributes([locator])
    context.setFeature(feature)
    assert bool(expression.evaluate(context)) is shown


def test_grid_label_field_fallback():
    named = memory_layer("grid", "Polygon?crs=EPSG:4326&field=id:integer&field=NAME:string")
    styles.build_default_style(named, "grid")
    assert named.labelsEnabled()
    settings = named.labeling().settings()
    assert settings.fieldName == "NAME"
    show = settings.dataDefinedProperties().property(compat.LABEL_PROPERTY_SHOW)
    assert '"NAME"' in show.expressionString()
    bare = memory_layer("grid", "Polygon?crs=EPSG:4326&field=id:integer")
    styles.build_default_style(bare, "grid")
    assert not bare.labelsEnabled()
    assert isinstance(bare.renderer(), QgsSingleSymbolRenderer)


# --- .qml files -------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", styles.STYLE_KINDS)
def test_qml_file_was_generated_on_the_oldest_qgis(kind):
    path = styles.style_path(kind)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert re.search(r'version="3\.34\.', text), "regenerate with scripts/make_styles.py on 3.34"
    assert 'styleCategories="Symbology|Labeling"' in text
    assert "fontFamily=" not in text


@pytest.mark.parametrize("kind", styles.STYLE_KINDS)
def test_qml_file_loads_and_matches_the_code(kind):
    from_file = memory_layer(kind)
    _message, ok = from_file.loadNamedStyle(
        styles.style_path(kind), categories=styles.style_categories()
    )
    assert ok
    from_code = memory_layer(kind)
    styles.build_default_style(from_code, kind)
    assert from_file.renderer().type() == from_code.renderer().type()
    if kind == "grid":
        assert from_file.labelsEnabled()
        file_settings = from_file.labeling().settings()
        code_settings = from_code.labeling().settings()
        assert file_settings.fieldName == code_settings.fieldName
        assert file_settings.placement == code_settings.placement
        assert (
            file_settings.dataDefinedProperties()
            .property(compat.LABEL_PROPERTY_SHOW)
            .expressionString()
            == code_settings.dataDefinedProperties()
            .property(compat.LABEL_PROPERTY_SHOW)
            .expressionString()
        )
        file_fill = from_file.renderer().symbol().symbolLayer(0)
        assert file_fill.brushStyle() == compat.BRUSH_NONE
        return
    assert category_summary(from_file) == category_summary(from_code)
    file_categories = from_file.renderer().categories()
    code_categories = from_code.renderer().categories()
    for file_category, code_category in zip(file_categories, code_categories):
        file_symbol, code_symbol = file_category.symbol(), code_category.symbol()
        assert type(file_symbol) is type(code_symbol)
        assert file_symbol.opacity() == pytest.approx(code_symbol.opacity())
    assert symbol_for(from_file, None) == styles.OTHER_BAND_COLOR.upper()


@pytest.mark.parametrize("kind", styles.STYLE_KINDS)
def test_apply_default_style(kind):
    layer = memory_layer(kind)
    layer.setFieldAlias(0, "My alias")
    styles.apply_default_style(layer, kind)
    assert layer.attributeAlias(0) == "My alias"  # only symbology and labels change
    if kind == "grid":
        assert layer.labelsEnabled()
        assert layer.labeling().settings().fieldName == "locator"
    else:
        assert category_summary(layer)[-1] == ("", styles.OTHER_BAND_COLOR.upper(), "Other bands")
        assert symbol_for(layer, "40m") == styles.BAND_COLORS["40m"].upper()


@pytest.mark.parametrize("kind", ["qso", "grid"])
def test_apply_default_style_without_qml_builds_in_code(kind, tmp_path, monkeypatch):
    monkeypatch.setattr(styles, "STYLES_DIR", str(tmp_path))
    layer = memory_layer(kind)
    styles.apply_default_style(layer, kind)
    reference = memory_layer(kind)
    styles.build_default_style(reference, kind)
    assert layer.renderer().type() == reference.renderer().type()
    if kind == "qso":
        assert category_summary(layer) == category_summary(reference)


def test_apply_default_style_with_broken_qml(tmp_path, monkeypatch, log_messages):
    (tmp_path / "qso.qml").write_text("<qgis><renderer-v2 type=", encoding="utf-8")
    monkeypatch.setattr(styles, "STYLES_DIR", str(tmp_path))
    layer = memory_layer("qso")
    styles.apply_default_style(layer, "qso")
    assert symbol_for(layer, "20m") == styles.BAND_COLORS["20m"].upper()
    assert any(tag == "HamQ" and "qso.qml" in message for message, tag, _ in log_messages)


def test_grid_layer_without_locator_field_does_not_use_the_qml():
    layer = memory_layer("grid", "Polygon?crs=EPSG:4326&field=grid:string")
    styles.apply_default_style(layer, "grid")
    assert layer.labeling().settings().fieldName == "grid"


def test_other_bands_label_follows_the_language():
    layer = memory_layer("qso")
    set_language(LANG_SR_LATN)
    styles.apply_default_style(layer, "qso")
    assert category_summary(layer)[-1][2] == "Ostali opsezi"
    set_language(LANG_SR_CYRL)
    styles.retranslate_style(layer)
    assert category_summary(layer)[-1][2] == "Остали опсези"
    set_language(LANG_EN)
    styles.retranslate_style(layer)
    assert category_summary(layer)[-1][2] == "Other bands"


def test_retranslate_style_keeps_a_label_the_user_changed():
    layer = memory_layer("qso")
    styles.apply_default_style(layer, "qso")
    renderer = layer.renderer()
    renderer.updateCategoryLabel(len(renderer.categories()) - 1, "Everything else")
    set_language(LANG_SR_LATN)
    styles.retranslate_style(layer)
    assert category_summary(layer)[-1][2] == "Everything else"


def test_retranslate_style_ignores_other_renderers():
    layer = memory_layer("qso")
    styles.retranslate_style(layer)  # the default single symbol renderer
    assert not isinstance(layer.renderer(), QgsCategorizedSymbolRenderer)


class Legend:
    """The legend of a layer as the Layers panel shows it: one ``QgsLayerTreeModel`` that
    lives through the test (a new model would read the renderer afresh)."""

    def __init__(self, project, layer: QgsVectorLayer) -> None:
        self.model = QgsLayerTreeModel(project.layerTreeRoot())
        self.node = project.layerTreeRoot().findLayer(layer.id())
        assert self.node is not None
        self.rebuilds = 0
        layer.legendChanged.connect(self._count)

    def _count(self) -> None:
        self.rebuilds += 1

    def last_label(self) -> str:
        QCoreApplication.processEvents()
        return self.model.layerLegendNodes(self.node)[-1].data(Qt.ItemDataRole.DisplayRole)


def test_other_bands_legend_entry_follows_the_language(clean_project):
    """The label is changed in place, so the legend must be told (once per change)."""
    layer = memory_layer("qso")
    styles.apply_default_style(layer, "qso")
    clean_project.addMapLayer(layer)
    legend = Legend(clean_project, layer)
    assert legend.last_label() == "Other bands"
    set_language(LANG_SR_LATN)
    styles.retranslate_style(layer)
    assert legend.last_label() == "Ostali opsezi"
    assert legend.rebuilds == 1
    styles.retranslate_style(layer)  # nothing changed: the legend is not rebuilt again
    assert legend.rebuilds == 1
    set_language(LANG_SR_CYRL)
    styles.retranslate_style(layer)
    assert legend.last_label() == "Остали опсези"
    set_language(LANG_EN)
    styles.retranslate_style(layer)
    assert legend.last_label() == "Other bands"
    assert legend.rebuilds == 3


def test_legend_entry_of_a_label_the_user_changed_stays(clean_project):
    layer = memory_layer("qso_path")
    styles.apply_default_style(layer, "qso_path")
    clean_project.addMapLayer(layer)
    layer.renderer().updateCategoryLabel(len(layer.renderer().categories()) - 1, "Rest")
    layer.legendChanged.emit()
    legend = Legend(clean_project, layer)
    set_language(LANG_SR_LATN)
    styles.retranslate_style(layer)
    assert legend.last_label() == "Rest"
    assert legend.rebuilds == 0


def test_apply_default_style_to_a_layer_in_the_legend(clean_project):
    """The .qml file holds the English label; translating it after loading must reach a
    legend that already shows the layer."""
    layer = memory_layer("qso")
    clean_project.addMapLayer(layer)
    legend = Legend(clean_project, layer)
    set_language(LANG_SR_LATN)
    styles.apply_default_style(layer, "qso")
    assert legend.last_label() == "Ostali opsezi"
    styles.apply_default_style(layer, "qso")
    assert legend.last_label() == "Ostali opsezi"


@pytest.mark.parametrize(
    ("kind", "uri"),
    [
        ("qso", URIS["qso_path"]),
        ("qso_path", URIS["qso"]),
        ("grid", URIS["qso"]),
    ],
)
def test_wrong_geometry_is_a_programmer_error(kind, uri):
    layer = QgsVectorLayer(uri, "wrong", "memory")
    with pytest.raises(ValueError, match=kind):
        styles.apply_default_style(layer, kind)


def test_unknown_kind_is_a_programmer_error():
    with pytest.raises(ValueError, match="kind"):
        styles.apply_default_style(memory_layer("qso"), "contour")
    with pytest.raises(ValueError, match="kind"):
        styles.style_path("contour")
