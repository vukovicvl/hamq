---
name: dxcc-cty
description: Resolving country / DXCC entity, continent, CQ and ITU zone from a callsign using the AD1C cty.dat file (and cty.csv for ADIF DXCC codes) - file format, override syntax, longest-prefix matching, callsign cleanup, WAE-only entities, download and caching. Read for hamq/core/cty.py and net/cty_download.py.
---

# DXCC lookup with cty.dat

Source: Big CTY by AD1C, https://www.country-files.com. Updated often.
Where this skill and `docs/ARCHITECTURE.md` differ, the contract and the code win.
Verified download URLs (`core/cty.py`): `DEFAULT_CTY_URL` =
`https://www.country-files.com/bigcty/cty.dat`, `DEFAULT_CTY_CSV_URL` =
`https://www.country-files.com/bigcty/cty.csv` (no page links the csv; it sits next to
cty.dat). cty.csv has one row per entity: primary prefix (with `*`), name, ADIF DXCC
code, continent, zones, position, UTC offset, entries. HamQ takes only the code
(column 3) from it.

**Do not commit cty.dat to the repo** (nor cty.csv); `scripts/package.py` never
packs them. The user downloads both with one click: HamQ offers it at the first start
(message bar hint), and *Download cty.dat* in the menu and *Download now* in the
settings refresh them. Cache in the QGIS profile folder: `settings.cty_cache_path()`
= `<QgsApplication.qgisSettingsDirPath()>/hamq/cty.dat`, cty.csv next to it. The
download date (UTC, ISO 8601) is stored in `hamq/cty_downloaded`.

Download (`net/cty_download.py`, `CtyManager.download()`): both files through
`QgsNetworkAccessManager` (QGIS proxy and SSL settings), never blocking; aborted after
30 s without data or for a file over 8 MB. A background thread checks them (cty.dat
at least 300 entities, cty.csv a DXCC code for at least as many); only then they
replace the cache (temporary files, `os.replace`). On any failure the old cache stays;
exactly one `downloadFinished(ok, message)` per download. `load_cached_cty()` reads
the cache without network and Qt objects (Processing algorithms, `QgsTask` workers).
QSOs already in the log get missing DXCC data from a new cty.dat through *Recalculate*.

## Format

Each entity starts with a header line of 8 colon-terminated fields:

```
Serbia:                   15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:
    YT,YU,=4O0A,=4O5W,=YT2A/LH,=YU1CA/LH;
```

| # | Field | Note |
|---|---|---|
| 1 | Country name | |
| 2 | CQ zone | |
| 3 | ITU zone | |
| 4 | Continent | 2 letters |
| 5 | Latitude | degrees, + North |
| 6 | Longitude | degrees, **+ West** (flip sign for QGIS) |
| 7 | UTC offset | hours, local time = UTC - offset |
| 8 | Primary prefix | leading `*` = not a DXCC entity (WAE only) |

Followed by one or more indented lines of comma-separated prefixes,
the last ending with `;`. The header above is the real one (Big CTY of 15 September
2026, 346 entities); the entry list is trimmed. Always read real data. The primary
prefix is an identifier, not an entry (46 entities, e.g. `UA`, do not list it). A
callsign listed twice: the first occurrence wins (AD1C: read top to bottom).

WAE-only entities (`*IT9` Sicily, `*TA1` European Turkey, `*4U1V`, `*GM/s`, `*IG9`,
`*JW/b`) count for their parent DXCC entity: `CtyMatch.dxcc` is the parent's code
(cty.csv gives it to the WAE row) and `dxcc_name` the parent's name (Sicily ->
`Italy`; DXCC 390 is `Turkey`, also for cty.dat's "Asiatic Turkey"). `core/qso.py`
stores that name as `country`; position, continent and zones stay the WAE entity's.
Without cty.csv there are no codes and a fixed table gives the parent names.

## Prefix entries and overrides

- `YU` plain prefix
- `=YU1ABC` exact callsign match (takes priority over any prefix)
- Suffix overrides on an entry, any combination:
  - `(nn)` CQ zone
  - `[nn]` ITU zone
  - `<lat/lon>` coordinates (lon + West)
  - `{XX}` continent
  - `~n~` UTC offset

Example: `=VE3XYZ(4)[3]` -> exact call, CQ 4, ITU 3.

Parse with a regex per entry:
`^(=?)([A-Z0-9/]+)(?:\((\d+)\))?(?:\[(\d+)\])?(?:<([-\d.]+)/([-\d.]+)>)?(?:\{(\w+)\})?(?:~([-\d.]+)~)?$`
(order in the file can vary, so a tolerant approach is to strip each
override type with its own regex). `core/cty.py` accepts all five in any order; the
Big CTY itself uses only `(cq)` and `[itu]`. A broken entity or entry is skipped with
a warning.

## Lookup algorithm

1. Clean the callsign: uppercase, remove all whitespace. Longer than 64 characters,
   or characters other than `A-Z 0-9 /`: `None`.
2. Exact match on `=` entries: the whole call, then after dropping non-location
   suffixes (step 3) from the end, then without non-location parts after the first
   (`9A70DP/P/KA` -> `9A70DP/KA`). If found, return with overrides applied.
3. Handle portable designators:
   - `/P`, `/M`, `/MM`, `/AM`, `/QRP`, `/A`, `/B`, and digit-only suffixes
     like `/1` are not location prefixes. Drop them. (`/MM` and `/AM` mean
     no DXCC entity; return `None` for maritime/aeronautical mobile.) HamQ also
     drops every single character, the activities `/LH /LS /LT /FF /FD /YL`
     (lighthouse and lightship, WWFF, field day, YL operator: not Norway, Argentina,
     France, Latvia) and
     letters-only suffixes of 3 or more (`/LGT`, `/JOTA`). Two-letter suffixes and
     ones with a digit are locations (`/YU`, `/KH6`).
   - `PREFIX/CALL` or `CALL/PREFIX`: the part that looks like a prefix
     (shorter one, or the one matching a known prefix) decides the entity.
     Example: `YU/DL1ABC` -> Serbia, `DL1ABC/YU` -> Serbia. On equal length the part
     with the longer prefix match wins (`K1A/KL7` -> Alaska), then the first part. A
     location that matches no prefix leaves the home call to decide (its exact entry
     first: `4U1VIC/XX` -> Vienna Intl Ctr).
   - A home call without a digit is no callsign (`YUGO`, `N/A`): `None`.
4. Longest-prefix match: try `call[:n]` for n from len down to 1 against a
   dict of prefixes. First hit wins.
5. Apply per-entry overrides, else entity defaults.
6. KG4 rule (cty.dat cannot express it, WSJT-X has it too): only `KG4` plus two
   letters (`KG4AB`) is Guantanamo Bay; `KG4` plus one or three letters (`KG4A`,
   `KG4ABC`) is a US call, with the United States defaults.

Build the lookup dict once when loading (`dict[str, Entry]`), not per query.

## Test cases

Pick these from the real cty.dat in a fixture excerpt and assert entity name,
continent and zones:

| Call | Expected entity |
|---|---|
| YU1AB | Serbia |
| YT0A | Serbia |
| 9A1GS | Croatia |
| W1AW | United States |
| VK2XYZ | Australia |
| DL1ABC/P | Fed. Rep. of Germany |
| YU/DL1ABC | Serbia |
| DL1ABC/MM | None |
| KH6XX | Hawaii |
| an exact `=` call from the fixture | its override zones (`AD1C`, `=AD1C(4)[7]`: United States, CQ 4, ITU 7) |
| IT9ABC | Sicily (WAE; DXCC 248, `dxcc_name` Italy) |
| KG4AB / KG4ABC | Guantanamo Bay / United States |
| DL1ABC/LH | Fed. Rep. of Germany |

Keep the fixture small (10 to 20 entities) and commit only that excerpt,
with a comment crediting AD1C. It is `tests/fixtures/cty/cty_excerpt.dat` and
`cty_excerpt.csv`: 20 of the 346 entities of the Big CTY of 15 September 2026, entry
lists trimmed, CRLF and a `#` credit header; its `README.md` gives the source, the
dates and the licence notice.
