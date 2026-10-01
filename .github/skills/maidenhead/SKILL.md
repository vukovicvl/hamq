---
name: maidenhead
description: Maidenhead (QTH / IARU) locator system - encoding with integer cell arithmetic, decoding, bounds, validation, normalize and grid generation (levels, cell limit, antimeridian and poles), with verified test vectors. Read for hamq/core/maidenhead.py and the grid algorithm.
---

# Maidenhead locator

Where this skill and `docs/ARCHITECTURE.md` differ, the contract and the code win.

## Structure

| Pair | Characters | Divisions | Lon size | Lat size |
|---|---|---|---|---|
| Field | `A`-`R` | 18 x 18 | 20° | 10° |
| Square | `0`-`9` | 10 x 10 | 2° | 1° |
| Subsquare | `a`-`x` | 24 x 24 | 5' (2/24°) | 2.5' (1/24°) |
| Extended | `0`-`9` | 10 x 10 | 30" (2/240°) | 15" (1/240°) |

Each pair is (lon, lat). Longitude first. Origin is 180° W, 90° S.
Convention: field uppercase, subsquare lowercase (`KN04ft`). Accept any case on input.

## Encode

Use integer cell arithmetic, not repeated float `%`: count extended cells (1/240° of
latitude, 1/120° of longitude, 43 200 on either axis; a field, square, subsquare and
extended square span 2400, 240, 10 and 1 of them). Float `%` puts many cell corners
into the wrong cell, and even a plain `floor` needs a tiny epsilon: the edge
`44 + 1/24` is `44.041666666666664`, and `(lat + 90) * 240` gives `32169.999999999996`.

```python
import math

PAIRS = (("ABCDEFGHIJKLMNOPQR", 2400), ("0123456789", 240),
         ("abcdefghijklmnopqrstuvwx", 10), ("0123456789", 1))  # characters, cells per step

def to_locator(lat: float, lon: float, precision: int = 6) -> str:
    if precision not in (2, 4, 6, 8):
        raise ValueError("precision must be 2, 4, 6 or 8")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinate out of range")
    # extended cell index 0..43199; 1e-9 units absorbs float noise at an edge, and
    # min() clamps the north pole and the antimeridian into the last cell
    ilat = min(max(math.floor((lat + 90) * 240 + 1e-9), 0), 43199)
    ilon = min(max(math.floor((lon + 180) * 120 + 1e-9), 0), 43199)
    s = ""
    for chars, step in PAIRS[: precision // 2]:
        s += chars[ilon // step % len(chars)] + chars[ilat // step % len(chars)]
    return s
```

`precision` is 2, 4, 6 or 8 characters. A point on a cell edge belongs to the cell
north and east of it.

## Decode

`to_bounds(loc) -> (lat_min, lon_min, lat_max, lon_max)` by summing cell offsets
(integer units, one division per edge, so neighbouring cells share bit-identical edges).
`to_latlon(loc)` returns the **center** of the cell, not the corner. Both accept any
case and raise `ValueError` for an invalid locator. `cell_size(level)` gives
`(dlat, dlon)`.

## Validation

Regex (case-insensitive):
`^[A-R]{2}([0-9]{2}([A-X]{2}([0-9]{2})?)?)?$`

Valid lengths: 2, 4, 6, 8. Reject odd lengths.
`is_valid` accepts no surrounding whitespace and no 10 characters (`re.ASCII`, so
look-alike letters do not match). `normalize` strips, fixes the case (`kn04FT` ->
`KN04ft`) and cuts a 10-character locator (fifth pair `a`-`x`) to 8; anything else
invalid raises `ValueError`.
In ADIF, `GRIDSQUARE` may carry 2 to 8 characters (2 = a whole field, see the adif
skill). Some loggers write 10 characters; truncate to 8 and log a warning
(`core/qso.py`).

## Test vectors (verified)

| Place | Lat | Lon | 6 chars | 8 chars |
|---|---|---|---|---|
| Beograd | 44.8125 | 20.4612 | KN04ft | KN04ft55 |
| Novi Sad | 45.2671 | 19.8335 | JN95wg | JN95wg04 |
| München | 48.14666 | 11.60833 | JN58td | JN58td25 |
| Washington DC | 38.8977 | -77.0366 | FM18lv | FM18lv55 |
| Sydney | -33.8688 | 151.2093 | QF56od | QF56od51 |

Edge cases to test:
- `(-90, -180)` -> `AA00aa`
- `(90, 180)` -> `RR99xx` (clamped; `RR99xx99` at 8)
- `(0, 0, 8)` -> `JJ00aa00`; `(-1e-6, -1e-6, 8)` -> `II99xx99`
- Round trip: `to_locator(*to_latlon(loc), len(loc)) == normalize(loc)` for random locators
  of every length; the south-west corner `to_bounds(loc)[:2]` gives `loc` too
- `to_locator(*to_latlon("KN04ft"), 8) == "KN04ft55"` (a centre lies on finer edges)
- `is_valid("KN04")` True, `is_valid("KN0")` False, `is_valid("SA00")` False
- `normalize(" kn04FT ")` -> `KN04ft`, `normalize("KN04ft55ab")` -> `KN04ft55`

## Grid generation

Generate polygons for a chosen level inside an extent (EPSG:4326):
- Levels: field, square, subsquare and extended square (2, 4, 6, 8 characters).
- Snap the extent outward to the cell size of the level (`snap_extent`, clamped to the
  world; a zero-size extent gives the cell that contains it; an extent wholly outside
  the world has no cells).
- Iterate lon then lat, emit rectangle + attribute `locator` (`iter_cells`, lazy; each
  rectangle equals `to_bounds(locator)`).
- Guard: subsquare level over a large extent explodes (whole world = 18*18*10*10*24*24 = 18.7 M cells).
  `count_cells` is constant time; above `MAX_GRID_CELLS = 200_000` (all parts of the
  extent together) the algorithm refuses and suggests a smaller extent or a coarser
  level.
- Extent (`processing/alg_grid.py`, `extent_to_wgs84`): converted to EPSG:4326 point
  by point along its edges (`transformBoundingBox` differs between QGIS versions) and
  clamped to the world. Across the antimeridian it gives two parts, west and east of
  180° (core `snap_extent` refuses min > max, so the caller splits); around a pole it
  covers all longitudes up to the pole; points that cannot be transformed are skipped.
- For display at small scales, a label rule by level is enough; do not generate the world at subsquare level.
  The grid style shows locator labels only from the scale where they fit
  (`styles.GRID_LABEL_SCALES`: fields 1:200M, squares 1:15M, subsquares 1:500k,
  extended 1:40k).
