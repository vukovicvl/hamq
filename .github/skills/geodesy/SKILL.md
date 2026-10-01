---
name: geodesy
description: Distance, bearing and great-circle lines for QSO paths - pure-Python formulas for core and how far they are from WGS84, QgsDistanceArea for geodesic lines and its quirks, antimeridian and antipode handling, azimuthal equidistant projection and helper layer. Read for distance/bearing fields, the qso_path layer and the azimuthal map.
---

# Geodesy for QSO paths

Where this skill and `docs/ARCHITECTURE.md` differ, the contract and the code win.

## In core (no QGIS)

Use a spherical model in `core/geo.py` for speed and testability. Against WGS84
geodesics (Karney) the sphere is off by -0.45 % to +0.56 % in distance (the extremes
are short north-south paths near the poles and the equator; Beograd-Sydney +0.02 %),
and by up to about 0.3° in initial bearing up to 15 000 km, more on longer paths and
tens of degrees near the antipode. Fine for ham use. The stored `distance_km` /
`bearing_deg` are these spherical values (PLAN.md data model).

```python
from math import radians, degrees, sin, cos, atan2, sqrt
R = 6371.0088  # mean Earth radius, km

def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lon2 - lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    a = min(max(a, 0.0), 1.0)  # rounding can pass 1 for antipodal points (domain error)
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    x = sin(dl) * cos(p2)
    y = cos(p1)*sin(p2) - sin(p1)*cos(p2)*cos(dl)
    return (degrees(atan2(x, y)) + 360) % 360
```

Long path bearing = `(short + 180) % 360`, long path distance = `2 * pi * R - short`
(40030.2 km minus the short path): `geo.long_path(distance_km, bearing_deg)`.

The rest of `core/geo.py`: `is_antipodal(..., tolerance_km=20.0)`, `destination(lat,
lon, bearing_deg, distance_km)`, `great_circle(..., step_km=100.0)` (parts of
`(lat, lon)` points split at ±180°, `[]` for antipodal points, `[[point]]` for the
same point) and `aeqd_proj(lat, lon)`.

## Test vectors (WGS84 ellipsoid reference)

From Beograd (44.8125, 20.4612):

| To | Distance km | Bearing ° |
|---|---|---|
| München (48.14666, 11.60833) | 773.6 | 301.8 |
| Washington (38.8977, -77.0366) | 7608.7 | 303.9 |
| Sydney (-33.8688, 151.2093) | 15676.1 | 91.0 |

Spherical results must be within 0.5 % distance and 0.5° bearing of these
(`QgsDistanceArea` on WGS84 confirms the table: 773.61 / 7608.71 / 15676.14 km; the
sphere gives 15679.7 km to Sydney).

## Geodesic lines in QGIS

```python
from qgis.core import QgsDistanceArea, QgsCoordinateReferenceSystem, QgsPointXY, QgsGeometry
from hamq.core import geo

da = QgsDistanceArea()
da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
da.setEllipsoid("WGS84")
length_m = geo.distance_km(lat1, lon1, lat2, lon2) * 1000
interval = min(100000, length_m / 2)  # 100 km step, at most half the path (see below)
parts = da.geodesicLine(QgsPointXY(lon1, lat1), QgsPointXY(lon2, lat2),
                        interval, True)   # break at antimeridian
geom = QgsGeometry.fromMultiPolylineXY(parts)
```

`breakLine=True` returns separate parts when the path crosses ±180°. Without
it, a Pacific path is drawn across the whole map. Always use MultiLineString.

`gpkg.geodesic_path()` (the `qso_path` layer) works around `geodesicLine` quirks seen
on QGIS 3.34 to 4.2:
- its first vertex is always one interval along the line, so on a path shorter than
  the interval it lies beyond the end: the interval is at most half the length;
- near a pole a step can jump the antimeridian without a break (152.7° -> -115.9°):
  such a segment is cut at ±180° at the interpolated latitude;
- a path starting or ending exactly on ±180° gets a zero-length part: parts that are
  no line are dropped.

The line is the WGS84 geodesic, but `qso_path.distance_km` / `bearing_deg` are the
QSO's spherical values, so a `$length` differs from them by up to about 0.6 %.

Antipodal points (distance close to 20 000 km) have no unique great circle.
Skip the path and warn: `geo.is_antipodal` (within 20 km of the antipode) gives no
path, with a warning; the QSO keeps its point. Points closer than 1 m get no path
either.

## Azimuthal equidistant map

Centered on my QTH, straight lines from center are great circles:

```
+proj=aeqd +lat_0=<lat> +lon_0=<lon> +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs
```

Create with `QgsCoordinateReferenceSystem.fromProj(proj_string)` and set as
project CRS. Add distance rings (circles in this CRS) every 2500 km and
azimuth lines every 30° as an optional helper layer.

`geo.aeqd_proj(lat, lon)` writes 6 decimals (`+lat_0=44.812500`). `gui/azimuthal.py`
(`AzimuthalMap`), for the centre of my locator:
- `enable()` keeps the project CRS, canvas CRS and view, sets the aeqd CRS and shows
  ±21 000 km;
- helper memory layer "Azimuthal map grid (KN04ft)" (translated), last in the "HamQ"
  group: 8 rings every 2500 km up to 20 000 km (circles of 360 vertices; map units
  are km, so plain polar coordinates) and 12 lines every 30° from the centre, labelled
  "2500 km" / "30°", dashed grey;
- `disable()` restores CRS and view and removes the helper (and the group it created);
  a cleared or another project turns the map off, a CRS the user picks stays, a new
  locator re-centres;
- the state (locator, CRS and view to restore) is saved with the project in the
  helper's custom property `hamq/azimuthal`, and `skipMemoryLayersCheck` stops the
  QGIS question about temporary layers: a project saved with the map on opens with it
  on again.
