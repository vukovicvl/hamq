"""hamq.qgis_io.layers: QSO layers in the project, refresh, field aliases, translation."""

from __future__ import annotations

import os
import threading

import pytest
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)

from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, set_language
from hamq.core.qso import QSO_FIELDS, Station, records_to_qsos
from hamq.events import events
from hamq.qgis_io import gpkg, layers


@pytest.fixture(autouse=True)
def english():
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)


def qsos(count: int = 3, start: int = 0):
    records = [
        {
            "CALL": f"T{index}T",
            "QSO_DATE": "20260915",
            "TIME_ON": f"18{index:02d}00",
            "BAND": "20m",
            "MODE": "FT8",
            "GRIDSQUARE": "JN95",
        }
        for index in range(start, start + count)
    ]
    result, _ = records_to_qsos(records, station=Station("YU1XX", "KN04ft"))
    return result


def alias(layer: QgsVectorLayer, name: str) -> str:
    return layer.attributeAlias(layer.fields().lookupField(name))


_written = iter(range(1_000_000))


def write_directly(path: str, count: int) -> None:
    """Add QSO points through a layer outside the project (another program, say)."""
    writer = QgsVectorLayer(gpkg.layer_uri(path, "qso"), "writer", "ogr")
    features = []
    for index in range(count):
        number = next(_written)
        feature = QgsFeature(writer.fields())
        feature.setAttribute("call", f"X{number}X")
        feature.setAttribute("dedup_key", f"X{number}X|direct")
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(-50.0 - index, 10.0)))
        features.append(feature)
    assert writer.dataProvider().addFeatures(features)[0]
    del writer


# --- loading ---------------------------------------------------------------------------------


def test_load_layers_creates_the_file_group_and_styled_layers(tmp_path, clean_project):
    other = QgsVectorLayer("Point?crs=EPSG:4326", "Other layer", "memory")
    clean_project.addMapLayer(other)
    path = str(tmp_path / "logs" / "log.gpkg")

    qso_layer, path_layer = layers.load_layers(path)

    assert os.path.isfile(path)
    assert qso_layer.isValid() and path_layer.isValid()
    assert qso_layer.source() == gpkg.layer_uri(path, "qso")
    assert path_layer.source() == gpkg.layer_uri(path, "qso_path")
    assert (qso_layer.name(), path_layer.name()) == ("QSOs", "QSO paths")
    root = clean_project.layerTreeRoot()
    first = root.children()[0]
    assert first.name() == layers.GROUP_NAME  # the group is on top
    assert [node.layerId() for node in first.children()] == [qso_layer.id(), path_layer.id()]
    assert isinstance(qso_layer.renderer(), QgsCategorizedSymbolRenderer)
    assert isinstance(path_layer.renderer(), QgsCategorizedSymbolRenderer)
    assert qso_layer.renderer().classAttribute() == "band"
    assert alias(qso_layer, "call") == "Callsign"
    assert alias(qso_layer, "distance_km") == "Distance (km)"
    assert alias(path_layer, "qso_fid") == "QSO ID"
    assert alias(qso_layer, "fid") == ""
    assert qso_layer.displayExpression() == '"call"'


def test_load_layers_twice_adds_nothing(tmp_gpkg, clean_project):
    first = layers.load_layers(tmp_gpkg)
    count = len(clean_project.mapLayers())
    second = layers.load_layers(tmp_gpkg)
    assert second == first
    assert len(clean_project.mapLayers()) == count
    groups = [n for n in clean_project.layerTreeRoot().children() if n.name() == layers.GROUP_NAME]
    assert len(groups) == 1


def test_load_layers_uses_an_existing_group_and_adds_the_missing_layer(tmp_gpkg, clean_project):
    gpkg.ensure_gpkg(tmp_gpkg)
    root = clean_project.layerTreeRoot()
    root.addGroup("Base maps")
    group = root.addGroup(layers.GROUP_NAME)  # below the other group
    paths_by_hand = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso_path"), "my paths", "ogr")
    clean_project.addMapLayer(paths_by_hand, False)
    group.addLayer(paths_by_hand)

    qso_layer, path_layer = layers.load_layers(tmp_gpkg)

    assert path_layer is paths_by_hand
    assert path_layer.name() == "my paths"  # a layer added by hand is left as it is
    assert [node.name() for node in root.children()] == ["Base maps", layers.GROUP_NAME]
    assert [n.layerId() for n in root.findGroup(layers.GROUP_NAME).children()] == [
        qso_layer.id(),
        path_layer.id(),
    ]


def test_load_layers_keeps_a_style_saved_in_the_geopackage(tmp_gpkg, clean_project):
    gpkg.ensure_gpkg(tmp_gpkg)
    styled = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso"), "styled", "ogr")
    styled.setRenderer(QgsSingleSymbolRenderer.defaultRenderer(styled.geometryType()))
    save_v2 = getattr(styled, "saveStyleToDatabaseV2", None)  # QGIS 3.40+; the old one is
    if save_v2 is not None:  # deprecated in QGIS 4
        save_v2("mine", "", True, "")
    else:
        error = styled.saveStyleToDatabase("mine", "", True, "")
        assert not error, error
    del styled

    qso_layer, path_layer = layers.load_layers(tmp_gpkg)

    assert isinstance(qso_layer.renderer(), QgsSingleSymbolRenderer)
    assert isinstance(path_layer.renderer(), QgsCategorizedSymbolRenderer)
    assert alias(qso_layer, "call") == "Callsign"


def test_load_layers_reports_a_bad_file(tmp_gpkg, clean_project):
    with open(tmp_gpkg, "w", encoding="utf-8") as handle:
        handle.write("not a geopackage")
    with pytest.raises(gpkg.GpkgError):
        layers.load_layers(tmp_gpkg)
    assert clean_project.mapLayers() == {}


def test_load_layers_names_follow_the_language(tmp_gpkg, clean_project):
    set_language(LANG_SR_LATN)
    qso_layer, path_layer = layers.load_layers(tmp_gpkg)
    assert (qso_layer.name(), path_layer.name()) == ("Veze", "Putanje veza")
    assert alias(qso_layer, "call") == "Pozivni znak"
    categories = qso_layer.renderer().categories()
    assert categories[-1].label() == "Ostali opsezi"


# --- finding ----------------------------------------------------------------------------------


def test_find_layers_by_source(tmp_path, clean_project):
    path = str(tmp_path / "log.gpkg")
    other_path = str(tmp_path / "other.gpkg")
    gpkg.ensure_gpkg(path)
    gpkg.ensure_gpkg(other_path)
    assert layers.find_layers(path) == (None, None)

    link = tmp_path / "link.gpkg"
    os.symlink(path, link)
    (tmp_path / "x").mkdir()
    by_link = QgsVectorLayer(gpkg.layer_uri(str(link), "qso"), "by link", "ogr")
    spelled = QgsVectorLayer(
        gpkg.layer_uri(str(tmp_path / "x" / ".." / "log.gpkg"), "QSO_PATH"), "spelled", "ogr"
    )
    other = QgsVectorLayer(gpkg.layer_uri(other_path, "qso"), "other", "ogr")
    filtered = QgsVectorLayer(
        gpkg.layer_uri(path, "qso") + "|subset=\"band\" = '20m'", "filtered", "ogr"
    )
    memory = QgsVectorLayer("Point?crs=EPSG:4326", "qso", "memory")
    for layer in (memory, other, by_link, spelled, filtered):  # each new layer goes on top
        assert layer.isValid(), layer.name()
        clean_project.addMapLayer(layer)

    qso_layer, path_layer = layers.find_layers(path)
    assert qso_layer is by_link  # the filtered layer is higher, but filtered
    assert path_layer is spelled
    assert set(layers.matching_layers(path)) == {by_link, spelled, filtered}
    assert layers.find_layers(other_path) == (other, None)


def test_find_layers_prefers_the_hamq_group(tmp_gpkg, clean_project):
    gpkg.ensure_gpkg(tmp_gpkg)
    loose = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso"), "loose", "ogr")
    clean_project.addMapLayer(loose)
    grouped = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso"), "grouped", "ogr")
    clean_project.addMapLayer(grouped, False)
    clean_project.layerTreeRoot().addGroup(layers.GROUP_NAME).addLayer(grouped)
    assert layers.find_layers(tmp_gpkg)[0] is grouped


def test_find_layers_takes_the_highest_unfiltered_layer(tmp_gpkg, clean_project):
    gpkg.ensure_gpkg(tmp_gpkg)
    lower = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso"), "lower", "ogr")
    upper = QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso"), "upper", "ogr")
    clean_project.addMapLayer(lower)
    clean_project.addMapLayer(upper)
    assert layers.find_layers(tmp_gpkg)[0] is upper


def test_find_layers_skips_invalid_layers(tmp_path, clean_project):
    path = str(tmp_path / "gone.gpkg")
    broken = QgsVectorLayer(gpkg.layer_uri(path, "qso"), "broken", "ogr")
    assert not broken.isValid()
    clean_project.addMapLayer(broken)
    assert layers.find_layers(path) == (None, None)
    qso_layer, _ = layers.load_layers(path)  # a new, valid layer next to the broken one
    assert qso_layer is not broken and qso_layer.isValid()


# --- refreshing ---------------------------------------------------------------------------------


def test_refresh_layers_shows_new_data(tmp_gpkg, clean_project):
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    repaints = []
    qso_layer.repaintRequested.connect(lambda *args: repaints.append(args))
    write_directly(tmp_gpkg, 2)
    layers.refresh_layers(tmp_gpkg)
    assert qso_layer.featureCount() == 2
    assert qso_layer.extent().xMinimum() == pytest.approx(-51.0)
    assert repaints


def test_refresh_layers_leaves_edited_layers_alone(tmp_gpkg, clean_project):
    gpkg.insert_qsos(tmp_gpkg, qsos(2))
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    assert qso_layer.startEditing()
    fid = min(feature.id() for feature in qso_layer.getFeatures())
    index = qso_layer.fields().lookupField("rst_rcvd")
    assert qso_layer.changeAttributeValue(fid, index, "-05")
    write_directly(tmp_gpkg, 1)
    layers.refresh_layers(tmp_gpkg)
    assert qso_layer.isEditable()
    assert qso_layer.editBuffer().changedAttributeValues() == {fid: {index: "-05"}}
    assert qso_layer.commitChanges(), qso_layer.commitErrors()
    assert [row["rst_rcvd"] for row in gpkg.read_qso_rows(tmp_gpkg)][0] == "-05"


def test_refresh_layers_outside_the_main_thread_does_nothing(tmp_gpkg, clean_project):
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    write_directly(tmp_gpkg, 1)
    errors = []

    def work() -> None:
        try:
            layers.refresh_layers(tmp_gpkg)
            layers.retranslate_layers()
        except BaseException as exc:  # reported below
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(30)
    assert errors == []
    assert qso_layer.featureCount() in (0, -1)  # the old count (QGIS 3.34: -1, unknown)
    layers.refresh_layers(tmp_gpkg)
    assert qso_layer.featureCount() == 1


# --- aliases and translation ------------------------------------------------------------------


def test_every_column_has_an_alias():
    names = {name for name, _ in QSO_FIELDS} | {"qso_fid"}
    assert names == set(layers.FIELD_ALIASES)


def test_apply_field_aliases_follows_the_language(tmp_gpkg, clean_project):
    qso_layer, path_layer = layers.load_layers(tmp_gpkg)
    set_language(LANG_SR_LATN)
    layers.apply_field_aliases(qso_layer)
    assert alias(qso_layer, "call") == "Pozivni znak"
    assert alias(qso_layer, "band") == "Opseg"
    assert alias(qso_layer, "dxcc") == "DXCC"
    set_language(LANG_SR_CYRL)
    layers.apply_field_aliases(qso_layer)
    assert alias(qso_layer, "call") == "Позивни знак"
    assert alias(qso_layer, "dedup_key") == "Кључ за дупликате"
    set_language(LANG_EN)
    layers.apply_field_aliases(qso_layer)
    assert alias(qso_layer, "call") == "Callsign"
    assert alias(path_layer, "mode") == "Mode"


def test_apply_field_aliases_keeps_aliases_the_user_set(tmp_gpkg, clean_project):
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    qso_layer.setFieldAlias(qso_layer.fields().lookupField("call"), "Znak")
    set_language(LANG_SR_LATN)
    layers.apply_field_aliases(qso_layer)
    assert alias(qso_layer, "call") == "Znak"
    assert alias(qso_layer, "band") == "Opseg"


def test_apply_field_aliases_on_a_layer_hamq_did_not_load():
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=call:string&field=own:string&field=CQ_ZONE:integer",
        "memory",
        "memory",
    )
    layer.setFieldAlias(2, "Zone")
    layers.apply_field_aliases(layer)
    assert layer.attributeAlias(0) == "Callsign"
    assert layer.attributeAlias(1) == ""
    assert layer.attributeAlias(2) == "Zone"  # set before: the user's


def test_retranslate_layers(tmp_gpkg, clean_project):
    qso_layer, path_layer = layers.load_layers(tmp_gpkg)
    path_layer.setName("My paths")
    set_language(LANG_SR_LATN)
    layers.retranslate_layers()
    assert qso_layer.name() == "Veze"
    assert path_layer.name() == "My paths"  # renamed by the user
    assert alias(qso_layer, "call") == "Pozivni znak"
    assert alias(path_layer, "qso_fid") == "ID veze"
    assert qso_layer.renderer().categories()[-1].label() == "Ostali opsezi"
    set_language(LANG_SR_CYRL)
    layers.retranslate_layers()
    assert qso_layer.name() == "Везе"
    set_language(LANG_EN)
    layers.retranslate_layers()
    assert qso_layer.name() == "QSOs"
    assert path_layer.name() == "My paths"


def test_connect_events(tmp_gpkg, clean_project):
    qso_layer, _ = layers.load_layers(tmp_gpkg)
    disconnect = layers.connect_events()
    try:
        write_directly(tmp_gpkg, 1)
        events().dataChanged.emit(tmp_gpkg)
        assert qso_layer.featureCount() == 1
        set_language(LANG_SR_LATN)
        events().languageChanged.emit(LANG_SR_LATN)
        assert qso_layer.name() == "Veze"
        events().dataChanged.emit(os.path.join(os.path.dirname(tmp_gpkg), "other.gpkg"))
    finally:
        disconnect()
    disconnect()  # safe twice
    write_directly(tmp_gpkg, 1)
    events().dataChanged.emit(tmp_gpkg)
    assert qso_layer.featureCount() in (1, -1)  # no longer connected: not refreshed
    set_language(LANG_EN)
    events().languageChanged.emit(LANG_EN)
    assert qso_layer.name() == "Veze"


def test_slots_log_errors_instead_of_raising(tmp_gpkg, clean_project, monkeypatch, log_messages):
    layers.load_layers(tmp_gpkg)
    disconnect = layers.connect_events()

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(layers, "refresh_layers", broken)
    monkeypatch.setattr(layers, "retranslate_layers", broken)
    try:
        events().dataChanged.emit(tmp_gpkg)
        events().languageChanged.emit(LANG_EN)
    finally:
        disconnect()
    messages = [message for message, tag, _ in log_messages if tag == "HamQ"]
    assert sum("boom" in message for message in messages) == 2


def test_layers_are_saved_and_read_with_the_project(tmp_path, clean_project):
    path = str(tmp_path / "log.gpkg")
    qso_layer, _ = layers.load_layers(path)
    project_file = str(tmp_path / "project.qgz")
    assert clean_project.write(project_file)
    clean_project.clear()
    assert clean_project.read(project_file)
    found, _ = layers.find_layers(path)
    assert found is not None and found.name() == "QSOs"
    set_language(LANG_SR_LATN)
    layers.retranslate_layers()
    assert found.name() == "Veze"
    assert alias(found, "call") == "Pozivni znak"
    QgsProject.instance().clear()
