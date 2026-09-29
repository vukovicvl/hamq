"""Maidenhead (QTH / IARU) locator: encode, decode, validate and grid cells.

A locator is a sequence of (longitude, latitude) character pairs counted from
180 W, 90 S: field ``A``-``R`` (20 x 10 degrees), square ``0``-``9`` (2 x 1),
subsquare ``a``-``x`` (5' x 2.5') and extended square ``0``-``9`` (30" x 15").
The canonical form has an uppercase field and a lowercase subsquare (``KN04ft55``).

All arithmetic works on integer cell indices at the extended level: longitude in
units of 1/120 degree, latitude in units of 1/240 degree. Both axes then have
43 200 units, and a field, square, subsquare and extended square span 2400, 240,
10 and 1 units on either axis. Degrees are produced from those integers with a
single correctly rounded division, so neighbouring cells share bit-identical
edges and every edge returned by :func:`to_bounds` maps back to its own cell in
:func:`to_locator` (the south and west edges belong to a cell, the north and east
edges to its neighbours).

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Iterator

__all__ = [
    "LEVEL_EXTENDED",
    "LEVEL_FIELD",
    "LEVEL_SQUARE",
    "LEVEL_SUBSQUARE",
    "MAX_GRID_CELLS",
    "VALID_LENGTHS",
    "cell_size",
    "count_cells",
    "is_valid",
    "iter_cells",
    "normalize",
    "snap_extent",
    "to_bounds",
    "to_latlon",
    "to_locator",
]

LEVEL_FIELD, LEVEL_SQUARE, LEVEL_SUBSQUARE, LEVEL_EXTENDED = 2, 4, 6, 8
VALID_LENGTHS = (2, 4, 6, 8)
# Largest grid the grid algorithm should generate; above it, ask for a smaller extent.
MAX_GRID_CELLS = 200_000

_LAT_PER_DEG = 240  # extended units per degree of latitude (15")
_LON_PER_DEG = 120  # extended units per degree of longitude (30")
_UNITS = 180 * _LAT_PER_DEG  # units along either axis: 43 200 (== 360 * _LON_PER_DEG)
_LAT_OFFSET = 90 * _LAT_PER_DEG  # units from the south pole to the equator
_LON_OFFSET = 180 * _LON_PER_DEG  # units from 180 W to the prime meridian
# Size of one cell of each level, in units; the same on both axes.
_SPAN = {LEVEL_FIELD: 2400, LEVEL_SQUARE: 240, LEVEL_SUBSQUARE: 10, LEVEL_EXTENDED: 1}
# (first character, units per step) of each character pair
_PAIRS = ((ord("A"), 2400), (ord("0"), 240), (ord("A"), 10), (ord("0"), 1))
# Tolerance in units against floating point representation error only: a position
# within 1e-9 units (4e-12 degree of latitude, 8e-12 of longitude, about a micrometre)
# of a cell edge counts as on the edge. Edges and centres produced by this module come
# back from (deg + 90) * 240 or (deg + 180) * 120 within 7.3e-12 units (one ulp).
_EPS = 1e-9

_FIELD_CHARS = "ABCDEFGHIJKLMNOPQR"
_SUBSQUARE_CHARS = "abcdefghijklmnopqrstuvwx"
_DIGITS = "0123456789"

# re.ASCII keeps IGNORECASE from matching look-alikes such as KELVIN SIGN or long s.
_LOCATOR_RE = re.compile(
    r"[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?)?", re.IGNORECASE | re.ASCII
)
_TEN_CHAR_RE = re.compile(r"[A-R]{2}[0-9]{2}[A-X]{2}[0-9]{2}[A-X]{2}", re.IGNORECASE | re.ASCII)


def is_valid(locator: str) -> bool:
    """Return True if ``locator`` is a 2, 4, 6 or 8 character Maidenhead locator.

    Letter case does not matter (``"kn04FT"`` is valid). Surrounding whitespace and
    10-character locators are not valid here; clean user or file input with
    :func:`normalize` first. Non-string values give False.
    """
    return isinstance(locator, str) and _LOCATOR_RE.fullmatch(locator) is not None


def normalize(locator: str) -> str:
    """Return the canonical form of a locator: ``" kn04FT "`` -> ``"KN04ft"``.

    Surrounding whitespace is stripped, the field is uppercased and the subsquare
    lowercased. A 10-character locator (fifth pair ``a``-``x``) is cut to its
    8-character prefix. Raises ValueError for anything else that is not valid.
    """
    if isinstance(locator, str):
        text = locator.strip()
        if len(text) == 10 and _TEN_CHAR_RE.fullmatch(text):
            text = text[:8]
        if _LOCATOR_RE.fullmatch(text):
            text = text.upper()
            return text[:4] + text[4:6].lower() + text[6:]
    raise ValueError(f"invalid Maidenhead locator: {locator!r}")


def to_locator(lat: float, lon: float, precision: int = 6) -> str:
    """Return the locator of the cell containing (lat, lon), ``precision`` characters long.

    ``precision`` is one of :data:`VALID_LENGTHS`. A position on a cell edge belongs to
    the cell to its north and east; the north pole and the antimeridian (lat 90,
    lon 180) are clamped into the last cell (``RR99xx99``).
    Raises ValueError for a bad precision, a coordinate outside the world (including
    NaN) or a value that is not a number (such as None).
    """
    level = _check_level(precision, "precision")
    lat = _as_float(lat, "lat")
    lon = _as_float(lon, "lon")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"coordinate out of range: lat={lat!r}, lon={lon!r}")
    ilat = _floor_units((lat + 90.0) * _LAT_PER_DEG)
    ilon = _floor_units((lon + 180.0) * _LON_PER_DEG)
    return _encode(ilat, ilon, level)


def to_bounds(locator: str) -> tuple[float, float, float, float]:
    """Return the cell of ``locator`` as ``(lat_min, lon_min, lat_max, lon_max)`` in degrees.

    Any letter case is accepted; raises ValueError if ``is_valid(locator)`` is False.
    """
    ilat, ilon, span = _parse(locator)
    return (
        _lat_degrees(ilat),
        _lon_degrees(ilon),
        _lat_degrees(ilat + span),
        _lon_degrees(ilon + span),
    )


def to_latlon(locator: str) -> tuple[float, float]:
    """Return the centre ``(lat, lon)`` of the cell of ``locator`` (not its corner).

    Any letter case is accepted; raises ValueError if ``is_valid(locator)`` is False.
    """
    ilat, ilon, span = _parse(locator)
    # centre in half units: an exact integer numerator and a single rounding
    lat = (2 * (ilat - _LAT_OFFSET) + span) / (2 * _LAT_PER_DEG)
    lon = (2 * (ilon - _LON_OFFSET) + span) / (2 * _LON_PER_DEG)
    return lat, lon


def cell_size(level: int) -> tuple[float, float]:
    """Return ``(dlat, dlon)`` in degrees of a cell at ``level`` (2, 4, 6 or 8 characters).

    Example: ``cell_size(LEVEL_SQUARE) == (1.0, 2.0)``. Raises ValueError for a bad level.
    """
    span = _SPAN[_check_level(level, "level")]
    return span / _LAT_PER_DEG, span / _LON_PER_DEG


def snap_extent(
    lat_min: float, lon_min: float, lat_max: float, lon_max: float, level: int
) -> tuple[float, float, float, float]:
    """Grow an extent outward to the cell edges of ``level``, clamped to the world.

    Returns ``(lat_min, lon_min, lat_max, lon_max)``: the union of the cells that
    :func:`iter_cells` yields. An extent already on cell edges is returned unchanged; a
    zero-width or zero-height extent keeps the single row or column of cells that
    contains it, so a point extent gives the cell of that point. An extent that only
    touches the edge of the world gets the cells on that edge (lat 90..95 -> top row).
    Raises ValueError for a bad level, NaN, a value that is not a number, min > max (an
    extent crossing the antimeridian must be split by the caller), and for an extent
    wholly outside the world, which has no cells (:func:`count_cells` returns 0 for it).
    """
    _, span, (row_first, row_end), (col_first, col_end) = _grid(
        lat_min, lon_min, lat_max, lon_max, level
    )
    if row_first == row_end:  # no cells, see _grid
        raise ValueError(
            f"extent outside the world: lat {lat_min!r}..{lat_max!r}, lon {lon_min!r}..{lon_max!r}"
        )
    return (
        _lat_degrees(row_first * span),
        _lon_degrees(col_first * span),
        _lat_degrees(row_end * span),
        _lon_degrees(col_end * span),
    )


def count_cells(lat_min: float, lon_min: float, lat_max: float, lon_max: float, level: int) -> int:
    """Return how many cells :func:`iter_cells` yields for the extent, in constant time.

    Compare the result with :data:`MAX_GRID_CELLS` before generating a grid. An extent
    wholly outside the world (a canvas panned past 180 degrees) gives 0.
    Raises ValueError like :func:`snap_extent`, except for such an extent.
    """
    _, _, (row_first, row_end), (col_first, col_end) = _grid(
        lat_min, lon_min, lat_max, lon_max, level
    )
    return (row_end - row_first) * (col_end - col_first)


def iter_cells(
    lat_min: float, lon_min: float, lat_max: float, lon_max: float, level: int
) -> Iterator[tuple[str, tuple[float, float, float, float]]]:
    """Yield ``(locator, (lat_min, lon_min, lat_max, lon_max))`` for every cell of the extent.

    The cells tile :func:`snap_extent` of the same arguments. They come column by
    column: longitude west to east in the outer loop, latitude south to north in the
    inner loop. Each bounds tuple equals ``to_bounds(locator)`` exactly. The iterator is
    lazy; check :func:`count_cells` first for large extents. Nothing is yielded for an
    extent wholly outside the world. Arguments are checked when this function is called,
    raising ValueError like :func:`count_cells`.
    """
    level, span, (row_first, row_end), (col_first, col_end) = _grid(
        lat_min, lon_min, lat_max, lon_max, level
    )
    return _generate_cells(level, span, row_first, row_end, col_first, col_end)


# --- helpers ----------------------------------------------------------------------------


def _check_level(value: int, name: str) -> int:
    """Return ``value`` as an int if it is one of VALID_LENGTHS, else raise ValueError."""
    if value not in VALID_LENGTHS:  # also rejects "6", None and True (== 1)
        raise ValueError(f"{name} must be one of {VALID_LENGTHS}, got {value!r}")
    return int(value)


def _as_float(value: float, name: str) -> float:
    """``float(value)``, raising ValueError instead of TypeError (None) or OverflowError."""
    try:
        return float(value)
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"cannot convert {name} to float: {exc}") from exc


def _parse(locator: str) -> tuple[int, int, int]:
    """Return ``(lat_units, lon_units, span)`` of the south-west corner of a locator's cell."""
    if not is_valid(locator):
        raise ValueError(f"invalid Maidenhead locator: {locator!r}")
    text = locator.upper()
    ilat = ilon = 0
    for pair in range(len(text) // 2):
        first, step = _PAIRS[pair]
        ilon += (ord(text[2 * pair]) - first) * step
        ilat += (ord(text[2 * pair + 1]) - first) * step
    return ilat, ilon, _SPAN[len(text)]


def _floor_units(units: float) -> int:
    """Index of the extended cell containing a position ``units`` from the origin, clamped."""
    return min(max(math.floor(units + _EPS), 0), _UNITS - 1)


def _ceil_units(units: float) -> int:
    """Smallest extended cell edge at or above ``units``, clamped to the world."""
    return min(max(math.ceil(units - _EPS), 0), _UNITS)


def _lat_degrees(units: int) -> float:
    return (units - _LAT_OFFSET) / _LAT_PER_DEG


def _lon_degrees(units: int) -> float:
    return (units - _LON_OFFSET) / _LON_PER_DEG


def _axis_chars(units: int, level: int) -> str:
    """The characters of one axis (every other character of the locator) for a cell index."""
    chars = _FIELD_CHARS[units // 2400]
    if level >= LEVEL_SQUARE:
        chars += _DIGITS[units // 240 % 10]
    if level >= LEVEL_SUBSQUARE:
        chars += _SUBSQUARE_CHARS[units // 10 % 24]
    if level >= LEVEL_EXTENDED:
        chars += _DIGITS[units % 10]
    return chars


def _encode(ilat: int, ilon: int, level: int) -> str:
    """Locator of the extended cell (ilat, ilon), ``level`` characters long."""
    return "".join(map(operator.add, _axis_chars(ilon, level), _axis_chars(ilat, level)))


def _axis_range(
    low: float, high: float, half_range: float, per_deg: int, span: int
) -> tuple[int, int]:
    """First and one-past-last cell index (at ``span``) covering ``[low, high]`` on one axis.

    ``[low, high]`` must touch ``[-half_range, half_range]``; the part outside is clamped.
    """
    low = min(max(low, -half_range), half_range)
    high = min(max(high, -half_range), half_range)
    first = _floor_units((low + half_range) * per_deg) // span
    end = (_ceil_units((high + half_range) * per_deg) + span - 1) // span  # round up
    # at least the one row / column that contains a zero-size extent
    return first, min(max(end, first + 1), _UNITS // span)


def _grid(
    lat_min: float, lon_min: float, lat_max: float, lon_max: float, level: int
) -> tuple[int, int, tuple[int, int], tuple[int, int]]:
    """Validate an extent and return ``(level, span, (row_first, row_end), (col_first, col_end))``.

    Both ranges are empty (no cells) when the extent does not touch the world.
    """
    level = _check_level(level, "level")
    lat_min = _as_float(lat_min, "lat_min")
    lon_min = _as_float(lon_min, "lon_min")
    lat_max = _as_float(lat_max, "lat_max")
    lon_max = _as_float(lon_max, "lon_max")
    # the comparisons are also False for NaN
    if not (lat_min <= lat_max and lon_min <= lon_max):
        raise ValueError(
            f"invalid extent: lat {lat_min!r}..{lat_max!r}, lon {lon_min!r}..{lon_max!r}"
        )
    span = _SPAN[level]
    # Same closed world as to_locator: an extent that only touches its edge (lat_min == 90)
    # gets the edge cells, one beyond it (lat_min > 90) gets none instead of being clamped.
    if lat_min > 90.0 or lat_max < -90.0 or lon_min > 180.0 or lon_max < -180.0:
        return level, span, (0, 0), (0, 0)
    rows = _axis_range(lat_min, lat_max, 90.0, _LAT_PER_DEG, span)
    cols = _axis_range(lon_min, lon_max, 180.0, _LON_PER_DEG, span)
    return level, span, rows, cols


def _generate_cells(
    level: int, span: int, row_first: int, row_end: int, col_first: int, col_end: int
) -> Iterator[tuple[str, tuple[float, float, float, float]]]:
    """Generator behind :func:`iter_cells`; the rows are computed once and reused."""
    rows = [
        (_axis_chars(row * span, level), _lat_degrees(row * span), _lat_degrees((row + 1) * span))
        for row in range(row_first, row_end)
    ]
    for col in range(col_first, col_end):
        ilon = col * span
        lon_chars = _axis_chars(ilon, level)
        west = _lon_degrees(ilon)
        east = _lon_degrees(ilon + span)
        for lat_chars, south, north in rows:
            yield "".join(map(operator.add, lon_chars, lat_chars)), (south, west, north, east)
