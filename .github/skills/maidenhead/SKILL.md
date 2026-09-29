---
name: maidenhead
description: Maidenhead (QTH / IARU) locator system - encoding, decoding, bounds, validation and grid generation, with verified test vectors. Read for hamq/core/maidenhead.py and the grid algorithm.
---

# Maidenhead locator

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

```python
def to_locator(lat: float, lon: float, precision: int = 6) -> str:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinate out of range")
    # clamp the north pole and antimeridian into the last cell
    lon = min(lon + 180.0, 360.0 - 1e-9)
    lat = min(lat + 90.0, 180.0 - 1e-9)
    s = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    lon %= 20; lat %= 10
    s += str(int(lon // 2)) + str(int(lat // 1))
    lon %= 2; lat %= 1
    if precision >= 6:
        s += chr(97 + int(lon // (2/24))) + chr(97 + int(lat // (1/24)))
        lon %= 2/24; lat %= 1/24
    if precision >= 8:
        s += str(int(lon // (2/240))) + str(int(lat // (1/240)))
    return s
```

`precision` is 4, 6 or 8 characters.

## Decode

`to_bounds(loc) -> (lat_min, lon_min, lat_max, lon_max)` by summing cell offsets.
`to_latlon(loc)` returns the **center** of the cell, not the corner.

## Validation

Regex (case-insensitive):
`^[A-R]{2}([0-9]{2}([A-X]{2}([0-9]{2})?)?)?$`

Valid lengths: 2, 4, 6, 8. Reject odd lengths.
In ADIF, `GRIDSQUARE` may carry 4, 6 or 8 characters. Some loggers write
10 characters; truncate to 8 and log a warning.

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
- `(90, 180)` -> `RR99xx` (clamped)
- Round trip: `to_locator(*to_latlon(loc), len(loc)) == loc.upper()[:2] + ...` for random locators
- `is_valid("KN04")` True, `is_valid("KN0")` False, `is_valid("SA00")` False

## Grid generation

Generate polygons for a chosen level inside an extent (EPSG:4326):
- Snap the extent outward to the cell size of the level.
- Iterate lon then lat, emit rectangle + attribute `locator`.
- Guard: subsquare level over a large extent explodes (whole world = 18*18*10*10*24*24 = 18.7 M cells).
  Refuse or warn above ~200 000 cells and suggest a smaller extent.
- For display at small scales, a label rule by level is enough; do not generate the world at subsquare level.
