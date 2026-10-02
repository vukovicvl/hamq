---
name: adif
description: ADIF .adi log format - tag syntax, header, records, the fields HamQ uses, date/time and LAT/LON formats, band derivation, tolerant parsing rules (byte-length tolerance), the duplicate key, import limits and a reference parser. Read for hamq/core/adif.py, core/qso.py and the Import ADIF algorithm.
---

# ADIF (.adi)

Specification: https://adif.org (current 3.1.x). HamQ v0.1.0 reads `.adi` only (no ADX,
no ADIF export yet; `format_record` / `format_document` serve tests and
`scripts/make_demo_log.py`). Where this skill and `docs/ARCHITECTURE.md` differ, the
contract and the code win.

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

- Read the file as bytes. Decode as UTF-8 (a BOM is removed, a UTF-16 BOM decodes as
  UTF-16), else the whole file as latin-1, with a warning. Keep line ends: lengths
  count CRLF. Known limit: a Windows-1250 log is read as latin-1, so `Š Ž Č Ć Đ` come
  out wrong (strict `xfail` tests hold the wanted result).
- Length is counted in characters of the decoded text. Narrow byte-length tolerance
  (contract, `docs/ARCHITECTURE.md` "core/adif.py"): when LENGTH characters would
  swallow a well-formed following tag (`<NAME:n>`, `<EOR>`, `<EOH>`) or run past the
  end, and LENGTH UTF-8 bytes end on a character boundary, hold no tag and end
  cleanly, use the byte reading and add a warning (some loggers count bytes; Serbian
  names like `Đorđe` expose it). Otherwise still take LENGTH characters (what is left
  at the end of the file); nothing cleverer. Warn when a value is followed by
  anything but whitespace, a tag or a `//` comment.
- LoTW: `<MY_STATE:2>CO // Colorado` is no length problem, and lengthless `APP_*`
  markers (`<APP_LoTW_EOF>`) are ignored silently. Other lengthless or invalid tags
  (`<NAME>x`, `<NAME:x>`, `<CALL :5>`) are skipped with a warning.
- User-defined names may hold spaces (xlog `<Seq (S):3>001` -> field `SEQ (S)`); such a
  tag is read only when its value fits, else it is free text.
- The header ends at the first `<EOH>` tag (an `<EOH>` inside a value of the declared
  length is data). QSO fields (`CALL`, `QSO_DATE`, `TIME_ON`) before it mean there is
  no header. A later `<EOH>` (joined files) is handled with a warning, no QSO lost.
- A repeated field: the last value wins, with a warning. A zero-length field is `""`;
  `core/qso.py` treats it as missing.
- Unknown fields: keep them in `adif_extra` (JSON), do not drop.
- A record without `CALL` is skipped with a warning.
- A record with bad date/time (also a date before 1930) is skipped with a warning.
- Never raise on a single bad record. Return `(records, warnings)`. Warnings are
  translated (`tr()`), at most 100, then "Further warnings not listed: {count}".

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

This is only the core idea. `hamq/core/adif.py` is the implementation: it adds the
rules above in linear time. Do not replace it with this sketch.

## Fields HamQ uses

| Field | Format | Notes |
|---|---|---|
| CALL | string | required, uppercase |
| QSO_DATE | YYYYMMDD | required, UTC |
| TIME_ON | HHMM or HHMMSS | required, UTC |
| BAND | e.g. `20m`, `70cm` | lowercase; derive from FREQ if missing (see below) |
| FREQ | MHz, decimal | `14,074` accepted; 0 or negative is invalid |
| MODE, SUBMODE | enum | FT4 is `MODE=MFSK SUBMODE=FT4` in ADIF 3.1; stored uppercased as logged |
| GRIDSQUARE | 2 to 8 chars | other station; 10 chars cut to 8 (warning); invalid kept as logged, not used for the position |
| MY_GRIDSQUARE | | my station, fallback to settings; without MY_LAT/MY_LON a coarser prefix of my settings locator (`KN04` for `KN04ft`, the WSJT-X "My Grid") is refined to it |
| LAT, LON | `XDDD MM.MMM` | e.g. `N044 48.750`, `E020 27.672`; kept in `adif_extra` |
| MY_LAT, MY_LON | `XDDD MM.MMM` | my position for this QSO (path origin); kept in `adif_extra` |
| RST_SENT, RST_RCVD | string | |
| DXCC | int | ADIF entity code (0-999, 0 = no entity), if logger filled it |
| COUNTRY, CONT, CQZ, ITUZ | | if present, prefer over cty.dat; invalid values (CQZ 1-40, ITUZ 1-90, CONT one of the 7) are filled from cty.dat |
| STATION_CALLSIGN, OPERATOR | | kept in `adif_extra` only |

## LAT/LON format

`XDDD MM.MMM`: hemisphere letter, 3-digit degrees, minutes with 3 decimals.
`S033 52.128` -> -33.8688. `W077 02.196` -> -77.0366.

`parse_latlon` also takes 1-2 degree digits, a lowercase letter and a decimal comma;
minutes >= 60, latitude > 90 or longitude > 180 give `None`. It cannot know the field,
so `core/qso.py` requires `N`/`S` for LAT and `E`/`W` for LON, uses only a complete
pair and ignores `0/0` (a placeholder some programs write), each with a warning; the
same for MY_LAT/MY_LON.

## Band from frequency (MHz)

`core/bands.py` has the whole ADIF 3.1 band list (2190m to submm, edges included);
the common bands:

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

A `BAND` that is no ADIF band takes the band of `FREQ` (warning), or is kept as logged
(warning) when `FREQ` gives none. A `FREQ` outside every band without `BAND`: band
unknown, with a warning.

## Position priority

1. LAT/LON present (a valid pair) -> `loc_source = latlon`
2. GRIDSQUARE present -> center of cell -> `loc_source = grid`. Exception: a
   2-character locator (a 20 x 10 degree field) yields to the cty.dat position when
   that lies inside the field; otherwise the field centre is used, with a warning.
3. cty.dat entity lat/lon (entry overrides applied) -> `loc_source = cty`
4. none -> keep the QSO without geometry, count in warnings

Path origin: MY_LAT/MY_LON > MY_GRIDSQUARE > my locator in the settings > none (no
distance, bearing or path). `my_gridsquare` = the record's value, else the 6-character
locator of a usable MY_LAT/MY_LON, else the settings locator.

## Dedup key

`f"{CALL}|{QSO_DATE}{TIME_ON[:4]}|{BAND}|{MODE}"`. Minute precision, because
some loggers round seconds. Call uppercased, band lowercased. MODE is
`modes.dedup_mode(MODE, SUBMODE)`, one value however a logger wrote the contact:

- a mode whose ADIF submodes are only variants of it (SSB, PSK, JT65, JT9, JT4, CW,
  RTTY, OLIVIA, HELL, DOMINO, THOR, QRA64, ...) gives the family of MODE, else of
  SUBMODE: `SSB`, `SSB`+`USB`/`LSB` and `MODE=USB` -> `SSB`; `PSK`+`PSK31` and
  `MODE=PSK31` -> `PSK`; `JT65`+`JT65B` and `MODE=JT65B` -> `JT65`;
- MFSK and DIGITALVOICE are umbrella modes whose submodes are modes of their own:
  they keep the submode (`MFSK`+`FT4` and `MODE=FT4` -> `FT4`, `DIGITALVOICE`+`C4FM` ->
  `C4FM`); every other mode gives `display_mode` (SUBMODE, else MODE: `FT8`).

Statistics, the panel and `qso_path.mode` show `display_mode`, not the dedup mode.
Importing a file again adds nothing, and the LoTW confirmations in `lotw.adi` (FT4 as
`MODE=FT4`) have the keys of the same QSOs in `wsjtx_log.adi` (`MFSK`/`FT4`).

## Import ADIF (`hamq:import_adif`)

- Refused before reading: anything that is not a regular file, a file above
  `MAX_SIZE_MB` (advanced parameter, default 200; reading takes about ten times the
  file size in memory) and one with more than `RECORDS_PER_MB = 10_000` `<EOR>` tags
  per MB of that limit.
- `source` is `adif:<file name>`. With my locator set, a record without MY_GRIDSQUARE
  and without a usable MY_LAT/MY_LON gets `APP_HAMQ_STATION_GRID` = `Y` in `adif_extra`
  (`gpkg.STATION_GRID_KEY`): its `my_gridsquare` came from the settings, so
  *Recalculate* moves it to a changed locator. Live WSJT-X and manual QSOs get no mark.

## Test fixtures (`tests/fixtures/adif`, described in its README)

Bytes matter: `*.adi -text` in `.gitattributes`; read them as bytes (`read_adi`).

- Loggers: `wsjtx_log.adi` (`WSJT-X ADIF Export<eoh>`: header text without fields,
  lowercase tags, FT4 as `MFSK`/`FT4`), `n1mm.adi`, `log4om.adi`, `qrz_export.adi`,
  `lotw.adi` (`// comments`, `<APP_LoTW_EOF>`), `xlog.adi` (`<Seq (S):3>`)
- UTF-8 names: `utf8_name.adi` (`<NAME:5>Đorđe`, characters), `utf8_bytes.adi`
  (`<NAME:7>Đorđe`, bytes: the tolerance rule)
- Encodings: `latin1.adi`, `cp1250.adi`, `mixed_encoding.adi`, `bom_crlf.adi`
- Structure: `no_eoh.adi` (no `<EOH>`), `missing_eor.adi` (last record without
  `<EOR>`), `wrong_length.adi` (lengths too short and too long)
