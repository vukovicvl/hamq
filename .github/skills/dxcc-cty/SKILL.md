---
name: dxcc-cty
description: Resolving country / DXCC entity, continent, CQ and ITU zone from a callsign using the AD1C cty.dat file - file format, override syntax, longest-prefix matching, callsign cleanup, download and caching. Read for hamq/core/cty.py and net/cty_download.py.
---

# DXCC lookup with cty.dat

Source: Big CTY by AD1C, https://www.country-files.com. Updated often.
**Do not commit cty.dat to the repo.** Download on first use, cache in the
QGIS profile folder (`QgsApplication.qgisSettingsDirPath() + "hamq/cty.dat"`),
offer a "Refresh cty.dat" action. Store the download date in settings.

## Format

Each entity starts with a header line of 8 colon-terminated fields:

```
Serbia:                   15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:
    4N,4O,YT,YU,=YU1ABC/P;
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
the last ending with `;`. The values above are illustrative; always read real data.

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
override type with its own regex).

## Lookup algorithm

1. Clean the callsign: uppercase, strip spaces.
2. Exact match on `=` entries. If found, return with overrides applied.
3. Handle portable designators:
   - `/P`, `/M`, `/MM`, `/AM`, `/QRP`, `/A`, `/B`, and digit-only suffixes
     like `/1` are not location prefixes. Drop them. (`/MM` and `/AM` mean
     no DXCC entity; return `None` for maritime/aeronautical mobile.)
   - `PREFIX/CALL` or `CALL/PREFIX`: the part that looks like a prefix
     (shorter one, or the one matching a known prefix) decides the entity.
     Example: `YU/DL1ABC` -> Serbia, `DL1ABC/YU` -> Serbia.
4. Longest-prefix match: try `call[:n]` for n from len down to 1 against a
   dict of prefixes. First hit wins.
5. Apply per-entry overrides, else entity defaults.

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
| an exact `=` call from the fixture | its override zones |

Keep the fixture small (10 to 20 entities) and commit only that excerpt,
with a comment crediting AD1C.
