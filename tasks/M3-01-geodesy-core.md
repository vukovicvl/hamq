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
- [x] Review fix round: short paths between distinct points are tested. Beograd-Pancevo
      (15.5 km) gives `[[BEOGRAD, PANCEVO]]`. Paths of 1 mm to 49 km from Beograd are
      tested with steps of 1 and 100 km, plus short paths across the antimeridian and
      at the poles. The same-point threshold is bracketed: 1e-13 rad gives one point,
      1e-11 rad gives a line
- [x] Review fix round: a subnormal `step_km` (1e-310, 5e-324) raises `ValueError`
      instead of `OverflowError`, and the message no longer embeds a 300-digit number
- [x] Review fix round: the module docstring states the measured accuracy against
      WGS84 (it overstated it). The `distance_km`, `bearing_deg` and `great_circle`
      docstrings say exactly what counts as "the same point"

## Acceptance criteria
- [x] `pytest tests/core/test_geo.py -q` passes (283 tests) on Python 3.14 (host)
      and 3.9 (Docker). All of `tests/core` passes (2512 passed, 4 xfailed)
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
    - Points less than 1e-12 rad (about 6 micrometres) apart are the same point and
      give `[[(lat1, lon1)]]`. Any pair farther apart is a line of at least two
      points, however short. Antipodal points (the `is_antipodal` default) give
      `[]`, and callers must warn.
    - `ValueError` for non-finite coordinates, a latitude outside [-90, 90],
      `step_km <= 0` or NaN, or more than 1 000 000 segments. The segment count is
      checked as a float before `math.ceil`, so a subnormal step is refused too.
  - `aeqd_proj` returns
    `+proj=aeqd +lat_0=<lat> +lon_0=<lon> +x_0=0 +y_0=0 +datum=WGS84 +units=km +no_defs`
    with 6 decimals.
  - Speed: 10 000 paths from Beograd with a 100 km step (about 1 M points) take
    about 1.0 s on Python 3.14 and 1.9 s on 3.9, about 1-2 us per point.
- Review fix round (reviewer verdict "approve"; one "should" finding, three nits, all
  reproduced first):
  - Short distinct paths were untested: raising the same-point threshold to 1e-2 rad
    (64 km) still passed all 207 tests, and Beograd-Pancevo then became a single
    point. New tests: `test_great_circle_local_contact_is_a_line`,
    `test_great_circle_same_point_tolerance` (1e-13 rad gives one point, 1e-11 rad a
    line, at 4 places x 3 directions), `test_great_circle_short_paths` (1 mm to
    49.3 km, 3 bearings, steps 1 and 100 km, exact point count) and
    `test_great_circle_short_paths_in_awkward_places` (across the antimeridian, over
    and next to the poles). They fail on every threshold mutant from 1e-11 to 1e-2
    rad. No code change was needed: the code was right, the tests were missing.
  - `step_km=1e-310` / `5e-324` raised `OverflowError` from `math.ceil(inf)`, and
    `1e-300` put a 300-digit count into the message. Fixed as described above. The
    message is now e.g. "step_km=1e-300 is too small: the 15679.7 km path would need
    more than 1000000 segments". Regression tests:
    `test_great_circle_rejects_absurdly_small_step` (failed before the fix) and
    `test_great_circle_segment_limit_is_one_million`.
  - The module docstring overstated the accuracy against WGS84. Re-measured with
    pyproj 3.7.2 (Karney) on the host (scratch scripts; about 200 000 random pairs
    plus targeted samples near each band edge):
    - distance: -0.45 % to +0.56 %; the extremes are short north-south paths near the
      poles and the equator.
    - bearing: max 0.26 degrees up to 15 000 km, 0.78 up to 18 000 km, 1.71 up to
      19 000 km and 3.5 up to 19 500 km; up to 69 degrees for points 20-500 km from
      the antipode.
    - antimeridian crossing latitude: max 0.08 degrees up to 10 000 km, 0.31 up to
      15 000 km, 0.90 up to 18 000 km, 2.1 up to 19 000 km and 4.4 up to 19 500 km.

    The docstring now gives these bands. `test_distance_error_extremes_against_wgs84`
    pins the distance range with WGS84 reference lengths (for example 1 degree north
    from the equator: 110.574389 km, +0.56 %).
  - `bearing_deg` promised 0.0 "for the same point". That holds only for numerically
    identical points: `bearing_deg(10, 180, 10, -180)` is 90.0. The docstring now
    says so; behaviour is unchanged. The `distance_km` docstring was clarified the
    same way (about 1e-12 km for one point written two ways), and
    `test_bearing_same_point_is_zero` now also covers identical points at the poles
    and on +-180.
- `tests/core/test_geo.py` (new, 283 tests, about 1 s).
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

**Commands run** (latest results, after the review fix round)
- `python3 -m pytest -p no:cacheprovider tests/core/test_geo.py -q`: 283 passed. Before
  the fix of `great_circle`, the same run gave 3 failed (the new tiny-step tests),
  280 passed.
- `python3 -m pytest -p no:cacheprovider tests/core -q`: 2512 passed, 4 xfailed (other
  tasks' known xfails in `test_i18n.py`).
- `docker run --rm -v "$PWD":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_geo.py tests/core/test_architecture.py -q"`:
  326 passed on Python 3.9.25 (283 + 43).
- `ruff check hamq/core/geo.py tests/core/test_geo.py`: all checks passed.
  `ruff format --check`: 2 files already formatted (ruff 0.16.9).
- Branch coverage (coverage.py, scratch venv, data file kept out of the repo): 100 %
  of the lines (183) and branches (56) in `geo.py`.
- Mutation check (27 hand-made mutants in a scratch copy): all 27 killed. The first
  round had 21, for example no start flip, a linear crossing latitude, no pole
  override, the plain longitude difference, no second rounding guard, no clamp and
  `sin(pi)` not zeroed. The fix round added 6: same-point threshold 1e-10 / 1e-4 /
  1e-2 rad, `math.ceil` before the limit check, the limit doubled, and the full
  segment count in the message.
- Fix-round fuzzing (scratch copy of the fuzzer, reusing `check_path`):
  - 1 028 010 cases with segments capped at 2000. They include pairs 1e-12 to 1
    degree apart next to +-180, 0 and the poles. Results: 890 395 drawn paths
    (159 696 split), 41 754 antipodal and 95 861 same-point results.
  - 10 660 cases with steps of 1e-6 to 1 km. 1356 of them were correctly refused
    (more than 1 000 000 segments).
  - No invariant failures. A single-point result only ever came from a pair under
    1e-8 km apart.
- First-round fuzzing (scratch script reusing `check_path`, about 185 000
  adversarial cases on the final algorithm): longitudes within a few ulps of +-180 and 0, latitudes at
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
  Its "Error vs WGS84 is under 0.5 %" is slightly optimistic: the sphere reaches
  +0.56 % on short north-south paths at the equator (-0.45 % near the poles). The
  skill's three test vectors are within 0.5 %.
- For the orchestrator (contract/plan mismatch, not changed here): PLAN.md lists
  `qso.distance_km` as "geodetsko, WGS84", but the contract makes `core/geo`
  spherical. If the import fills `distance_km` from `geo.distance_km`, the stored
  values are spherical (Beograd-Washington 7589.36 km against 7608.71 km on WGS84).
  Either the QGIS side computes it with `QgsDistanceArea` (WGS84), or PLAN.md
  should say spherical. The M3 acceptance (+-0.5 % for Beograd-Sydney) holds either
  way.
- For the QGIS side (`gpkg.geodesic_path`, `azimuthal.py`):
  - `great_circle` returns `[]` exactly when `is_antipodal(...)` with the default
    tolerance is true, so check `is_antipodal` first to log the warning.
  - Only the same point (less than 1e-12 rad, about 6 micrometres, apart) gives a
    single-point part, which is not a valid line: skip it. Any two distinct
    stations, even 1 mm apart, give a line of at least two points.
  - Parts are `(lat, lon)`: swap to x/y for `QgsPointXY`.
  - Spherical paths differ from `QgsDistanceArea.geodesicLine` (WGS84) by -0.45 % to
    +0.56 % in length. The crossing latitude at +-180 differs by up to about 0.1
    degrees on paths up to 10 000 km, 0.3 up to 15 000 km, 1 up to 18 000 km and 2
    up to 19 000 km (median 0.03 over random Pacific crossings). Bearings differ by
    up to about 0.3 degrees up to 15 000 km, more on longer paths and tens of
    degrees near the antipode. See the module docstring.
