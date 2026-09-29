---
name: adif
description: ADIF .adi log format - tag syntax, header, records, the fields HamQ uses, date/time and LAT/LON formats, band derivation, tolerant parsing rules and a reference parser. Read for hamq/core/adif.py and the Import ADIF algorithm.
---

# ADIF (.adi)

Specification: https://adif.org (current 3.1.x). HamQ reads `.adi` only in MVP.

## Syntax

```
optional free text header
<ADIF_VER:5>3.1.4
<PROGRAMID:6>WSJT-X
<EOH>
<CALL:5>YU1AB <QSO_DATE:8>20260915 <TIME_ON:6>184500 <BAND:3>20m
<MODE:3>FT8 <FREQ:9>14.075123 <GRIDSQUARE:4>KN04 <RST_SENT:3>-10
<RST_RCVD:3>-12 <EOR>
```

- Tag: `<NAME:LENGTH>` or `<NAME:LENGTH:TYPE>` followed by exactly LENGTH characters of data.
- Names are case-insensitive. Normalize to uppercase.
- Anything between tags is ignored (spaces, newlines, comments).
- Header is everything before `<EOH>`. If the file starts with `<`, there may be no header text, but `<EOH>` can still appear. If there is no `<EOH>` at all, treat the whole file as records.
- `<EOR>` ends a record. `<EOH>` and `<EOR>` have no length.

## Tolerant parsing rules (real files break the spec)

- Read the file as bytes. Decode as UTF-8, fall back to latin-1 on error.
- Length is counted in characters of the decoded text. If the value would
  run past a following `<` that clearly starts a new tag, still take LENGTH
  characters; do not try to be clever. Log a warning if the next non-space
  character after the value is not `<` or end of file.
- Unknown fields: keep them in `adif_extra` (JSON), do not drop.
- A record without `CALL` is skipped with a warning.
- A record with bad date/time is skipped with a warning.
- Never raise on a single bad record. Return `(records, warnings)`.

## Reference parser

```python
import re
TAG = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+))?(?::[A-Za-z])?>")

def parse_adi(text: str):
    records, warnings, cur = [], [], {}
    pos = 0
    m = re.search(r"<eoh>", text, re.I)
    if m:
        pos = m.end()
    while True:
        m = TAG.search(text, pos)
        if not m:
            break
        name = m.group(1).upper()
        if name == "EOR":
            if cur:
                records.append(cur)
            cur = {}
            pos = m.end()
            continue
        if name == "EOH":
            pos = m.end()
            continue
        n = int(m.group(2) or 0)
        value = text[m.end(): m.end() + n]
        cur[name] = value.strip()
        pos = m.end() + n
    if cur:
        warnings.append("last record has no <EOR>, kept")
        records.append(cur)
    return records, warnings
```

## Fields HamQ uses

| Field | Format | Notes |
|---|---|---|
| CALL | string | required, uppercase |
| QSO_DATE | YYYYMMDD | required, UTC |
| TIME_ON | HHMM or HHMMSS | required, UTC |
| BAND | e.g. `20m`, `70cm` | lowercase; derive from FREQ if missing |
| FREQ | MHz, decimal | |
| MODE, SUBMODE | enum | FT4 is `MODE=MFSK SUBMODE=FT4` in ADIF 3.1 |
| GRIDSQUARE | 4 to 8 chars | other station |
| MY_GRIDSQUARE | | my station, fallback to settings |
| LAT, LON | `XDDD MM.MMM` | e.g. `N044 48.750`, `E020 27.672` |
| RST_SENT, RST_RCVD | string | |
| DXCC | int | ADIF entity code, if logger filled it |
| COUNTRY, CONT, CQZ, ITUZ | | if present, prefer over cty.dat |
| STATION_CALLSIGN, OPERATOR | | |

## LAT/LON format

`XDDD MM.MMM`: hemisphere letter, 3-digit degrees, minutes with 3 decimals.
`S033 52.128` -> -33.8688. `W077 02.196` -> -77.0366.

## Band from frequency (MHz)

| Band | Range |
|---|---|
| 160m | 1.8-2.0 |
| 80m | 3.5-4.0 |
| 60m | 5.06-5.45 |
| 40m | 7.0-7.3 |
| 30m | 10.1-10.15 |
| 20m | 14.0-14.35 |
| 17m | 18.068-18.168 |
| 15m | 21.0-21.45 |
| 12m | 24.89-24.99 |
| 10m | 28.0-29.7 |
| 6m | 50-54 |
| 4m | 70-71 |
| 2m | 144-148 |
| 70cm | 420-450 |
| 23cm | 1240-1300 |

## Position priority

1. LAT/LON present -> `loc_source = latlon`
2. GRIDSQUARE present -> center of cell -> `loc_source = grid`
3. cty.dat entity lat/lon -> `loc_source = cty`
4. none -> keep the QSO without geometry, count in warnings

## Dedup key

`f"{CALL}|{QSO_DATE}{TIME_ON[:4]}|{BAND}|{MODE}"`. Minute precision, because
some loggers round seconds.

## Test fixtures to have

- WSJT-X `wsjtx_log.adi` (no header text, just `<EOH>`)
- N1MM, Log4OM, QRZ.com export
- File with UTF-8 name (`<NAME:6>Đorđe`)
- File with no `<EOH>`
- Record missing `<EOR>` at the end
- Wrong length on one field
