"""hamq.gui.azimuthal.AzimuthalMap: azimuthal equidistant project CRS and helper layer."""

from __future__ import annotations

import json
import math

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from hamq.core import maidenhead
from hamq.core.geo import aeqd_proj
from hamq.core.i18n import LANG_EN, LANG_SR_LATN, set_language
from hamq.events import events
from hamq.gui.azimuthal import (
    AZIMUTH_KIND,
    GROUP_NAME,
    RING_KIND,
    STATE_PROPERTY,
    AzimuthalMap,
    azimuths,
    ring_distances,
)
from hamq.qgis_io import compat
from hamq.settings import HamQSettings

pytest.importorskip("qgis.gui", reason="qgis.gui is not available in this build")

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


@pytest.fixture
def expect_slot_errors():
    """Requested by tests that make the map fail on purpose."""


@pytest.fixture(autouse=True)
def no_slot_errors(log_messages, request):
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)
    if "expect_slot_errors" in request.fixturenames:
        return
    errors = [m for m, tag, level in log_messages if tag == "HamQ" and level == compat.MSG_CRITICAL]
    assert errors == []


@pytest.fixture
def settings(clean_settings):
    settings = HamQSettings()
    settings.my_grid = "KN04ft"
    return settings


@pytest.fixture
def canvas(iface, clean_project):
    canvas = iface.mapCanvas()
    canvas.resize(800, 600)
    return canvas


def start_project(canvas, authid):
    """Project and canvas CRS (QGIS keeps them equal) and a 20 x 20 degree view."""
    crs = QgsCoordinateReferenceSystem(authid)
    QgsProject.instance().setCrs(crs)
    canvas.setDestinationCrs(crs)
    transform = QgsCoordinateTransform(WGS84, crs, QgsProject.instance())
    extent = transform.transformBoundingBox(QgsRectangle(10, 35, 30, 55))
    canvas.setExtent(extent)
    return crs, canvas.extent()


@pytest.fixture
def azimuthal(iface, settings, canvas):
    azimuthal = AzimuthalMap(iface, settings)
    changes = []
    azimuthal.enabledChanged.connect(changes.append)
    azimuthal.changes = changes
    yield azimuthal
    azimuthal.cleanup()


def expected_crs(locator):
    return QgsCoordinateReferenceSystem.fromProj(aeqd_proj(*maidenhead.to_latlon(locator)))


def hamq_group():
    return QgsProject.instance().layerTreeRoot().findGroup(GROUP_NAME)


@pytest.mark.parametrize("authid", ["EPSG:3857", "EPSG:4326", "EPSG:3035"])
def test_enable_and_disable_restore_the_crs(azimuthal, canvas, authid):
    original, extent = start_project(canvas, authid)
    assert azimuthal.enable() is True
    assert azimuthal.is_enabled()
    assert azimuthal.center_locator() == "KN04ft"
    project = QgsProject.instance()
    assert project.crs() == expected_crs("KN04ft")
    assert "+proj=aeqd" in project.crs().toProj()
    assert canvas.mapSettings().destinationCrs() == project.crs()
    assert azimuthal.crs() == project.crs()
    layer = azimuthal.helper_layer()
    assert isinstance(layer, QgsVectorLayer)
    assert project.mapLayer(layer.id()) is layer
    assert hamq_group() is not None and hamq_group().findLayer(layer.id()) is not None
    assert canvas.extent().contains(QgsRectangle(-20000, -20000, 20000, 20000))
    layer_id = layer.id()

    azimuthal.disable()
    assert not azimuthal.is_enabled()
    assert project.crs() == original
    assert canvas.mapSettings().destinationCrs() == original
    assert project.mapLayer(layer_id) is None
    assert hamq_group() is None  # created by the map and empty again
    assert azimuthal.helper_layer() is None
    center = canvas.extent().center()
    assert center.x() == pytest.approx(extent.center().x())
    assert center.y() == pytest.approx(extent.center().y())
    azimuthal.disable()  # safe twice
    assert project.crs() == original
    assert azimuthal.changes == [True, False]


def test_rings_and_azimuth_lines(azimuthal, canvas):
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    layer = azimuthal.helper_layer()
    assert layer.crs() == expected_crs("KN04ft")
    assert [field.name() for field in layer.fields()] == ["kind", "value", "label"]
    features = list(layer.getFeatures())
    rings = [f for f in features if f["kind"] == RING_KIND]
    lines = [f for f in features if f["kind"] == AZIMUTH_KIND]
    assert ring_distances() == [2500, 5000, 7500, 10000, 12500, 15000, 17500, 20000]
    assert azimuths() == list(range(0, 360, 30))
    assert len(rings) == 8 and len(lines) == 12 and len(features) == 20
    assert [f["label"] for f in rings] == [f"{d} km" for d in ring_distances()]
    assert rings[0]["label"] == "2500 km"
    assert [f["label"] for f in lines] == [f"{a}°" for a in azimuths()]
    assert lines[0]["label"] == "0°"
    for feature in rings:
        radius = feature["value"]
        points = feature.geometry().asPolyline()
        assert points[0] == points[-1]  # closed
        assert all(math.hypot(p.x(), p.y()) == pytest.approx(radius) for p in points)
    east = next(f for f in lines if f["value"] == 90.0).geometry().asPolyline()
    assert east[0].x() == pytest.approx(0.0) and east[0].y() == pytest.approx(0.0)
    assert east[-1].x() == pytest.approx(20000.0) and east[-1].y() == pytest.approx(0.0, abs=1e-6)
    assert layer.labelsEnabled()
    assert layer.labeling() is not None
    assert layer.labeling().settings().fieldName == "label"
    assert layer.labeling().settings().placement == compat.LABEL_PLACEMENT_LINE


def test_the_rings_are_true_distances_from_my_qth(azimuthal, canvas):
    start_project(canvas, "EPSG:4326")
    assert azimuthal.enable()
    layer = azimuthal.helper_layer()
    to_wgs84 = QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance())
    measure = QgsDistanceArea()
    measure.setSourceCrs(WGS84, QgsProject.instance().transformContext())
    measure.setEllipsoid("WGS84")
    lat, lon = maidenhead.to_latlon("KN04ft")
    qth = QgsPointXY(lon, lat)
    assert to_wgs84.transform(QgsPointXY(0, 0)).distance(qth) < 1e-6
    for feature in layer.getFeatures():
        if feature["kind"] != RING_KIND or feature["value"] > 10000:
            continue
        for point in feature.geometry().asPolyline()[::45]:
            km = measure.measureLine(qth, to_wgs84.transform(point)) / 1000.0
            assert km == pytest.approx(feature["value"], rel=1e-3)


@pytest.mark.parametrize("grid", ["", "XYZ"])
def test_requires_a_valid_locator(iface, clean_settings, canvas, grid):
    settings = HamQSettings()
    QgsProject.instance()  # the project of the canvas fixture
    if grid:
        from qgis.core import QgsSettings

        QgsSettings().setValue("hamq/my_grid", grid)  # e.g. a hand-edited value
    original, _extent = start_project(canvas, "EPSG:3857")
    azimuthal = AzimuthalMap(iface, settings)
    try:
        iface.messageBar().clearWidgets()
        assert azimuthal.enable() is False
        assert not azimuthal.is_enabled()
        assert QgsProject.instance().crs() == original
        assert QgsProject.instance().mapLayers() == {}
        items = iface.messageBar().items()
        assert len(items) == 1
        assert items[0].level() == compat.MSG_WARNING
        assert "valid QTH locator" in items[0].text()
        set_language(LANG_SR_LATN)
        iface.messageBar().clearWidgets()
        assert azimuthal.enable() is False
        assert "ispravan QTH lokator" in iface.messageBar().items()[0].text()
    finally:
        azimuthal.cleanup()


@pytest.mark.parametrize("fail_restore", [False, True])
def test_a_failure_while_switching_on_leaves_the_project_as_it_was(
    azimuthal, canvas, monkeypatch, log_messages, expect_slot_errors, fail_restore
):
    original, _extent = start_project(canvas, "EPSG:3857")

    def broken(*_args):
        raise RuntimeError("no memory layer for the test")

    monkeypatch.setattr(AzimuthalMap, "_build_layer", broken)
    if fail_restore:  # removing the (old) helper works once, then fails while restoring
        calls = []
        remove_layer = AzimuthalMap._remove_layer

        def remove_once(self):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("no removal for the test")
            remove_layer(self)

        monkeypatch.setattr(AzimuthalMap, "_remove_layer", remove_once)
    assert azimuthal.enable() is False
    assert not azimuthal.is_enabled()
    assert azimuthal.changes == []
    assert QgsProject.instance().crs() == original  # restored in both cases
    assert canvas.mapSettings().destinationCrs() == original
    assert QgsProject.instance().mapLayers() == {}
    errors = [m for m, tag, level in log_messages if level == compat.MSG_CRITICAL]
    assert len(errors) == (2 if fail_restore else 1)
    assert "no memory layer for the test" in errors[0]
    if fail_restore:
        assert "no removal for the test" in errors[1]
    azimuthal.disable()  # nothing to do
    assert azimuthal.changes == []


def test_existing_hamq_group_is_kept(azimuthal, canvas):
    start_project(canvas, "EPSG:3857")
    project = QgsProject.instance()
    other = QgsVectorLayer("Point?crs=EPSG:4326", "qso", "memory")
    project.addMapLayer(other, False)
    group = project.layerTreeRoot().addGroup(GROUP_NAME)
    group.addLayer(other)
    assert azimuthal.enable()
    helper = azimuthal.helper_layer()
    assert hamq_group().findLayer(helper.id()) is not None
    assert len(project.layerTreeRoot().findGroups()) == 1  # no second HamQ group
    azimuthal.disable()
    assert hamq_group() is not None
    assert [node.layerId() for node in hamq_group().findLayers()] == [other.id()]


def test_enabling_twice_keeps_one_helper_and_the_first_crs(azimuthal, canvas):
    original, _extent = start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    assert azimuthal.enable()
    helpers = [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if layer.name().startswith("Azimuthal map grid")
    ]
    assert len(helpers) == 1
    azimuthal.disable()
    assert QgsProject.instance().crs() == original
    assert azimuthal.changes == [True, False]


def test_new_locator_recenters(azimuthal, canvas, settings):
    original, _extent = start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    settings.my_grid = "JN58td"
    events().settingsChanged.emit()
    assert azimuthal.is_enabled()
    assert azimuthal.center_locator() == "JN58td"
    assert QgsProject.instance().crs() == expected_crs("JN58td")
    assert azimuthal.helper_layer().name() == "Azimuthal map grid (JN58td)"
    assert len(QgsProject.instance().mapLayers()) == 1
    settings.my_grid = ""  # an invalid locator keeps the current centre
    events().settingsChanged.emit()
    assert azimuthal.center_locator() == "JN58td"
    azimuthal.disable()
    assert QgsProject.instance().crs() == original
    assert azimuthal.changes == [True, False]


def test_project_cleared_while_enabled(azimuthal, canvas):
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    QgsProject.instance().clear()
    assert not azimuthal.is_enabled()
    assert azimuthal.helper_layer() is None
    assert azimuthal.changes == [True, False]
    crs = QgsCoordinateReferenceSystem("EPSG:32634")
    QgsProject.instance().setCrs(crs)
    azimuthal.disable()  # nothing to restore into the new project
    assert QgsProject.instance().crs() == crs
    assert azimuthal.changes == [True, False]


def test_user_picks_another_crs(azimuthal, canvas):
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    layer_id = azimuthal.helper_layer().id()
    crs = QgsCoordinateReferenceSystem("EPSG:32634")
    QgsProject.instance().setCrs(crs)
    assert not azimuthal.is_enabled()
    assert QgsProject.instance().mapLayer(layer_id) is None
    assert QgsProject.instance().crs() == crs  # the user's choice stays
    azimuthal.disable()
    assert QgsProject.instance().crs() == crs
    assert azimuthal.changes == [True, False]


def test_user_removes_the_helper_layer(azimuthal, canvas):
    original, _extent = start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    QgsProject.instance().removeMapLayer(azimuthal.helper_layer().id())
    assert azimuthal.is_enabled()
    assert azimuthal.helper_layer() is None
    azimuthal.disable()
    assert QgsProject.instance().crs() == original


def test_language_change_renames_the_helper(azimuthal, canvas):
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    assert azimuthal.helper_layer().name() == "Azimuthal map grid (KN04ft)"
    set_language(LANG_SR_LATN)
    events().languageChanged.emit(LANG_SR_LATN)
    assert azimuthal.helper_layer().name() == "Mreža azimutalne karte (KN04ft)"


def test_set_enabled_slot(azimuthal, canvas):
    original, _extent = start_project(canvas, "EPSG:3857")
    assert azimuthal.set_enabled(True) is True
    assert azimuthal.set_enabled(False) is False
    assert QgsProject.instance().crs() == original


def test_checkable_action_follows_the_map(iface, settings, canvas):
    """The wiring the plugin uses: toggled -> set_enabled, enabledChanged -> setChecked."""
    start_project(canvas, "EPSG:3857")
    azimuthal = AzimuthalMap(iface, settings)
    action = compat.QAction("Azimuthal map", iface.mainWindow())
    action.setCheckable(True)
    action.toggled.connect(azimuthal.set_enabled)
    azimuthal.enabledChanged.connect(action.setChecked)
    try:
        settings.my_grid = ""
        action.trigger()  # no locator: the map stays off and the action unchecks itself
        assert not azimuthal.is_enabled()
        assert not action.isChecked()

        settings.my_grid = "KN04ft"
        action.trigger()
        assert azimuthal.is_enabled() and action.isChecked()
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:32634"))  # by the user
        assert not azimuthal.is_enabled() and not action.isChecked()

        action.trigger()
        assert azimuthal.is_enabled() and action.isChecked()
        action.trigger()
        assert not azimuthal.is_enabled() and not action.isChecked()
        assert QgsProject.instance().crs() == QgsCoordinateReferenceSystem("EPSG:32634")
    finally:
        azimuthal.cleanup()
        action.deleteLater()


def skips_scratch_layer_question(layer) -> bool:
    """QGIS asks "temporary scratch layers ... will be lost" before closing a project
    (quit, new or another project) for a memory layer with features, unless the layer
    has the custom property ``skipMemoryLayersCheck``."""
    return bool(int(layer.customProperty("skipMemoryLayersCheck") or 0))


def test_helper_never_triggers_the_scratch_layer_question(azimuthal, canvas, settings):
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    assert skips_scratch_layer_question(azimuthal.helper_layer())
    settings.my_grid = "JN58td"  # re-centred: a new helper layer
    events().settingsChanged.emit()
    assert azimuthal.center_locator() == "JN58td"
    assert skips_scratch_layer_question(azimuthal.helper_layer())


def save_and_reopen(tmp_path, before_reading=None):
    """Write the project, clear it, (change something,) read it again."""
    path = str(tmp_path / "azimuthal.qgz")
    project = QgsProject.instance()
    assert project.write(path)
    project.clear()
    if before_reading is not None:
        before_reading()
    assert project.read(path)
    return project


def test_project_saved_with_the_map_on_reopens_with_it(azimuthal, canvas, tmp_path):
    """The helper is a memory layer: a project keeps the layer but not its features."""
    original, extent = start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    project = save_and_reopen(tmp_path)
    assert azimuthal.is_enabled()
    assert azimuthal.changes == [True, False, True]  # cleared by reading, then on again
    assert azimuthal.center_locator() == "KN04ft"
    assert project.crs() == expected_crs("KN04ft")
    assert azimuthal.crs() == project.crs()
    layer = azimuthal.helper_layer()
    assert layer is not None
    assert list(project.mapLayers().values()) == [layer]  # the saved helper, filled again
    features = list(layer.getFeatures())
    assert len([f for f in features if f["kind"] == RING_KIND]) == 8
    assert len([f for f in features if f["kind"] == AZIMUTH_KIND]) == 12
    assert skips_scratch_layer_question(layer)
    assert hamq_group().findLayer(layer.id()) is not None
    assert not project.isDirty()  # opening a project changes nothing in it
    # switching off restores the CRS and the view from before the map was switched on
    azimuthal.disable()
    assert not azimuthal.is_enabled()
    assert project.crs() == original
    assert canvas.mapSettings().destinationCrs() == original
    assert project.mapLayers() == {}
    assert hamq_group() is None  # the map created the group, now empty again
    center = canvas.extent().center()
    assert center.x() == pytest.approx(extent.center().x())
    assert center.y() == pytest.approx(extent.center().y())
    assert azimuthal.changes == [True, False, True, False]


def test_reopened_helper_follows_the_hamq_language(azimuthal, canvas, tmp_path):
    start_project(canvas, "EPSG:4326")
    assert azimuthal.enable()  # saved with the English name
    save_and_reopen(tmp_path, before_reading=lambda: set_language(LANG_SR_LATN))
    assert azimuthal.helper_layer().name() == "Mreža azimutalne karte (KN04ft)"


def test_saved_helper_without_the_azimuthal_crs_is_removed(azimuthal, canvas, tmp_path):
    """E.g. the CRS was changed in a QGIS without HamQ: the empty helper is a leftover."""
    start_project(canvas, "EPSG:3857")
    assert azimuthal.enable()
    project = QgsProject.instance()
    other = QgsCoordinateReferenceSystem("EPSG:32634")
    project.blockSignals(True)  # HamQ does not see this change
    try:
        project.setCrs(other)
    finally:
        project.blockSignals(False)
    save_and_reopen(tmp_path)
    assert not azimuthal.is_enabled()
    assert project.mapLayers() == {}
    assert hamq_group() is None  # the map had created it for the helper
    assert project.crs() == other
    assert azimuthal.changes == [True, False]


def test_unusable_saved_helpers(azimuthal, canvas, tmp_path):
    """An empty saved helper that cannot be used is a leftover; features are never lost."""
    start_project(canvas, "EPSG:3857")
    project = QgsProject.instance()
    uri = "LineString?crs=EPSG:3857&field=kind:string&field=value:double&field=label:string"

    def tagged(name, state):
        layer = QgsVectorLayer(uri, name, "memory")
        layer.setCustomProperty(STATE_PROPERTY, state)
        assert project.addMapLayer(layer) is layer
        return layer

    tagged("unreadable", "not json")
    tagged("another centre", json.dumps({"locator": "JN58td"}))  # not the project CRS
    project.layerTreeRoot().addGroup(GROUP_NAME)  # the user's group: it stays
    save_and_reopen(tmp_path)
    assert project.mapLayers() == {}
    assert hamq_group() is not None
    assert not azimuthal.is_enabled()

    kept = tagged("with features", "{}")  # e.g. features kept by another plugin
    feature = QgsFeature(kept.fields())
    feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(1, 1)]))
    assert kept.dataProvider().addFeatures([feature])[0]
    project.readProject.emit(QDomDocument())
    assert project.mapLayer(kept.id()) is kept
    assert not azimuthal.is_enabled()
    assert azimuthal.changes == []


def test_cleanup_restores_and_disconnects(iface, settings, canvas):
    obj = events()
    before = (obj.receivers(obj.languageChanged), obj.receivers(obj.settingsChanged))
    original, _extent = start_project(canvas, "EPSG:3857")
    azimuthal = AzimuthalMap(iface, settings)
    assert azimuthal.enable()
    azimuthal.cleanup()
    assert QgsProject.instance().crs() == original
    assert QgsProject.instance().mapLayers() == {}
    azimuthal.cleanup()
    assert (obj.receivers(obj.languageChanged), obj.receivers(obj.settingsChanged)) == before
    # disconnected from the project too: a CRS change or clear is no longer seen
    changes = []
    azimuthal.enabledChanged.connect(changes.append)
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    QgsProject.instance().clear()
    QgsProject.instance().readProject.emit(QDomDocument())
    assert changes == []
