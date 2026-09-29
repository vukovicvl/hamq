"""Tests for hamq.core.geo (task M3-01)."""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path

import pytest

from hamq.core import geo
from hamq.core.geo import (
    EARTH_RADIUS_KM,
    aeqd_proj,
    bearing_deg,
    destination,
    distance_km,
    great_circle,
    is_antipodal,
    long_path,
)

BEOGRAD = (44.8125, 20.4612)
BEOGRAD_ANTIPODE = (-44.8125, -159.5388)
MUNCHEN = (48.14666, 11.60833)
PANCEVO = (44.8708, 20.6403)  # 15.5 km from Beograd: a local VHF/UHF contact
WASHINGTON = (38.8977, -77.0366)
SYDNEY = (-33.8688, 151.2093)
TOKYO = (35.6762, 139.6503)
LOS_ANGELES = (34.0522, -118.2437)

# .github/skills/geodesy/SKILL.md, WGS84 reference values from Beograd (confirmed with
# QgsDistanceArea on WGS84: 773.61 km / 301.76, 7608.71 km / 303.85, 15676.14 km / 90.99).
SKILL_VECTORS = [
    ("München", MUNCHEN, 773.6, 301.8),
    ("Washington", WASHINGTON, 7608.7, 303.9),
    ("Sydney", SYDNEY, 15676.1, 91.0),
]

CIRCUMFERENCE_KM = 2.0 * math.pi * EARTH_RADIUS_KM
HALF_CIRCUMFERENCE_KM = math.pi * EARTH_RADIUS_KM
EPS_KM = 1e-6


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two angles in degrees (359.9 vs 0.1 -> 0.2)."""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def km_to_deg(km: float) -> float:
    """Arc length on the model sphere -> central angle in degrees."""
    return math.degrees(km / EARTH_RADIUS_KM)


def crossing_lat_oracle(lat1: float, lon1: float, lat2: float, lon2: float, lon: float) -> float:
    """Latitude where the great circle through two points crosses meridian ``lon``.

    Independent closed form (Aviation Formulary, "latitude of a point on a great
    circle"); only valid for great circles that are not meridians.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    l1, l2, lo = math.radians(lon1), math.radians(lon2), math.radians(lon)
    num = math.sin(p1) * math.cos(p2) * math.sin(lo - l2)
    num -= math.sin(p2) * math.cos(p1) * math.sin(lo - l1)
    den = math.cos(p1) * math.cos(p2) * math.sin(l1 - l2)
    return math.degrees(math.atan(num / den))


def wrap(lon: float) -> float:
    """Longitude into [-180, 180] (test helper, independent of the module)."""
    while lon > 180.0:
        lon -= 360.0
    while lon < -180.0:
        lon += 360.0
    return lon


def same_endpoint_lon(got: float, expected: float) -> bool:
    """Endpoint longitudes must be exact; on the antimeridian the sign may flip."""
    return got == expected or (abs(got) == 180.0 and abs(expected) == 180.0)


def check_path(parts, start, end, step_km):
    """Assert every great_circle() guarantee for a drawable path; return the length in km."""
    assert isinstance(parts, list)
    assert 1 <= len(parts) <= 2, "a short great-circle path crosses the antimeridian at most once"
    total = 0.0
    segments = 0
    for part in parts:
        assert isinstance(part, list)
        assert len(part) >= 2, "every part of a real path is a line"
        for point in part:
            assert isinstance(point, tuple) and len(point) == 2
            lat, lon = point
            assert isinstance(lat, float) and isinstance(lon, float)
            assert math.isfinite(lat) and math.isfinite(lon)
            assert -90.0 <= lat <= 90.0
            assert -180.0 <= lon <= 180.0
        for (lat_a, lon_a), (lat_b, lon_b) in zip(part, part[1:]):
            assert abs(lon_b - lon_a) <= 180.0, "longitude jump inside a part"
            seg = distance_km(lat_a, lon_a, lat_b, lon_b)
            assert seg <= step_km + EPS_KM
            total += seg
            segments += 1
    # first and last points are the inputs
    first, last = parts[0][0], parts[-1][-1]
    assert first[0] == float(start[0]) and same_endpoint_lon(first[1], wrap(start[1]))
    assert last[0] == float(end[0]) and same_endpoint_lon(last[1], wrap(end[1]))
    # parts meet on the antimeridian at the same latitude
    for part_a, part_b in zip(parts, parts[1:]):
        (lat_a, lon_a), (lat_b, lon_b) = part_a[-1], part_b[0]
        assert lat_a == lat_b
        assert (lon_a, lon_b) in ((180.0, -180.0), (-180.0, 180.0))
    # all points lie on the short great-circle arc, in order: the pieces add up exactly
    # (triangle inequality is an equality only on the arc)
    assert total == pytest.approx(distance_km(*start, *end), abs=1e-6 + 1e-9 * segments)
    return total


def point_count(parts) -> int:
    return sum(len(part) for part in parts)


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------


def test_earth_radius_constant():
    assert EARTH_RADIUS_KM == 6371.0088


def test_no_qgis_or_pyqt_imports():
    tree = ast.parse(Path(geo.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            assert root not in {"qgis", "PyQt5", "PyQt6", "sip"}, name


# ---------------------------------------------------------------------------
# distance_km
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "point", "ref_km", "ref_bearing"), SKILL_VECTORS)
def test_skill_vectors_distance(name, point, ref_km, ref_bearing):
    d = distance_km(*BEOGRAD, *point)
    assert abs(d - ref_km) / ref_km <= 0.005, (name, d)


def test_plan_m3_acceptance_beograd_sydney():
    # PLAN.md M3: Beograd - Sydney within +-0.5 % of the WGS84 value 15676.1 km
    d = distance_km(*BEOGRAD, *SYDNEY)
    assert 15676.1 * 0.995 <= d <= 15676.1 * 1.005


@pytest.mark.parametrize(
    ("a", "b", "wgs84_km", "error_pct"),
    [
        ((0.0, 0.0), (1.0, 0.0), 110.574389, 0.56),  # north from the equator: the maximum
        ((89.0, 0.0), (90.0, 0.0), 111.693865, -0.45),  # up to the North Pole: the minimum
        ((-90.0, 0.0), (-89.0, 0.0), 111.693865, -0.45),  # away from the South Pole
        ((0.0, 0.0), (0.0, 1.0), 111.319491, -0.11),  # east along the equator
    ],
)
def test_distance_error_extremes_against_wgs84(a, b, wgs84_km, error_pct):
    # The module docstring states -0.45 % to +0.56 % against WGS84, the extremes being
    # short north-south paths at the equator and at the poles (WGS84 lengths: pyproj,
    # Karney's algorithm).
    d = distance_km(*a, *b)
    assert round((d - wgs84_km) / wgs84_km * 100, 2) == error_pct


def test_distance_symmetric():
    rng = random.Random(3)
    pairs = [(BEOGRAD, p) for _, p, _, _ in SKILL_VECTORS] + [(TOKYO, LOS_ANGELES)]
    for _ in range(500):
        pairs.append(
            (
                (rng.uniform(-90, 90), rng.uniform(-180, 180)),
                (rng.uniform(-90, 90), rng.uniform(-180, 180)),
            )
        )
    for a, b in pairs:
        assert distance_km(*a, *b) == distance_km(*b, *a)


def test_distance_same_point_is_zero():
    assert distance_km(*BEOGRAD, *BEOGRAD) == 0.0
    assert distance_km(90.0, 0.0, 90.0, 123.0) == pytest.approx(0.0, abs=1e-9)
    assert distance_km(10.0, 180.0, 10.0, -180.0) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((0.0, 0.0), (0.0, 90.0), math.pi / 2 * EARTH_RADIUS_KM),
        ((0.0, 0.0), (90.0, 0.0), math.pi / 2 * EARTH_RADIUS_KM),
        ((0.0, 0.0), (-90.0, 77.0), math.pi / 2 * EARTH_RADIUS_KM),
        ((0.0, 0.0), (1.0, 0.0), math.pi / 180 * EARTH_RADIUS_KM),
        ((0.0, 179.5), (0.0, -179.5), math.pi / 180 * EARTH_RADIUS_KM),
        ((0.0, 0.0), (0.0, 180.0), HALF_CIRCUMFERENCE_KM),
        ((90.0, 0.0), (-90.0, 0.0), HALF_CIRCUMFERENCE_KM),
    ],
)
def test_distance_known_arcs(a, b, expected):
    assert distance_km(*a, *b) == pytest.approx(expected, abs=1e-6)


def test_distance_antipodal_points_do_not_raise():
    # The bare haversine from the skill raises "math domain error" here: rounding makes
    # a = 1.0000000000000002 and sqrt(1 - a) fails.
    assert distance_km(69.51232454868148, 86.5812282599507, -69.51232454868148, -93.4187717400493)
    rng = random.Random(7)
    for _ in range(20000):
        lat, lon = rng.uniform(-90, 90), rng.uniform(-180, 180)
        d = distance_km(lat, lon, -lat, wrap(lon + 180.0))
        assert d == pytest.approx(HALF_CIRCUMFERENCE_KM, abs=1e-3)


def test_distance_nan_propagates():
    assert math.isnan(distance_km(float("nan"), 0.0, 10.0, 10.0))


def test_distance_latitudes_beyond_the_pole_do_not_raise():
    # Garbage in (|lat| > 90), but no "math domain error": rounding makes the haversine
    # term slightly negative here. (134.59, -9.99) is the same point as (45.41, 170.01).
    d = distance_km(134.58915783827467, -9.991712312597997, 45.41084216172543, 170.008287687402)
    assert d == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# bearing_deg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "point", "ref_km", "ref_bearing"), SKILL_VECTORS)
def test_skill_vectors_bearing(name, point, ref_km, ref_bearing):
    b = bearing_deg(*BEOGRAD, *point)
    assert angle_diff(b, ref_bearing) <= 0.5, (name, b)


@pytest.mark.parametrize(
    "origin", [BEOGRAD, SYDNEY, (0.0, 0.0), (-45.0, 100.0), (10.0, 180.0), (89.9, -120.0)]
)
def test_bearing_to_north_pole_is_zero(origin):
    for pole_lon in (0.0, 45.0, origin[1], -170.0):
        b = bearing_deg(*origin, 90.0, pole_lon)
        assert 0.0 <= b < 360.0
        assert angle_diff(b, 0.0) <= 1e-9


@pytest.mark.parametrize("origin", [BEOGRAD, SYDNEY, (0.0, 0.0), (-89.9, 60.0)])
def test_bearing_to_south_pole_is_180(origin):
    assert bearing_deg(*origin, -90.0, 0.0) == pytest.approx(180.0, abs=1e-9)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((0.0, 0.0), (0.0, 10.0), 90.0),  # due east on the equator
        ((0.0, 0.0), (0.0, -10.0), 270.0),  # due west
        ((0.0, 0.0), (10.0, 0.0), 0.0),  # due north
        ((0.0, 0.0), (-10.0, 0.0), 180.0),  # due south
        ((0.0, 179.0), (0.0, -179.0), 90.0),  # east across the antimeridian
        ((0.0, -179.0), (0.0, 179.0), 270.0),  # west across the antimeridian
        ((10.0, 180.0), (20.0, -180.0), 0.0),  # north along the antimeridian
    ],
)
def test_bearing_cardinal_directions(start, end, expected):
    b = bearing_deg(*start, *end)
    assert angle_diff(b, expected) <= 1e-9


def test_bearing_range():
    rng = random.Random(11)
    for _ in range(5000):
        b = bearing_deg(
            rng.uniform(-90, 90),
            rng.uniform(-180, 180),
            rng.uniform(-90, 90),
            rng.uniform(-180, 180),
        )
        assert 0.0 <= b < 360.0


@pytest.mark.parametrize(
    "point", [BEOGRAD, (0.0, 0.0), (90.0, 0.0), (-90.0, 45.0), (10.0, 180.0), (-10.0, -180.0)]
)
def test_bearing_same_point_is_zero(point):
    # Numerically identical points only. One point written two ways (+180 / -180, a pole
    # with two longitudes) gives an arbitrary value, as the docstring says.
    assert bearing_deg(*point, *point) == 0.0


def test_bearing_beograd_sydney_heads_east():
    # WGS84 says 90.99 deg; the sphere gives 91.22 (within the 0.5 deg budget)
    short = bearing_deg(*BEOGRAD, *SYDNEY)
    assert 90.5 < short < 91.5


# ---------------------------------------------------------------------------
# long_path
# ---------------------------------------------------------------------------


def test_long_path_basic():
    d, b = long_path(1000.0, 45.0)
    assert d == pytest.approx(CIRCUMFERENCE_KM - 1000.0)
    assert b == 225.0
    assert CIRCUMFERENCE_KM == pytest.approx(40030.2, abs=0.05)


@pytest.mark.parametrize(
    ("bearing", "expected"),
    [
        (0.0, 180.0),
        (90.0, 270.0),
        (180.0, 0.0),
        (270.0, 90.0),
        (359.5, 179.5),
        (179.5, 359.5),
        (-90.0, 90.0),
        (-200.0, 340.0),
        (540.0, 0.0),
        (-180.00000000000003, 0.0),  # -2.8e-14 + 360 rounds to 360: must come back as 0
    ],
)
def test_long_path_bearing(bearing, expected):
    _, b = long_path(5000.0, bearing)
    assert 0.0 <= b < 360.0
    assert b == pytest.approx(expected, abs=1e-12)


def test_long_path_non_finite_bearing_is_nan():
    assert math.isnan(long_path(100.0, math.nan)[1])
    assert math.isnan(long_path(100.0, math.inf)[1])


def test_long_path_beograd_sydney():
    short_d = distance_km(*BEOGRAD, *SYDNEY)
    short_b = bearing_deg(*BEOGRAD, *SYDNEY)
    long_d, long_b = long_path(short_d, short_b)
    assert short_d + long_d == pytest.approx(CIRCUMFERENCE_KM)
    assert long_d == pytest.approx(40030.2 - 15679.7, abs=0.1)
    assert angle_diff(long_b, short_b + 180.0) <= 1e-9
    # going the long way round really ends in Sydney
    lat, lon = destination(*BEOGRAD, long_b, long_d)
    assert distance_km(lat, lon, *SYDNEY) < 1e-6


def test_long_path_zero_distance_is_full_circle():
    assert long_path(0.0, 0.0) == (pytest.approx(CIRCUMFERENCE_KM), 180.0)


# ---------------------------------------------------------------------------
# is_antipodal
# ---------------------------------------------------------------------------


def near_beograd_antipode(km: float) -> tuple[float, float]:
    """Point ``km`` from Beograd's antipode, along the meridian (independent of destination)."""
    return BEOGRAD_ANTIPODE[0] + km_to_deg(km), BEOGRAD_ANTIPODE[1]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ((0.0, 0.0), (0.0, 180.0)),
        ((0.0, 0.0), (0.0, -180.0)),
        (BEOGRAD, BEOGRAD_ANTIPODE),
        ((90.0, 0.0), (-90.0, 0.0)),
        ((90.0, 0.0), (-90.0, 123.0)),
        ((10.0, 180.0), (-10.0, 0.0)),
    ],
)
def test_is_antipodal_true(a, b):
    assert is_antipodal(*a, *b)
    assert is_antipodal(*b, *a)


@pytest.mark.parametrize("km", [0.0, 1.0, 15.0, 19.9])
def test_is_antipodal_within_default_tolerance(km):
    assert is_antipodal(*BEOGRAD, *near_beograd_antipode(km))
    assert is_antipodal(*BEOGRAD, *near_beograd_antipode(-km))


@pytest.mark.parametrize("km", [20.1, 25.0, 50.0, 500.0])
def test_is_antipodal_false_for_near_but_not(km):
    assert not is_antipodal(*BEOGRAD, *near_beograd_antipode(km))
    assert not is_antipodal(*BEOGRAD, *near_beograd_antipode(-km))


def test_is_antipodal_false_along_parallel():
    # 25 km off the antipode along the parallel (small circle, still > 20 km of arc)
    dlon = km_to_deg(25.0 / math.cos(math.radians(BEOGRAD_ANTIPODE[0])))
    assert not is_antipodal(*BEOGRAD, BEOGRAD_ANTIPODE[0], BEOGRAD_ANTIPODE[1] + dlon)
    assert not is_antipodal(0.0, 0.0, 0.0, 179.0)  # ~111 km from the antipode


def test_is_antipodal_custom_tolerance():
    assert is_antipodal(*BEOGRAD, *near_beograd_antipode(25.0), tolerance_km=30.0)
    assert not is_antipodal(*BEOGRAD, *near_beograd_antipode(15.0), tolerance_km=10.0)


@pytest.mark.parametrize("b", [BEOGRAD, MUNCHEN, WASHINGTON, SYDNEY])
def test_is_antipodal_false_for_ordinary_paths(b):
    assert not is_antipodal(*BEOGRAD, *b)


# ---------------------------------------------------------------------------
# destination
# ---------------------------------------------------------------------------

ROUND_TRIP_PAIRS = [
    (BEOGRAD, MUNCHEN),
    (BEOGRAD, WASHINGTON),
    (BEOGRAD, SYDNEY),
    (TOKYO, LOS_ANGELES),
    (LOS_ANGELES, TOKYO),
    ((0.0, 170.0), (0.0, -170.0)),
    ((80.0, 10.0), (80.0, -170.0)),  # over the north pole
    ((-60.0, -30.0), (-70.0, 150.0)),  # over the south pole
    ((10.0, 180.0), (20.0, -170.0)),
    ((89.0, 0.0), (-45.0, 45.0)),
]


@pytest.mark.parametrize(("a", "b"), ROUND_TRIP_PAIRS)
def test_destination_round_trip(a, b):
    lat, lon = destination(*a, bearing_deg(*a, *b), distance_km(*a, *b))
    assert -180.0 <= lon <= 180.0
    assert distance_km(lat, lon, *b) < 1e-6


def test_destination_inverse_random():
    rng = random.Random(5)
    for _ in range(3000):
        lat, lon = rng.uniform(-89.0, 89.0), rng.uniform(-180, 180)
        theta = rng.uniform(0.0, 360.0)
        d = rng.uniform(1.0, HALF_CIRCUMFERENCE_KM - 100.0)
        lat2, lon2 = destination(lat, lon, theta, d)
        assert -90.0 <= lat2 <= 90.0 and -180.0 <= lon2 <= 180.0
        assert distance_km(lat, lon, lat2, lon2) == pytest.approx(d, abs=1e-6)
        assert angle_diff(bearing_deg(lat, lon, lat2, lon2), theta) <= 1e-6


def test_destination_zero_distance_is_start():
    assert destination(*BEOGRAD, 123.0, 0.0) == BEOGRAD
    assert destination(90.0, 45.0, 10.0, 0.0) == (90.0, 45.0)


def test_destination_equator():
    quarter = math.pi / 2 * EARTH_RADIUS_KM
    lat, lon = destination(0.0, 0.0, 90.0, quarter)
    assert lat == pytest.approx(0.0, abs=1e-9)
    assert lon == pytest.approx(90.0, abs=1e-9)
    lat, lon = destination(0.0, 0.0, 0.0, quarter)
    assert lat == pytest.approx(90.0, abs=1e-9)


def test_destination_wraps_longitude():
    arc = math.radians(20.0) * EARTH_RADIUS_KM
    lat, lon = destination(0.0, 170.0, 90.0, arc)
    assert (lat, lon) == (pytest.approx(0.0, abs=1e-9), pytest.approx(-170.0, abs=1e-9))
    lat, lon = destination(0.0, -170.0, 270.0, arc)
    assert (lat, lon) == (pytest.approx(0.0, abs=1e-9), pytest.approx(170.0, abs=1e-9))


def test_destination_from_the_poles_matches_bearing_convention():
    d = 1000.0
    lat, lon = destination(90.0, 0.0, 180.0, d)
    assert lat == pytest.approx(90.0 - km_to_deg(d), abs=1e-9)
    assert lon == pytest.approx(0.0, abs=1e-9)
    lat, lon = destination(90.0, 0.0, 0.0, d)
    assert abs(lon) == pytest.approx(180.0, abs=1e-9)
    assert angle_diff(bearing_deg(90.0, 0.0, lat, lon), 0.0) <= 1e-9
    lat, lon = destination(-90.0, 30.0, 0.0, d)
    assert lat == pytest.approx(-90.0 + km_to_deg(d), abs=1e-9)
    assert lon == pytest.approx(30.0, abs=1e-9)
    for theta in (10.0, 100.0, 250.0):
        lat, lon = destination(90.0, 20.0, theta, d)
        assert bearing_deg(90.0, 20.0, lat, lon) == pytest.approx(theta, abs=1e-9)


def test_destination_due_north_and_south_keep_the_longitude():
    for theta in (0.0, 180.0, 360.0, -180.0):
        assert destination(*BEOGRAD, theta, 500.0)[1] == BEOGRAD[1]
        assert destination(0.0, 0.0, theta, 500.0)[1] == 0.0  # sin(pi) would give 8e-16
    # over the South Pole onto the opposite meridian
    lat, lon = destination(-80.0, 0.0, 180.0, math.radians(20.0) * EARTH_RADIUS_KM)
    assert lat == pytest.approx(-80.0, abs=1e-9)
    assert lon == 180.0
    # over the North Pole onto the opposite meridian
    lat, lon = destination(80.0, 10.0, 0.0, math.radians(20.0) * EARTH_RADIUS_KM)
    assert lat == pytest.approx(80.0, abs=1e-9)
    assert lon == -170.0


def test_destination_non_finite_gives_nan():
    lat, lon = destination(math.nan, 0.0, 0.0, 100.0)
    assert math.isnan(lat) and math.isnan(lon)
    assert math.isnan(destination(0.0, math.inf, 0.0, 100.0)[1])


def test_destination_negative_distance_goes_backwards():
    a = destination(*BEOGRAD, 30.0, -500.0)
    b = destination(*BEOGRAD, 210.0, 500.0)
    assert distance_km(*a, *b) < 1e-9


def test_destination_full_circle_returns_home():
    lat, lon = destination(*BEOGRAD, 77.0, CIRCUMFERENCE_KM)
    assert distance_km(lat, lon, *BEOGRAD) < 1e-6


# ---------------------------------------------------------------------------
# great_circle
# ---------------------------------------------------------------------------


def test_great_circle_beograd_sydney_is_one_part():
    parts = great_circle(*BEOGRAD, *SYDNEY)
    assert len(parts) == 1
    assert parts[0][0] == BEOGRAD
    assert parts[0][-1] == SYDNEY
    check_path(parts, BEOGRAD, SYDNEY, 100.0)
    n = math.ceil(distance_km(*BEOGRAD, *SYDNEY) / 100.0)
    assert point_count(parts) == n + 1


def test_great_circle_tokyo_los_angeles_splits_at_antimeridian():
    parts = great_circle(*TOKYO, *LOS_ANGELES)
    assert len(parts) == 2
    first, second = parts
    assert first[0] == TOKYO and second[-1] == LOS_ANGELES
    assert first[-1][1] == 180.0 and second[0][1] == -180.0
    assert first[-1][0] == second[0][0]
    crossing = first[-1][0]
    assert crossing == pytest.approx(crossing_lat_oracle(*TOKYO, *LOS_ANGELES, 180.0), abs=1e-9)
    # QgsDistanceArea on WGS84 crosses at 47.633; the sphere is close
    assert crossing == pytest.approx(47.633, abs=0.5)
    assert all(lon > 0 for _, lon in first) and all(lon < 0 for _, lon in second)
    check_path(parts, TOKYO, LOS_ANGELES, 100.0)
    n = math.ceil(distance_km(*TOKYO, *LOS_ANGELES) / 100.0)
    assert point_count(parts) == n + 1 + 2


def test_great_circle_los_angeles_tokyo_reverse_direction():
    parts = great_circle(*LOS_ANGELES, *TOKYO)
    assert len(parts) == 2
    assert parts[0][-1][1] == -180.0 and parts[1][0][1] == 180.0
    forward = great_circle(*TOKYO, *LOS_ANGELES)
    assert parts[0][-1][0] == pytest.approx(forward[0][-1][0], abs=1e-9)
    check_path(parts, LOS_ANGELES, TOKYO, 100.0)


@pytest.mark.parametrize("step", [1.0, 25.0, 100.0, 333.3, 5000.0, 1e9, math.inf])
@pytest.mark.parametrize(("a", "b"), [(BEOGRAD, SYDNEY), (TOKYO, LOS_ANGELES), (BEOGRAD, MUNCHEN)])
def test_great_circle_spacing(a, b, step):
    parts = great_circle(*a, *b, step_km=step)
    check_path(parts, a, b, step)
    n = max(1, math.ceil(distance_km(*a, *b) / step))
    assert point_count(parts) == n + 1 + 2 * (len(parts) - 1)


def test_great_circle_huge_step_gives_only_endpoints():
    assert great_circle(*BEOGRAD, *SYDNEY, step_km=1e9) == [[BEOGRAD, SYDNEY]]
    parts = great_circle(*TOKYO, *LOS_ANGELES, step_km=1e9)
    assert [len(p) for p in parts] == [2, 2]


def test_great_circle_random_paths():
    rng = random.Random(2024)
    crossings = 0
    for _ in range(400):
        a = (rng.uniform(-90, 90), rng.uniform(-180, 180))
        b = (rng.uniform(-90, 90), rng.uniform(-180, 180))
        step = rng.choice([10.0, 100.0, 750.0])
        parts = great_circle(*a, *b, step_km=step)
        if is_antipodal(*a, *b):
            assert parts == []
            continue
        check_path(parts, a, b, step)
        if len(parts) == 2:
            crossings += 1
            lat_c = parts[0][-1][0]
            if abs(math.sin(math.radians(a[1] - b[1]))) > 0.01:  # oracle needs a non-meridian
                assert lat_c == pytest.approx(crossing_lat_oracle(*a, *b, 180.0), abs=1e-7)
    assert crossings > 50


def test_great_circle_splits_exactly_when_the_path_crosses():
    # The path crosses the antimeridian iff its longitude runs past +-180 on the way:
    # eastward (0 < dlon < 180) from lon1 with lon1 + dlon > 180, and the mirror case.
    rng = random.Random(99)
    for _ in range(400):
        lat1, lat2 = rng.uniform(-80, 80), rng.uniform(-80, 80)
        lon1 = rng.uniform(-180, 180)
        dlon = rng.uniform(-179.0, 179.0)
        lon2 = wrap(lon1 + dlon)
        if is_antipodal(lat1, lon1, lat2, lon2):
            continue
        expected = 2 if not -180.0 <= lon1 + dlon <= 180.0 else 1
        assert len(great_circle(lat1, lon1, lat2, lon2)) == expected


def test_great_circle_meridian_and_equator_paths():
    parts = great_circle(10.0, 20.0, 50.0, 20.0, step_km=50.0)
    assert len(parts) == 1
    assert all(lon == pytest.approx(20.0, abs=1e-12) for _, lon in parts[0])
    check_path(parts, (10.0, 20.0), (50.0, 20.0), 50.0)
    parts = great_circle(0.0, 0.0, 0.0, 90.0)
    assert all(lat == pytest.approx(0.0, abs=1e-12) for lat, _ in parts[0])
    lons = [lon for _, lon in parts[0]]
    assert lons == sorted(lons)
    check_path(parts, (0.0, 0.0), (0.0, 90.0), 100.0)


POLAR_PATHS = [
    # (start, end, parts)
    ((80.0, 10.0), (80.0, -170.0), 2),  # meridian 10 -> north pole -> meridian -170
    ((-80.0, 10.0), (-80.0, -170.0), 2),  # same over the south pole
    ((60.0, 0.0), (60.0, 180.0), 1),  # over the pole, ends on the antimeridian
    ((89.0, 45.0), (89.0, -135.0), 2),
    ((70.0, 100.0), (70.0, -81.0), 2),  # passes near the pole, crosses 180
    ((70.0, 100.0), (70.0, -79.0), 1),  # passes near the pole, crosses 0
    ((90.0, 0.0), (0.0, 45.0), 1),  # starts at the pole
    ((90.0, 100.0), (0.0, -170.0), 2),  # starts at the pole, meridian across 180
    ((0.0, 170.0), (90.0, -170.0), 2),  # ends at the pole
    ((-90.0, 0.0), (45.0, 90.0), 1),
    ((0.0, 0.0), (90.0, 180.0), 1),
    ((90.0, 180.0), (0.0, 0.0), 1),
    ((-30.0, 20.0), (-10.0, -160.0), 2),  # meridian circle over the south pole
]


@pytest.mark.parametrize(("a", "b", "expected_parts"), POLAR_PATHS)
def test_great_circle_polar_paths(a, b, expected_parts):
    for step in (100.0, 37.0, 1e9):
        parts = great_circle(*a, *b, step_km=step)
        check_path(parts, a, b, step)
        assert len(parts) == expected_parts


def test_great_circle_over_the_pole_reaches_it():
    parts = great_circle(80.0, 10.0, 80.0, -170.0)
    lats = [lat for part in parts for lat, _ in part]
    assert max(lats) == pytest.approx(90.0, abs=1e-6)
    # the split happens at the pole itself
    assert parts[0][-1][0] == pytest.approx(90.0, abs=1e-6)
    # every point is exactly on one of the two meridians, or is the split point
    assert {lon for part in parts for _, lon in part} == {10.0, -170.0, 180.0, -180.0}


@pytest.mark.parametrize(
    ("lat1", "lat2", "n"),
    [
        (80.0, 80.0, 22),  # sample 11 of 22 on the pole
        (70.0, 60.0, 5),  # sample 2 of 5; rounding leaves x = -5.6e-17 there
        (60.0, 80.0, 4),
        (50.0, 80.0, 5),
        (-50.0, -80.0, 5),  # South Pole
    ],
)
def test_great_circle_sample_on_the_pole(lat1, lat2, n):
    # The step puts one sample exactly on the pole. It has no longitude of its own: it
    # keeps the meridian it came from (lon 10), and the path splits right there.
    a, b = (lat1, 10.0), (lat2, -170.0)
    step = distance_km(*a, *b) / n * (1 + 1e-9)
    parts = great_circle(*a, *b, step_km=step)
    check_path(parts, a, b, step)
    pole = math.copysign(90.0, lat1)
    assert parts[0][-2:] == [(pole, 10.0), (pole, 180.0)]
    assert parts[1][0] == (pole, -180.0)
    assert point_count(parts) == n + 1 + 2


ANTIMERIDIAN_ENDPOINTS = [
    # (start, end, expected first lon, expected last lon, parts)
    ((10.0, 180.0), (20.0, -170.0), -180.0, -170.0, 1),  # starts on it, heads east
    ((10.0, -180.0), (20.0, 170.0), 180.0, 170.0, 1),  # starts on it, heads west
    ((10.0, 180.0), (20.0, 170.0), 180.0, 170.0, 1),
    ((10.0, -180.0), (20.0, -170.0), -180.0, -170.0, 1),
    ((10.0, 170.0), (20.0, 180.0), 170.0, 180.0, 1),  # ends on it, arrives from the east side
    ((10.0, 170.0), (20.0, -180.0), 170.0, 180.0, 1),
    ((10.0, -170.0), (20.0, 180.0), -170.0, -180.0, 1),
    ((10.0, -170.0), (20.0, -180.0), -170.0, -180.0, 1),
    ((10.0, 180.0), (50.0, 180.0), 180.0, 180.0, 1),  # along the antimeridian
    ((10.0, 180.0), (50.0, -180.0), 180.0, 180.0, 1),
    ((10.0, -180.0), (50.0, -180.0), -180.0, -180.0, 1),
    ((10.0, -180.0), (-50.0, 180.0), -180.0, -180.0, 1),
    ((0.0, 180.0), (0.0, 0.5), 180.0, 0.5, 1),  # heads west almost half way round
    ((10.0, 179.0), (20.0, -1.0), 179.0, -1.0, 2),  # dlon = 180: over the north pole
    ((10.0, 180.0), (20.0, 0.0), -180.0, 0.0, 1),  # same, starting on the antimeridian
]


@pytest.mark.parametrize(("a", "b", "first_lon", "last_lon", "n_parts"), ANTIMERIDIAN_ENDPOINTS)
def test_great_circle_points_on_the_antimeridian(a, b, first_lon, last_lon, n_parts):
    if is_antipodal(*a, *b):
        pytest.skip("antipodal")
    parts = great_circle(*a, *b, step_km=50.0)
    assert len(parts) == n_parts
    assert parts[0][0] == (a[0], first_lon)
    assert parts[-1][-1] == (b[0], last_lon)
    check_path(parts, a, b, 50.0)


@pytest.mark.parametrize(
    ("a", "b", "first_lon"),
    [
        # 180 - (-179.99999999999997) rounds to 360 and would hide the real step of
        # 2.8e-14 degrees east across the antimeridian (found by fuzzing)
        ((90.0, 180.0), (-2.7059927810790043, -179.99999999999997), -180.0),
        ((10.0, 180.0), (-20.0, -179.99999999999997), -180.0),
        ((10.0, -180.0), (-20.0, 179.99999999999997), 180.0),
        ((-10.0, 179.99999999999997), (20.0, -180.0), 179.99999999999997),
    ],
)
def test_great_circle_tiny_step_across_the_antimeridian(a, b, first_lon):
    parts = great_circle(*a, *b, step_km=950.0)
    assert len(parts) == 1
    assert parts[0][0] == (a[0], first_lon)
    check_path(parts, a, b, 950.0)


@pytest.mark.parametrize(
    ("lon1", "lon2"),
    [
        (168.4, -11.599999999999993),
        (150.1, -29.900000000000002),
        (179.27, -0.7299999999999878),
        (-127.0, 53.00000000000002),
        (-145.62825432486522, 34.3717456751348),
    ],
)
def test_great_circle_nearly_opposite_meridians(lon1, lon2):
    # Longitudes (almost) 180 apart where the difference rounds past +-180 twice
    # (168.4 -> -11.599999999999993: -180, then 180.00000000000003; -127 ->
    # 53.00000000000002: 180.00000000000003, then -180; found by searching). The path
    # goes (almost) over a pole and must stay whole.
    for lat1, lat2 in ((40.0, 30.0), (-40.0, -30.0), (10.0, 60.0)):
        parts = great_circle(lat1, lon1, lat2, lon2)
        check_path(parts, (lat1, lon1), (lat2, lon2), 100.0)
        assert len(parts) == 1


@pytest.mark.parametrize(
    ("a", "b", "step"),
    [
        # found by fuzzing: leaving a pole along the antimeridian, rounding puts samples at
        # 180.00000000000003 / -180.00000000000003 before they are clipped to +-180
        ((-90.0, 54.904), (76.3002572338898, -180.0), 1260.1423155716511),
        ((89.99999999999999, -1.0162965579860952), (-14.0, -180.0), 1288.6576852401022),
    ],
)
def test_great_circle_from_a_pole_along_the_antimeridian(a, b, step):
    parts = great_circle(*a, *b, step_km=step)
    assert len(parts) == 1
    check_path(parts, a, b, step)
    assert all(abs(abs(lon) - 180.0) < 1e-9 for _, lon in parts[0][1:])


def test_great_circle_along_antimeridian_keeps_one_side():
    parts = great_circle(10.0, 180.0, 50.0, -180.0, step_km=100.0)
    assert len(parts) == 1
    assert all(lon == 180.0 for _, lon in parts[0])
    parts = great_circle(-50.0, -180.0, 10.0, 180.0, step_km=100.0)
    assert all(lon == -180.0 for _, lon in parts[0])


def test_great_circle_sample_on_the_antimeridian():
    # Equator paths symmetric about the antimeridian with an even number of segments put
    # the middle sample on +-180 (exactly or within rounding). Either way the path must
    # split into two proper parts meeting there; when the sample is exactly on it, the
    # sample itself is the split point (one point fewer than an interpolated split).
    hits = 0
    for lon1 in (100.0, 120.0, 135.0, 150.0, 160.0, 170.0, 175.0, 179.0):
        lon2 = -lon1
        d = distance_km(0.0, lon1, 0.0, lon2)
        for n in (2, 4, 6, 10):
            step = d / n * (1 + 1e-9)
            parts = great_circle(0.0, lon1, 0.0, lon2, step_km=step)
            check_path(parts, (0.0, lon1), (0.0, lon2), step)
            assert len(parts) == 2
            assert point_count(parts) in (n + 2, n + 3)
            if point_count(parts) == n + 2:
                hits += 1
    assert hits > 0, "no sample landed exactly on the antimeridian; extend the search"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (BEOGRAD, BEOGRAD),
        ((90.0, 0.0), (90.0, 45.0)),
        ((-90.0, 10.0), (-90.0, -170.0)),
        ((10.0, 180.0), (10.0, -180.0)),
    ],
)
def test_great_circle_identical_points(a, b):
    assert great_circle(*a, *b) == [[(float(a[0]), float(a[1]))]]


def test_great_circle_local_contact_is_a_line():
    # Two stations 15.5 km apart must give a 2-point line. A single-point part is not a
    # line (the QGIS side skips it), so the path of a local contact would vanish.
    assert great_circle(*BEOGRAD, *PANCEVO) == [[BEOGRAD, PANCEVO]]
    assert great_circle(*PANCEVO, *BEOGRAD) == [[PANCEVO, BEOGRAD]]


@pytest.mark.parametrize("start", [BEOGRAD, (0.0, 0.0), (-60.0, 179.9), (89.0, -45.0)])
@pytest.mark.parametrize("bearing", [0.0, 90.0, 225.0])
def test_great_circle_same_point_tolerance(start, bearing):
    # Points less than 1e-12 rad (about 6 micrometres) apart count as the same point
    # (rounding noise, or one point written two ways); 1e-11 rad (64 micrometres) apart
    # is already a line.
    same = destination(*start, bearing, 1e-13 * EARTH_RADIUS_KM)
    assert same != start
    assert great_circle(*start, *same) == [[start]]
    near = destination(*start, bearing, 1e-11 * EARTH_RADIUS_KM)
    assert great_circle(*start, *near) == [[start, near]]


@pytest.mark.parametrize("km", [1e-6, 1.1e-3, 0.1, 4.7, 15.5, 49.3])
@pytest.mark.parametrize("bearing", [0.0, 135.0, 270.0])
@pytest.mark.parametrize("step", [1.0, 100.0])
def test_great_circle_short_paths(km, bearing, step):
    # 1 mm, 1 m, 100 m and local VHF/UHF distances: evenly spaced lines, never one point
    b = destination(*BEOGRAD, bearing, km)
    parts = great_circle(*BEOGRAD, *b, step_km=step)
    check_path(parts, BEOGRAD, b, step)
    assert len(parts) == 1
    assert len(parts[0]) == max(1, math.ceil(distance_km(*BEOGRAD, *b) / step)) + 1


@pytest.mark.parametrize(
    ("a", "b", "n_parts"),
    [
        ((10.0, 179.99), (10.01, -179.99), 2),  # 2.5 km east across the antimeridian
        ((-10.0, -179.999), (-10.0, 179.999), 2),  # 220 m west across it
        ((0.0, 180.0), (0.0, -179.9999999), 1),  # 1 cm east of a start on the antimeridian
        ((89.9999, 0.0), (89.9999, 180.0), 1),  # 22 m over the North Pole
        ((-89.9999, 10.0), (-89.9999, -170.0), 2),  # 22 m over the South Pole and across 180
        ((90.0, 0.0), (89.99999, 33.0), 1),  # 1.1 m from the North Pole
        ((0.0, 0.0), (1e-9, 1e-9), 1),  # 0.16 mm on the equator
    ],
)
@pytest.mark.parametrize("step", [1.0, 100.0])
def test_great_circle_short_paths_in_awkward_places(a, b, n_parts, step):
    parts = great_circle(*a, *b, step_km=step)
    check_path(parts, a, b, step)
    assert len(parts) == n_parts


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ((0.0, 0.0), (0.0, 180.0)),
        (BEOGRAD, BEOGRAD_ANTIPODE),
        ((90.0, 0.0), (-90.0, 0.0)),
        (BEOGRAD, near_beograd_antipode(15.0)),
        (BEOGRAD, near_beograd_antipode(-19.9)),
    ],
)
def test_great_circle_antipodal_is_empty(a, b):
    assert great_circle(*a, *b) == []


def test_great_circle_just_outside_antipodal_tolerance_is_drawn():
    b = near_beograd_antipode(25.0)
    parts = great_circle(*BEOGRAD, *b)
    check_path(parts, BEOGRAD, b, 100.0)
    assert sum(1 for part in parts for _ in part) > 190


@pytest.mark.parametrize("step", [0.0, -1.0, float("nan"), -math.inf])
def test_great_circle_rejects_bad_step(step):
    with pytest.raises(ValueError):
        great_circle(*BEOGRAD, *SYDNEY, step_km=step)


def test_great_circle_rejects_step_that_is_too_small():
    with pytest.raises(ValueError):
        great_circle(*BEOGRAD, *SYDNEY, step_km=1e-6)


@pytest.mark.parametrize("step", [1e-300, 1e-310, 5e-324])
def test_great_circle_rejects_absurdly_small_step(step):
    # 1e-310 and 5e-324 are subnormal: path length / step overflows to inf, and
    # math.ceil(inf) raised OverflowError instead of ValueError. 1e-300 put a 300-digit
    # segment count into the message.
    with pytest.raises(ValueError) as excinfo:
        great_circle(*BEOGRAD, *SYDNEY, step_km=step)
    assert len(str(excinfo.value)) < 150


def test_great_circle_segment_limit_is_one_million():
    d = distance_km(*BEOGRAD, *MUNCHEN)
    with pytest.raises(ValueError, match="1000000"):
        great_circle(*BEOGRAD, *MUNCHEN, step_km=d / 1_000_000.5)


@pytest.mark.parametrize(
    "coords",
    [
        (91.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -90.5, 0.0),
        (float("nan"), 0.0, 10.0, 10.0),
        (0.0, math.inf, 10.0, 10.0),
    ],
)
def test_great_circle_rejects_bad_coordinates(coords):
    with pytest.raises(ValueError):
        great_circle(*coords)


def test_great_circle_wraps_input_longitudes():
    parts = great_circle(0.0, 190.0, 10.0, 200.0)
    assert parts[0][0] == (0.0, -170.0)
    assert parts[-1][-1] == (10.0, -160.0)
    assert len(parts) == 1
    parts = great_circle(0.0, -190.0, 10.0, -200.0)
    assert parts[0][0] == (0.0, 170.0)
    assert parts[-1][-1] == (10.0, 160.0)
    assert len(parts) == 1


def test_great_circle_accepts_ints_and_returns_floats():
    parts = great_circle(0, 0, 0, 10)
    assert parts[0][0] == (0.0, 0.0) and parts[0][-1] == (0.0, 10.0)
    assert all(isinstance(v, float) for part in parts for p in part for v in p)


# ---------------------------------------------------------------------------
# aeqd_proj
# ---------------------------------------------------------------------------


def test_aeqd_proj_beograd():
    assert aeqd_proj(*BEOGRAD) == (
        "+proj=aeqd +lat_0=44.812500 +lon_0=20.461200 +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs"
    )


def test_aeqd_proj_format():
    s = aeqd_proj(*SYDNEY)
    assert s.startswith("+proj=aeqd +lat_0=-33.868800 +lon_0=151.209300 ")
    assert s.endswith(" +units=km +no_defs")
    assert "+datum=WGS84" in s


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (1.23456789, 2.0000004, "+lat_0=1.234568 +lon_0=2.000000"),
        (-0.0, -0.0000001, "+lat_0=0.000000 +lon_0=0.000000"),  # no "-0.000000"
        (90.0, 0.0, "+lat_0=90.000000 +lon_0=0.000000"),
        (-90.0, -180.0, "+lat_0=-90.000000 +lon_0=-180.000000"),
        (0.0, 180.0, "+lat_0=0.000000 +lon_0=180.000000"),
        (0.0, 190.0, "+lat_0=0.000000 +lon_0=-170.000000"),
        (0.0, -190.0, "+lat_0=0.000000 +lon_0=170.000000"),
        (0.0, 540.0, "+lat_0=0.000000 +lon_0=180.000000"),
        (0.0, -540.0, "+lat_0=0.000000 +lon_0=-180.000000"),
        (0.0, 720.5, "+lat_0=0.000000 +lon_0=0.500000"),
        (0, 20, "+lat_0=0.000000 +lon_0=20.000000"),
    ],
)
def test_aeqd_proj_numbers(lat, lon, expected):
    assert f" {expected} " in aeqd_proj(lat, lon)


@pytest.mark.parametrize(
    ("lat", "lon"), [(90.5, 0.0), (-91.0, 0.0), (float("nan"), 0.0), (0.0, math.inf)]
)
def test_aeqd_proj_rejects_bad_coordinates(lat, lon):
    with pytest.raises(ValueError):
        aeqd_proj(lat, lon)
