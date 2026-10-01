# ADIF test fixtures

Small, realistic `.adi` files for `hamq/core/adif.py` and later `core/qso.py`
tests. Callsigns are fictional (suffix `XYZ`, own station `YU1QQ` in
`KN04ft`, Beograd: `MY_LAT`/`MY_LON` `N044 48.750`/`E020 27.672` = 44.8125,
20.4612). Where a record has both, `LAT`/`LON` lie in its `GRIDSQUARE` and
`MY_LAT`/`MY_LON` in its `MY_GRIDSQUARE` (checked by a test), so later tests
may use either. Field lengths matter byte for byte, so these files are
marked `-text` in `.gitattributes` (no line-end conversion) and must be read
as bytes (`read_adi`), never with text-mode newline translation.

## Files

| File | Style / encoding | What it tests |
|---|---|---|
| `wsjtx_log.adi` | WSJT-X log file: `WSJT-X ADIF Export<eoh>`, lowercase tags, one record per line | header text without fields; FT8 and FT4 (`mode MFSK` + `submode FT4`); `station_callsign`, `my_gridsquare`, `freq`, `band`, `rst_*`; zero-length `gridsquare` for a /MM station |
| `n1mm.adi` | N1MM Logger+ contest export (CQ WPX CW), starts directly with `<ADIF_VER>`, uppercase | header fields without free text; uppercase bands (`20M`); `CONTEST_ID`, `STX`/`SRX`; `APP_N1MM_*` fields (one empty); no locators (position must come from cty.dat) |
| `log4om.adi` | Log4OM 2 export with header text and `CREATED_TIMESTAMP` | `DXCC`, `COUNTRY`, `CONT`, `CQZ`, `ITUZ`, `LAT`/`LON`, `MY_LAT`/`MY_LON`, `MY_GRIDSQUARE`, a 10-character locator |
| `qrz_export.adi` | QRZ.com logbook download: several free-text header lines, lowercase, one field per line, fields sorted | `app_qrzlog_*` fields, `email` of length 0, `LAT`/`LON` of the other station, `MY_*` fields, an invalid locator typed by hand |
| `utf8_name.adi` | UTF-8, lengths counted in characters (ADIF-correct) | Serbian names and places (`Đorđe Petrović`, `Miloš Šćekić`, `Čačak`, `Užice`), a Cyrillic comment |
| `utf8_bytes.adi` | same records, lengths counted in UTF-8 bytes | the byte-count tolerance rule: parses to the same records as `utf8_name.adi`, with 5 warnings (fields whose extra bytes are covered by whitespace are read correctly without a warning) |
| `lotw.adi` | ARRL LoTW QSL download: report text, `APP_LoTW_*` fields, `// comment` after values, ends with the lengthless `<APP_LoTW_EOF>` | comments after values and the EOF marker give no warnings; uppercase bands, `MODE=FT4`; DXCC data of the other station |
| `no_eoh.adi` | sloppy export without header and without `<EOH>` | whole file is records; record-level problems for `qso.py` |
| `missing_eor.adi` | header, last record has no `<EOR>` | last record kept with a warning |
| `wrong_length.adi` | hand-edited log | record 2: `NAME` declared too short (`Alek`); record 3: `NAME` declared too long, swallows `<QTH:7>` (`Ivan <QTH:`, QTH lost); records 4 and 5 parse normally; 2 warnings |
| `latin1.adi` | ISO 8859-1 bytes (not valid UTF-8) | latin-1 fallback (`Jürgen Müller`, `José Muñoz`, `François`), plus the "read as Latin-1" warning |
| `cp1250.adi` | Windows-1250 bytes (code page of older Serbian / Central European Windows loggers), CRLF | Serbian names and places (`Miloš Šćekić`, `Đorđe Petrović`, `Žarko Čučković`, `Šabac`, `Čačak`, `Požarevac`); `Š š Ž ž` are bytes `0x8A 0x9A 0x8E 0x9E`, C1 control codes in Latin-1. The contract's Latin-1 fallback reads the QSOs but not these letters; a strict `xfail` test holds the wanted result until the contract change request (task M2-01, Notes) is decided |
| `mixed_encoding.adi` | a UTF-8 log (LF) joined with a Latin-1 log (CRLF), as `cat a.adi b.adi` makes it | second header (joined files warning); the contract reads the whole file as Latin-1, so the Latin-1 part (`Jürgen Müller`, `François`) is right and the UTF-8 part (`Đorđe Petrović`, `Čačak`, `Miloš Šćekić`, `Niš`) is garbled with length warnings, no QSO is lost; a strict `xfail` test holds the wanted result (see `cp1250.adi`) |
| `xlog.adi` | modeled on a real xlog 2.0.11 export (author line with a placeholder address): header text with an `<e-mail>` in angle brackets, `ADIF_VER` 2.2.7, one record per line and `<EOR>` on its own line; `FREQ` as `21` | user-defined field names with a space and parentheses (`<Seq (S):3>001`, `<Seq (R):4>1043`, contest serial numbers) read as fields `SEQ (S)` and `SEQ (R)`; no warnings |
| `bom_crlf.adi` | UTF-8 with BOM, CRLF line ends | BOM removed; CRLF between tags; a `NOTES` value with CRLF inside (counted as 2 characters) |

## Records for later tests (`core/qso.py`, statistics, map)

| Case | File, record (1-based) |
|---|---|
| Beograd -> Sydney with `LAT`/`LON` (`S033 52.128`, `E151 12.558`) and `MY_LAT`/`MY_LON` (`N044 48.750`, `E020 27.672`) | `log4om.adi` 1 (`VK2XYZ`, `QF56od`) |
| 10-character locator `JN75xt74oj` (cut to 8 with a warning) | `log4om.adi` 2 (`9A3XYZ`) |
| Invalid locator `ZZ00` | `no_eoh.adi` 5 (`YU1XYZ`) |
| Invalid locator `JO6` (3 characters) | `qrz_export.adi` 3 (`DL1XYZ`) |
| Record without `CALL` | `no_eoh.adi` 2 |
| Bad date `20260230` | `no_eoh.adi` 3 (`YT1XYZ`) |
| Bad time `2460` | `no_eoh.adi` 4 (`YU1XYZ`) |
| /MM call, empty locator | `wsjtx_log.adi` 6 (`OH2XYZ/MM`) |
| IT9 (Sicily) | `wsjtx_log.adi` 1, `n1mm.adi` 3, `log4om.adi` 5 (`IT9XYZ`) |
| KH6 (Hawaii) | `wsjtx_log.adi` 3, `log4om.adi` 3 (`KH6XYZ`) |
| VK | `log4om.adi` 1 (`VK2XYZ`), `qrz_export.adi` 1 (`VK3XYZ`, `QF22oc`, `LAT`/`LON` inside it) |
| W | `wsjtx_log.adi` 2 (`W1XYZ`), `n1mm.adi` 2 (`W3XYZ`), `qrz_export.adi` 2 (`W5XYZ`) |
| 9A | `wsjtx_log.adi` 4 (`9A2XYZ`, FT4), `n1mm.adi` 1, `log4om.adi` 2 |
| YU | `wsjtx_log.adi` 5 (`YU7XYZ`, FT4), `n1mm.adi` 4, `log4om.adi` 4, `utf8_name.adi` |
| FT4 as `MODE=MFSK SUBMODE=FT4` | `wsjtx_log.adi` 4 and 5 |
| Band only as uppercase `20M` / `40M` / `15M` | `n1mm.adi` |
| Same QSO from two sources (re-import / dedup): `lotw.adi` 1 and 2 confirm `wsjtx_log.adi` 2 and 4; FT4 is `MODE=FT4` in LoTW and `MODE=MFSK SUBMODE=FT4` in WSJT-X, same dedup key | `lotw.adi`, `wsjtx_log.adi` |
| QSO with DXCC data already filled in by the logger | `log4om.adi`, `qrz_export.adi` 1 and 2, `lotw.adi` |
| User-defined fields (`SEQ (S)`, `SEQ (R)`) for `adif_extra` | `xlog.adi` 1 and 2 |

## Regenerating

The files were generated by a throw-away script that computes the lengths;
edit them only with a tool that keeps bytes and line ends as they are, and
update the lengths by hand (in characters, or in bytes for `utf8_bytes.adi`).
