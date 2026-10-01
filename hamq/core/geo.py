"""Spherical geodesy for QSO paths: distance, bearing, long path, great-circle lines.

The Earth is a sphere of radius :data:`EARTH_RADIUS_KM` (IUGG mean radius), which keeps
this module fast and testable without QGIS (see the geodesy skill). Against WGS84
geodesics (Karney's algorithm, measured on random paths) the sphere is off by:

- distance: -0.45 % to +0.56 %. The extremes are short north-south paths near the
  poles and near the equator; Beograd-Sydney is +0.02 %.
- initial bearing: up to about 0.3 degrees on paths up to 15,000 km, 1 degree up to
  18,000 km, 2 degrees up to 19,000 km and 4 degrees up to 19,500 km. Within 500 km
  of the antipode the two models can point tens of degrees apart.
- latitude where a path crosses the antimeridian: up to about 0.1 degrees on paths up
  to 10,000 km, 0.3 degrees up to 15,000 km, 1 degree up to 18,000 km and 2 degrees up
  to 19,000 km, more on longer paths.

Conventions: angles in decimal degrees, points in ``(lat, lon)`` order with
latitudes in [-90, 90], distances in km, bearings clockwise from true north in
[0, 360). Longitudes returned by this module are in [-180, 180].

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088
"""Mean Earth radius (IUGG R1) in km."""

_CIRCUMFERENCE_KM = 2.0 * math.pi * EARTH_RADIUS_KM  # about 40030.2 km
_ANTIPODAL_TOLERANCE_KM = 20.0
# Central angle (radians) below which two points count as the same (about 6 micrometres).
_SAME_POINT_RAD = 1e-12
# Distance from the Earth's axis (unit sphere) below which a point sits on a pole and
# its longitude is meaningless.
_POLE_EPS = 1e-12
# great_circle() refuses to build paths with more segments than this (a step_km that is
# far too small is a programming error, not something to grind through).
_MAX_SEGMENTS = 1_000_000


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points (haversine on a sphere).

    Symmetric, ``0.0`` for numerically identical points (about 1e-12 km for one point
    written two ways, such as longitudes +180 and -180) and ``pi * EARTH_RADIUS_KM``
    (about 20015.1 km) for antipodal points. NaN input gives NaN.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    # Rounding can push ``a`` just past 1 for (nearly) antipodal points, where
    # sqrt(1 - a) would raise "math domain error".
    if a > 1.0:
        a = 1.0
    elif a < 0.0:
        a = 0.0
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (azimuth) from point 1 to point 2 along the short path.

    Degrees clockwise from true north, in [0, 360). For the same point the bearing
    is undefined: numerically identical points give ``0.0``, but one point written
    two ways (longitudes +180 and -180 or 360 apart, a pole with two different
    longitudes) gives an arbitrary value. For antipodal points every direction is a
    shortest path and the value is arbitrary too. At a pole, bearings are measured
    as if standing on meridian ``lon1`` right next to the pole (so from the North
    Pole 0 points toward ``lon1 + 180`` and 180 toward ``lon1``); :func:`destination`
    uses the same convention.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def long_path(distance_km: float, bearing_deg: float) -> tuple[float, float]:
    """Long path from a short path; returns ``(distance_km, bearing_deg)``.

    The long path runs along the same great circle the other way round. Its
    distance is the circumference minus the short-path distance
    (``2 * pi * EARTH_RADIUS_KM - distance_km``, about 40030.2 km minus the short
    path) and its initial bearing is the reverse of the short-path bearing,
    ``(bearing_deg + 180) % 360``. The result is in the same order as the arguments.
    """
    return _CIRCUMFERENCE_KM - distance_km, _norm360(bearing_deg + 180.0)


def is_antipodal(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    tolerance_km: float = _ANTIPODAL_TOLERANCE_KM,
) -> bool:
    """True when point 2 lies within ``tolerance_km`` of the antipode of point 1.

    Antipodal points have no unique great circle (every great circle through one
    passes through the other), so no path can be drawn and the bearing is
    meaningless. Close to the antipode the path direction also swings wildly with
    small position errors (a locator is only a few km precise), hence the default
    tolerance of 20 km. Symmetric in the two points.
    """
    x1, y1, z1 = _unit_vector(lat1, lon1)
    x2, y2, z2 = _unit_vector(lat2, lon2)
    cross = math.sqrt(
        (y1 * z2 - z1 * y2) ** 2 + (z1 * x2 - x1 * z2) ** 2 + (x1 * y2 - y1 * x2) ** 2
    )
    dot = x1 * x2 + y1 * y2 + z1 * z2
    # angle between point 2 and the antipode of point 1 (-v1); atan2 keeps it accurate
    # when it is small, unlike pi - distance
    return math.atan2(cross, -dot) * EARTH_RADIUS_KM <= tolerance_km


def destination(
    lat: float, lon: float, bearing_deg: float, distance_km: float
) -> tuple[float, float]:
    """Point reached from ``(lat, lon)`` along a great circle (spherical direct problem).

    Starts with the initial ``bearing_deg`` and covers ``distance_km``; returns
    ``(lat, lon)`` with the longitude in [-180, 180]. A negative distance goes the
    opposite way and distances beyond half the circumference carry on round the
    globe. It inverts :func:`distance_km` / :func:`bearing_deg`:
    ``destination(a, bearing_deg(a, b), distance_km(a, b))`` is ``b``.
    """
    if distance_km == 0:
        return float(lat), _wrap_lon(float(lon))
    phi = math.radians(lat)
    theta = math.radians(bearing_deg)
    delta = distance_km / EARTH_RADIUS_KM
    sin_phi, cos_phi = math.sin(phi), math.cos(phi)
    sin_d, cos_d = math.sin(delta), math.cos(delta)
    cos_t = math.cos(theta)
    # due north / south keeps the longitude exactly (sin(pi) is 1.2e-16, not 0)
    sin_t = 0.0 if bearing_deg % 180.0 == 0.0 else math.sin(theta)
    # In a frame turned about the axis so the start lies on longitude 0, the start is
    # P = (cos phi, 0, sin phi) and the unit tangent along the bearing is
    # t = cos(theta) * north + sin(theta) * east = (-sin phi cos theta, sin theta,
    # cos phi cos theta). The destination is cos(delta) * P + sin(delta) * t. Unlike the
    # asin/atan2 textbook form this stays well defined at the poles.
    x = cos_d * cos_phi - sin_d * sin_phi * cos_t
    y = sin_d * sin_t
    z = cos_d * sin_phi + sin_d * cos_phi * cos_t
    lat2 = math.degrees(math.atan2(z, math.hypot(x, y)))
    lon2 = lon + math.degrees(math.atan2(y, x))
    return lat2, _wrap_lon(lon2)


def great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, step_km: float = 100.0
) -> list[list[tuple[float, float]]]:
    """Points along the short great-circle path from point 1 to point 2.

    Returns the path as a list of parts, each a list of ``(lat, lon)`` points, ready
    for a MultiLineString:

    - Points are spaced evenly along the great circle (spherical interpolation), at
      most ``step_km`` apart.
    - The first point is ``(lat1, lon1)`` and the last one ``(lat2, lon2)``, exactly
      (longitudes outside [-180, 180] are wrapped first). One exception: an endpoint
      lying exactly on the antimeridian keeps its latitude but takes the longitude
      sign (+180 or -180) of the side the path runs on, so that no part jumps across
      the whole map.
    - A path that crosses the antimeridian is split there into two parts: the first
      ends at longitude +180 (or -180) and the second starts at -180 (or +180), both
      at the same interpolated latitude. A short path crosses at most once. Within a
      part consecutive longitudes never differ by more than 180 degrees (exactly 180
      only where the path passes over a pole).
    - Every part of a path has at least two points, however short the path (two
      stations 1 mm apart give a 2-point line). The one exception is the same point:
      points less than 1e-12 rad (about 6 micrometres) apart, including one point
      written two ways (longitudes +180 and -180, a pole with two longitudes), give
      one part with the single point ``[[(lat1, lon1)]]``. That is not a line and
      callers must not draw it as one.
    - Antipodal points (:func:`is_antipodal` with its default 20 km tolerance) have
      no unique great circle and give ``[]``. Callers must treat an empty list as
      "no path" and warn the user.

    Raises ``ValueError`` for non-finite coordinates, a latitude outside [-90, 90],
    a ``step_km`` that is not positive, or a step so small that the path would need
    more than 1,000,000 segments.
    """
    lat1, lon1 = _checked_point(lat1, lon1)
    lat2, lon2 = _checked_point(lat2, lon2)
    step_km = float(step_km)
    if not step_km > 0.0:
        raise ValueError(f"step_km must be greater than 0, got {step_km!r}")

    # Longitude change along the path, in (-180, 180]: > 0 heads east, < 0 west. Over a
    # short great-circle arc the longitude moves monotonically by exactly this much
    # (jumping by 180 where the arc passes over a pole).
    dlon = _delta_lon(lon1, lon2)

    # Unit vectors in a frame turned about the axis so point 1 lies on longitude 0.
    # y1 is exactly 0, so every interpolated y is 0 or has the sign of dlon, and
    # atan2(y, x) is the sample's longitude offset from lon1: continuous along the
    # path, with no wrap-around and no rounding noise flipping sides.
    phi1, phi2, dl = math.radians(lat1), math.radians(lat2), math.radians(dlon)
    x1, z1 = math.cos(phi1), math.sin(phi1)
    cos_phi2 = math.cos(phi2)
    # sin(pi) is 1.2e-16, not 0: keep paths over a pole (dlon 180) exactly on their meridians
    sin_dl = 0.0 if dlon == 180.0 else math.sin(dl)
    x2, y2, z2 = cos_phi2 * math.cos(dl), cos_phi2 * sin_dl, math.sin(phi2)
    cross = math.sqrt((z1 * y2) ** 2 + (z1 * x2 - x1 * z2) ** 2 + (x1 * y2) ** 2)
    delta = math.atan2(cross, x1 * x2 + z1 * z2)  # central angle, radians

    if delta < _SAME_POINT_RAD:
        return [[(lat1, lon1)]]
    if is_antipodal(lat1, lon1, lat2, lon2):
        return []

    length_km = delta * EARTH_RADIUS_KM
    # Checked as a float before math.ceil(): a subnormal step_km makes it inf.
    segments = length_km / step_km
    if not segments <= _MAX_SEGMENTS:
        raise ValueError(
            f"step_km={step_km!r} is too small: the {length_km:.1f} km path would need "
            f"more than {_MAX_SEGMENTS} segments"
        )
    n = max(1, math.ceil(segments))

    # Samples: latitude and longitude offset from lon1 (degrees).
    lats = [lat1]
    offsets = [0.0]
    sin_delta = math.sin(delta)
    for i in range(1, n):
        f = i / n
        wa = math.sin((1.0 - f) * delta) / sin_delta
        wb = math.sin(f * delta) / sin_delta
        x = wa * x1 + wb * x2
        y = wb * y2
        z = wa * z1 + wb * z2
        r = math.hypot(x, y)
        lats.append(math.degrees(math.atan2(z, r)))
        # a sample on a pole has no longitude of its own: keep the one it came from
        offsets.append(math.degrees(math.atan2(y, x)) if r > _POLE_EPS else offsets[-1])
    lats.append(lat2)
    offsets.append(dlon)

    # A start exactly on the antimeridian takes the sign of the side the path runs on.
    start = lon1
    if dlon > 0.0 and start == 180.0:
        start = -180.0
    elif dlon < 0.0 and start == -180.0:
        start = 180.0

    # The path crosses the antimeridian when it runs past +180 heading east (or past
    # -180 heading west). An end exactly on the antimeridian is reached, not crossed.
    if dlon > 0.0:
        crosses = lon2 < start and lon2 != -180.0
        boundary = 180.0
    else:
        crosses = dlon < 0.0 and lon2 > start and lon2 != 180.0
        boundary = -180.0

    # Unwrapped longitudes: continuous along the path, start + offset.
    lons = [start + offset for offset in offsets]

    if not crosses:
        if lon2 in (180.0, -180.0):  # end on the antimeridian: side the path arrives from
            end_lon = 180.0 if dlon > 0.0 else -180.0 if dlon < 0.0 else start
        else:
            end_lon = lon2
        part = [(lat, _clamp_lon(lon)) for lat, lon in zip(lats, lons)]
        part[0] = (lat1, start)
        part[-1] = (lat2, end_lon)
        return [part]

    shift = 360.0 if dlon > 0.0 else -360.0
    lons[-1] = lon2 + shift  # the end, unwrapped past the antimeridian
    # first segment whose far end lies beyond the antimeridian
    if dlon > 0.0:
        k = next(i for i in range(n) if lons[i + 1] > 180.0)
    else:
        k = next(i for i in range(n) if lons[i + 1] < -180.0)

    head = [(lats[i], _clamp_lon(lons[i])) for i in range(k + 1)]
    tail = [(lats[i], _clamp_lon(lons[i] - shift)) for i in range(k + 1, n + 1)]
    head[0] = (lat1, start)
    tail[-1] = (lat2, lon2)
    if lons[k] == boundary:
        # the sample itself lies on the antimeridian (never the start, see above)
        crossing_lat = lats[k]
    else:
        crossing_lat = _crossing_lat(
            lats[k], offsets[k], lats[k + 1], offsets[k + 1], boundary - start
        )
        head.append((crossing_lat, boundary))
    tail.insert(0, (crossing_lat, -boundary))
    return [head, tail]


def aeqd_proj(lat: float, lon: float) -> str:
    """PROJ string of an azimuthal equidistant projection centred on ``(lat, lon)``.

    Straight lines through the centre are great circles and distances from the
    centre are true, the classic ham radio "great circle map". Units are km and the
    coordinates are written with 6 decimals, e.g. for Beograd
    ``+proj=aeqd +lat_0=44.812500 +lon_0=20.461200 +x_0=0 +y_0=0 +datum=WGS84
    +units=km +no_defs``. Raises ``ValueError`` for non-finite coordinates or a
    latitude outside [-90, 90]; the longitude is wrapped into [-180, 180].
    """
    lat, lon = _checked_point(lat, lon)
    return (
        f"+proj=aeqd +lat_0={_format_deg(lat)} +lon_0={_format_deg(lon)} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unit_vector(lat: float, lon: float) -> tuple[float, float, float]:
    """Earth-centred unit vector of a point (x toward 0/0, y toward 0/90E, z to the North Pole)."""
    phi, lam = math.radians(lat), math.radians(lon)
    cos_phi = math.cos(phi)
    return cos_phi * math.cos(lam), cos_phi * math.sin(lam), math.sin(phi)


def _crossing_lat(lat_a: float, lon_a: float, lat_b: float, lon_b: float, lon_c: float) -> float:
    """Latitude where the great-circle segment a-b meets meridian ``lon_c``.

    All longitudes relative to the same frame. The segment's chord is cut with the
    meridian plane and the cut point is projected back onto the sphere: exact for
    great circles and well defined at the poles (a segment over a pole meets every
    meridian there).
    """
    ax, ay, az = _unit_vector(lat_a, lon_a)
    bx, by, bz = _unit_vector(lat_b, lon_b)
    c = math.radians(lon_c)
    nx, ny = -math.sin(c), math.cos(c)  # normal of the meridian plane
    sa = ax * nx + ay * ny
    sb = bx * nx + by * ny
    t = sa / (sa - sb) if sa != sb else 0.0
    t = min(1.0, max(0.0, t))
    x = ax + t * (bx - ax)
    y = ay + t * (by - ay)
    z = az + t * (bz - az)
    return math.degrees(math.atan2(z, math.hypot(x, y)))


def _checked_point(lat: float, lon: float) -> tuple[float, float]:
    """Validate a point for the explicit geometry functions; wraps the longitude."""
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError(f"coordinates must be finite numbers, got ({lat!r}, {lon!r})")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude must be within [-90, 90], got {lat!r}")
    return lat, _wrap_lon(lon)


def _wrap_lon(lon: float) -> float:
    """Longitude into [-180, 180]; values already inside (including +-180) are kept."""
    if -180.0 <= lon <= 180.0:
        return lon
    if not math.isfinite(lon):
        return math.nan
    lon = math.fmod(lon, 360.0)  # exact, in (-360, 360)
    if lon > 180.0:
        lon -= 360.0
    elif lon < -180.0:
        lon += 360.0
    return lon


def _delta_lon(lon1: float, lon2: float) -> float:
    """``lon2 - lon1`` wrapped into (-180, 180], for longitudes in [-180, 180].

    Across the antimeridian the plain difference loses its low bits
    (``-179.99999999999997 - 180.0`` rounds to -360, hiding a real step of 2.8e-14
    degrees east), so ``lon2`` is moved by 360 first, which is exact there.
    """
    delta = lon2 - lon1
    if delta > 180.0:
        delta = (lon2 - 360.0) - lon1
    elif delta <= -180.0:
        delta = (lon2 + 360.0) - lon1
    if delta > 180.0:  # rounding right at +-180
        delta -= 360.0
    elif delta <= -180.0:
        delta += 360.0
    return delta


def _clamp_lon(lon: float) -> float:
    """Clip rounding noise at the antimeridian (180.00000000000003 -> 180.0)."""
    return max(-180.0, min(180.0, lon))


def _norm360(angle: float) -> float:
    """Angle into [0, 360)."""
    if not math.isfinite(angle):
        return math.nan
    angle = math.fmod(angle, 360.0)  # exact, in (-360, 360)
    if angle < 0.0:
        angle += 360.0
        if angle >= 360.0:  # a tiny negative angle + 360 rounds to 360
            angle = 0.0
    return angle + 0.0  # -0.0 -> 0.0


def _format_deg(value: float) -> str:
    """Six decimals, never ``-0.000000``."""
    text = f"{value:.6f}"
    return "0.000000" if text == "-0.000000" else text
