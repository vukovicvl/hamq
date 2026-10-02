"""Processing provider ``hamq`` and its algorithms.

``hamq:locator_to_point``, ``hamq:maidenhead_grid``, ``hamq:import_adif`` and
``hamq:recalculate``: registration and translated names, results on the ADIF and
cty.dat fixtures (cty.dat comes from a monkeypatched ``load_cached_cty``), the
``events().dataChanged`` notification from ``postProcessAlgorithm`` (also through a
background ``QgsProcessingAlgRunnerTask``, as the toolbox runs them), cancelation,
the PLAN acceptance timings (marked ``slow``) and a ``qgis_process`` smoke test.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, Qt

from hamq.core import adif, geo, maidenhead
from hamq.core.cty import CtyDatabase
from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, latin_to_cyrillic, set_language
from hamq.events import events
from hamq.gui import icon_path
from hamq.net import cty_download
from hamq.processing import alg_grid, common
from hamq.processing import provider as provider_module
from hamq.processing.alg_grid import extent_to_wgs84
from hamq.processing.provider import HamQProvider
from hamq.qgis_io import compat, gpkg, layers
from hamq.settings import HamQSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
ADIF = FIXTURES / "adif"
CTY = FIXTURES / "cty"
ALGORITHM_IDS = (
    "hamq:locator_to_point",
    "hamq:maidenhead_grid",
    "hamq:import_adif",
    "hamq:recalculate",
)
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
EDIT_NEW = (
    "The QSO layers are in edit mode. The new QSOs were saved in the GeoPackage "
    "and show up after you save or discard your edits."
)
EDIT_RECALCULATED = (
    "The QSO layers are in edit mode. The recalculated values were saved in the "
    "GeoPackage; saving your edits may overwrite some of them."
)


# --- fixtures and helpers -----------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def hamq_provider(qgis_processing):
    """The ``hamq`` provider in the Processing registry for the tests of this module."""
    registry = QgsApplication.processingRegistry()
    existing = registry.providerById("hamq")
    if existing is not None:
        yield existing
        return
    provider = HamQProvider()
    assert registry.addProvider(provider)
    yield provider
    registry.removeProvider(provider)


@pytest.fixture(autouse=True)
def english(hamq_provider):
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)
    hamq_provider.refreshAlgorithms()


@pytest.fixture(scope="module")
def cty_database() -> CtyDatabase:
    return CtyDatabase.from_files(str(CTY / "cty_excerpt.dat"), str(CTY / "cty_excerpt.csv"))


@pytest.fixture
def cty_calls(monkeypatch, cty_database) -> list:
    """``load_cached_cty`` returns the cty.dat excerpt; the list records the calls."""
    calls: list = []

    def fake(path=None):
        calls.append(path)
        return cty_database

    monkeypatch.setattr(cty_download, "load_cached_cty", fake)
    return calls


@pytest.fixture
def no_cty(monkeypatch) -> list:
    """``load_cached_cty`` finds no cty.dat; the list records the calls."""
    calls: list = []

    def fake(path=None):
        calls.append(path)

    monkeypatch.setattr(cty_download, "load_cached_cty", fake)
    return calls


@pytest.fixture
def data_changed():
    """``(path, in the main thread)`` of every ``events().dataChanged`` emission."""
    received: list[tuple[str, bool]] = []

    def slot(path: str) -> None:
        received.append((path, common.in_main_thread()))

    events().dataChanged.connect(slot)
    yield received
    events().dataChanged.disconnect(slot)


class Feedback(QgsProcessingFeedback):
    """Collects the messages an algorithm sends."""

    def __init__(self) -> None:
        super().__init__()
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[tuple[str, bool]] = []

    def pushInfo(self, info: str) -> None:
        self.infos.append(info)

    def pushWarning(self, warning: str) -> None:
        self.warnings.append(warning)

    def reportError(self, error: str, fatalError: bool = False) -> None:
        self.errors.append((error, fatalError))


def run(algorithm_id: str, parameters: dict, feedback: Feedback | None = None):
    """``processing.run`` (in this thread); returns ``(results, feedback)``."""
    import processing

    feedback = feedback if feedback is not None else Feedback()
    return processing.run(algorithm_id, parameters, feedback=feedback), feedback


def run_in_task(algorithm_id: str, parameters: dict, feedback: Feedback | None = None):
    """Run like the Processing toolbox does: ``processAlgorithm`` in a worker thread of a
    ``QgsProcessingAlgRunnerTask``, ``postProcessAlgorithm`` in the main thread. Returns
    ``(ok, results, feedback)`` after the task finished and the events were processed."""
    feedback = feedback if feedback is not None else Feedback()
    algorithm = QgsApplication.processingRegistry().createAlgorithmById(algorithm_id)
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    outcome: dict = {}

    def executed(ok: bool, results: dict) -> None:
        outcome["ok"], outcome["results"] = ok, results

    task = QgsProcessingAlgRunnerTask(algorithm, parameters, context, feedback)
    task.executed.connect(executed)
    QgsApplication.taskManager().addTask(task)
    deadline = time.monotonic() + 120.0
    while "ok" not in outcome and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    for _ in range(20):  # queued signals from the worker thread
        QCoreApplication.processEvents()
    assert "ok" in outcome, "the Processing task did not finish"
    return outcome["ok"], outcome["results"], feedback


def import_parameters(adif_path, gpkg_path: str, **extra) -> dict:
    parameters = {
        "INPUT": str(adif_path),
        "GPKG": gpkg_path,
        "MY_GRID": "KN04ft",
        "USE_CTY": True,
        "LOAD_LAYERS": False,
    }
    parameters.update(extra)
    return parameters


def write_log(path: Path, count: int, seed: int = 20261001) -> Path:
    """An ADIF file with ``count`` distinct QSOs (random locators, own locator KN04ft)."""
    rng = random.Random(seed)
    bands = ["160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"]
    records = []
    for i in range(count):
        records.append(
            {
                "CALL": f"T{i:05d}X",
                "QSO_DATE": "20260915",
                "TIME_ON": f"{i // 600 % 24:02d}{i // 10 % 60:02d}00",
                "BAND": rng.choice(bands),
                "MODE": "FT8",
                "GRIDSQUARE": maidenhead.to_locator(
                    rng.uniform(-60.0, 75.0), rng.uniform(-180.0, 180.0), 6
                ),
            }
        )
    path.write_bytes(adif.format_document(records).encode("utf-8"))
    return path


def features(layer: QgsVectorLayer) -> list:
    return sorted(layer.getFeatures(), key=lambda feature: feature.id())


def rows_by_call(path: str) -> dict[str, dict]:
    return {row["call"]: row for row in gpkg.read_qso_rows(path)}


# --- provider -----------------------------------------------------------------------------------


def test_provider_lists_the_four_algorithms(hamq_provider):
    assert [alg_class.__name__ for alg_class in provider_module.ALGORITHMS] == [
        "LocatorToPointAlgorithm",
        "MaidenheadGridAlgorithm",
        "ImportAdifAlgorithm",
        "RecalculateAlgorithm",
    ]
    assert sorted(alg.id() for alg in hamq_provider.algorithms()) == sorted(ALGORITHM_IDS)
    registry = QgsApplication.processingRegistry()
    groups = {}
    for algorithm_id in ALGORITHM_IDS:
        algorithm = registry.algorithmById(algorithm_id)
        assert algorithm is not None, algorithm_id
        assert algorithm.provider().id() == "hamq"
        assert algorithm.displayName() and algorithm.shortHelpString()
        assert algorithm.tags() and all(tag == tag.strip() for tag in algorithm.tags())
        assert os.path.isfile(algorithm.svgIconPath())
        assert not algorithm.icon().isNull()
        assert type(algorithm.createInstance()) is type(algorithm)
        groups[algorithm_id] = (algorithm.groupId(), algorithm.group())
    assert groups == {
        "hamq:locator_to_point": ("maidenhead", "Maidenhead locators"),
        "hamq:maidenhead_grid": ("maidenhead", "Maidenhead locators"),
        "hamq:import_adif": ("log", "QSO log"),
        "hamq:recalculate": ("log", "QSO log"),
    }
    assert registry.algorithmById("hamq:maidenhead_grid").svgIconPath() == icon_path("grid.svg")


def test_parameters_and_outputs():
    registry = QgsApplication.processingRegistry()

    def names(algorithm_id):
        algorithm = registry.algorithmById(algorithm_id)
        return (
            [p.name() for p in algorithm.parameterDefinitions()],
            sorted(o.name() for o in algorithm.outputDefinitions()),
        )

    assert names("hamq:locator_to_point") == (["LOCATORS", "OUTPUT"], ["OUTPUT"])
    assert names("hamq:maidenhead_grid") == (["EXTENT", "LEVEL", "OUTPUT"], ["OUTPUT"])
    assert names("hamq:import_adif") == (
        ["INPUT", "GPKG", "MY_GRID", "USE_CTY", "LOAD_LAYERS", "MAX_SIZE_MB"],
        ["DUPLICATES", "GPKG", "IMPORTED", "SKIPPED"],
    )
    assert names("hamq:recalculate") == (
        ["GPKG", "MY_GRID", "USE_CTY", "FORCE_STATION"],
        ["GPKG", "UPDATED"],
    )
    grid = registry.algorithmById("hamq:maidenhead_grid")
    assert grid.parameterDefinition("LEVEL").options() == [
        "Field (2 characters, 20° x 10°)",
        "Square (4 characters, 2° x 1°)",
        "Subsquare (6 characters, 5' x 2.5')",
        'Extended square (8 characters, 30" x 15")',
    ]
    assert grid.parameterDefinition("LEVEL").defaultValue() == 1
    importer = registry.algorithmById("hamq:import_adif")
    assert importer.parameterDefinition("MY_GRID").flags() & compat.PARAM_FLAG_OPTIONAL
    assert importer.parameterDefinition("USE_CTY").defaultValue() is True
    assert importer.parameterDefinition("LOAD_LAYERS").defaultValue() is True
    assert importer.parameterDefinition("GPKG").defaultFileExtension() == "gpkg"
    limit = importer.parameterDefinition("MAX_SIZE_MB")
    assert limit.defaultValue() == 200 and limit.minimum() == 1
    assert limit.flags() & compat.PARAM_FLAG_ADVANCED
    assert "memory" in limit.help()


@pytest.mark.parametrize("language", [LANG_SR_LATN, LANG_SR_CYRL])
def test_names_follow_the_language(hamq_provider, language):
    convert = latin_to_cyrillic if language == LANG_SR_CYRL else (lambda text: text)
    set_language(language)
    hamq_provider.refreshAlgorithms()
    registry = QgsApplication.processingRegistry()
    expected = {
        "hamq:locator_to_point": "Lokator u tačku",
        "hamq:maidenhead_grid": "Napravi Maidenhead mrežu",
        "hamq:import_adif": "Uvezi ADIF",
        "hamq:recalculate": "Ponovo izračunaj rastojanja i DXCC podatke",
    }
    for algorithm_id, name in expected.items():
        assert registry.algorithmById(algorithm_id).displayName() == convert(name)
    assert sorted(alg.displayName() for alg in hamq_provider.algorithms()) == sorted(
        convert(name) for name in expected.values()
    )
    grid = registry.algorithmById("hamq:maidenhead_grid")
    assert grid.group() == convert("Maidenhead lokatori")
    assert grid.parameterDefinition("LEVEL").description() == convert("Nivo")
    assert grid.parameterDefinition("LEVEL").options()[1] == convert("Kvadrat (4 znaka, 2° x 1°)")
    assert "EPSG:4326" in grid.shortHelpString()
    importer = registry.algorithmById("hamq:import_adif")
    assert importer.group() == convert("Log veza")
    assert importer.parameterDefinition("MY_GRID").description() == convert("Moj QTH lokator")
    assert importer.outputDefinition("IMPORTED").description() == convert("Uvezene veze")
    assert "*.adi *.adif" in importer.parameterDefinition("INPUT").fileFilter()
    # and back
    set_language(LANG_EN)
    hamq_provider.refreshAlgorithms()
    assert registry.algorithmById("hamq:import_adif").displayName() == "Import ADIF"
    assert registry.algorithmById("hamq:import_adif").group() == "QSO log"


def test_messages_are_translated_at_run_time():
    set_language(LANG_SR_LATN)
    _, feedback = run("hamq:locator_to_point", {"LOCATORS": "KN04ft XX99", "OUTPUT": "memory:"})
    assert feedback.errors == [("XX99 nije ispravan Maidenhead lokator, preskočen je", False)]
    with pytest.raises(QgsProcessingException, match="ćelija"):
        run(
            "hamq:maidenhead_grid",
            {"EXTENT": "-25,45,34,72 [EPSG:4326]", "LEVEL": 2, "OUTPUT": "memory:"},
        )


# --- locator to point ---------------------------------------------------------------------------


def test_locator_to_point_creates_center_points():
    results, feedback = run(
        "hamq:locator_to_point",
        {"LOCATORS": "KN04ft, jn95WG;FM18lv\nQF56od\tKN04  KN", "OUTPUT": "memory:"},
    )
    layer = results["OUTPUT"]
    assert layer.crs().authid() == "EPSG:4326"
    assert layer.geometryType() == compat.GEOMETRY_POINT
    assert [field.name() for field in layer.fields()] == ["locator", "precision", "lat", "lon"]
    got = [
        (f["locator"], f["precision"], f["lat"], f["lon"], f.geometry().asPoint())
        for f in features(layer)
    ]
    assert [row[0] for row in got] == ["KN04ft", "JN95wg", "FM18lv", "QF56od", "KN04", "KN"]
    for locator, precision, lat, lon, point in got:
        assert precision == len(locator)
        assert (lat, lon) == maidenhead.to_latlon(locator)
        assert (point.x(), point.y()) == (lon, lat)
    assert feedback.errors == []
    assert "Points: 6" in feedback.infos


def test_locator_to_point_reports_and_skips_invalid_locators():
    results, feedback = run(
        "hamq:locator_to_point",
        {"LOCATORS": "KN04ft XX99, KN0; SA00 KN04fy KN04ft5", "OUTPUT": "memory:"},
    )
    assert [f["locator"] for f in features(results["OUTPUT"])] == ["KN04ft"]
    assert feedback.errors == [
        (f"{token} is not a valid Maidenhead locator, skipped", False)
        for token in ("XX99", "KN0", "SA00", "KN04fy", "KN04ft5")
    ]


def test_locator_to_point_cuts_ten_characters():
    results, feedback = run(
        "hamq:locator_to_point", {"LOCATORS": "kn04ft55ab", "OUTPUT": "memory:"}
    )
    assert [(f["locator"], f["precision"]) for f in features(results["OUTPUT"])] == [
        ("KN04ft55", 8)
    ]
    assert feedback.warnings == ["Locator kn04ft55ab was cut to 8 characters: KN04ft55"]


@pytest.mark.parametrize("text", ["XX99 KN0", " ,;\n "])
def test_locator_to_point_without_a_valid_locator_fails(text):
    with pytest.raises(QgsProcessingException, match="No valid Maidenhead locator"):
        run("hamq:locator_to_point", {"LOCATORS": text, "OUTPUT": "memory:"})


def test_locator_to_point_needs_a_value():
    with pytest.raises(QgsProcessingException, match="LOCATORS"):  # checked by Processing
        run("hamq:locator_to_point", {"LOCATORS": "", "OUTPUT": "memory:"})


def test_locator_to_point_writes_a_geopackage(tmp_path):
    path = str(tmp_path / "points.gpkg")
    results, _ = run("hamq:locator_to_point", {"LOCATORS": "KN04ft JN95wg", "OUTPUT": path})
    assert results["OUTPUT"] == path
    layer = QgsVectorLayer(path, "points", "ogr")
    assert layer.isValid() and layer.featureCount() == 2
    assert {f["locator"] for f in layer.getFeatures()} == {"KN04ft", "JN95wg"}


# --- grid: extent ---------------------------------------------------------------------------------


CONTEXT = QgsCoordinateTransformContext()


def test_extent_in_wgs84_is_clamped_to_the_world():
    assert extent_to_wgs84(QgsRectangle(18, 40, 23, 47), WGS84, CONTEXT) == [
        (40.0, 18.0, 47.0, 23.0)
    ]
    assert extent_to_wgs84(QgsRectangle(170, 80, 200, 100), WGS84, CONTEXT) == [
        (80.0, 170.0, 90.0, 180.0)
    ]
    assert extent_to_wgs84(QgsRectangle(-500, -100, 500, 100), WGS84, CONTEXT) == [
        (-90.0, -180.0, 90.0, 180.0)
    ]
    # no CRS (typed without one, project without CRS): degrees
    assert extent_to_wgs84(
        QgsRectangle(18, 40, 23, 47), QgsCoordinateReferenceSystem(), CONTEXT
    ) == [(40.0, 18.0, 47.0, 23.0)]


@pytest.mark.parametrize(
    "rect", [(200, 0, 210, 10), (-200, 0, -190, 10), (0, 95, 10, 100), (0, -100, 10, -91)]
)
def test_extent_outside_the_world_has_no_parts(rect):
    assert extent_to_wgs84(QgsRectangle(*rect), WGS84, CONTEXT) == []


def test_extent_in_web_mercator():
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    to_crs = QgsCoordinateTransform(WGS84, crs, CONTEXT)
    corners = to_crs.transformBoundingBox(QgsRectangle(-25, 34, 45, 72))
    parts = extent_to_wgs84(corners, crs, CONTEXT)
    assert len(parts) == 1
    assert parts[0] == pytest.approx((34.0, -25.0, 72.0, 45.0), abs=1e-6)


def test_extent_wider_than_the_world_in_web_mercator():
    """QGIS 3.34's transformBoundingBox gives xMinimum > xMaximum for this one."""
    parts = extent_to_wgs84(
        QgsRectangle(-3e7, -3e7, 3e7, 3e7), QgsCoordinateReferenceSystem("EPSG:3857"), CONTEXT
    )
    assert len(parts) == 1
    lat_min, lon_min, lat_max, lon_max = parts[0]
    assert (lon_min, lon_max) == (-180.0, 180.0)
    assert lat_min == pytest.approx(-88.961, abs=1e-3)
    assert lat_max == pytest.approx(88.961, abs=1e-3)


def test_extent_across_the_antimeridian_is_split():
    """PDC Mercator (EPSG:3832) is centered on 150 E: x 2000 to 6000 km crosses 180."""
    parts = extent_to_wgs84(
        QgsRectangle(2e6, -1e6, 6e6, 1e6), QgsCoordinateReferenceSystem("EPSG:3832"), CONTEXT
    )
    assert len(parts) == 2
    (south, west, north, east), (south2, west2, north2, east2) = parts
    assert (east, west2) == (180.0, -180.0)
    assert west == pytest.approx(167.966, abs=1e-3)
    assert east2 == pytest.approx(-156.101, abs=1e-3)
    assert (south, north) == (south2, north2) == pytest.approx((-9.006, 9.006), abs=1e-3)


def test_extent_around_a_pole_reaches_it():
    crs = QgsCoordinateReferenceSystem("EPSG:3995")  # Arctic polar stereographic
    parts = extent_to_wgs84(QgsRectangle(-1e6, -1e6, 1e6, 1e6), crs, CONTEXT)
    assert len(parts) == 1
    lat_min, lon_min, lat_max, lon_max = parts[0]
    assert (lon_min, lat_max, lon_max) == (-180.0, 90.0, 180.0)
    assert lat_min == pytest.approx(77.037, abs=1e-3)
    beside = extent_to_wgs84(QgsRectangle(1e6, 1e6, 2e6, 2e6), crs, CONTEXT)
    assert beside == [pytest.approx((64.386, 116.565, 77.037, 153.435), abs=1e-3)]


def test_extent_of_the_azimuthal_map_beyond_the_antipode():
    """The HamQ azimuthal map (aeqd in km on KN04ft): corners past the antipode cannot be
    transformed; both poles are inside, so the whole world."""
    crs = QgsCoordinateReferenceSystem.fromProj(
        "+proj=aeqd +lat_0=44.8125 +lon_0=20.4583 +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs"
    )
    assert crs.isValid()
    assert extent_to_wgs84(QgsRectangle(-25000, -25000, 25000, 25000), crs, CONTEXT) == [
        (-90.0, -180.0, 90.0, 180.0)
    ]
    near = extent_to_wgs84(QgsRectangle(-500, -500, 500, 500), crs, CONTEXT)
    assert len(near) == 1
    lat_min, lon_min, lat_max, lon_max = near[0]
    assert lat_min < 44.8125 < lat_max and lon_min < 20.4583 < lon_max
    assert lat_max - lat_min < 10 and lon_max - lon_min < 15


# --- grid: algorithm ------------------------------------------------------------------------------


def grid(extent: str, level: int, feedback: Feedback | None = None):
    results, feedback = run(
        "hamq:maidenhead_grid", {"EXTENT": extent, "LEVEL": level, "OUTPUT": "memory:"}, feedback
    )
    return results["OUTPUT"], feedback


@pytest.mark.parametrize(
    "level, extent, box",
    [
        (0, "-30,50,30,70 [EPSG:4326]", (30, -30, 70, 50)),
        (1, "18,23,40,47 [EPSG:4326]", (40, 18, 47, 23)),
        (2, "20.1,20.9,44.6,45.1 [EPSG:4326]", (44.6, 20.1, 45.1, 20.9)),
        (3, "20.45,20.47,44.81,44.82 [EPSG:4326]", (44.81, 20.45, 44.82, 20.47)),
    ],
)
def test_grid_cells_match_the_core(level, extent, box):
    layer, feedback = grid(extent, level)
    length = alg_grid.LEVELS[level]
    expected = list(maidenhead.iter_cells(*box, length))
    assert layer.crs().authid() == "EPSG:4326"
    assert layer.geometryType() == compat.GEOMETRY_POLYGON
    assert [field.name() for field in layer.fields()] == ["locator"]
    got = features(layer)
    assert [f["locator"] for f in got] == [locator for locator, _ in expected]
    for feature, (locator, bounds) in zip(got, expected):
        rect = feature.geometry().boundingBox()
        assert (rect.yMinimum(), rect.xMinimum(), rect.yMaximum(), rect.xMaximum()) == bounds
        assert maidenhead.to_bounds(locator) == bounds
        assert len(locator) == length
    assert f"Grid cells: {len(expected)}" in feedback.infos


def test_grid_extent_in_web_mercator():
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    rect = QgsCoordinateTransform(WGS84, crs, CONTEXT).transformBoundingBox(
        QgsRectangle(13.3, 41.2, 22.7, 46.6)
    )
    extent = f"{rect.xMinimum()},{rect.xMaximum()},{rect.yMinimum()},{rect.yMaximum()} [EPSG:3857]"
    layer, _ = grid(extent, 1)
    assert sorted(f["locator"] for f in features(layer)) == sorted(
        locator for locator, _ in maidenhead.iter_cells(41.2, 13.3, 46.6, 22.7, 4)
    )


def test_grid_across_the_antimeridian():
    layer, feedback = grid("2000000,6000000,-1000000,1000000 [EPSG:3832]", 0)
    assert sorted(f["locator"] for f in features(layer)) == ["AI", "AJ", "BI", "BJ", "RI", "RJ"]
    for feature in features(layer):
        assert feature.geometry().boundingBox().width() == 20.0
    assert len([info for info in feedback.infos if info.startswith("Extent in EPSG:4326")]) == 2


def test_grid_of_a_polar_map_has_every_field_at_the_pole():
    layer, _ = grid("-1000000,1000000,-1000000,1000000 [EPSG:3995]", 0)
    locators = {f["locator"] for f in features(layer)}
    # latitude 77.04 to 90: the rows Q (70-80) and R (80-90) of every field column
    assert locators == {chr(ord("A") + i) + row for i in range(18) for row in "QR"}


def test_grid_of_the_whole_world_in_web_mercator():
    layer, _ = grid("-30000000,30000000,-30000000,30000000 [EPSG:3857]", 0)
    assert layer.featureCount() == 18 * 18


def test_grid_clamps_to_the_world():
    layer, _ = grid("170,200,80,100 [EPSG:4326]", 1)
    expected = [locator for locator, _ in maidenhead.iter_cells(80, 170, 90, 180, 4)]
    assert [f["locator"] for f in features(layer)] == expected


def test_grid_outside_the_world_fails():
    with pytest.raises(QgsProcessingException, match="outside the world"):
        grid("200,210,0,10 [EPSG:4326]", 1)


def test_grid_refuses_too_many_cells():
    count = maidenhead.count_cells(34, -25, 72, 45, maidenhead.LEVEL_SUBSQUARE)
    assert count > maidenhead.MAX_GRID_CELLS
    with pytest.raises(QgsProcessingException) as raised:
        grid("-25,45,34,72 [EPSG:4326]", 2)
    message = str(raised.value)
    assert f"{count} cells" in message and str(maidenhead.MAX_GRID_CELLS) in message
    assert "smaller extent or a coarser level" in message


def test_grid_cancel_stops_early():
    feedback = Feedback()
    feedback.cancel()
    layer, _ = grid("-180,180,-90,90 [EPSG:4326]", 1, feedback)
    assert layer.featureCount() < 18 * 18 * 100


def test_grid_layer_loaded_by_processing_gets_the_grid_style(clean_project):
    import processing

    results = processing.runAndLoadResults(
        "hamq:maidenhead_grid",
        {"EXTENT": "18,23,40,47 [EPSG:4326]", "LEVEL": 1, "OUTPUT": "TEMPORARY_OUTPUT"},
    )
    layer = clean_project.mapLayer(results["OUTPUT"])
    assert isinstance(layer, QgsVectorLayer)
    assert layer.featureCount() == maidenhead.count_cells(40, 18, 47, 23, 4)
    assert layer.labelsEnabled()
    assert layer.labeling().settings().fieldName == "locator"
    # the post-processor is shared by all runs and outlives the algorithm objects
    assert alg_grid.grid_styler() is alg_grid.grid_styler()


@pytest.mark.slow
def test_grid_of_europe_at_square_level_under_2_s():
    """PLAN M1: the square-level grid for Europe in less than 2 s."""
    start = time.perf_counter()
    layer, _ = grid("-25,45,34,72 [EPSG:4326]", 1)
    elapsed = time.perf_counter() - start
    print(f"\nEurope, squares: {layer.featureCount()} cells in {elapsed:.3f} s")
    assert layer.featureCount() == maidenhead.count_cells(34, -25, 72, 45, 4) == 36 * 38
    assert elapsed < 2.0


# --- import ADIF ----------------------------------------------------------------------------------


def test_import_wsjtx_log(tmp_gpkg, cty_calls, data_changed):
    results, feedback = run("hamq:import_adif", import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg))
    assert results == {"IMPORTED": 6, "DUPLICATES": 0, "SKIPPED": 0, "GPKG": tmp_gpkg}
    assert cty_calls == [None]
    rows = rows_by_call(tmp_gpkg)
    assert sorted(rows) == ["9A2XYZ", "IT9XYZ", "KH6XYZ", "OH2XYZ/MM", "W1XYZ", "YU7XYZ"]
    assert {row["source"] for row in rows.values()} == {"adif:wsjtx_log.adi"}
    assert (rows["9A2XYZ"]["mode"], rows["9A2XYZ"]["submode"]) == ("MFSK", "FT4")
    assert rows["IT9XYZ"]["country"] == "Italy"  # Sicily counts for Italy (cty.dat)
    assert rows["IT9XYZ"]["loc_source"] == "grid"
    assert rows["W1XYZ"]["distance_km"] == pytest.approx(
        geo.distance_km(*maidenhead.to_latlon("KN04ft"), *maidenhead.to_latlon("FN42"))
    )
    assert rows["OH2XYZ/MM"]["loc_source"] is None  # maritime mobile: no position
    assert "Imported: 6, duplicates: 0, skipped: 0" in feedback.infos
    assert "Records in the ADIF file: 6" in feedback.infos
    assert any(info.startswith("cty.dat loaded, DXCC entities: ") for info in feedback.infos)
    assert data_changed == [(tmp_gpkg, True)]
    assert QgsVectorLayer(gpkg.layer_uri(tmp_gpkg, "qso_path"), "p", "ogr").featureCount() == 5


def test_import_twice_gives_duplicates(tmp_gpkg, cty_calls, data_changed):
    parameters = import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg)
    run("hamq:import_adif", parameters)
    results, feedback = run("hamq:import_adif", parameters)
    assert results == {"IMPORTED": 0, "DUPLICATES": 6, "SKIPPED": 0, "GPKG": tmp_gpkg}
    assert len(gpkg.read_qso_rows(tmp_gpkg)) == 6
    # LoTW confirmations of two of the WSJT-X QSOs (FT4 as MODE=FT4 there): duplicates too
    results, _ = run("hamq:import_adif", import_parameters(ADIF / "lotw.adi", tmp_gpkg))
    assert results["DUPLICATES"] >= 2
    assert data_changed == [(tmp_gpkg, True)] * 3


def test_import_skips_bad_records_and_logs_them(tmp_gpkg, no_cty, log_messages):
    results, feedback = run(
        "hamq:import_adif", import_parameters(ADIF / "no_eoh.adi", tmp_gpkg, MY_GRID="")
    )
    assert results == {"IMPORTED": 2, "DUPLICATES": 0, "SKIPPED": 3, "GPKG": tmp_gpkg}
    assert any("missing CALL, skipped" in warning for warning in feedback.warnings)
    assert sum("QSO_DATE or TIME_ON, skipped" in warning for warning in feedback.warnings) == 2
    logged = [message for message, tag, _ in log_messages if tag == "HamQ"]
    report = [message for message in logged if message.startswith("Warnings while importing")]
    assert len(report) == 1
    assert "Record 2" in report[0] and "invalid locator ZZ00" in report[0]
    assert "Imported: 2, duplicates: 0, skipped: 3" in feedback.infos


def test_import_without_cty_dat_warns(tmp_gpkg, no_cty):
    _, feedback = run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg))
    assert no_cty == [None]
    assert any("cty.dat has not been downloaded" in warning for warning in feedback.warnings)
    assert {row["country"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {None}


def test_import_with_use_cty_off_does_not_load_it(tmp_gpkg, cty_calls):
    _, feedback = run(
        "hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, USE_CTY=False)
    )
    assert cty_calls == []
    assert not any("cty.dat" in message for message in feedback.warnings + feedback.infos)


def test_import_positions_and_distances_from_cty_and_my_locator(tmp_gpkg, cty_calls):
    """N1MM logs have no locators: positions come from cty.dat, my QTH from MY_GRID."""
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID="kn04FT"))
    rows = rows_by_call(tmp_gpkg)
    assert rows["9A5XYZ"]["country"] == "Croatia" and rows["9A5XYZ"]["loc_source"] == "cty"
    assert rows["9A5XYZ"]["my_gridsquare"] == "KN04ft"
    assert all(row["distance_km"] is not None for row in rows.values() if row["loc_source"])


def test_import_my_lat_lon_give_my_gridsquare_without_the_station_mark(tmp_path, tmp_gpkg, no_cty):
    """REL-01: a record with MY_LAT/MY_LON and no MY_GRIDSQUARE got my locator (KN04ft)
    with the STATION_GRID_KEY mark while its path started at MY_LAT/MY_LON, and a later
    Recalculate rewrote the column but not the origin. Now it gets the cell of MY_LAT /
    MY_LON (Novi Sad, JN95wg) and no mark; a record without them still gets both."""

    def field(name: str, value: str) -> str:
        return f"<{name}:{len(value)}>{value}"

    common = field("QSO_DATE", "20260915") + field("BAND", "20m") + field("MODE", "FT8")
    portable = field("MY_LAT", "N045 16.250") + field("MY_LON", "E019 52.500")
    adi = tmp_path / "portable.adi"
    adi.write_text(
        "HamQ test\n<EOH>\n"
        + field("CALL", "PORTABLE")
        + field("TIME_ON", "1845")
        + common
        + field("GRIDSQUARE", "JO62")
        + portable
        + "<EOR>\n"
        + field("CALL", "HOME")
        + field("TIME_ON", "1846")
        + common
        + field("GRIDSQUARE", "JO62")
        + "<EOR>\n",
        encoding="utf-8",
    )
    results, _ = run("hamq:import_adif", import_parameters(adi, tmp_gpkg, MY_GRID="KN04ft"))
    assert results["IMPORTED"] == 2
    rows = rows_by_call(tmp_gpkg)
    portable_row, home = rows["PORTABLE"], rows["HOME"]
    assert portable_row["my_gridsquare"] == "JN95wg"
    assert gpkg.STATION_GRID_KEY not in json.loads(portable_row["adif_extra"])
    assert home["my_gridsquare"] == "KN04ft"
    assert json.loads(home["adif_extra"]) == {gpkg.STATION_GRID_KEY: "Y"}
    target = maidenhead.to_latlon("JO62")
    distance = geo.distance_km(*maidenhead.to_latlon("JN95wg"), *target)
    assert portable_row["distance_km"] == pytest.approx(distance)

    results, _ = run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "JN05"})
    assert results["UPDATED"] == 1  # HOME follows my locator, PORTABLE keeps its own QTH
    rows = rows_by_call(tmp_gpkg)
    assert rows["PORTABLE"]["my_gridsquare"] == "JN95wg"
    assert rows["PORTABLE"]["distance_km"] == pytest.approx(distance)
    assert rows["HOME"]["my_gridsquare"] == "JN05"


def test_import_without_my_locator_has_no_distances(tmp_gpkg, cty_calls):
    _, feedback = run(
        "hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID="")
    )
    assert {row["distance_km"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {None}
    assert any("my QTH unknown" in warning for warning in feedback.warnings)


def test_import_rejects_an_invalid_locator(tmp_gpkg, no_cty):
    with pytest.raises(QgsProcessingException, match="XYZ is not a valid Maidenhead locator"):
        run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID="XYZ"))
    assert not os.path.exists(tmp_gpkg)
    algorithm = QgsApplication.processingRegistry().createAlgorithmById("hamq:import_adif")
    ok, message = algorithm.checkParameterValues(
        import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID="KN0"), QgsProcessingContext()
    )
    assert not ok and "KN0" in message


def test_import_of_a_missing_file_fails(tmp_path, tmp_gpkg, no_cty):
    with pytest.raises(QgsProcessingException, match="could not be read"):
        run("hamq:import_adif", import_parameters(tmp_path / "missing.adi", tmp_gpkg))


def test_import_refuses_a_file_above_the_size_limit(tmp_path, tmp_gpkg, no_cty, data_changed):
    """Reading takes about ten times the file size in memory: a limit keeps QGIS alive."""
    big = tmp_path / "big.adi"
    record = adif.format_record({"CALL": "YU1AA", "QSO_DATE": "20260101", "TIME_ON": "1200"})
    big.write_text("x" * (1024 * 1024) + "\n<EOH>\n" + record, encoding="utf-8")
    with pytest.raises(QgsProcessingException, match=r"is too large: 1\.1 MB, the limit is 1 MB"):
        run("hamq:import_adif", import_parameters(big, tmp_gpkg, MAX_SIZE_MB=1))
    assert not os.path.exists(tmp_gpkg) and data_changed == []
    results, _ = run("hamq:import_adif", import_parameters(big, tmp_gpkg, MAX_SIZE_MB=2))
    assert results["IMPORTED"] == 1


def test_import_refuses_more_records_than_the_limit_allows(tmp_path, tmp_gpkg, no_cty):
    """Tiny records cost far more memory than their bytes: 10 000 records per MB of the
    limit. End tags count in any case and with a length."""
    many = tmp_path / "many.adi"
    many.write_bytes(b"<A:0><EOR>\n" * 9_998 + b"<a:0><eor>\n<a:0><Eor:0>\n")
    results, _ = run("hamq:import_adif", import_parameters(many, tmp_gpkg, MAX_SIZE_MB=1))
    assert (results["IMPORTED"], results["SKIPPED"]) == (0, 10_000)
    many.write_bytes(many.read_bytes() + b"<A:0><EOR>")
    with pytest.raises(QgsProcessingException, match="has too many records: more than 10\u00a0000"):
        run("hamq:import_adif", import_parameters(many, tmp_gpkg, MAX_SIZE_MB=1))


def test_import_refuses_what_is_not_a_regular_file(tmp_path, tmp_gpkg, no_cty):
    with pytest.raises(QgsProcessingException, match="could not be read: not a regular file"):
        run("hamq:import_adif", import_parameters(tmp_path, tmp_gpkg))
    if os.path.exists("/dev/zero"):  # endless: reading it would never finish
        with pytest.raises(QgsProcessingException, match="not a regular file"):
            run("hamq:import_adif", import_parameters("/dev/zero", tmp_gpkg))
    assert not os.path.exists(tmp_gpkg)


def test_import_of_a_file_without_records_warns(tmp_path, tmp_gpkg, no_cty):
    empty = tmp_path / "empty.adi"
    empty.write_bytes(b"Nothing here\n<ADIF_VER:5>3.1.4\n<EOH>\n")
    results, feedback = run("hamq:import_adif", import_parameters(empty, tmp_gpkg))
    assert results["IMPORTED"] == 0 and results["SKIPPED"] == 0
    assert any(warning.startswith("No QSO records found in") for warning in feedback.warnings)


def test_import_defaults_come_from_the_settings(tmp_path, clean_settings, no_cty):
    settings = HamQSettings()
    settings.my_grid = "jn95wg"
    settings.gpkg_path = str(tmp_path / "settings.gpkg")
    algorithm = QgsApplication.processingRegistry().createAlgorithmById("hamq:import_adif")
    assert algorithm.parameterDefinition("MY_GRID").defaultValue() == "JN95wg"
    assert algorithm.parameterDefinition("GPKG").defaultValue() == settings.gpkg_path
    recalculate = QgsApplication.processingRegistry().createAlgorithmById("hamq:recalculate")
    assert recalculate.parameterDefinition("GPKG").defaultValue() == settings.gpkg_path
    assert recalculate.parameterDefinition("MY_GRID").defaultValue() == "JN95wg"
    results, _ = run("hamq:import_adif", {"INPUT": str(ADIF / "n1mm.adi"), "LOAD_LAYERS": False})
    assert results["GPKG"] == settings.gpkg_path
    assert {row["my_gridsquare"] for row in gpkg.read_qso_rows(settings.gpkg_path)} == {"JN95wg"}


def test_import_sends_the_first_warnings_to_the_feedback_and_all_to_the_log(
    tmp_path, tmp_gpkg, no_cty, log_messages
):
    records = [
        {"CALL": f"B{i:02d}XYZ", "QSO_DATE": "20260230", "TIME_ON": "1200"} for i in range(30)
    ]
    path = tmp_path / "bad.adi"
    path.write_bytes(adif.format_document(records).encode("utf-8"))
    results, feedback = run("hamq:import_adif", import_parameters(path, tmp_gpkg, USE_CTY=False))
    assert results["SKIPPED"] == 30
    shown = [w for w in feedback.warnings if "skipped" in w and w.startswith("Record")]
    assert len(shown) == common.FEEDBACK_WARNINGS
    assert feedback.warnings[common.FEEDBACK_WARNINGS] == (
        "More warnings in the QGIS message log (HamQ tab): 10"
    )
    report = [m for m, tag, _ in log_messages if m.startswith("Warnings while importing")]
    assert len(report) == 1 and report[0].count("skipped") == 30


def test_import_adds_the_layers_to_the_project(tmp_gpkg, no_cty, clean_project):
    parameters = import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg, LOAD_LAYERS=True)
    run("hamq:import_adif", parameters)
    qso_layer, path_layer = layers.find_layers(tmp_gpkg)
    assert qso_layer is not None and path_layer is not None
    assert qso_layer.featureCount() == 6  # OH2XYZ/MM is a row without a point
    count = len(clean_project.mapLayers())
    run("hamq:import_adif", parameters)  # loaded already: nothing added
    assert len(clean_project.mapLayers()) == count


def test_import_in_a_background_task(tmp_gpkg, cty_calls, data_changed, clean_project):
    ok, results, feedback = run_in_task(
        "hamq:import_adif", import_parameters(ADIF / "log4om.adi", tmp_gpkg, LOAD_LAYERS=True)
    )
    assert ok, feedback.errors
    assert results["IMPORTED"] == 5 and results["GPKG"] == tmp_gpkg
    assert data_changed == [(tmp_gpkg, True)]  # from postProcessAlgorithm, main thread
    assert all(layer is not None for layer in layers.find_layers(tmp_gpkg))


def test_import_cancel_keeps_and_announces_the_saved_qsos(tmp_path, tmp_gpkg, no_cty, data_changed):
    path = write_log(tmp_path / "big.adi", 3000)
    feedback = Feedback()

    def cancel_after_the_first_chunk(progress: float) -> None:
        if progress >= 70.0:  # step 3 of 3 (saving) is past its first chunk
            feedback.cancel()

    feedback.progressChanged.connect(cancel_after_the_first_chunk)
    results, _ = run("hamq:import_adif", import_parameters(path, tmp_gpkg, USE_CTY=False), feedback)
    assert 0 < results["IMPORTED"] < 3000
    assert len(gpkg.read_qso_rows(tmp_gpkg)) == results["IMPORTED"]
    assert any(warning.startswith("Import canceled.") for warning in feedback.warnings)
    assert data_changed == [(tmp_gpkg, True)]  # once, not again in postProcessAlgorithm


def test_import_canceled_in_a_task_is_announced_in_the_main_thread(
    tmp_path, tmp_gpkg, no_cty, data_changed
):
    """Processing skips postProcessAlgorithm for a canceled task: the algorithm announces
    the saved QSOs from the worker thread and Qt delivers it in the main thread."""
    path = write_log(tmp_path / "big.adi", 3000)
    feedback = Feedback()

    def cancel_after_the_first_chunk(progress: float) -> None:
        if progress >= 70.0:
            feedback.cancel()

    feedback.progressChanged.connect(
        cancel_after_the_first_chunk, type=Qt.ConnectionType.DirectConnection
    )
    ok, _, _ = run_in_task(
        "hamq:import_adif", import_parameters(path, tmp_gpkg, USE_CTY=False), feedback
    )
    assert not ok
    saved = len(gpkg.read_qso_rows(tmp_gpkg))
    assert 0 < saved < 3000
    assert data_changed == [(tmp_gpkg, True)]


def test_import_warns_about_layers_in_edit_mode_once(tmp_gpkg, no_cty, clean_project):
    run("hamq:import_adif", import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg, LOAD_LAYERS=True))
    qso_layer, _ = layers.find_layers(tmp_gpkg)
    assert qso_layer.startEditing()
    try:
        # main thread: insert_qsos warns itself
        _, feedback = run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg))
        assert feedback.warnings.count(EDIT_NEW) == 1
        # worker thread: the algorithm adds the warning (checked in prepareAlgorithm)
        ok, results, feedback = run_in_task(
            "hamq:import_adif", import_parameters(ADIF / "log4om.adi", tmp_gpkg)
        )
        assert ok and results["IMPORTED"] == 5
        assert feedback.warnings.count(EDIT_NEW) == 1
    finally:
        qso_layer.rollBack()


@pytest.mark.slow
def test_import_of_10000_qsos_under_10_s(tmp_path, tmp_gpkg, no_cty):
    """PLAN M2: 10 000 QSOs in less than 10 s; importing the file again adds none."""
    path = write_log(tmp_path / "big.adi", 10_000)
    start = time.perf_counter()
    results, _ = run("hamq:import_adif", import_parameters(path, tmp_gpkg))
    elapsed = time.perf_counter() - start
    print(f"\nImport ADIF, 10 000 QSOs: {elapsed:.2f} s ({compat.QGIS_VERSION_INT})")
    assert results == {"IMPORTED": 10_000, "DUPLICATES": 0, "SKIPPED": 0, "GPKG": tmp_gpkg}
    assert elapsed < 10.0
    start = time.perf_counter()
    results, _ = run("hamq:import_adif", import_parameters(path, tmp_gpkg))
    print(f"again: {time.perf_counter() - start:.2f} s")
    assert (results["IMPORTED"], results["DUPLICATES"]) == (0, 10_000)


# --- recalculate ----------------------------------------------------------------------------------


def test_recalculate_after_setting_my_locator(tmp_gpkg, cty_calls, data_changed):
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID=""))
    assert {row["distance_km"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {None}
    data_changed.clear()
    results, feedback = run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "KN04ft"})
    placed = [row for row in gpkg.read_qso_rows(tmp_gpkg) if row["loc_source"]]
    assert len(placed) == 4  # EA8 (Canary Islands) is not in the cty.dat excerpt
    assert results == {"UPDATED": len(placed), "GPKG": tmp_gpkg}
    assert all(row["distance_km"] is not None and row["bearing_deg"] is not None for row in placed)
    assert f"Updated QSOs: {len(placed)}" in feedback.infos
    assert data_changed == [(tmp_gpkg, True)]


def test_recalculate_keeps_or_replaces_the_logged_qth(tmp_gpkg, no_cty):
    run("hamq:import_adif", import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg))
    run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "JN95wg"})
    assert {row["my_gridsquare"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {"KN04ft"}
    results, _ = run(
        "hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "JN95wg", "FORCE_STATION": True}
    )
    assert results["UPDATED"] == 6
    assert {row["my_gridsquare"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {"JN95wg"}


def test_recalculate_moves_qsos_imported_without_their_own_qth(tmp_gpkg, cty_calls, cty_database):
    """n1mm.adi has no MY_GRIDSQUARE: its QSOs got my locator at the import and follow a
    new one. The WSJT-X QSOs were logged with MY_GRIDSQUARE KN04ft and keep it."""
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg))
    run("hamq:import_adif", import_parameters(ADIF / "wsjtx_log.adi", tmp_gpkg))
    before = {row["fid"]: row for row in gpkg.read_qso_rows(tmp_gpkg)}
    results, _ = run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "JN95wg"})
    rows = gpkg.read_qso_rows(tmp_gpkg)
    moved = [row for row in rows if row["source"] == "adif:n1mm.adi"]
    kept = [row for row in rows if row["source"] == "adif:wsjtx_log.adi"]
    assert (len(moved), len(kept)) == (5, 6)
    assert results["UPDATED"] == 5
    assert {row["my_gridsquare"] for row in moved} == {"JN95wg"}
    origin = maidenhead.to_latlon("JN95wg")
    for row in moved:
        match = cty_database.lookup(row["call"])
        if match is None:  # EA8 is not in the cty.dat excerpt: no position
            assert row["distance_km"] is None
        else:
            assert row["distance_km"] == pytest.approx(
                geo.distance_km(*origin, match.lat, match.lon)
            )
    assert {row["my_gridsquare"] for row in kept} == {"KN04ft"}
    for row in kept:
        assert row["distance_km"] == before[row["fid"]]["distance_km"]
    nine_a = next(row for row in moved if row["call"] == "9A5XYZ")
    assert nine_a["distance_km"] == pytest.approx(358.4, abs=0.1)  # was 407.6 from KN04ft


def test_import_and_recalculate_of_a_read_only_geopackage_fail(
    tmp_path, no_cty, data_changed, english
):
    path = str(tmp_path / "log.gpkg")
    run("hamq:import_adif", import_parameters(ADIF / "wsjtx_log.adi", path))
    data_changed.clear()
    locked = tmp_path / "locked"
    locked.mkdir()
    file_mode = os.stat(path).st_mode
    os.chmod(path, file_mode & ~0o222)
    os.chmod(locked, 0o555)
    try:
        if os.access(path, os.W_OK) or os.access(locked, os.W_OK):
            pytest.skip("this user may write read-only files (root?)")
        with pytest.raises(QgsProcessingException, match="is read-only. Check the file"):
            run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", path))
        with pytest.raises(QgsProcessingException, match="is read-only"):
            run("hamq:recalculate", {"GPKG": path, "MY_GRID": "JN95wg"})
        new = str(locked / "new.gpkg")
        with pytest.raises(QgsProcessingException, match="is not writable, so .* cannot be"):
            run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", new))
    finally:
        os.chmod(path, file_mode)
        os.chmod(locked, 0o755)
    assert data_changed == []
    assert len(gpkg.read_qso_rows(path)) == 6


def test_recalculate_fills_dxcc_data_from_cty(tmp_gpkg, monkeypatch, cty_database, data_changed):
    monkeypatch.setattr(cty_download, "load_cached_cty", lambda path=None: None)
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg))
    assert {row["country"] for row in gpkg.read_qso_rows(tmp_gpkg)} == {None}
    monkeypatch.setattr(cty_download, "load_cached_cty", lambda path=None: cty_database)
    results, _ = run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "KN04ft"})
    rows = rows_by_call(tmp_gpkg)
    assert results["UPDATED"] == sum(1 for row in rows.values() if row["country"]) == 4
    assert rows["9A5XYZ"]["country"] == "Croatia"
    assert rows["9A5XYZ"]["loc_source"] == "cty" and rows["9A5XYZ"]["distance_km"] > 0


def test_recalculate_in_a_background_task(tmp_gpkg, cty_calls, data_changed):
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, MY_GRID=""))
    data_changed.clear()
    ok, results, _ = run_in_task("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "KN04ft"})
    assert ok and results["UPDATED"] == 4  # EA8 has no position: nothing to change
    assert data_changed == [(tmp_gpkg, True)]


def test_recalculate_accepts_any_file_name_the_import_accepts(tmp_path, no_cty):
    """Processing matches file patterns case-sensitively: LOG.GPKG must still be accepted."""
    path = str(tmp_path / "LOG.GPKG")
    results, _ = run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", path, MY_GRID=""))
    assert results["IMPORTED"] == 5
    results, _ = run("hamq:recalculate", {"GPKG": path, "MY_GRID": "KN04ft", "USE_CTY": False})
    assert results["GPKG"] == path


def test_recalculate_of_a_missing_geopackage_fails(tmp_gpkg, no_cty, data_changed):
    with pytest.raises(QgsProcessingException, match="does not exist"):
        run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "KN04ft"})
    assert not os.path.exists(tmp_gpkg)
    assert data_changed == []


def test_recalculate_rejects_an_invalid_locator(tmp_gpkg, no_cty):
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg))
    with pytest.raises(QgsProcessingException, match="SS99 is not a valid Maidenhead locator"):
        run("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "SS99"})


def test_recalculate_warns_about_layers_in_edit_mode_from_a_task(tmp_gpkg, no_cty, clean_project):
    run("hamq:import_adif", import_parameters(ADIF / "n1mm.adi", tmp_gpkg, LOAD_LAYERS=True))
    qso_layer, _ = layers.find_layers(tmp_gpkg)
    assert qso_layer.startEditing()
    try:
        ok, _, feedback = run_in_task("hamq:recalculate", {"GPKG": tmp_gpkg, "MY_GRID": "JN95wg"})
        assert ok
        assert feedback.warnings.count(EDIT_RECALCULATED) == 1
    finally:
        qso_layer.rollBack()


# --- data changed notification ------------------------------------------------------------------


def test_notification_never_raises(monkeypatch, log_messages):
    def broken():
        raise RuntimeError("no events here")

    monkeypatch.setattr(common, "events", broken)
    common.notify_data_changed("/tmp/x.gpkg")
    assert any("no events here" in message for message, tag, _ in log_messages if tag == "HamQ")


# --- qgis_process ---------------------------------------------------------------------------------


QGIS_PROCESS = shutil.which("qgis_process")


@pytest.mark.slow
@pytest.mark.skipif(QGIS_PROCESS is None, reason="qgis_process is not installed")
def test_qgis_process_runs_the_algorithms_without_the_plugin_gui(tmp_path):
    """The provider in the command line tool: no iface, no panel; postProcessAlgorithm
    still runs (and must not fail)."""
    environment = dict(os.environ)
    environment.update(
        {
            "QGIS_CUSTOM_CONFIG_PATH": str(tmp_path / "profile"),
            "QGIS_PLUGINPATH": str(REPO_ROOT),
            "QT_QPA_PLATFORM": "offscreen",
        }
    )

    def qgis_process(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [QGIS_PROCESS, *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    version = qgis_process("--version")
    if version.returncode != 0:
        pytest.skip(f"qgis_process does not start here: {version.stderr.strip()[-200:]}")
    enabled = qgis_process("plugins", "enable", "hamq")
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr

    gpkg_path = str(tmp_path / "log.gpkg")
    imported = qgis_process(
        "--json",
        "run",
        "hamq:import_adif",
        "--",
        f"INPUT={ADIF / 'wsjtx_log.adi'}",
        f"GPKG={gpkg_path}",
        "MY_GRID=KN04ft",
        "USE_CTY=false",
    )
    assert imported.returncode == 0, imported.stderr[-2000:]
    results = json.loads(imported.stdout)["results"]
    assert (results["IMPORTED"], results["DUPLICATES"], results["SKIPPED"]) == (6, 0, 0)
    assert len(gpkg.read_qso_rows(gpkg_path)) == 6

    points_path = str(tmp_path / "points.gpkg")
    points = qgis_process(
        "--json",
        "run",
        "hamq:locator_to_point",
        "--",
        "LOCATORS=KN04ft JN95wg",
        f"OUTPUT={points_path}",
    )
    assert points.returncode == 0, points.stderr[-2000:]
    layer = QgsVectorLayer(points_path, "points", "ogr")
    assert layer.featureCount() == 2

    recalculated = qgis_process(
        "--json", "run", "hamq:recalculate", "--", f"GPKG={gpkg_path}", "MY_GRID=JN95wg"
    )
    assert recalculated.returncode == 0, recalculated.stderr[-2000:]
    assert json.loads(recalculated.stdout)["results"]["UPDATED"] == 0  # logged QTH kept
