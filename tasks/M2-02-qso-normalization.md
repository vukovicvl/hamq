# M2-02: QSO normalization (ADIF record -> QSO row)

**Milestone:** M2
**Status:** done
**Skills:** adif, maidenhead, geodesy, dxcc-cty

## Goal
`hamq/core/qso.py` per the contract section "core/qso.py": turn one ADIF record (as
`core/adif.py` returns it) into one row of the `qso` layer, covering validation, the dedup
key, the position priority, DXCC enrichment from cty.dat and distance/bearing from my
station, with translated warnings and no exceptions on bad data.

## Scope
Files that may be created or changed:
- `hamq/core/qso.py`
- `tests/core/test_qso.py`
- `hamq/i18n/sr_Latn/core_qso.json`
- `tasks/M2-02-qso-normalization.md`

## Out of scope
- GeoPackage writing, dedup against the database, geodesic path lines (`qgis_io/gpkg.py`).
- Processing *Import ADIF*, WSJT-X live import, the controller.
- `maidenhead.py`, `adif.py`, `geo.py`, `cty.py` (finished and reviewed; used, not changed).
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `pyproject.toml` (owned by the orchestrator).

## Checklist
- [x] `QSO_FIELDS` exactly in PLAN.md order without `fid`, kinds per PLAN (`int`, `real`,
      `text`, `datetime`); `PATH_FIELDS` per the contract
- [x] `Station(call="", grid="")` with `latlon()` (centre of the locator, any case and
      whitespace, 10 characters cut like `normalize`, `None` if empty or invalid)
- [x] `Qso` dataclass with the contract fields in order, `attributes()` in `QSO_FIELDS` order,
      `display_mode` property; `display_mode` re-exported from `core/modes.py`
- [x] `record_to_qso` / `records_to_qsos` with the contract signatures
- [x] CALL required, `QSO_DATE` + `TIME_ON` must parse (skip + warning); aware UTC datetimes
- [x] band: `BAND` normalized, else from `FREQ`; mode / submode uppercased; dedup key via
      `adif.dedup_key` with `display_mode` (FT4 as `MODE=FT4` or `MFSK`/`FT4` -> same key);
      since the release pass with `modes.dedup_mode` (see Notes)
- [x] `gridsquare` / `my_gridsquare` normalized; 10 characters cut to 8 + warning; invalid kept
      as logged + warning + not used for a position
- [x] position `LAT`/`LON` > `GRIDSQUARE` > cty.dat > none (`loc_source`); origin `MY_LAT`/`MY_LON`
      > `MY_GRIDSQUARE` > `station.grid` > none; `my_gridsquare` = record value or `station.grid`
      (two refinements in the release pass, see Notes)
- [x] `DXCC`/`COUNTRY`/`CONT`/`CQZ`/`ITUZ` from the record win; missing ones from cty.dat
      (`CtyMatch.dxcc`, `CtyMatch.dxcc_name`); WAE-only entity -> DXCC entity name (documented)
- [x] distance / bearing with `core/geo.py` when position and origin exist
- [x] `adif_extra`: JSON of exactly the fields without a column (sorted keys, `ensure_ascii=False`)
- [x] warnings through `tr()` with `{placeholders}`, naming the record (number, call, date/time);
      Serbian catalog `core_qso.json`
- [x] tests on the real fixtures (`tests/fixtures/adif/*.adi`, `tests/fixtures/cty/`), edge cases,
      fuzzing, 10 000-record timing test (`@pytest.mark.slow`)

## Acceptance criteria
- [x] `pytest tests/core -q` passes (host Python 3.14 and Docker `python:3.9-slim`)
- [x] `ruff check` and `ruff format --check` pass for the new files
- [x] Beograd -> Sydney (`log4om.adi` record 1) within 0.5 % of 15676.1 km and 0.5 deg of 91.0 deg
- [x] 10 000 records converted in < 2 s (measured 0.51 s on 3.14, 0.81 s on 3.9)
- [x] No `qgis` / `PyQt` import in `hamq/core/qso.py` (`tests/core/test_architecture.py`)
- [x] Every `tr()` literal in the catalog, catalog canonical (`tests/core/test_i18n_catalog.py`,
      also with `HAMQ_STRICT_I18N=1`)

## Result

### What changed
- `hamq/core/qso.py` (new): `QSO_FIELDS`, `PATH_FIELDS`, `Station`, `Qso`, `display_mode`
  (re-export), `record_to_qso`, `records_to_qsos`, all as in the contract; only private helpers
  added (no new public names, no new parameters). The module docstring lists every rule.
- Behaviour, in order of the conversion:
  1. Field names are matched case-insensitively, values stripped; `None` and zero-length values
     count as missing (fixture README note from M2-01). Non-text values are converted with
     `str()`; an item that is not a mapping is a record without CALL.
  2. Missing CALL, or `QSO_DATE` + `TIME_ON` that `adif.parse_qso_datetime` rejects: skipped
     with a warning. CALL is stored uppercase and stripped.
  3. `FREQ` via `adif.parse_freq` (invalid -> `None` + warning). Band: `BAND` normalized
     (`20M` -> `20m`) when it is an ADIF band, else the band of `FREQ`.
  4. `MODE` / `SUBMODE` uppercased; dedup key `adif.dedup_key(call, YYYYMMDD, HHMM, band or "",
     display_mode(mode, submode))`. Release pass: `modes.dedup_mode(mode, submode)` instead.
  5. `GRIDSQUARE`, `MY_GRIDSQUARE` through `maidenhead.normalize`.
  6. `LAT`/`LON` and `MY_LAT`/`MY_LON` via `adif.parse_latlon`, used only as a complete pair.
  7. `DXCC` 0-999, `CQZ` 1-40, `ITUZ` 1-90 (ASCII digits only, `05` -> 5), `CONT` one of the
     seven continents (uppercased), `COUNTRY` as logged.
  8. Position: `LAT`/`LON` -> `latlon`, else locator centre -> `grid`, else cty.dat match ->
     `cty`, else none (release pass: a 2-character locator yields to cty.dat inside its field). cty.dat is consulted only when a position or a DXCC value is missing;
     it fills each missing value (`dxcc` = `CtyMatch.dxcc`, `country` = `CtyMatch.dxcc_name`,
     `cont`, `cq_zone`, `itu_zone` with the per-entry overrides, `lat`/`lon`).
  9. `country` for a WAE-only entity is the DXCC entity name (`IT9XYZ` -> `Italy`, DXCC 248),
     while position, continent and zones are Sicily's own. Reason: `core/stats.py` counts DXCC
     by code and falls back to the country name; with "Sicily" as country, a log without
     cty.csv codes would count Sicily and Italy as two entities. Documented in the module
     docstring and tested (`wsjtx_log.adi` 1, `n1mm.adi` 3).
  10. Origin: `MY_LAT`/`MY_LON` > `MY_GRIDSQUARE` > station locator > none; distance and
      bearing with `geo.distance_km` / `geo.bearing_deg` from the origin (release pass: a
      coarser `MY_GRIDSQUARE` is refined to the station locator).
  11. `adif_extra` = `json.dumps({fields without a column}, ensure_ascii=False, sort_keys=True)`,
      empty values of those fields included (`<APP_N1MM_EXCHANGE1:0>`, `<email:0>`), `"{}"` when
      none. The 16 fields with a column are never repeated there.
- Warnings (all via `tr()`, English source + Serbian Latin in `core_qso.json`, 21 texts):
  the record is named `Record {index} ({call}, {date} {time})` in `records_to_qsos` (same
  numbering as the ADIF parser's warnings) and `QSO {call}, {date} {time}` in `record_to_qso`;
  date and time as logged, values shortened to 40 characters on one line.
  `record_to_qso` also warns when the QSO has no position or my QTH is unknown;
  `records_to_qsos` counts those in two summary lines instead and caps the other warnings at
  100 plus "Further warnings not listed: {count}" (same text and translation as
  `core_adif.json` / `core_cty.json`).
- Additions beyond the contract text (each documented in the module docstring and tested; see
  Notes for the requested contract wording):
  - a `BAND` that is not an ADIF band takes the band of `FREQ` (warning) or is kept as logged
    (warning); a `FREQ` outside every band without `BAND` gives a warning;
  - invalid `DXCC`/`CQZ`/`ITUZ`/`CONT` values count as missing (warning, then cty.dat fills them);
  - `LAT` must carry `N`/`S` and `LON` `E`/`W` (`adif.parse_latlon` accepts any letter for any
    field), a lone `LAT` or `LON` is not used, `0/0` is treated as a placeholder; the same for
    `MY_LAT`/`MY_LON`; each with a warning, falling through to the locator;
  - the record's DXCC code differing from the cty.dat code gives a warning (the record wins and
    missing values are still filled, as the contract says);
  - an invalid station locator gives one warning per call ("My locator ... is invalid").
- `tests/core/test_qso.py` (new): 167 tests. Fixtures used: `wsjtx_log.adi`, `log4om.adi`,
  `n1mm.adi`, `lotw.adi`, `no_eoh.adi`, `qrz_export.adi`, `xlog.adi`, `utf8_name.adi` with concrete
  values, plus an invariant test over all 15 `.adi` fixtures; synthetic records for each rule;
  a 9 000-conversion fuzz test (garbage values, full-width digits, `2_91`, NUL, `<EOR>`);
  catalog checks; Serbian Latin and Cyrillic output; `@pytest.mark.slow` 10 000-record test.
  Expected positions were worked out by hand from the locator definition and the cty.dat
  header lines; distances against the skill's WGS84 table, re-computed independently with
  Vincenty (Sydney 15676.14 km / 90.99 deg, München 773.61 / 301.76, Washington 7608.71 /
  303.85); the KN04ft -> JM77 and Beograd -> VK3 values with an independent haversine.
- `hamq/i18n/sr_Latn/core_qso.json` (new): 21 messages, canonical JSON; Cyrillic output checked
  by transliterating every entry (technical tokens such as `MY_LAT/MY_LON`, `cty.dat`, `MHz`,
  `QTH`, `HamQ` stay Latin).

### Commands run (final state)
- `python3 -m pytest -p no:cacheprovider tests/core/test_qso.py -q` -> 167 passed (Python 3.14.4)
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 3172 passed, 8 xfailed (the xfails
  belong to other modules)
- `python3 -m pytest -p no:cacheprovider tests/core/test_i18n_catalog.py tests/core/test_architecture.py -q`
  -> 323 passed, 2 xfailed; `HAMQ_STRICT_I18N=1 ... -k core_qso` -> 4 passed
- Docker `python:3.9-slim` (Python 3.9.25, repository mounted `:ro`,
  `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`): `tests/core/test_qso.py
  tests/core/test_architecture.py tests/core/test_i18n_catalog.py` -> 490 passed, 2 xfailed;
  `tests/core` -> 3172 passed, 8 xfailed
- Docker `qgis/qgis:3.44-trixie` (Python 3.13.5): `tests/core/test_qso.py` -> 167 passed
- `ruff check` / `ruff format --check` (ruff 0.16.9) on `hamq/core/qso.py tests/core/test_qso.py`
  -> clean
- QGIS tests that were skipped until this module existed (`importorskip("hamq.core.qso")`):
  `test_make_fields_from_qso_fields`, `test_station` (settings), `test_record_is_ready_for_record_to_qso`
  (manual QSO dialog): host QGIS 4.2 -> 3 passed; `scripts/test_qgis.sh <target> -k
  "qso_fields or test_station or record_to_qso"` -> 5 passed each on 3.44, 4.0 and 3.34
- Timing, 10 000 records (13-15 fields, cty lookups, 3/4 with locators), best of 5:
  0.51 s (Python 3.14.4), 0.81 s (Python 3.9.25); the test asserts < 2 s
- Mutation check on a scratch copy of the repository (never the repo): 71 mutants of the rules
  (priorities, record-vs-cty precedence, WAE country, ranges, hemisphere and 0/0 checks, warning
  cap, summaries, labels, adif_extra flags, key/value normalization, ...): 69 killed, 2 survive
  and both are equivalent (one only removed a comment; putting seconds into the time passed to
  `adif.dedup_key` changes nothing because `dedup_key` cuts `TIME_ON` to 4 characters). A first
  run had one more survivor (`source=None` not converted to `""`); it got a test and is killed.

### Manual checks still needed
- None for this core module (no GUI). End-to-end checks (Import ADIF into a GeoPackage,
  WSJT-X live QSOs) belong to the tasks that call it.

## Notes
- `CHANGELOG.md` did not exist yet and was outside this task's scope; the orchestrator has
  since written the entry ("QSO normalization", under `## [Unreleased]`).
- `tests/fixtures/cty/README.md` says the excerpt is used only by `tests/core/test_cty.py`; it is
  now also used by `tests/core/test_qso.py` (README not in this task's scope).
- Contract requests for the orchestrator (not made here):
  1. `my_gridsquare`: the contract stores `station.grid` when the record has no `MY_GRIDSQUARE`,
     even when it has `MY_LAT`/`MY_LON` (a portable QSO then shows the home locator next to a
     path that starts elsewhere). Suggest: `MY_GRIDSQUARE`, else the 6-character locator of
     `MY_LAT`/`MY_LON`, else `station.grid`. Implemented as the contract says today.
  2. DXCC conflict: when the record's `DXCC` and the cty.dat code differ, the contract still
     fills missing `country`/`cont`/zones/position from that cty.dat match, i.e. from another
     entity than the record's (e.g. dxcc 230 with country "Serbia"). Implemented as written,
     with a warning. Suggest: do not use such a match, or use the cty.dat entity with the
     record's code (`CtyDatabase.entities` by `dxcc`) for country/continent/position.
  3. Wording for the additions listed under Result (unknown `BAND`, invalid DXCC values,
     `LAT`/`LON` hemisphere and `0/0`, warning cap and summaries, per-record warnings of
     `record_to_qso`, record numbering), so callers can rely on them.
- Observation, not a bug: `adif.parse_latlon("E044 48.750")` returns 44.81; the parser cannot
  know the field, so `qso.py` checks the hemisphere letter itself.
- **Release pass (2026-10-01)**, three rule changes (core fixer; contract updated in
  docs/ARCHITECTURE.md, sections "core/modes.py" and "core/qso.py"):
  1. **Duplicate key mode:** `modes.dedup_mode(mode, submode)` replaces `display_mode` in
     the key. For an ADIF 3.1.7 mode whose submodes are only variants of it (SSB, PSK,
     JT65, JT9, JT4, CW, RTTY, OLIVIA, ...) the key holds the family of `MODE` (else of
     `SUBMODE`): `MODE=SSB`, `MODE=SSB SUBMODE=USB` and the older `MODE=USB` are one QSO,
     as are `PSK31` / `PSK` and `JT65B` / `JT65`. MFSK and DIGITALVOICE keep the submode
     (`FT4`, `C4FM`); every other mode keeps `display_mode`. Found by the reviewer: a
     DXKeeper export (`SSB` + `USB`) and a LoTW download (`SSB`) of the same two QSOs
     gave four rows. `display_mode` still names the mode in statistics, panel and paths.
  2. **2-character `GRIDSQUARE`:** a whole 20 x 10 degree field yields to the cty.dat
     position when that lies inside the field; otherwise the field centre is used, with
     the new warning "locator {value} in GRIDSQUARE is only a Maidenhead field ...".
  3. **Coarse `MY_GRIDSQUARE`:** without `MY_LAT`/`MY_LON`, a `MY_GRIDSQUARE` that is a
     strict prefix of a valid station locator (`KN04` with `KN04ft`, as WSJT-X writes its
     "My Grid") is replaced by the station locator for the origin and for
     `my_gridsquare`; a finer locator or another cell is kept as logged.
  New tests: `tests/core/test_modes.py` (158 tests: every ADIF submode family, umbrella
  modes, case, blanks, non-text values) and 14 more test functions in
  `tests/core/test_qso.py` (209 tests in all). Core suite: 3453 passed, 7 xfailed after
  the core fixes (3472 passed, 7 xfailed with the packaging tests of the release pass).
- Still open (`qgis_io`, not this module): `gpkg.recalculate` keeps a stored `grid` point
  from a 2-character `GRIDSQUARE` even when cty.dat, downloaded later, has a position
  inside that field (`_replaces` ranks `grid` above `cty`), so a file imported without
  cty.dat keeps the field centre (9A2XYZ `JN`: 823 km) after *Recalculate*; a new import
  places such QSOs correctly.
