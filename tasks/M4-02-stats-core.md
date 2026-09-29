# M4-02: QSO statistics core (`hamq/core/stats.py`)

**Milestone:** M4
**Status:** done
**Skills:** adif

## Goal
A pure Python module that computes the numbers for the dock's Statistics tab.
`compute_stats(rows)` takes QSO rows: attribute dicts keyed by the `QSO_FIELDS` names, as
`qgis_io.gpkg.read_qso_rows` returns them. It returns:
- the QSO count;
- DXCC entities, unique calls and grid squares;
- QSOs per continent, band and mode;
- the longest QSO;
- the first and last QSO time.

It implements the "core/stats.py" section of `docs/ARCHITECTURE.md`.

## Scope
Files that may be created or changed:
- `hamq/core/stats.py`
- `tests/core/test_stats.py`
- `tasks/M4-02-stats-core.md`

## Out of scope
- The Statistics tab of the dock, which translates the labels, and `qgis_io.gpkg.read_qso_rows`.
- `core/qso.py`. It is not imported: the field names come from the contract, and modes and
  bands come from `core/modes.py` and `core/bands.py`.
- `CHANGELOG.md`: not edited in this parallel run (see Notes).
- A translation catalog. The module has no user-visible strings, so there is no
  `hamq/i18n/sr_Latn/core_stats.json`.

## Checklist
- [x] Public names exactly as in the contract:
  - `CONTINENTS`;
  - `LongestQso` and `QsoStats`, with the contract's field names, order and types;
  - `compute_stats(rows)`.

  `__all__` lists these four names. Everything else is private.
- [x] Uses `display_mode`, `band_sort_key`, `normalize_band` and `band_from_freq`; does not
      import `core/qso.py`
- [x] Tests written first
- [x] `dxcc_count`:
  - distinct codes, given as an int, an integral float or numeric text (`296`, `"296"`,
    `" 296 "`, `"296.0"`, `296.0`, `Decimal`);
  - a row without a usable code counts by its country name (casefold, stripped, whitespace
    collapsed);
  - a row with neither a code nor a name does not count;
  - the mixed-source caveat is in the docstring.
- [x] `unique_calls`: case-insensitive, stripped
- [x] `grid_count`:
  - the first 4 characters must be two letters A-R and two digits, in any case;
  - ASCII only (KELVIN SIGN, fullwidth and Arabic-Indic digits are rejected);
  - only `gridsquare` counts.
- [x] `by_continent`:
  - `CONTINENTS` order, then other values alphabetically, then `"?"`;
  - uppercased;
  - no zero counts.
- [x] `by_band`: `band_sort_key` order, unknown bands after the known ones, `"?"` last
- [x] `by_mode`: `display_mode(mode, submode)`, `"?"` when missing, sorted by count descending,
      then name
- [x] `longest`:
  - the maximum `distance_km`;
  - ignores None, NaN, inf, negatives, bools, bytes and non-numeric values;
  - accepts numeric text;
  - on a tie, the earliest `qso_datetime` wins.
- [x] `first_qso`, `last_qso`:
  - a `datetime` (naive means UTC) or ISO 8601 text;
  - text accepts `Z` on Python 3.9, `+00:00` and other offsets, with or without seconds,
    milli- or microseconds, and a space instead of `T`;
  - unparsable values are ignored;
  - results are always UTC aware.
- [x] Robust input:
  - missing keys, `None` and QGIS `NULL` (`None` in QGIS 4, a null `QVariant` in QGIS 3);
  - empty strings, numeric strings and wrong types.

  Bad values never raise.
- [x] 50 000 rows in < 0.5 s (test marked `slow`)
- [x] Python 3.9 compatible, no `qgis`/`PyQt` imports, ruff clean

## Acceptance criteria
- [x] `pytest tests/core/test_stats.py` passes on these Pythons:
  - 3.14.4 on the host;
  - 3.9.25 in the `python:3.9-slim` Docker image;
  - 3.13.5 in the QGIS 3.44 and QGIS 4.0 Docker images.
- [x] `ruff check` and `ruff format --check` pass on both files
- [x] 50 000 rows in < 0.5 s. The worst-case log (3/4 of the times as ISO text, nearly unique
      calls, grids and times), best of 3:

  | Python | Time |
  |---|---|
  | 3.14 | 0.16 s |
  | 3.10 (QGIS 3.34 image) | 0.21 s |
  | 3.9 | 0.27 s |
- [x] Correct on real QGIS values: QGIS 3.34 (minimum), 3.44 (Qt5) and 4.2 (Qt6)

## Result

**What changed**
- `hamq/core/stats.py` (new). A single pass over the rows collects raw values only. Text is
  normalized once per distinct value after the loop, because logs repeat the same calls,
  bands, modes and countries.

  ISO text goes through one grammar (`_ISO_RE`), so every Python version accepts the same
  strings. The common layouts take a fast path through `datetime.fromisoformat`. That path is
  limited to layouts that Python 3.9 through 3.14 all read the same way:
  - "T" or a space between date and time;
  - hours 00-23, minutes and seconds 00-59;
  - a 3 or 6 digit fraction;
  - a `+HH:MM` offset, or a `Z` that is cut off first.

  A fuzz test checks that the fast path agrees with the general parser: about 10 800 mutated
  strings, 2 200 of them on the fast path. The fast path made the 50k-row test 2 to 3 times
  faster (0.36 s -> 0.16 s on 3.14).
- `tests/core/test_stats.py` (new, 230 tests, about 1.7 s). Besides the rules above it
  covers:
  - the realistic 11-QSO log of a Belgrade station, with a full `QSO_FIELDS` row shape;
  - that the order of rows does not matter;
  - plain `dict` results, and input that is not modified;
  - sqlite3 rows, and `Mapping` types other than `dict`;
  - a generator consumed once, and 3 000 rows of random garbage (no exception);
  - naive times that stay UTC while the computer's zone is UTC+9 (`TZ=JST-9`);
  - `QDateTime` fakes for every time spec, in the PyQt5 (int) and PyQt6 (enum) styles.

**Behaviour choices the callers should know**
1. **DXCC names seen next to a code.** A code-less row does not count again if its country
   name also appears on a row that has a code. This refines the literal rule, under which
   `{dxcc: 296, country: "Serbia"}` plus `{dxcc: None, country: "Serbia"}` would count as
   2 entities. Such mixes happen, for example WSJT-X rows logged before cty.csv was
   downloaded.

   A name that never appears next to a code still counts separately. That is the caveat in
   the docstring: code 230 plus the cty.dat name "Germany" count as 2.
2. **DXCC code 0 means "not in any DXCC entity" (ADIF, for /MM and /AM).** Such rows do not
   count, not even by name. Negative codes, non-integral values and non-numeric text are
   unusable, so the row falls back to its name. `True`/`False` are not codes: `True` would
   otherwise count as code 1, Canada.
3. **`unique_calls` compares calls as logged.** `YU1AB` and `YU1AB/P` are two calls.
4. **`grid_count` checks only the first 4 characters.** `KN04zz` counts as `KN04`. A
   2-character locator (`KN`) does not count.
5. **`by_band` falls back to `freq_mhz`.** When `band` is missing or blank, the band comes
   from `freq_mhz` (for example, 14.074 gives `20m`). This is an addition to the task text.
   A logged band always wins over the frequency.
6. **`"?"` in `by_mode`.** As the task says, `"?"` is sorted like any other mode (by count,
   then name), so it can come first. In `by_continent` and `by_band` it is always last.
7. **`LongestQso` fields:**
   - `call` is uppercased, `""` when missing;
   - `country` is stripped but otherwise as logged, `None` when blank;
   - `band` is normalized or derived from `freq_mhz`, `None` when unknown;
   - `mode` is the display mode (`FT4` for MFSK/FT4), `None` when missing;
   - `qso_datetime` is in UTC;
   - a distance of `-0.0` is reported as `0.0`.

   On a tie, the earliest time wins. A row with a time beats a row without one; otherwise the
   first row wins.
8. **Accepted times:**
   - `datetime`: naive means UTC; aware values are converted to UTC;
   - `date`: midnight UTC;
   - ISO text, extended or basic format (`20260915T184500Z`), a date alone, and a fraction of
     any length (cut to microseconds).

   Rejected and ignored:
   - hours without minutes;
   - `24:00`, a leap second `:60`, offsets of 24 h or more;
   - mixed basic and extended formats;
   - non-ASCII digits;
   - trailing text such as ` UTC`;
   - results outside the `datetime` range after conversion to UTC.
9. **`QDateTime` values are accepted (duck typing, no Qt import).** QGIS returns them for
   GeoPackage datetime fields; checked on 3.34, 3.44 and 4.2. `toPyDateTime()` returns
   naive wall-clock fields in the value's own time spec. So:
   - values in UTC, with an offset, or with a time zone are converted to UTC;
   - a LocalTime value is read like a naive datetime: its fields are taken as UTC.

   QGIS uses LocalTime for datetimes stored without a zone, and PyQt builds LocalTime values
   from Python datetimes. `toUTC()` would shift those by the computer's zone (checked:
   20:45 becomes 18:45 with TZ=Europe/Belgrade). Invalid values are ignored, whether
   `isValid()` is False or `toPyDateTime()` raises.
10. **Accepted rows:**
    - `dict`s, any `Mapping`, and objects with `keys()` such as `sqlite3.Row` (useful when
      reading the GeoPackage with sqlite3);
    - other items (`None`, strings, lists) are skipped and not counted in `total`;
    - `rows=None` gives empty stats.

**Commands run**
- `python3 -m pytest -p no:cacheprovider tests/core/test_stats.py -q`: 230 passed
  (Python 3.14.4).
- `docker run --rm -v <repo>:/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim
  sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_stats.py -q"`:
  230 passed (Python 3.9.25).
- The same pytest run in `qgis/qgis:3.44-trixie` and `qgis/qgis:4.0-trixie`: 230 passed
  in each (Python 3.13.5).
- `camptocamp/qgis-server:3.34` (Python 3.10.12, no pytest): a smoke script (realistic log,
  `Z` text, 50k rows) passed.
- Real-QGIS scratch script (not in the repo) on host QGIS 4.2.1 (PyQt6), Docker QGIS 3.44
  (PyQt5) and QGIS 3.34, all with TZ=Europe/Belgrade. It checks:
  - `QDateTime` in LocalTime, UTC, OffsetFromUTC and TimeZone specs;
  - invalid `QDateTime()` values and `NULL`, including a row where every field is `NULL`;
  - a GeoPackage written and read back through QGIS, with rows built as
    `{name: feature[name]}`.

  All checks passed. On 3.34 the image has no tz database, so `QTimeZone("Asia/Tokyo")` is
  invalid there and is correctly ignored; an offset zone (UTC+9) was checked instead.

  50k rows with raw `QDateTime` values:

  | QGIS | Time |
  |---|---|
  | 4.2 | 0.18 s |
  | 3.44 | 0.20 s |
  | 3.34 | 0.29 s |
- `ruff check` and `ruff format --check` on both files: clean (ruff 0.16.9).
- Mutation check (scratch script, not in the repo): 55 deliberate bugs were injected into a
  copy of the module, and the tests caught 54. The survivor removes `math.isfinite` from
  `_number`. It is an equivalent mutant: every caller already rejects inf and NaN (the
  distance range check, `is_integer()`, `band_from_freq`). The check stays because it is
  part of the helper's contract.
- `python3 -m pytest -p no:cacheprovider tests/core -q` (the whole directory, other tasks'
  files included): 2162 passed, 4 xfailed and 3 failed. All 3 failures are in
  `tests/core/test_i18n.py`: `hamq/core/i18n.py` was replaced during this task and is
  another task's work in progress. `tests/core/test_architecture.py`, which scans every
  `hamq/**/*.py`, passes with `stats.py`.

**Manual checks still needed:** none for core. The Statistics tab should be checked when the
dock is written.

## Notes
- AGENTS.md's definition of done asks for a `CHANGELOG.md` entry under `## Unreleased`. It
  was not edited because the file is outside this task's scope. Suggested line: "QSO
  statistics core: QSOs, DXCC entities, unique calls, grid squares, QSOs per continent, band
  and mode, longest QSO, first and last QSO (M4-02)."
- For the dock and `qgis_io.gpkg` authors:
  - `read_qso_rows` may return QGIS values unchanged (`QDateTime`, `NULL`), because
    `compute_stats` handles them. Converting `QDateTime` to `datetime` there works too.
  - A 50k-QSO log takes 0.16-0.29 s, depending on the Python version and value types (about
    3-6 us per row). AGENTS.md asks for no more than about 100 ms of UI-thread blocking, and
    logs above roughly 20 000 QSOs exceed that. For large logs, run `read_qso_rows` and
    `compute_stats` in a `QgsTask`. `compute_stats` is pure and keeps no state, so it is
    thread-safe.
  - The dock should show the `"?"` key as a translated label (for example
    `tr("Unknown")`).
- `docs/ARCHITECTURE.md` needs no contract change. Optional clarifications of the stats
  comments, text only:
  - `by_band`: `"?"` is last;
  - `dxcc_count`: code 0 does not count, and a name seen next to a code is not counted again;
  - `LongestQso.mode` is the display mode.
