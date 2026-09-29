# M1-01: Maidenhead core (`hamq/core/maidenhead.py`)

**Milestone:** M1
**Status:** done
**Skills:** maidenhead

## Goal
Pure Python Maidenhead module: locator <-> coordinates (2, 4, 6 and 8 characters),
cell bounds and centre, validation and normalization, plus the cell arithmetic
(snap, count, iterate) behind the *Generate Maidenhead grid* algorithm. It
implements the "core/maidenhead.py" section of `docs/ARCHITECTURE.md`.

## Scope
Files that may be created or changed:
- `hamq/core/maidenhead.py`
- `tests/core/test_maidenhead.py`
- `tasks/M1-01-maidenhead-core.md`

## Out of scope
- Processing algorithms *Locator to point* and *Generate Maidenhead grid*, and the
  toolbar locator search (separate tasks that call this module).
- `CHANGELOG.md` (not edited in this parallel run, see Notes).
- Translation catalog: the module has no user-visible strings (the `ValueError`
  messages are for programmers).

## Checklist
- [x] Public names and signatures exactly as in the contract: `LEVEL_FIELD`,
      `LEVEL_SQUARE`, `LEVEL_SUBSQUARE`, `LEVEL_EXTENDED`, `VALID_LENGTHS`,
      `MAX_GRID_CELLS`, `is_valid`, `normalize`, `to_locator`, `to_bounds`,
      `to_latlon`, `cell_size`, `snap_extent`, `count_cells`, `iter_cells`
- [x] Integer cell arithmetic at the extended level (lon 1/120 degree, lat 1/240
      degree, 43 200 units per axis). The epsilon (1e-9 units) only absorbs
      representation error
- [x] Tests written first. All skill vectors pass at 2, 4, 6 and 8 characters
- [x] `to_latlon` returns the cell centre, and `to_locator(centre)` round-trips.
      Centres that fall on finer cell edges also resolve (`KN` -> `KN55aa00`)
- [x] `to_bounds` is exact at every level (compared with `==` against `Fraction` values)
- [x] `is_valid`: skill cases, odd lengths, wrong letters (S in the field pair, Y at
      subsquare), whitespace, lowercase, non-ASCII look-alikes, non-strings
- [x] `normalize`: case (`KN04FT` -> `KN04ft`), whitespace, 10 -> 8 cut, invalid ->
      `ValueError`
- [x] Edges: (-90, -180) -> `AA00aa`, (90, 180) -> `RR99xx`. Bad precision and
      out-of-range input (including NaN/inf) raise `ValueError`
- [x] Round trip for 10 000 random locators and 10 000 random coordinates (fixed seeds)
- [x] `cell_size` per level. `snap_extent` snaps outward, clamps to the world, and
      leaves already-aligned extents unchanged
- [x] An extent wholly outside the world has no cells (`count_cells` 0, `iter_cells`
      empty, `snap_extent` raises `ValueError`). An extent that only touches the edge
      of the world gets the edge cells, consistent with `to_locator` (review fix)
- [x] Non-numeric coordinates (None, ints too large for a float) raise `ValueError`,
      not `TypeError` or `OverflowError` (review nit)
- [x] `count_cells == len(list(iter_cells))`. Cells are unique, cover the extent, and
      bounds equal `to_bounds(locator)`. `count_cells` is O(1) (the world at level 8
      answers instantly)
- [x] Europe at square level: timing test marked `slow`
- [x] Python 3.9 compatible, no `qgis`/`PyQt` imports, ruff clean

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` and `ruff format --check` pass on the changed files
- [x] Skill vectors hold at 6 and 8 characters (Beograd 44.8125, 20.4612 -> `KN04ft` / `KN04ft55`)
- [x] Europe (lon -25..45, lat 34..72) at square level: 1368 cells generated in core
      in under 1 ms, well under the 2 s budget from PLAN.md M1

## Result

**What changed**
- `hamq/core/maidenhead.py` (new). All positions become integer indices of
  extended cells, and every character pair comes from integer division. Degrees
  come from a single correctly rounded division of an exact integer, so
  neighbouring cells share bit-identical edges and every cell edge maps back to
  its own cell. A cell owns its south and west edges; the north and east edges
  belong to its neighbours. The north pole and 180 E fall into the last cell.
  Speed: `to_locator` takes about 2 us per call; 198 144 subsquare cells
  generate in about 0.1 s.
- `tests/core/test_maidenhead.py` (new, 256 tests, about 1.6 s). Besides the
  requested cases it covers:
  - the south-west corner of every cell in several regions (the whole world at
    square level, KN04 at subsquare level, and extended regions) maps to that cell;
  - the north and east edges map to the neighbours;
  - edges with float representation error (`44 + 1/24` gives 32169.999999999996
    units);
  - non-ASCII look-alikes (KELVIN SIGN, long s), which `re.IGNORECASE` alone would
    accept;
  - point and line extents;
  - extents wholly outside the world, extents that only touch its edge, and
    2 000 random point extents inside, on and just outside the edge of the world
    (each must agree with `to_locator`);
  - lazy iteration of the world at level 8;
  - the `MAX_GRID_CELLS` guard: Europe at subsquare level is 766 080 cells.

**Fix round (review 1)**
- Finding (should): an extent that does not touch the world still produced cells.
  `_axis_range` clamped it onto the edge of the world and then kept at least one
  row or column. For example, `count_cells(30, 200, 60, 250, 4)` returned 30, and
  the point extent (95, 250) gave `RR99` although `to_locator(95, 250)` raises.
  The finding was reproduced. Regression tests were added first, and 17 of them
  failed before the fix. `_grid` now returns empty ranges when
  `lat_min > 90 or lat_max < -90 or lon_min > 180 or lon_max < -180`. These are
  the same closed bounds that `to_locator` accepts, with no epsilon: one ulp
  beyond 90 gives no cells, and exactly 90 gives the top row. `snap_extent`
  raises `ValueError` in that case, since there are no cells whose union it
  could return. A degenerate extent clamped onto the edge was rejected because
  passing it back to `count_cells` would give cells again. Docstrings were
  updated.
- Nit from the review summary: `to_locator(None, x)` and `count_cells(None, ...)`
  raised `TypeError`, and `to_locator(10**400, 0)` raised `OverflowError`. A
  private `_as_float` now turns both into `ValueError`. This matches the
  docstrings and behaviour choice 3 (an `except ValueError` also covers NULL
  values).
- New tests (24): `test_extent_outside_the_world_has_no_cells` (9 extents at every
  level), `test_extent_touching_the_world_edge_gives_the_edge_cells` (5, passing
  before and after the fix, so the boundary rule is pinned),
  `test_point_extent_has_a_cell_exactly_when_to_locator_accepts_the_point`
  (random, fixed seed), `test_to_locator_non_numbers_raise_value_error` (5), and
  4 more cases in `test_invalid_extent_raises`.

**Behaviour choices the callers should know**
1. `is_valid`, `to_bounds` and `to_latlon` do not strip whitespace and do not
   accept 10-character locators. `is_valid(x)` is True exactly when
   `to_bounds(x)` succeeds. Clean user and file input with `normalize()` first.
2. `normalize` cuts a 10-character locator to 8 characters only when characters
   9 and 10 are letters a-x (the fifth pair of the 10-character system). So
   `KN04ft55ab` becomes `KN04ft55`, while `KN04ft5512` and 12-character input
   raise `ValueError`.
3. Non-string input: `is_valid` returns False. `normalize`, `to_bounds` and
   `to_latlon` raise `ValueError`, not `TypeError`, so `except ValueError` also
   covers QGIS NULL values.
4. `to_locator` converts `lat`/`lon` with `float()`, so ints, numeric strings and
   numpy values work. NaN, inf and values that cannot be converted (None, an int
   too large for a float) raise `ValueError`, never `TypeError` or
   `OverflowError`.
5. Extents raise `ValueError` for min > max, NaN, or a value that is not a number.
   An extent that crosses the antimeridian must be split by the caller. The parts
   of an extent outside the world are clamped (±inf is allowed). A zero-width or
   zero-height extent keeps the one row or column that contains it, so a point
   extent gives exactly the cell that `to_locator` returns. The world is closed,
   as in `to_locator`: an extent that only touches its edge (lat 90..95, lon
   180..190) gets the cells on that edge. An extent wholly outside the world has
   no cells. This happens, for example, when an EPSG:4326 canvas is panned past
   180°. In that case `count_cells` returns 0, `iter_cells` yields nothing and
   `snap_extent` raises `ValueError`. The grid algorithm should check
   `count_cells(...) == 0` first and tell the user. Core does not wrap
   longitudes, so to draw cells east of 180° the caller must shift them by
   -360° itself.
6. `iter_cells` checks its arguments when called, then iterates lazily: longitude
   (west to east) in the outer loop, latitude (south to north) in the inner loop.
   It does not enforce `MAX_GRID_CELLS`; the grid algorithm must check
   `count_cells(...)` first.

**Commands run** (after the fix round)
- `python3 -m pytest -p no:cacheprovider tests/core/test_maidenhead.py -q` -> 256 passed
  (host Python 3.14). Before the fix, 17 of the 24 new tests failed. The 7 that
  passed were the 5 edge-touching guards and the two text inputs (`"north"`,
  `"west"`), which already raised `ValueError`.
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 2338 passed, 4 xfailed.
  The xfails are other agents' i18n tests.
- `docker run --rm -v <repo>:/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim
  sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_maidenhead.py -q"`
  -> 256 passed (Python 3.9.25). With `tests/core/test_architecture.py` added -> 299 passed.
- `qgis/qgis:3.44-trixie` (Python 3.13.5), same command -> 256 passed.
- `camptocamp/qgis-server:3.34` (Python 3.10.12, minimum QGIS): smoke script with
  the reviewer's evidence calls -> 0 cells, nothing yielded, and `ValueError` for
  all the off-world extents; lat 90..95 gives the top row; the vectors are OK.
- Host QGIS 4.2.1 Python (with `qgis.core` imported): import, vectors, and the
  off-world and edge-touching cases -> OK.
- `ruff check` and `ruff format --check` on both files -> clean (ruff 0.16.9).
- Speed after the fix: `to_locator` about 2.1 us per call, the Europe square grid
  about 0.6 ms, and 198 144 subsquares about 0.1 s (unchanged).
- Scratch checks, not in the repo:
  - 60 000 random extents (8 731 wholly outside the world, the rest inside,
    straddling or touching it) against an exact `Fraction` reference ->
    0 mismatches in count, snapped extent and iterated cells.
  - 8 wrong fixes injected into a copy of the module were all caught by the
    tests: no check, `>=` instead of `>`, lat only, lon only, north/east only,
    clamped `snap_extent`, plain `float()`, and an epsilon on the world check.
- Extra checks from the first round (scratch scripts, not in the repo):
  - an exact `Fraction` reference agreed with `to_locator` on 200 000 random
    points (0 differences);
  - 19 deliberate bugs were injected into a copy of the module, and the tests
    caught every one. They included no epsilon, epsilon 1e-3, the skill's float
    encoder, a missing clamp, a wrong iteration order, and case-insensitive
    matching without `re.ASCII`.

**Manual checks still needed:** none for core. The < 2 s criterion including QGIS
layer creation belongs to the grid algorithm task.

## Notes
- AGENTS.md's definition of done asks for a `CHANGELOG.md` entry under
  `## Unreleased`. The file does not exist in the repository yet, and it is
  outside this task's scope. Suggested line: "Maidenhead core: locator <->
  coordinates (2 to 8 characters), cell bounds and centre, validation,
  normalization, grid cell helpers (M1-01)."
- Request for `docs/ARCHITECTURE.md` (not edited, outside scope). Add two
  comments to the `core/maidenhead.py` section; the names and signatures stay as
  they are:
  1. `count_cells` is 0 and `iter_cells` is empty for an extent wholly outside
     the world, and `snap_extent` raises `ValueError` for it.
  2. `normalize` cuts a 10-character locator only when its fifth pair is a-x.
  The grid algorithm depends on the first and `core/qso.py` on the second.
- The encoder snippet in `.github/skills/maidenhead/SKILL.md` (repeated float `%`)
  passes the skill's vectors but puts cell corners into the wrong cell: 56% of
  subsquare and 71% of extended south-west corners tested in field KN. Suggest
  replacing it with the integer-unit method used here (outside this task's scope).
