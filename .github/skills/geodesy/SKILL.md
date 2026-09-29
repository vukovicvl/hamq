---
name: geodesy
description: Distance, bearing and great-circle lines for QSO paths - pure-Python formulas for core, QgsDistanceArea for geodesic lines, antimeridian handling, azimuthal equidistant projection. Read for distance/bearing fields and the qso_path layer.
---

# Geodesy for QSO paths

## In core (no QGIS)

Use a spherical model in `core/geo.py` for speed and testability. Error vs WGS84
is under 0.5 %, fine for ham use.

```python
from math import radians, degrees, sin, cos, atan2, sqrt
R = 6371.0088  # mean Earth radius, km

def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lon2 - lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    x = sin(dl) * cos(p2)
    y = cos(p1)*sin(p2) - sin(p1)*cos(p2)*cos(dl)
    return (degrees(atan2(x, y)) + 360) % 360
```

Long path bearing = `(short + 180) % 360`, long path distance = `40030 - short`.

## Test vectors (WGS84 ellipsoid reference)

From Beograd (44.8125, 20.4612):

| To | Distance km | Bearing ° |
|---|---|---|
| München (48.14666, 11.60833) | 773.6 | 301.8 |
| Washington (38.8977, -77.0366) | 7608.7 | 303.9 |
| Sydney (-33.8688, 151.2093) | 15676.1 | 91.0 |

Spherical results must be within 0.5 % distance and 0.5° bearing of these.

## Geodesic lines in QGIS

```python
from qgis.core import QgsDistanceArea, QgsCoordinateReferenceSystem, QgsPointXY, QgsGeometry

da = QgsDistanceArea()
da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
da.setEllipsoid("WGS84")
parts = da.geodesicLine(QgsPointXY(lon1, lat1), QgsPointXY(lon2, lat2),
                        100000, True)   # 100 km step, break at antimeridian
geom = QgsGeometry.fromMultiPolylineXY(parts)
```

`breakLine=True` returns separate parts when the path crosses ±180°. Without
it, a Pacific path is drawn across the whole map. Always use MultiLineString.

Antipodal points (distance close to 20 000 km) have no unique great circle.
Skip the path and warn.

## Azimuthal equidistant map

Centered on my QTH, straight lines from center are great circles:

```
+proj=aeqd +lat_0=<lat> +lon_0=<lon> +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs
```

Create with `QgsCoordinateReferenceSystem.fromProj(proj_string)` and set as
project CRS. Add distance rings (buffers in this CRS) every 2500 km and
azimuth lines every 30° as an optional helper layer.
