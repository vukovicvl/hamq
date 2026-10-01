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
- [x] Review round 2: KG4 rule (Guantanamo Bay only for KG4 + two letters), activity
      suffixes `/FF /FD /LT /LS /YL`, 64-character limit against quadratic input,
      order-independent equal-length split, exact candidates tried before the `/MM` rule,
      tests that kill the reviewer's surviving mutants, Guantanamo Bay added to the fixture
- [x] Review round 3: warning when a given cty.csv leaves entities without a DXCC code,
      leading BOM ignored by `from_text`, exact home call used when the location part
      matches no prefix, tests for the range checks, header continent case, csv code range
      and repeated suffix removal, fixture back to 20 entities (Japan dropped), Result and
      Notes refreshed

## Acceptance criteria
- [x] `python3 -m pytest tests/core/test_cty.py -q` passes (host Python 3.14 and Docker
      `python:3.9-slim`)
- [x] `ruff check` and `ruff format --check` pass for the new files
- [x] The skill's test calls give the correct entity, continent and zones
- [x] Full Big CTY: load < 0.5 s, 10 000 lookups < 0.3 s
- [x] No `qgis` / `PyQt` import in `hamq/core/cty.py`

## Findings: the real files (downloaded 2026-09-29, links re-checked 2026-09-30)

### URLs
- The direct link `https://www.country-files.com/bigcty/cty.dat` is on the WSJT-X page
  (<https://www.country-files.com/contest/wsjt-x/>). The Big CTY page
  (<https://www.country-files.com/big-cty/>) does not link the file: its only link into
  `bigcty/` is `bigcty/backup/exceptions.htm`. The release post
  (<https://www.country-files.com/big-cty-15-september-2026/>) links the release zip
  `bigcty/download/2026/bigcty-20260915.zip`. No page links `bigcty/cty.csv`; it sits next
  to `cty.dat` on the server and was verified by its content.
- Both URLs answer 200 (`application/octet-stream`, Last-Modified 15 Sep 2026, 341 435 and
  293 942 bytes) and are byte-identical to `cty.dat` / `cty.csv` inside the release zip. The
  content is the release of 15 September 2026 (`=VER20260915` in Canada; `=VERSION`
  resolves to Rotuma Island, as announced).
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
  the 1-letter ones are F G I K M N R U W; 64 have 5 characters, e.g. `RI1AN(29)[69]` in
  Antarctica). No prefix contains `/`; 525 exact calls have two or more `/`
  (`=9A/DJ1KW/LH`, `=VE2/G3ZAY/P[4]`).
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
  ending with `;`. Codes match the ADIF enumeration (1..522; Serbia 296, Germany 230, USA
  291, Italy 248, Croatia 497, Montenegro 514, Bosnia 501, Canada 1, Australia 150, Japan 339,
  Hawaii 110, Alaska 6, Austria 206, Scotland 279, Turkey 390); no two DXCC rows share a code.
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

### Suffix rules and what AD1C's exception list can show
For every exact entry of the form `CALL/SUFFIX` in the Big CTY, the listed entity was
compared with the home call's prefix entity and the suffix's prefix entity:
- single letters: `/D` 1049 of 1049, `/F` 119/119, `/W` 118/118, `/N` 69/69, `/I` 55/55,
  `/U` 51/51, `/K` 32/32 and `/H` 244/245 are listed under the home entity; `/M` 85 home +
  62 other. `/LH` 773 home, 0 Norway.
- Caveat (round-3 review): the list only holds calls that a location rule gets wrong. A call
  whose suffix really is a location needs no entry, so these counts show that the
  operating-condition meaning occurs, not how often a suffix letter really means a country.
  The single-letter and `/YL /LS /LT` rules are therefore decisions, listed under Notes.
- three or more letters without a digit (`/QRP /QRPP /LGT /BCN /JOTA /ANT`) are never
  locations; the only letters-only prefix of 3+ characters in the file is `NLD`.
- two-letter suffixes (`/YU`, `/OE`, `/UA`) and suffixes with a digit (`/KH6`, `/VE3`, `/4O`,
  `/W1`) are locations, per the skill.
- digit-only suffixes: dropped, per the skill (see Notes for the measured alternative).
- WSJT-X (`Radio::effective_prefix`) ignores only the suffixes matching
  `[0-9AMPQR]|QRP|F[DF]|[AM]M|L[HT]|LGT`; every other suffix shorter than the call is a
  location there (`/F`, `/D`, `/YL`, `/LS`).

## Result

### What changed (state after review round 3)
- `hamq/core/cty.py`: `DEFAULT_CTY_URL`, `DEFAULT_CTY_CSV_URL`, `Entity`, `CtyMatch`
  (`dxcc`, `dxcc_name`), `CtyDatabase` (`entities`, `warnings`, `from_text`, `from_files`,
  `lookup`, `__len__`), `clean_call`. Line-based tolerant parser. The lookup dicts (exact
  call -> entry, prefix -> entry) are built once, with the entry values and their overrides
  precomputed. A leading byte order mark is ignored. `warnings` lists summaries first
  ("cty.dat contains no entities", or "cty.csv: entities without a DXCC code: N" when a
  given cty.csv leaves entities without a code). After them come at most 50 details (bad
  header, entry, csv row, missing `;`, stray text) and "Further warnings not listed: N".
- `lookup()` order as coded:
  1. `clean_call` (uppercase, all whitespace removed). Text longer than 64 characters or
     with characters other than `A-Z 0-9 /` gives `None`; empty parts (`//`, a leading or
     trailing `/`) are dropped.
  2. Exact `=` entries: the whole call; then after dropping non-location suffixes from the
     end one at a time (`IT9ACJ/I/BN/P/QRP` -> `IT9ACJ/I/BN`); then with every non-location
     part after the first removed, also from the middle (`9A70DP/P/KA` -> `9A70DP/KA`,
     `II0PN/P/MM` -> `II0PN/MM`).
  3. `/MM` or `/AM` in any part after the first -> `None`.
  4. Non-location suffixes are dropped: digits only, any single character,
     `FD FF LH LS LT YL`, and letters only with three or more characters (`QRP`, `JOTA`).
  5. One part left: the home call. Two or more: of the first two parts the shorter is the
     location. On equal length the part with the longer prefix match is the location; if
     both match equally long (or neither matches), the first part is.
  6. A home call without a digit gives `None` (`YUGO`, `N/A`).
  7. The location's longest prefix decides, with that entry's overrides. If the location
     matches no prefix (or there is none), the home call decides: its exact entry
     (`4U1VIC/XX` -> Vienna Intl Ctr), else its longest prefix, with the KG4 rule:
     `KG4` + one or three letters (`KG4A`, `KG4ABC`) is a US call (United States defaults,
     `matched == "K"`); only `KG4` + two letters is Guantanamo Bay.
- `tests/core/test_cty.py`: 92 test functions, 319 test cases.
- `tests/fixtures/cty/cty_excerpt.dat` (20 entities, 281 entries: 132 exact calls and 149
  prefixes), `cty_excerpt.csv` (20 rows), `README.md` (source, release and download dates,
  how it was cut, licence notice).
- `hamq/i18n/sr_Latn/core_cty.json`: 9 warning messages in Serbian (Latin); the new one is
  "cty.csv: entiteti bez DXCC broja: {count}".

### Round 3: what was done for each review finding
- Stale Result / Notes (should): refreshed. This covers the counts, the commands, the lookup
  order as coded and the source of the direct link. The note about a `w/` directory is
  removed (it no longer exists).
- Fixture had 21 entities (should): Japan dropped, back to 20 (the skill allows 10 to 20).
  No test depended on Japan alone: Australia and Hawaii cover large east and west
  longitudes, Asiatic Turkey covers AS, and `JA1ABC` was not in the skill's table. The KG4
  tests keep running on the real Guantanamo Bay / United States / Hawaii entries. The
  excerpt was re-checked against the real files (headers, entries on their original lines,
  csv columns 1-9, CRLF): 0 problems.
- cty.csv leaving entities without codes gave no warning (should): reproduced
  (`from_text(dat, "")` gave 346 entities without a code and `warnings == []`). Now one
  summary warning gives the count and is never capped. Full file: empty csv 346, first 40 %
  162, first 300 rows 46 (the reviewer's figures). A WAE entity without its own row takes
  the parent's code and is not counted. The regression tests (empty, blank line,
  comment-only, another file's rows, cut mid-row, 60 bad rows) failed before the fix.
- Range checks, header continent case and csv code range untested (should): confirmed with
  the reviewer's mutants (all survived). New tests: headers with CQ 0/41, ITU 0/91,
  latitude ±95, longitude (+W) ±200 and `inf`, UTC offset ±24 and ±30, and a header without
  its last `:` are rejected (one warning with the line). The limits (latitude ±90,
  longitude ±180, CQ 1/40, ITU 1/90, UTC offset -14/12) are accepted, and `eu` loads as
  `EU`. 17 bad overrides are rejected with a warning, among them `<95.00/0.00>`, `~30~` and
  non-ASCII digits. Overrides at the limits are accepted. csv codes 1/522/999 are accepted;
  0, 1000, -296, 296.0 and full-width digits are rejected.
- Repeated suffix removal untested (should): `IT9ACJ/I/BN/P/QRP` -> exact `IT9ACJ/I/BN`
  added; kills the `while` -> `if` mutant.
- Exact home call ignored in the fallback (nit): fixed. `4U1VIC/XX` gives Vienna Intl Ctr,
  `AD1C/XX` CQ 4 / ITU 7, `KG4CAN/XX` Hawaii and `4O0A/XX` Serbia (tests failed before the
  fix). Full file, HEAD vs now, 247 804 calls: 32 515 differ, every one because the location
  part matches no prefix and the home call is exact. No other lookup changed.
- BOM (nit): `from_text` strips a leading U+FEFF from both texts (before, `"﻿Serbia"`
  became the entity name, and a BOM before a `#` line gave a spurious warning). Tests for
  `from_text` (failed before) and for `from_files` with a BOM before the fixture's `#` line.
- Also covered with cheap tests (surviving mutants the reviewer's scratch run found):
  - the `JW/b` table entry (Bear Island counts for Svalbard, not Norway);
  - a 5-character prefix beating shorter ones (`RI1AN(29)[69]` against `R`);
  - an ambiguous csv code, where the parent comes from the table;
  - a WAE code taken from the parent row.

### Commands and outcomes (2026-09-30, after the round-3 changes)
- `python3 -m pytest -p no:cacheprovider tests/core/test_cty.py -q` -> 319 passed
  (Python 3.14.4).
- `docker run --rm -v <repo>:/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim sh -c
  "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_cty.py -q"`
  -> 319 passed (Python 3.9.25). With `tests/core/test_architecture.py` and
  `tests/core/test_i18n_catalog.py` added: 529 passed, 2 xfailed. Both xfails are the
  `from __future__` check for `hamq/__init__.py` and `hamq/core/__init__.py`, which belong
  to other owners.
- `python3 -m pytest -p no:cacheprovider tests/core -q` (whole core suite, host) -> 2700
  passed, 8 xfailed.
- `ruff check hamq/core/cty.py tests/core/test_cty.py` -> All checks passed;
  `ruff format --check` -> 2 files already formatted.
- The new warning was checked end to end with `core.i18n`: sr_Latn
  "cty.csv: entiteti bez DXCC broja: 20", sr_Cyrl "cty.csv: ентитети без DXCC броја: 20".
- Mutation runs (scratch copies, never the repo):
  - The reviewer's round-3 set: 22 of 27 killed. The 5 survivors:
    - 3 equivalent: `utf-8-sig` -> `utf-8` (from_text strips the BOM now), `text = text`
      and `body = body`;
    - `us_default_first_wae`, reachable only with two non-WAE entities whose primary prefix
      is `K`;
    - `split_uses_three`, calls with three or more location-like parts (see Notes).
  - The round-2 set: 10 of 10 killed.
  - 19 new mutants for this round's code: 18 killed. The survivor, the removed
    `if not slash` check in `<lat/lon>`, is equivalent because `float("")` raises anyway.
- Fuzzing (the reviewer's script, 3 seeds × 4 000 mutated fixture + csv inputs): 0
  exceptions; every match was in range; at most 52 warnings.

### Measurements on the full Big CTY (reviewer's bench script, best of 5)

| | Python 3.14.4 (host) | Python 3.9.25 (Docker) | target |
|---|---|---|---|
| `from_text(dat, csv)` | 64 ms | 90 ms | < 500 ms |
| `from_files(dat, csv)` | 67 ms | 93 ms | < 500 ms |
| 10 000 lookups: 30 % exact calls, 30 % prefix calls, 20 % `PREFIX/CALL/P`, 20 % garbage and special cases | 35 ms | 59 ms | < 300 ms |
| 10 000 lookups of 64-character worst cases | 119 ms | 196 ms | - |

Full file with the current code: 346 entities, 6 310 prefixes, 22 951 exact calls,
0 warnings. AD1C's README checks pass on it:
- `VERSION` Rotuma Island;
- `OR4TN` Antarctica;
- `MR6TMS` Scotland;
- `LW7DQQ/Y` Argentina, ITU 16;
- `UI9XA` European Russia;
- `ZS85SARL` South Africa.

The skill's table also passes, and so do the six WAE parents with their csv codes.

Other files of both release zips, loaded again with the current code:
- 0 warnings: contest `cty.dat` (also with its `cty.csv`), `cty_wt.dat`, `cty_wt_foc.dat`,
  `cty_wt_mod.dat`, `cty_rus.dat`, the continent files and `wf1b.dat`.
- Formats that are not cty.dat still load all their entities, with capped warnings:
  `swiftlog_cty.dat`, `cqww.dat` / `arrl.dat` (`$ ... $` lines) and `wl_cty.dat`.
- `country.dat` and `wpxloc.dat` give 0 entities and "cty.dat contains no entities".

### Manual checks still needed
- None for this core module. Integration (download, cache, qso.py) belongs to other tasks.

## Notes
- Contract additions (additive, not written to `docs/ARCHITECTURE.md`; for the orchestrator):
  - `Entity.dxcc_name: Optional[str] = None`: the DXCC name the entity counts for (the
    parent's name for WAE-only entities, "Turkey" for TA). `CtyMatch.dxcc_name` falls back to
    `entity.name`.
  - `CtyDatabase.warnings: list[str]` (translated; summaries first, see Result).
  - `CtyDatabase()` builds an empty database.
  - `from_files` raises `OSError` for an unreadable cty.dat but only warns for an unreadable
    cty.csv.
  - `from_text` ignores a leading BOM.
- `utc_offset` keeps the cty.dat sign (local time = UTC - utc_offset, Serbia -1.0), as the
  skill defines the field; only the longitude is flipped. Worth a comment in the contract.
- Decisions for the orchestrator. The behaviour is documented and tested but not changed in
  round 3; each alternative is a small change in `_is_modifier` / `_MODIFIER_SUFFIXES` plus
  tests.
  1. Single-letter suffixes are never locations (`DL1ABC/F` -> Germany, `W1AW/R` is not
     Russia). This goes beyond the skill (which names `/P /M /A /B`) and differs from
     WSJT-X (only `A M P Q R` and digits are ignored, so `/F` is France there). The exception
     list cannot settle how often a letter means a country (see the caveat under Findings).
     Alternative: WSJT-X's set.
  2. Digit-only suffixes are dropped, as the skill says. This is wrong for `EA1ABC/8` (Spain,
     should be Canary Islands), `UA9ABC/3` (Asiatic, should be European Russia) and
     `KH6ABC/4` (Hawaii, should be United States) unless the Big CTY lists the call.
     Measured alternative: replacing the call-area digit (`UA9ABC/3` -> `UA3ABC`) matches
     AD1C's listed entity for 2 070 of the 2 504 `CALL/<digit>` exceptions, dropping the
     digit for 854 (misses: Russia EU/AS, Spain/Canary/Balearic, US/KL7/KH6). It also creates
     false rare entities (`KH6ABC/4` -> Midway, 28 cases in the list; `KP4ABC/1` ->
     Navassa). Candidate: replace only inside known groups (European / Asiatic Russia and
     Kaliningrad; Spain, Balearic, Canary Islands, Ceuta & Melilla).
  3. `/YL`, `/LS` and `/LT` (with `/LH /FF /FD`) are activity suffixes, never locations
     (`DL1ABC/YL` -> Germany, not Latvia). WSJT-X agrees for `LH LT FF FD` but treats `LS`
     and `YL` as locations. The same caveat applies.
  4. With three or more location-like parts only the first two are used (`DL1ABC/OE/XX` ->
     Austria). This is unspecified by the skill and not pinned by a test (the surviving
     `split_uses_three` mutant); real exact entries with three parts are matched exactly
     before this rule.
- For `net/cty_download.py`:
  - Download both URLs.
  - Treat a download as valid only when `len(CtyDatabase.from_text(text)) > 0`: an HTML
    error page gives 0 entities.
  - Keep `cty.csv` next to `cty.dat` so DXCC codes are available.
  - A cty.csv that leaves entities without a code now shows up in `warnings`, and as
    `any(e.dxcc is None for e in db.entities)`: a truncated csv can be rejected or
    downloaded again.
- `CHANGELOG.md` was not updated (outside this task's file scope in the parallel run).
