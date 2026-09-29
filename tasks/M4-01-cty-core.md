# M4-01: cty.dat / cty.csv core (callsign -> DXCC entity)

**Milestone:** M4
**Status:** done
**Skills:** dxcc-cty

## Goal
Pure Python parser for the AD1C country files and a callsign lookup that gives the DXCC
entity, continent, CQ / ITU zone, coordinates and ADIF DXCC code (`hamq/core/cty.py`),
tested on a committed 20-entity excerpt of the real files.

## Scope
- `hamq/core/cty.py`
- `tests/core/test_cty.py`
- `tests/fixtures/cty/cty_excerpt.dat`, `tests/fixtures/cty/cty_excerpt.csv`, `tests/fixtures/cty/README.md`
- `hamq/i18n/sr_Latn/core_cty.json`
- `tasks/M4-01-cty-core.md`

## Out of scope
- Download, cache and refresh (`hamq/net/cty_download.py`), settings, GUI.
- Using the lookup in `core/qso.py`.
- `CHANGELOG.md` and `docs/ARCHITECTURE.md` (owned by the orchestrator in this parallel run;
  see Notes for the requested contract additions).

## Checklist
- [x] Find and verify the current direct URLs on country-files.com (curl + content check)
- [x] Study both formats on the real data (findings below)
- [x] Tolerant parser: blank and `#` lines skipped, overrides in any order, a broken entity
      or entry is skipped with a warning, longitude flipped to east-positive, lookup dicts
      built once
- [x] `lookup()`: cleanup, exact `=` first (also after dropping non-location suffixes),
      `/MM` `/AM` -> `None`, non-location suffixes, `PREFIX/CALL` and `CALL/PREFIX`,
      3-part calls, longest prefix, overrides, unknown -> `None`
- [x] DXCC identity: `Entity.dxcc` from cty.csv, parent DXCC entity for WAE-only entities
      (from the csv codes, with a verified fallback table)
- [x] Fixture excerpt (20 entities) with `#` credit header and README (source, dates, licence)
- [x] Tests: skill table, WAE, overrides, portable calls, cleanup, garbage, robustness, codes,
      catalog
- [x] Serbian catalog `core_cty.json` for every warning message
- [x] Load time and 10 000 lookups measured on the full real file

## Acceptance criteria
- [x] `python3 -m pytest tests/core/test_cty.py -q` passes (host Python 3.14 and Docker
      `python:3.9-slim`)
- [x] `ruff check` and `ruff format --check` pass for the new files
- [x] The skill's test calls give the correct entity, continent and zones
- [x] Full Big CTY: load < 0.5 s, 10 000 lookups < 0.3 s
- [x] No `qgis` / `PyQt` import in `hamq/core/cty.py`

## Findings: the real files (downloaded 2026-09-29)

### URLs
- The Big CTY page (<https://www.country-files.com/big-cty/>) and the WSJT-X page link the
  file directly as `https://www.country-files.com/bigcty/cty.dat`; the csv is next to it at
  `https://www.country-files.com/bigcty/cty.csv`. Both answer 200 (`application/octet-stream`,
  Last-Modified 15 Sep 2026) and are byte-identical to `cty.dat` / `cty.csv` inside the release
  zip `bigcty/download/2026/bigcty-20260915.zip`. The content is the release of 15 September
  2026 (`=VER20260915` in Canada; `=VERSION` resolves to Rotuma Island, as announced).
- Chosen: **Big CTY** (`DEFAULT_CTY_URL` / `DEFAULT_CTY_CSV_URL` above). It is the edition
  AD1C recommends for everyday logging: exception callsigns back to 2000 and all zone
  override prefixes. The contest edition (`/cty/cty.dat`, `/cty/cty.csv`, release CTY-3631)
  has the same 346 entities but only 2 708 exact calls and 4 798 prefixes.
- The release zip's `copyright.txt` carries an MIT-style permission notice (copyright line
  "Copyright © 1994-", holder missing in the original); reproduced in the fixture README.

### cty.dat
- 346 entities, ASCII, CRLF line endings, no blank or `#` lines, no tabs.
- Header: 8 colon-terminated fields (name padded to 26 columns; CQ zones zero-padded, e.g.
  `05`). Ranges in the file: CQ 1..40, ITU 1..90, continents AF EU OC AS NA SA (AN is not
  used by any header), latitude -90..80.68, longitude (+W) -179.2..178.0, UTC offset
  -14..+12 including -12.75, -5.75, 4.5, 9.5. UTC offset sign: Serbia `-1.0`, US `5.0`
  (local = UTC - offset).
- Body: indented (4 spaces) comma separated entries, every continued line ends with `,`,
  the last with `;`. 29 321 entries: 23 011 exact (`=`) and 6 310 prefixes (lengths 1..5;
  the 1-letter ones are F G I K M N R U W). No prefix contains `/`; 525 exact calls have two
  or more `/` (`=9A/DJ1KW/LH`, `=VE2/G3ZAY/P[4]`).
- Overrides actually present: only `(cq)` and `[itu]` (9 510 and 11 102 entries, always in
  the order `(..)[..]` when both). Of the other kinds, `<lat/lon>` and `~tz~` occur only in
  Win-Test's `cty_wt_mod.dat` of the same release (`3B6<-10.42/-56.58>`,
  `JT2[33]<48.08/-114.53>~-8.0~`, `#` comment lines inside entities) and `{cont}` in no file
  at all. The parser accepts all five kinds in any order and loads `cty_wt_mod.dat` with
  0 warnings.
- 60 exact calls appear twice (e.g. `=GB2ELH` in Scotland and Shetland Islands, `=4U1VIC` in
  Vienna Intl Ctr and Austria). AD1C's format page: read top to bottom, ignore the second
  occurrence. Implemented as "first occurrence wins"; no duplicate prefixes exist.
- 46 entities do not list their own primary prefix (`3D2/c`, `FT/g`, `IS`, `UA`, ...): the
  primary prefix is an identifier, not added to the prefix table.

### cty.csv
- 346 rows, 10 columns, no quoting, CRLF: primary prefix (with `*`), name, **ADIF DXCC
  code**, continent, CQ, ITU, lat, lon (+W), UTC offset, entries separated by spaces
  ending with `;`. Codes match the ADIF enumeration (Serbia 296, Germany 230, USA 291,
  Italy 248, Croatia 497, Montenegro 514, Bosnia 501, Canada 1, Australia 150, Japan 339,
  Hawaii 110, Alaska 6, Austria 206, Scotland 279, Turkey 390).
- Rows are matched to cty.dat entities by the raw primary prefix (all 346 match). Names
  differ in 2 rows (csv "Juan de Nova & Europa" / "Tristan da Cunha & Gough Islands" vs
  dat "Juan de Nova, Europa" / "Tristan da Cunha & Gough"); HamQ keeps the dat names. The
  Big csv's entry column is not a complete copy of the dat (US: 6 078 vs 8 035 entries), so
  only the code is taken from the csv.

### WAE-only (`*`) entities and their DXCC parent
The csv gives each WAE-only row the code of its DXCC entity, so the parent is the non-WAE
entity with the same code. All six agree with the fallback table used without the csv:

| WAE entity | primary prefix | csv code | parent DXCC entity |
|---|---|---|---|
| Vienna Intl Ctr | `*4U1V` | 206 | Austria (OE) |
| Shetland Islands | `*GM/s` | 279 | Scotland (GM) |
| African Italy | `*IG9` | 248 | Italy (I) |
| Sicily | `*IT9` | 248 | Italy (I) |
| Bear Island | `*JW/b` | 259 | Svalbard (JW) |
| European Turkey | `*TA1` | 390 | cty.dat "Asiatic Turkey" (TA) |

DXCC 390 is "Turkey"; cty.dat names the non-WAE part "Asiatic Turkey". `dxcc_name` gives
"Turkey" for both TA and TA1 (small documented table `_DXCC_NAMES`), so an Istanbul station
is not labelled "Asiatic Turkey".

### Suffix rules, checked against AD1C's own exception list
For every exact entry of the form `CALL/SUFFIX` in the Big CTY, the listed entity was
compared with the home call's prefix entity and the suffix's prefix entity:
- single letters: `/D` 1049 of 1049, `/F` 119/119, `/W` 118/118, `/N` 69/69, `/I` 55/55,
  `/U` 51/51, `/K` 32/32 and `/H` 244/245 resolve to the home entity; `/M` 85 home + 62
  other. None resolves to the country of the letter (France, USA, Italy, Russia, England).
  So every one-character suffix is treated as an operating condition (the skill lists
  `/P /M /A /B`), which also fixes `/R` (US rover -> not Russia).
- `/LH` 773 home, 0 Norway (LH is a Norwegian prefix): treated as non-location.
- three or more letters without a digit (`/QRP /QRPP /LGT /BCN /JOTA /ANT`) are never
  locations; the only letters-only prefix of 3+ characters in the file is `NLD`.
- two-letter suffixes (`/YU`, `/OE`, `/UA`) and suffixes with a digit (`/KH6`, `/VE3`, `/4O`,
  `/W1`) are locations, per the skill.
- digit-only suffixes: dropped, per the skill (see Notes for the measured alternative).

## Result

### What changed
- `hamq/core/cty.py`: `DEFAULT_CTY_URL`, `DEFAULT_CTY_CSV_URL`, `Entity`, `CtyMatch`
  (`dxcc`, `dxcc_name`), `CtyDatabase` (`entities`, `warnings`, `from_text`, `from_files`,
  `lookup`, `__len__`), `clean_call`. Line-based tolerant parser; lookup dicts
  (exact call -> entry, prefix -> entry) built once; entry values precomputed with overrides.
  Lookup order: exact full call -> exact after dropping trailing non-location suffixes ->
  `/MM` `/AM` = `None` -> drop non-location suffixes -> location part (shorter part; on a
  tie a listed prefix, then the first part) -> longest prefix; fallback to the home call if
  the location part matches nothing; the home call must contain a digit.
- `tests/core/test_cty.py`: 64 test functions, 187 test cases.
- `tests/fixtures/cty/cty_excerpt.dat` (20 entities, 302 entries), `cty_excerpt.csv`,
  `README.md` (source, release and download dates, how it was cut, licence notice).
- `hamq/i18n/sr_Latn/core_cty.json`: 8 warning messages in Serbian (Latin).

### Commands and outcomes
- `python3 -m pytest -p no:cacheprovider tests/core/test_cty.py -q` -> 187 passed (Python 3.14).
- `docker run --rm -v <repo>:/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim sh -c
  "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_cty.py -q"`
  -> 187 passed (Python 3.9.25).
- `ruff check hamq/core/cty.py tests/core/test_cty.py` -> All checks passed;
  `ruff format --check` -> already formatted.
- Mutation check (scratch copy): 14 deliberate bugs (no lon flip, last-wins duplicates,
  single letters as countries, no `/MM` rule, longer part as location, no progressive exact
  match, no WAE table, no Turkey name, no digit rule, no zone validation, no `#` skipping,
  no home fallback, no warning cap, csv ignored) -> all 14 caught by the tests.

### Measurements on the full Big CTY (scratch script, best of 7)

| | Python 3.14 (host) | Python 3.9 (Docker) | target |
|---|---|---|---|
| `from_text(dat)` | 54 ms | 78 ms | < 500 ms |
| `from_text(dat, csv)` / `from_files(dat, csv)` | 55 / 56 ms | 87 / 83 ms | < 500 ms |
| 10 000 lookups (mixed: exact, prefix, portable, garbage) | 27 ms (2.7 us each) | 52 ms | < 300 ms |

Full file: 346 entities, 6 310 prefixes, 22 951 exact calls, 0 warnings. AD1C's README
checks pass on it: `VERSION` Rotuma Island, `OR4TN` Antarctica, `MR6TMS` Scotland,
`LW7DQQ/Y` Argentina ITU 16, `UI9XA` European Russia, `ZS85SARL` South Africa. Other files of
the release: contest `cty.dat` 0 warnings, `cty_wt.dat` / `cty_wt_mod.dat` 0 warnings; formats
that are not cty.dat (SwiftLog with an extra code field, CT `cqww.dat` with `$ ... $` lines,
WriteLog `wl_cty.dat`) still load all entities with capped warnings.

### Manual checks still needed
- None for this core module. Integration (download, cache, qso.py) belongs to other tasks.

## Notes
- Contract additions (additive, not written to `docs/ARCHITECTURE.md`; for the orchestrator):
  `Entity.dxcc_name: Optional[str] = None` (DXCC name the entity counts for: parent name for
  WAE-only, "Turkey" for TA; `CtyMatch.dxcc_name` falls back to `entity.name`),
  `CtyDatabase.warnings: list[str]` (translated), `CtyDatabase()` builds an empty database,
  `from_files` raises `OSError` for an unreadable cty.dat but only warns for an unreadable
  cty.csv.
- `utc_offset` keeps the cty.dat sign (local time = UTC - utc_offset, Serbia -1.0), as the
  skill defines the field; only the longitude is flipped. Worth a comment in the contract.
- Digit-only suffixes are dropped, as the skill says. Measured alternative: replacing the
  call-area digit (`UA9ABC/3` -> `UA3ABC`) matches AD1C's listed entity for 2 070 of the
  2 504 `CALL/<digit>` exceptions, dropping for 854 (misses: Russia EU/AS,
  Spain/Canary/Balearic, US/KL7/KH6). It also creates false rare entities (`KH6ABC/4` ->
  Midway, 28 cases in the list; `KP4ABC/1` -> Navassa), so it was not adopted without a
  decision; the exact calls in the Big CTY cover the known cases. Candidate improvement:
  apply the replacement only inside known groups (European / Asiatic Russia and
  Kaliningrad; Spain, Balearic, Canary Islands, Ceuta & Melilla).
- For `net/cty_download.py`: download both URLs, treat a download as valid only when
  `len(CtyDatabase.from_text(text)) > 0` (an HTML error page gives 0 entities), keep
  `cty.csv` next to `cty.dat` so DXCC codes are available.
- Outside scope: a directory `w/` appeared at the repository root during this run, holding a
  copy of `hamq/` and `tests/` (including `hamq/core/cty.py`). It is not from this task; it
  should be removed before committing so ruff / pytest do not pick up duplicate modules.
- `CHANGELOG.md` was not updated (outside this task's file scope in the parallel run).
