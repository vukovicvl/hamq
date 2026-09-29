"""Tests for hamq.core.maidenhead (task M1-01)."""

from __future__ import annotations

import ast
import math
import random
import time
from fractions import Fraction as F
from pathlib import Path

import pytest

from hamq.core import maidenhead
from hamq.core.maidenhead import (
    LEVEL_EXTENDED,
    LEVEL_FIELD,
    LEVEL_SQUARE,
    LEVEL_SUBSQUARE,
    MAX_GRID_CELLS,
    VALID_LENGTHS,
    cell_size,
    count_cells,
    is_valid,
    iter_cells,
    normalize,
    snap_extent,
    to_bounds,
    to_latlon,
    to_locator,
)

# Verified vectors from .github/skills/maidenhead/SKILL.md: (place, lat, lon, 6 chars, 8 chars)
VECTORS = [
    ("Beograd", 44.8125, 20.4612, "KN04ft", "KN04ft55"),
    ("Novi Sad", 45.2671, 19.8335, "JN95wg", "JN95wg04"),
    ("München", 48.14666, 11.60833, "JN58td", "JN58td25"),
    ("Washington DC", 38.8977, -77.0366, "FM18lv", "FM18lv55"),
    ("Sydney", -33.8688, 151.2093, "QF56od", "QF56od51"),
]

EUROPE = (34.0, -25.0, 72.0, 45.0)  # lat_min, lon_min, lat_max, lon_max
WORLD = (-90.0, -180.0, 90.0, 180.0)

FIELD_CHARS = "ABCDEFGHIJKLMNOPQR"
SUBSQUARE_CHARS = "abcdefghijklmnopqrstuvwx"


def exact(*values: F | int) -> tuple[float, ...]:
    """Correctly rounded floats of exact rational values (what the module must return)."""
    return tuple(float(v) for v in values)


def random_locator(rng: random.Random, length: int) -> str:
    """A random valid locator of ``length`` characters in random letter case."""
    chars = [rng.choice(FIELD_CHARS), rng.choice(FIELD_CHARS)]
    if length >= 4:
        chars += [rng.choice("0123456789"), rng.choice("0123456789")]
    if length >= 6:
        chars += [rng.choice(SUBSQUARE_CHARS), rng.choice(SUBSQUARE_CHARS)]
    if length >= 8:
        chars += [rng.choice("0123456789"), rng.choice("0123456789")]
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in chars)


# --- constants ---------------------------------------------------------------------------


def test_constants():
    assert (LEVEL_FIELD, LEVEL_SQUARE, LEVEL_SUBSQUARE, LEVEL_EXTENDED) == (2, 4, 6, 8)
    assert VALID_LENGTHS == (2, 4, 6, 8)
    assert MAX_GRID_CELLS == 200_000


def test_module_is_pure_python():
    tree = ast.parse(Path(maidenhead.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"qgis", "PyQt5", "PyQt6", "sip"}


# --- to_locator ----------------------------------------------------------------------------


@pytest.mark.parametrize(("place", "lat", "lon", "loc6", "loc8"), VECTORS)
def test_skill_vectors(place, lat, lon, loc6, loc8):
    assert to_locator(lat, lon, 8) == loc8
    assert to_locator(lat, lon, 6) == loc6
    assert to_locator(lat, lon) == loc6  # default precision is 6
    assert to_locator(lat, lon, 4) == loc6[:4]
    assert to_locator(lat, lon, 2) == loc6[:2]


@pytest.mark.parametrize(("place", "lat", "lon", "loc6", "loc8"), VECTORS)
def test_skill_vector_points_lie_in_their_cells(place, lat, lon, loc6, loc8):
    for loc in (loc6, loc8):
        lat_min, lon_min, lat_max, lon_max = to_bounds(loc)
        assert lat_min <= lat < lat_max
        assert lon_min <= lon < lon_max


def test_vectors_near_edges_need_exact_arithmetic():
    # Beograd's latitude is exactly the southern edge of extended row '5' ...
    assert to_bounds("KN04ft55")[0] == 44.8125
    assert to_locator(44.8125, 20.4612, 8)[-1] == "5"
    # ... and München's longitude is only 0.0004 extended units (3.3e-6 degree, ~25 cm)
    # west of an edge, so the epsilon must stay far below that.
    assert to_locator(48.14666, 11.60833, 8) == "JN58td25"
    assert to_bounds("JN58td35")[1] - 11.60833 < 5e-6


@pytest.mark.parametrize(
    ("lat", "lon", "precision", "expected"),
    [
        (-90, -180, 6, "AA00aa"),
        (-90.0, -180.0, 8, "AA00aa00"),
        (90, 180, 6, "RR99xx"),  # north pole and antimeridian clamp into the last cell
        (90.0, 180.0, 8, "RR99xx99"),
        (90.0, -180.0, 6, "AR09ax"),
        (-90.0, 180.0, 6, "RA90xa"),
        (0, 0, 8, "JJ00aa00"),
        (-0.0, -0.0, 6, "JJ00aa"),
        (-1e-6, -1e-6, 8, "II99xx99"),  # 0.1 m south-west of the origin is another field
        (89.99999, 179.99999, 8, "RR99xx99"),
        (-89.99999, -179.99999, 2, "AA"),
    ],
)
def test_to_locator_edges(lat, lon, precision, expected):
    assert to_locator(lat, lon, precision) == expected


def test_to_locator_precisions():
    assert [to_locator(44.8125, 20.4612, p) for p in VALID_LENGTHS] == [
        "KN",
        "KN04",
        "KN04ft",
        "KN04ft55",
    ]
    assert to_locator(44.8125, 20.4612, precision=LEVEL_EXTENDED) == "KN04ft55"


@pytest.mark.parametrize("precision", [0, 1, 3, 5, 7, 9, 10, -6, 6.5, "6", None, True])
def test_to_locator_bad_precision(precision):
    with pytest.raises(ValueError):
        to_locator(44.8, 20.4, precision)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (90.000001, 0.0),
        (-90.000001, 0.0),
        (0.0, 180.000001),
        (0.0, -180.000001),
        (200.0, 20.0),  # the example from ARCHITECTURE.md
        (44.0, 360.0),
        (math.nan, 0.0),
        (0.0, math.nan),
        (math.inf, 0.0),
        (0.0, -math.inf),
    ],
)
def test_to_locator_out_of_range(lat, lon):
    with pytest.raises(ValueError):
        to_locator(lat, lon)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (None, 20.0),  # a NULL attribute value
        (44.0, None),
        (10**400, 0.0),  # float() raises OverflowError for this int
        (0.0, -(10**400)),
        ("north", 20.0),
    ],
    ids=["lat-None", "lon-None", "lat-huge-int", "lon-huge-int", "lat-text"],
)
def test_to_locator_non_numbers_raise_value_error(lat, lon):
    # ValueError, not TypeError or OverflowError, so `except ValueError` is enough
    with pytest.raises(ValueError):
        to_locator(lat, lon)


# --- to_bounds / to_latlon -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        ("AA", exact(-90, -180, -80, -160)),
        ("JN", exact(40, 0, 50, 20)),
        ("RR", exact(80, 160, 90, 180)),
        ("AA00", exact(-90, -180, -89, -178)),
        ("KN04", exact(44, 20, 45, 22)),
        ("RR99", exact(89, 178, 90, 180)),
        ("AA00aa", exact(-90, -180, F(-90) + F(1, 24), F(-180) + F(1, 12))),
        ("KN04ft", exact(F(44) + F(19, 24), F(20) + F(5, 12), F(44) + F(20, 24), F(41, 2))),
        (
            "QF56od",
            exact(F(-34) + F(3, 24), F(150) + F(14, 12), F(-34) + F(4, 24), F(150) + F(15, 12)),
        ),
        ("RR99xx", exact(F(90) - F(1, 24), F(180) - F(1, 12), 90, 180)),
        ("JJ00aa00", exact(0, 0, F(1, 240), F(1, 120))),
        (
            "KN04ft55",
            exact(F(44) + F(195, 240), F(20) + F(55, 120), F(44) + F(196, 240), F(20) + F(56, 120)),
        ),
        ("RR99xx99", exact(F(90) - F(1, 240), F(180) - F(1, 120), 90, 180)),
    ],
)
def test_to_bounds_exact(loc, expected):
    assert to_bounds(loc) == expected
    assert to_bounds(loc.lower()) == expected
    assert to_bounds(loc.upper()) == expected


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        ("KN", exact(45, 30)),
        ("KN04", exact(F(89, 2), 21)),
        ("KN04ft", exact(F(717, 16), F(20) + F(11, 24))),  # 44.8125, 20.4583...
        ("KN04ft55", exact(F(44) + F(391, 480), F(20) + F(111, 240))),
        ("AA00aa00", exact(F(-90) + F(1, 480), F(-180) + F(1, 240))),
        ("RR99xx99", exact(F(90) - F(1, 480), F(180) - F(1, 240))),
    ],
)
def test_to_latlon_exact_center(loc, expected):
    assert to_latlon(loc) == expected
    assert to_latlon(loc.swapcase()) == expected


@pytest.mark.parametrize(("place", "lat", "lon", "loc6", "loc8"), VECTORS)
def test_to_latlon_is_center_and_round_trips(place, lat, lon, loc6, loc8):
    for loc in (loc6[:2], loc6[:4], loc6, loc8):
        lat_min, lon_min, lat_max, lon_max = to_bounds(loc)
        c_lat, c_lon = to_latlon(loc)
        assert c_lat == pytest.approx((lat_min + lat_max) / 2, abs=1e-12)
        assert c_lon == pytest.approx((lon_min + lon_max) / 2, abs=1e-12)
        assert to_locator(c_lat, c_lon, len(loc)) == loc


def test_cell_centers_on_finer_edges_resolve_up():
    # The centre of a cell is an edge of finer cells, the worst case for float math.
    assert to_locator(*to_latlon("KN"), 8) == "KN55aa00"
    assert to_locator(*to_latlon("KN04"), 8) == "KN04mm00"
    assert to_locator(*to_latlon("KN04ft"), 8) == "KN04ft55"


@pytest.mark.parametrize("bad", ["", "K", "KN0", "SA00", "KN04yt", "KN04ft55ab", " KN04", "KN04 "])
def test_to_bounds_and_to_latlon_reject_invalid(bad):
    with pytest.raises(ValueError):
        to_bounds(bad)
    with pytest.raises(ValueError):
        to_latlon(bad)


@pytest.mark.parametrize("bad", [None, 1234, 44.8, b"KN04"])
def test_to_bounds_and_to_latlon_reject_non_strings(bad):
    with pytest.raises(ValueError):
        to_bounds(bad)
    with pytest.raises(ValueError):
        to_latlon(bad)


# --- exact boundaries --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extent", "level"),
    [
        (EUROPE, LEVEL_FIELD),
        (WORLD, LEVEL_SQUARE),
        ((44.0, 20.0, 45.0, 22.0), LEVEL_SUBSQUARE),  # all 576 subsquares of KN04
        ((44.0, 20.0, 45.0, 20.25), LEVEL_EXTENDED),  # 240 x 30 extended squares
        ((-34.0, 150.0, -33.9, 152.0), LEVEL_EXTENDED),
    ],
)
def test_cell_edges_are_exact(extent, level):
    # The float `%` encoder in the skill maps more than half of these corners to the
    # wrong cell; the integer arithmetic must get every one right.
    for loc, (lat_min, lon_min, lat_max, lon_max) in iter_cells(*extent, level):
        assert to_locator(lat_min, lon_min, level) == loc
        # just inside the northern and eastern edges is still this cell ...
        assert to_locator(lat_max - 1e-9, lon_max - 1e-9, level) == loc
        # ... and the edges themselves belong to the neighbours, which start exactly there
        if lat_max < 90.0:
            north = to_locator(lat_max, lon_min, level)
            assert to_bounds(north)[0] == lat_max
            assert to_bounds(north)[1] == lon_min
        if lon_max < 180.0:
            east = to_locator(lat_min, lon_max, level)
            assert to_bounds(east)[0] == lat_min
            assert to_bounds(east)[1] == lon_max


def test_edges_with_representation_error_still_resolve():
    # 44 + 1/24 is 44.041666666666664 in floating point (also the correctly rounded edge
    # that to_bounds returns), and (lat + 90) * 240 gives 32169.999999999996, so a plain
    # floor() picks the row below. The same happens with -180 + 2 * (2 / 24).
    lat_edge = 44 + 1 * (1 / 24)
    lon_edge = -180 + 2 * (2 / 24)
    assert math.floor((lat_edge + 90) * 240) == 32169  # the trap
    assert math.floor((lon_edge + 180) * 120) == 19
    assert to_bounds("KN04ab")[0] == lat_edge
    assert to_locator(lat_edge, 20.0, 6) == "KN04ab"
    assert to_locator(0.0, lon_edge, 6) == "AJ00ca"
    extent = (lat_edge, lon_edge, lat_edge + 1 / 24, lon_edge + 2 / 24)
    assert snap_extent(*extent, LEVEL_SUBSQUARE) == to_bounds("AN04cb")
    assert count_cells(*extent, LEVEL_SUBSQUARE) == 1


# --- is_valid --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "loc",
    [
        "KN",
        "kn",
        "KN04",
        "kn04",
        "KN04ft",
        "kn04FT",
        "KN04st",  # S is a valid subsquare letter (a-x), only invalid in the field pair
        "KN04ft55",
        "Kn04fT55",
        "AA00aa00",
        "RR99xx99",
        "JN95wg04",
    ],
)
def test_is_valid_true(loc):
    assert is_valid(loc) is True


@pytest.mark.parametrize(
    "loc",
    [
        # lengths
        "",
        "K",
        "KN0",
        "KN04f",
        "KN04ft5",
        "KN04ft55a",
        "KN04ft55ab",  # 10 characters: normalize() cuts them, is_valid() does not
        "KN04ft55ab12",
        # wrong characters
        "SA00",
        "AS00",
        "SS",
        "ZZ00",
        "KN04yt",
        "KN04ty",
        "KN04yy",
        "KN04zz",
        "KNAA",
        "KN0A",
        "11AA",
        "KN04f1",
        "KN04ft5a",
        "KN04ftab",
        "KN04ft-5",
        # whitespace is not stripped (use normalize for user input)
        " KN04",
        "KN04 ",
        " KN04 ",
        "KN04\n",
        "KN04ft\r\n",
        "\tKN04ft",
        "KN 04",
        # non-ASCII look-alikes (would match [A-R] with Unicode case folding)
        "KN04",  # KELVIN SIGN
        "ıN04",  # LATIN SMALL LETTER DOTLESS I
        "İN04",  # LATIN CAPITAL LETTER I WITH DOT ABOVE
        "KN04ſt",  # LATIN SMALL LETTER LONG S
        "ＫＮ04",  # full-width KN
        "KN０４",  # full-width digits
        "KN٠٤",  # Arabic-Indic digits
    ],
)
def test_is_valid_false(loc):
    assert is_valid(loc) is False


@pytest.mark.parametrize("value", [None, 1234, 44.8, b"KN04", ["KN04"]])
def test_is_valid_non_strings(value):
    assert is_valid(value) is False


# --- normalize -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("KN04FT", "KN04ft"),
        ("kn04ft", "KN04ft"),
        ("kn04FT", "KN04ft"),
        ("KN04ft", "KN04ft"),
        ("kn", "KN"),
        ("Kn04", "KN04"),
        ("kn04FT55", "KN04ft55"),
        ("  KN04ft  ", "KN04ft"),
        ("\tkn04ft55\r\n", "KN04ft55"),
        (" KN04 ", "KN04"),  # no-break spaces from copy and paste
        ("KN04ft55ab", "KN04ft55"),  # 10 characters are cut to 8
        ("kn04FT55AX", "KN04ft55"),
        (" JN58td25nx ", "JN58td25"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected
    assert normalize(expected) == expected  # idempotent
    assert is_valid(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "K",
        "KN0",
        "SA00",
        "KN04yz",
        "KN04ft5",
        "KN04ft55a",
        "KN04ft55abc",
        "KN04ft55ab12",  # 12 characters are not cut
        "KN04ft5512",  # the 5th pair of a 10-character locator is letters a-x
        "KN04ft55yz",
        "ZZ04ft55ab",  # 10 characters with an invalid 8-character prefix
        "KN04 ft",
        None,
        42,
    ],
)
def test_normalize_invalid(raw):
    with pytest.raises(ValueError):
        normalize(raw)


def test_to_locator_output_is_normalized():
    for _, lat, lon, _, _ in VECTORS:
        for level in VALID_LENGTHS:
            loc = to_locator(lat, lon, level)
            assert normalize(loc) == loc


# --- random round trips ------------------------------------------------------------------


def test_round_trip_random_locators():
    rng = random.Random(20260929)
    for _ in range(10_000):
        length = rng.choice(VALID_LENGTHS)
        loc = random_locator(rng, length)
        assert is_valid(loc)
        canonical = normalize(loc)
        assert canonical.upper() == loc.upper()
        lat, lon = to_latlon(loc)
        assert to_locator(lat, lon, length) == canonical
        lat_min, lon_min, lat_max, lon_max = to_bounds(loc)
        assert lat_min < lat < lat_max
        assert lon_min < lon < lon_max
        assert to_locator(lat_min, lon_min, length) == canonical
        assert to_bounds(canonical) == (lat_min, lon_min, lat_max, lon_max)


def test_round_trip_random_coordinates():
    rng = random.Random(4404)
    for _ in range(10_000):
        lat = rng.uniform(-90.0, 90.0)
        lon = rng.uniform(-180.0, 180.0)
        loc8 = to_locator(lat, lon, 8)
        assert is_valid(loc8)
        assert normalize(loc8) == loc8
        for level in VALID_LENGTHS:
            assert to_locator(lat, lon, level) == loc8[:level]
        lat_min, lon_min, lat_max, lon_max = to_bounds(loc8)
        assert lat_min <= lat < lat_max
        assert lon_min <= lon < lon_max
        assert to_locator(*to_latlon(loc8), 8) == loc8


def test_centers_of_coarse_cells_at_finer_precision():
    # Exhaustive over all fields and a random sample of squares and subsquares.
    for f_lon in FIELD_CHARS:
        for f_lat in FIELD_CHARS:
            loc = f_lon + f_lat
            assert to_locator(*to_latlon(loc), 8) == loc + "55aa00"
    rng = random.Random(7)
    for _ in range(2_000):
        square = random_locator(rng, 4)
        assert to_locator(*to_latlon(square), 8) == normalize(square) + "mm00"
        subsquare = random_locator(rng, 6)
        assert to_locator(*to_latlon(subsquare), 8) == normalize(subsquare) + "55"


# --- cell_size ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (LEVEL_FIELD, exact(10, 20)),
        (LEVEL_SQUARE, exact(1, 2)),
        (LEVEL_SUBSQUARE, exact(F(1, 24), F(1, 12))),
        (LEVEL_EXTENDED, exact(F(1, 240), F(1, 120))),
    ],
)
def test_cell_size(level, expected):
    assert cell_size(level) == expected
    lat_min, lon_min, lat_max, lon_max = to_bounds(to_locator(44.8125, 20.4612, level))
    assert lat_max - lat_min == pytest.approx(expected[0], rel=1e-9)
    assert lon_max - lon_min == pytest.approx(expected[1], rel=1e-9)


@pytest.mark.parametrize("level", [0, 1, 3, 5, 10, -2, 4.5, "4", None, True])
def test_bad_level_raises(level):
    with pytest.raises(ValueError):
        cell_size(level)
    with pytest.raises(ValueError):
        snap_extent(44.0, 20.0, 45.0, 22.0, level)
    with pytest.raises(ValueError):
        count_cells(44.0, 20.0, 45.0, 22.0, level)
    with pytest.raises(ValueError):
        iter_cells(44.0, 20.0, 45.0, 22.0, level)  # raises on the call, not on iteration


# --- snap_extent -------------------------------------------------------------------------


def test_snap_extent_outward():
    assert snap_extent(44.3, 20.7, 45.2, 21.9, LEVEL_SQUARE) == (44.0, 20.0, 46.0, 22.0)
    assert snap_extent(44.3, 20.7, 45.2, 21.9, LEVEL_FIELD) == (40.0, 20.0, 50.0, 40.0)
    assert snap_extent(*EUROPE, LEVEL_SQUARE) == (34.0, -26.0, 72.0, 46.0)
    assert snap_extent(*EUROPE, LEVEL_FIELD) == (30.0, -40.0, 80.0, 60.0)
    assert snap_extent(44.80, 20.44, 44.83, 20.48, LEVEL_SUBSQUARE) == to_bounds("KN04ft")
    assert snap_extent(44.8126, 20.4585, 44.8163, 20.4659, LEVEL_EXTENDED) == to_bounds("KN04ft55")
    assert snap_extent(-34.2, 150.8, -33.5, 151.5, LEVEL_SUBSQUARE) == exact(
        F(-35) + F(19, 24), F(150) + F(9, 12), F(-34) + F(12, 24), F(150) + F(18, 12)
    )


def test_snap_extent_clamps_to_world():
    for level in VALID_LENGTHS:
        assert snap_extent(-100.0, -200.0, 100.0, 200.0, level) == WORLD
        assert snap_extent(-math.inf, -math.inf, math.inf, math.inf, level) == WORLD
    assert snap_extent(85.5, 175.5, 95.0, 190.0, LEVEL_SQUARE) == (85.0, 174.0, 90.0, 180.0)
    assert snap_extent(-95.0, -190.0, -85.5, -175.5, LEVEL_SQUARE) == (-90.0, -180.0, -85.0, -174.0)


@pytest.mark.parametrize(
    "loc", ["JN", "KN04", "KN04ft", "KN04ft55", "AA00aa00", "RR99xx99", "QF56od"]
)
def test_snap_extent_keeps_aligned_extents(loc):
    bounds = to_bounds(loc)
    for level in VALID_LENGTHS:
        if level >= len(loc):  # an aligned cell is aligned at every finer level too
            assert snap_extent(*bounds, level) == bounds


def test_snap_extent_keeps_aligned_multi_cell_extent():
    south_west = to_bounds("KN04aa")
    north_east = to_bounds("KN15xx")
    extent = (south_west[0], south_west[1], north_east[2], north_east[3])
    assert extent == (44.0, 20.0, 46.0, 24.0)
    assert snap_extent(*extent, LEVEL_SUBSQUARE) == extent
    extent = (to_bounds("KN04ft00")[0], to_bounds("KN04ft00")[1]) + to_bounds("KN04gu99")[2:]
    assert snap_extent(*extent, LEVEL_EXTENDED) == extent
    assert count_cells(*extent, LEVEL_EXTENDED) == 20 * 20
    assert count_cells(*extent, LEVEL_SUBSQUARE) == 4


def test_snap_extent_world_unchanged():
    for level in VALID_LENGTHS:
        assert snap_extent(*WORLD, level) == WORLD


@pytest.mark.parametrize(
    "extent",
    [
        (45.0, 20.0, 44.0, 22.0),  # lat_min > lat_max
        (44.0, 22.0, 45.0, 20.0),  # lon_min > lon_max (antimeridian crossing not supported)
        (math.nan, 20.0, 45.0, 22.0),
        (44.0, math.nan, 45.0, 22.0),
        (44.0, 20.0, math.nan, 22.0),
        (44.0, 20.0, 45.0, math.nan),
        (None, 20.0, 45.0, 22.0),
        (44.0, 20.0, 45.0, None),
        (44.0, 20.0, 10**400, 22.0),  # OverflowError from float() must become ValueError
        (44.0, "west", 45.0, 22.0),
    ],
)
def test_invalid_extent_raises(extent):
    with pytest.raises(ValueError):
        snap_extent(*extent, LEVEL_SQUARE)
    with pytest.raises(ValueError):
        count_cells(*extent, LEVEL_SQUARE)
    with pytest.raises(ValueError):
        iter_cells(*extent, LEVEL_SQUARE)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (44.8125, 20.4612),
        (44.0, 20.0),
        (45.2671, 19.8335),
        (90.0, 180.0),
        (-90.0, -180.0),
        (0.0, 0.0),
    ],
)
def test_point_extent_is_the_cell_of_the_point(lat, lon):
    for level in VALID_LENGTHS:
        loc = to_locator(lat, lon, level)
        assert snap_extent(lat, lon, lat, lon, level) == to_bounds(loc)
        assert count_cells(lat, lon, lat, lon, level) == 1
        assert list(iter_cells(lat, lon, lat, lon, level)) == [(loc, to_bounds(loc))]


def test_line_extent_gives_one_row():
    # zero height on a row edge: the row that to_locator gives for that latitude
    cells = list(iter_cells(44.0, 20.3, 44.0, 23.9, LEVEL_SQUARE))
    assert [loc for loc, _ in cells] == ["KN04", "KN14"]
    assert count_cells(44.0, 20.3, 44.0, 23.9, LEVEL_SQUARE) == 2


# Extents that do not touch the world. A canvas in EPSG:4326 can be panned past 180
# degrees, so the grid algorithm can pass such an extent: it must get no cells, not a
# strip of cells along the edge of the world that the extent does not cover.
OUTSIDE_WORLD = [
    (30.0, 200.0, 60.0, 250.0),  # east of the antimeridian
    (30.0, -250.0, 60.0, -200.0),  # west of it
    (95.0, 0.0, 100.0, 10.0),  # north of the pole
    (-100.0, 0.0, -95.0, 10.0),  # south of the pole
    (95.0, 200.0, 100.0, 250.0),  # both
    (95.0, 250.0, 95.0, 250.0),  # a point (to_locator raises for it)
    (0.0, math.nextafter(180.0, math.inf), 10.0, 190.0),  # one ulp east of 180
    (math.nextafter(90.0, math.inf), -math.inf, math.inf, math.inf),  # one ulp north of 90
    (-math.inf, -math.inf, -95.0, math.inf),
]


@pytest.mark.parametrize("extent", OUTSIDE_WORLD)
def test_extent_outside_the_world_has_no_cells(extent):
    for level in VALID_LENGTHS:
        assert count_cells(*extent, level) == 0
        assert list(iter_cells(*extent, level)) == []
        with pytest.raises(ValueError):  # no cell to snap to
            snap_extent(*extent, level)


@pytest.mark.parametrize(
    ("extent", "edge", "expected"),
    [
        # An extent that only touches the edge of the world gets the cells on that edge,
        # the same as the zero-size extent on the edge (to_locator accepts lat 90 etc.).
        ((90.0, 0.0, 95.0, 10.0), (90.0, 0.0, 90.0, 10.0), "JR09 JR19 JR29 JR39 JR49".split()),
        ((-95.0, 0.0, -90.0, 10.0), (-90.0, 0.0, -90.0, 10.0), "JA00 JA10 JA20 JA30 JA40".split()),
        ((0.0, 180.0, 10.0, 190.0), (0.0, 180.0, 10.0, 180.0), [f"RJ9{i}" for i in range(10)]),
        ((0.0, -190.0, 10.0, -180.0), (0.0, -180.0, 10.0, -180.0), [f"AJ0{i}" for i in range(10)]),
        ((90.0, 180.0, 95.0, 190.0), (90.0, 180.0, 90.0, 180.0), ["RR99"]),
    ],
)
def test_extent_touching_the_world_edge_gives_the_edge_cells(extent, edge, expected):
    assert [loc for loc, _ in iter_cells(*extent, LEVEL_SQUARE)] == expected
    for level in VALID_LENGTHS:
        cells = list(iter_cells(*extent, level))
        assert cells == list(iter_cells(*edge, level))
        assert count_cells(*extent, level) == len(cells) > 0
        assert snap_extent(*extent, level) == snap_extent(*edge, level)
        # (not the far end of the edge: a cell starting there does not overlap the extent)
        locators = {loc for loc, _ in cells}
        for lat, lon in [edge[:2], ((edge[0] + edge[2]) / 2, (edge[1] + edge[3]) / 2)]:
            assert to_locator(lat, lon, level) in locators


def test_point_extent_has_a_cell_exactly_when_to_locator_accepts_the_point():
    rng = random.Random(95250)
    lat_edges = [-90.0, 90.0, math.nextafter(-90.0, -math.inf), math.nextafter(90.0, math.inf)]
    lon_edges = [-180.0, 180.0, math.nextafter(-180.0, -math.inf), math.nextafter(180.0, math.inf)]
    lat_edges += [-math.inf, math.inf]
    lon_edges += [-math.inf, math.inf]
    inside = outside = 0
    for _ in range(2_000):
        lat = rng.choice(lat_edges) if rng.random() < 0.3 else rng.uniform(-100.0, 100.0)
        lon = rng.choice(lon_edges) if rng.random() < 0.3 else rng.uniform(-200.0, 200.0)
        level = rng.choice(VALID_LENGTHS)
        point = (lat, lon, lat, lon)
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            inside += 1
            loc = to_locator(lat, lon, level)
            assert list(iter_cells(*point, level)) == [(loc, to_bounds(loc))]
            assert count_cells(*point, level) == 1
            assert snap_extent(*point, level) == to_bounds(loc)
        else:
            outside += 1
            with pytest.raises(ValueError):
                to_locator(lat, lon, level)
            assert count_cells(*point, level) == 0
            assert list(iter_cells(*point, level)) == []
            with pytest.raises(ValueError):
                snap_extent(*point, level)
    assert inside > 500 and outside > 500


# --- count_cells / iter_cells --------------------------------------------------------------


GRID_CASES = [
    ((44.3, 20.7, 45.2, 21.9), LEVEL_SQUARE),  # Beograd area, a few squares
    (EUROPE, LEVEL_FIELD),
    (EUROPE, LEVEL_SQUARE),
    ((44.70, 20.30, 44.90, 20.60), LEVEL_SUBSQUARE),
    ((44.80, 20.44, 44.83, 20.48), LEVEL_EXTENDED),
    (WORLD, LEVEL_FIELD),
    ((-34.2, 150.8, -33.5, 151.5), LEVEL_SUBSQUARE),  # Sydney
    ((85.5, 175.5, 95.0, 190.0), LEVEL_SQUARE),  # partly outside the world
    ((-0.3, -0.3, 0.3, 0.3), LEVEL_SUBSQUARE),  # around lat/lon 0
    ((-10.0, -10.0, 10.0, 10.0), LEVEL_SQUARE),  # extent edges exactly on cell edges
]


@pytest.mark.parametrize(("extent", "level"), GRID_CASES)
def test_iter_cells_matches_count_and_covers_extent(extent, level):
    cells = list(iter_cells(*extent, level))
    assert len(cells) == count_cells(*extent, level)
    locators = [loc for loc, _ in cells]
    assert len(set(locators)) == len(locators)

    for loc, bounds in cells:
        assert len(loc) == level
        assert normalize(loc) == loc
        assert bounds == to_bounds(loc)

    # the cells tile the snapped extent without gaps
    s_lat_min, s_lon_min, s_lat_max, s_lon_max = snap_extent(*extent, level)
    assert min(b[0] for _, b in cells) == s_lat_min
    assert min(b[1] for _, b in cells) == s_lon_min
    assert max(b[2] for _, b in cells) == s_lat_max
    assert max(b[3] for _, b in cells) == s_lon_max
    dlat, dlon = cell_size(level)
    rows = round((s_lat_max - s_lat_min) / dlat)
    cols = round((s_lon_max - s_lon_min) / dlon)
    assert len(cells) == rows * cols

    # no superfluous cells: each one overlaps the requested extent (clamped to the world)
    lat_min, lon_min = max(extent[0], -90.0), max(extent[1], -180.0)
    lat_max, lon_max = min(extent[2], 90.0), min(extent[3], 180.0)
    for _, (c_lat_min, c_lon_min, c_lat_max, c_lon_max) in cells:
        assert c_lat_min < lat_max and c_lat_max > lat_min
        assert c_lon_min < lon_max and c_lon_max > lon_min

    # every point of the extent is in one of the cells
    rng = random.Random(level)
    wanted = set(locators)
    points = [(lat_min, lon_min)] + [
        (rng.uniform(lat_min, lat_max), rng.uniform(lon_min, lon_max)) for _ in range(500)
    ]
    for lat, lon in points:
        assert to_locator(lat, lon, level) in wanted


def test_iter_cells_order_is_lon_then_lat():
    cells = list(iter_cells(44.3, 19.7, 46.2, 23.9, LEVEL_SQUARE))
    assert [loc for loc, _ in cells] == [
        "JN94",
        "JN95",
        "JN96",
        "KN04",
        "KN05",
        "KN06",
        "KN14",
        "KN15",
        "KN16",
    ]
    keys = [(bounds[1], bounds[0]) for _, bounds in iter_cells(*EUROPE, LEVEL_SQUARE)]
    assert keys == sorted(keys)


def test_iter_cells_is_lazy():
    cells = iter_cells(*WORLD, LEVEL_EXTENDED)  # 1.9e9 cells: must not be materialized
    assert next(cells) == ("AA00aa00", to_bounds("AA00aa00"))
    assert next(cells) == ("AA00aa01", to_bounds("AA00aa01"))


def test_children_per_cell():
    assert count_cells(*to_bounds("KN"), LEVEL_SQUARE) == 100
    assert count_cells(*to_bounds("KN04"), LEVEL_SUBSQUARE) == 576
    assert count_cells(*to_bounds("KN04ft"), LEVEL_EXTENDED) == 100
    assert count_cells(*to_bounds("KN04"), LEVEL_EXTENDED) == 57_600
    children = [loc for loc, _ in iter_cells(*to_bounds("KN04ft"), LEVEL_EXTENDED)]
    assert len(children) == 100
    assert all(loc.startswith("KN04ft") for loc in children)


def test_count_cells_world_and_europe():
    assert count_cells(*WORLD, LEVEL_FIELD) == 18 * 18
    assert count_cells(*WORLD, LEVEL_SQUARE) == 180 * 180
    assert count_cells(*WORLD, LEVEL_SUBSQUARE) == 18 * 18 * 10 * 10 * 24 * 24
    assert count_cells(*WORLD, LEVEL_EXTENDED) == 43_200 * 43_200
    assert count_cells(*EUROPE, LEVEL_SQUARE) == 38 * 36
    # the guard for the grid algorithm: Europe is fine at square level, not below
    assert count_cells(*EUROPE, LEVEL_SQUARE) < MAX_GRID_CELLS
    # -25 and 45 are subsquare edges (unlike square edges, which are even degrees)
    assert count_cells(*EUROPE, LEVEL_SUBSQUARE) == (38 * 24) * (70 * 12) > MAX_GRID_CELLS


def test_count_cells_is_constant_time():
    start = time.perf_counter()
    for _ in range(100):
        assert count_cells(*WORLD, LEVEL_EXTENDED) == 1_866_240_000
    assert time.perf_counter() - start < 0.5


@pytest.mark.slow
def test_europe_square_grid_is_fast():
    # PLAN.md M1: the square-level grid for Europe in under 2 s, QGIS layer included,
    # so the core part must be far below that.
    start = time.perf_counter()
    cells = list(iter_cells(*EUROPE, LEVEL_SQUARE))
    elapsed = time.perf_counter() - start
    assert len(cells) == 1368
    assert elapsed < 0.5


@pytest.mark.slow
def test_max_grid_cells_generation_time():
    # a grid at the guard limit (200 000 cells) is still quick in core
    extent = (40.0, 0.0, 56.0, 43.0)  # 384 rows x 516 columns of subsquares
    assert count_cells(*extent, LEVEL_SUBSQUARE) == 198_144 <= MAX_GRID_CELLS
    start = time.perf_counter()
    n = sum(1 for _ in iter_cells(*extent, LEVEL_SUBSQUARE))
    elapsed = time.perf_counter() - start
    assert n == count_cells(*extent, LEVEL_SUBSQUARE)
    assert elapsed < 2.0
