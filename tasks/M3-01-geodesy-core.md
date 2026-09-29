# M3-01: Geodesy core (`hamq/core/geo.py`)

**Milestone:** M3
**Status:** done
**Skills:** geodesy

## Goal
Pure Python spherical geodesy for QSO paths: distance, initial bearing, long path,
antipodal check, destination point, great-circle polylines split at the
antimeridian, and the azimuthal equidistant PROJ string. It implements the
"core/geo.py" section of `docs/ARCHITECTURE.md` with the formulas from the
geodesy skill.

## Scope
Files that may be created or changed:
- `hamq/core/geo.py`
- `tests/core/test_geo.py`
- `tasks/M3-01-geodesy-core.md`

## Out of scope
- The `qso_path` layer, `gpkg.geodesic_path`, styles and the azimuthal map
  button (QGIS side, separate tasks that call this module).
- `CHANGELOG.md` (not edited in this parallel run, see Notes).
- Translation catalog: the module has no user-visible strings (the `ValueError`
  messages are for programmers, as in `maidenhead.py`).

## Checklist
- [x] Public names and signatures exactly as in the contract: `EARTH_RADIUS_KM`,
      `distance_km`, `bearing_deg`, `long_path`, `is_antipodal`, `destination`,
      `great_circle`, `aeqd_proj`. No extra public names, only `_` helpers
- [x] Tests written first (the suite failed on import before `geo.py` existed)
- [x] `distance_km` / `bearing_deg`: skill formulas. The haversine term is clamped
      to [0, 1]: the bare skill code raises `math domain error` for about 3.8 % of
      exactly antipodal pairs (regression test)
- [x] Skill vectors from Beograd (München, Washington, Sydney) within 0.5 % distance
      and 0.5 degrees bearing; PLAN M3 acceptance Beograd-Sydney within +-0.5 % of
      15676.1 km
- [x] Symmetry (bit-identical), zero distance, known arcs, bearing to the North Pole
      = 0 and South Pole = 180 from many origins, due east on the equator = 90,
      range [0, 360)
- [x] `long_path`: `(2 * pi * R - d, (b + 180) % 360)`, in argument order,
      documented; the long path of Beograd-Sydney really ends in Sydney
      (via `destination`)
- [x] `is_antipodal`: true for (0,0)-(0,+-180), Beograd vs its antipode and the
      poles; false for points 20.1 / 25 / 50 / 500 km from the antipode; custom
      tolerance; symmetric. It measures the angle to the antipode directly
      (accurate near 0, unlike `pi * R - distance`)
- [x] `destination`: spherical direct problem, round trip with
      `bearing_deg` / `distance_km` to 1 mm, 3000 random inverse checks, poles
      (same convention as `bearing_deg`), negative distance, full circle,
      longitude wrap, due north/south keep the longitude exactly
- [x] `great_circle`: spacing <= `step_km`, first/last points exactly the inputs,
      one crossing split exactly at +-180 with the same latitude on both sides
      (checked against an independent closed-form crossing latitude), consecutive
      longitudes never jump more than 180 inside a part, identical points ->
      `[[point]]`, antipodal -> `[]`
- [x] Tokyo -> Los Angeles gives 2 parts meeting at +-180 (47.58 deg; QGIS WGS84
      gives 47.633); Beograd -> Sydney gives 1 part
- [x] Polar paths (over either pole, starting or ending at a pole, a sample exactly
      on the pole), endpoints exactly on +-180, paths along the antimeridian,
      a sample exactly on the antimeridian, out-of-range longitudes, bad
      step / coordinates -> `ValueError`
- [x] `aeqd_proj`: skill string, 6 decimals, never `-0.000000`, longitude wrapped,
      bad latitude -> `ValueError`
- [x] Python 3.9 compatible, no `qgis`/`PyQt` imports, ruff clean

## Acceptance criteria
- [x] `pytest tests/core/test_geo.py -q` passes (207 tests) on Python 3.14 (host)
      and 3.9 (Docker). All of `tests/core` passes except another task's
      `test_adif.py`, whose module is not written yet
- [x] `ruff check` and `ruff format --check` pass on the changed files
- [x] Beograd-Sydney distance within +-0.5 % of 15676.1 km (sphere: 15679.67 km, +0.02 %)
- [x] A path across the antimeridian is split into parts and is not stretched over
      the whole map (Tokyo-LA parts are 40.35 and 61.76 degrees wide)

## Result

**What changed**
- `hamq/core/geo.py` (new).
  - `distance_km` and `bearing_deg` use the skill code. The only addition is the
    clamp of the haversine term.
  - `long_path` returns `(distance, bearing)` for the other way round the same
    great circle: `2 * pi * R - d` (about 40030.2 km minus the short path) and
    `(b + 180) % 360`.
  - `is_antipodal` compares the angle between point 2 and the antipode of point 1
    (vector cross/dot products and `atan2`) with the tolerance; the default is 20 km.
  - `destination` solves the direct problem as `cos(d) * P + sin(d) * t` in a frame
    turned onto the start meridian. It stays defined at the poles, uses the same
    pole convention as `bearing_deg` and keeps the longitude exactly on due
    north/south courses.
  - `great_circle` interpolates spherically (slerp), with
    `n = ceil(distance / step_km)` equal segments. It works in a frame turned so
    that point 1 lies on longitude 0. The `y` of every sample then has the sign of
    the longitude change, so `atan2(y, x)` is a continuous offset from `lon1`:
    rounding noise never flips a sample across the antimeridian. The path crosses
    at most once; the crossing latitude comes from cutting the segment's chord with
    the meridian plane, which is exact on a great circle and well defined at a pole.
  - Edge rules in `great_circle`, all documented in the docstring:
    - An endpoint exactly on +-180 takes the sign of the side the path runs on;
      otherwise the first/last points are the inputs exactly.
    - A sample on a pole keeps the meridian it came from.
    - The longitude difference is computed so that `180 - (-179.99999999999997)`
      does not round to 360 and lose a real 2.8e-14 degree step (found by fuzzing).
      Two guards handle differences that round past +-180 twice.
    - Rounding noise (180.00000000000003 when leaving a pole along the antimeridian,
      found by fuzzing) is clipped to +-180.
    - Identical points (< 1e-12 rad) give `[[(lat1, lon1)]]`; antipodal points
      (the `is_antipodal` default) give `[]`, and callers must warn.
    - `ValueError` for non-finite coordinates, a latitude outside [-90, 90],
      `step_km <= 0` or NaN, or more than 1 000 000 segments.
  - `aeqd_proj` returns
    `+proj=aeqd +lat_0=<lat> +lon_0=<lon> +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs`
    with 6 decimals.
  - Speed: 10 000 paths from Beograd with a 100 km step (about 1 M points) take
    0.93 s, about 1 us per point.
- `tests/core/test_geo.py` (new, 207 tests, about 0.9 s).
  - `check_path` asserts every `great_circle` guarantee:
    - types and ranges;
    - exact endpoints;
    - spacing;
    - no longitude jump over 180 inside a part;
    - parts meeting at +-180 with equal latitude;
    - the segments adding up to the great-circle distance (equality in the
      triangle inequality, so every point lies on the arc and in order).
  - The crossing latitude is compared with an independent closed-form formula
    (Aviation Formulary).

**Commands run**
- `python3 -m pytest -p no:cacheprovider tests/core/test_geo.py -q`: 207 passed.
- `python3 -m pytest -p no:cacheprovider tests/core -q --continue-on-collection-errors`:
  1559 passed, 1 error (collection of another task's `tests/core/test_adif.py`,
  because `hamq/core/adif.py` does not exist yet).
- `docker run --rm -v "$PWD":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_geo.py -q"`:
  207 passed on Python 3.9.25; `tests/core/test_architecture.py` there: 42 passed.
- `ruff check hamq/core/geo.py tests/core/test_geo.py`: all checks passed.
  `ruff format --check`: already formatted.
- Branch coverage (coverage.py, scratch venv): 100 % of the lines and branches in
  `geo.py`.
- Mutation check (21 hand-made mutants in a scratch copy): all 21 killed. Examples:
  no start flip, a linear crossing latitude, no pole override, the plain longitude
  difference, no second rounding guard, no clamp, `sin(pi)` not zeroed.
- Fuzzing (scratch script reusing `check_path`, about 185 000 adversarial cases on
  the final algorithm): longitudes within a few ulps of +-180 and 0, latitudes at
  and next to the poles, opposite meridians +- ulps, steps from 0.3 km to infinity.
  - An earlier ad-hoc fuzz run found the lost-step bug, now fixed.
  - Later runs reached the clamp, and a targeted search found inputs for both
    rounding guards.
  - All of these inputs are regression tests. No invariant failures remain.
- Manual QGIS cross-check (scratch script, host QGIS 4.2.1 and Docker
  `camptocamp/qgis-server:3.34`, same results):
  - `QgsCoordinateReferenceSystem.fromProj(aeqd_proj(Beograd))` is valid, units km,
    Beograd -> (0, 0) and Sydney at 15676.1 km from the centre (the WGS84 value).
  - `great_circle` parts build valid MultiLineStrings with `fromMultiPolylineXY`.
    Tokyo-LA: 2 parts, 40.35 and 61.76 degrees wide.
  - The skill's WGS84 reference values match `QgsDistanceArea`: 773.61 / 7608.71 /
    15676.14 km and 301.76 / 303.85 / 90.99 degrees.

**Manual checks still needed**
- None for this core module. The M3 acceptance "the line across the antimeridian
  is not stretched over the whole map" still has to be checked visually once the
  `qso_path` layer exists (QGIS side task).

## Notes
- `CHANGELOG.md` was not edited (parallel-run instruction). Suggested entry under
  `## Unreleased`: "Added `hamq.core.geo`: spherical distance, bearing, long path,
  antipodal check, destination point, great-circle paths split at the
  antimeridian, azimuthal equidistant PROJ string."
- Geodesy skill (`.github/skills/geodesy/SKILL.md`, outside scope): its
  `distance_km` snippet raises `math domain error` for many exactly antipodal pairs
  (for example (69.51232454868148, 86.5812282599507) and its antipode). It should
  clamp `a` to [0, 1] as `geo.py` does. It also says "long path distance =
  40030 - short"; `geo.py` uses `2 * pi * R` = 40030.23 km.
- For the QGIS side (`gpkg.geodesic_path`, `azimuthal.py`):
  - `great_circle` returns `[]` exactly when `is_antipodal(...)` with the default
    tolerance is true, so check `is_antipodal` first to log the warning.
  - Identical points give a single-point part, which is not a valid line: skip it.
  - Parts are `(lat, lon)`: swap to x/y for `QgsPointXY`.
  - Spherical paths differ from `QgsDistanceArea.geodesicLine` (WGS84) by at most a
    few tenths of a percent in length and about 0.05 degrees in crossing latitude.
