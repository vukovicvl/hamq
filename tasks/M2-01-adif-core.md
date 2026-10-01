# M2-01: ADIF core parser, writer and fixtures

**Milestone:** M2
**Status:** done
**Skills:** adif

## Goal
`hamq/core/adif.py` per the contract section "core/adif.py": a tolerant .adi
parser (header, records, warnings) plus field helpers and a writer, with
realistic fixtures from several loggers for this and later tasks.

## Scope
Files that may be created or changed:
- `hamq/core/adif.py`
- `tests/core/test_adif.py`
- `tests/fixtures/adif/*` (`.adi` files, `README.md`, `.gitattributes`)
- `hamq/i18n/sr_Latn/core_adif.json`
- `tasks/M2-01-adif-core.md`

## Out of scope
- Record-level validation (missing CALL, bad date, locator rules): `core/qso.py`.
- Processing *Import ADIF*, GeoPackage writing, deduplication against the database.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md` (owned by the orchestrator).

## Checklist
- [x] `decode_bytes`: UTF-8 (BOM removed), latin-1 fallback, never raises for bytes-like input; CRLF kept (lengths count it); UTF-16 with BOM decoded too; `TypeError` for non-bytes-like input (programmer error)
- [x] `parse_document` / `parse_adi`: free header text, header fields, `<EOH>` optional, case-insensitive tags, type indicators ignored, text between tags ignored
- [x] last record without `<EOR>` kept + warning; zero-length fields kept as `""`; duplicate field: last wins + warning; field names uppercase, values stripped
- [x] `<` inside a value of correct length kept (`a<b`, even `<EOR>`); `<` in free header text is fine; CRLF
- [x] length past the end: take what is there + warning; too short / too long lengths: warning, later records parse; huge lengths, 4+ digit lengths and leading zeros handled
- [x] narrow byte-count rule (contract) with tests for character- and byte-counted Serbian names (`Đorđe Petrović`, `Miloš Šćekić`, Cyrillic) and for when it must NOT apply
- [x] header boundary: `<EOH>` inside values (header and records), only `<EOH>` inside a header value, `<EOH:0>`, `<EOR>` mentioned in header text, QSO fields before `<EOH>`
- [x] joined files (second `<EOH>`, record without `<EOR>` before it), records before the first `<EOH>`, lengthless and invalid tags
- [x] ADIF user-defined field names with spaces/punctuation (xlog `<Seq (S):3>001`) read, guarded against free text; the writer accepts them
- [x] `parse_latlon`, `parse_freq`, `parse_qso_datetime`, `dedup_key`, `format_record`, `format_document`, `read_adi`
- [x] round trip `parse(format(x)) == x` for 400 random documents (Serbian letters, Cyrillic, `<`, `>`, tag-like values, user-defined names, tag-like header values)
- [x] warnings through `tr()` with `{placeholders}`; Serbian catalog `core_adif.json` (sorted, `ensure_ascii=False`, indent 2)
- [x] fixtures + `README.md` (own-station data consistent); 10 000-record performance test (`@pytest.mark.slow`); linear-time tests for pathological input

## Acceptance criteria
- [x] `pytest tests/core -q` passes for this task's tests (`tests/core/test_adif.py`: 406 passed, 2 xfailed). The whole `tests/core` run at the end had failures only in `tests/core/test_i18n_catalog.py` for `hamq/gui/dock.py`, `hamq/gui/language.py`, `hamq/net/cty_download.py`, files other agents were editing at that moment (see Notes); an earlier full run passed (2821 passed, 8 xfailed)
- [x] `ruff check` and `ruff format --check` pass for `hamq/core/adif.py tests/core/test_adif.py`
- [x] Python 3.9 (Docker `python:3.9-slim`) passes `tests/core/test_adif.py` and `tests/core/test_architecture.py` (479 passed, 4 xfailed)
- [x] no `qgis` / `PyQt` import in `hamq/core/adif.py` (`tests/core/test_architecture.py`)
- [x] skill vectors: `S033 52.128` -> -33.8688, `W077 02.196` -> -77.0366, `N044 48.750` -> 44.8125
- [x] parsing 10 000 records < 1 s here (measured 0.37-0.39 s; test asserts < 2 s)

## Result
**What changed (current behaviour of `hamq/core/adif.py`)**
- Public API exactly as in the contract. `AdifDocument` fields have default factories.
- Decoding: UTF-8 (BOM removed), UTF-16 with BOM, else the whole file as latin-1 (contract).
  `read_adi` and `parse_document(bytes)` share one helper, so both add the first warning
  "File is not valid UTF-8, it was read as Latin-1 (ISO 8859-1)". `decode_bytes` /
  `parse_document` accept `str`, `None` and bytes-like objects; other types (an `int`, a
  list) raise `TypeError` (before: `decode_bytes(3)` gave three NUL characters,
  `parse_document(10**10)` tried a 10 GB allocation). `read_adi` raises `OSError` for a file
  it cannot open; content problems never raise.
- Lengths are characters. Narrow byte rule (contract): used only when LENGTH characters
  swallow a well-formed tag or run past the end, and LENGTH UTF-8 bytes end on a character
  boundary, contain no plain tag (`<NAME:n>`, `<EOR>`, `<EOH>`) and end cleanly. Text that
  only looks like a user-defined tag (`<Hvala lepo:3>`) inside a byte-counted value is data.
- Header: ends at the first `<EOH>` that is a tag. An `<EOH>` inside a value of the declared
  length (ending cleanly) is data if a later `<EOH>` ends the header, or if no header field
  came before it (a record value in a file without header). If header fields came first and
  no later `<EOH>` ends the header (none at all, or records come before it), that `<EOH>`
  ends the header and the value is cut there with "runs past the end of the header".
  No header when records come first: a QSO field (`CALL`, `QSO_DATE`, `TIME_ON`) before the
  `<EOH>`, or an `<EOR>` after a field that is no header field; an `<EOR>` before that
  (`Records end with <EOR>`) is header text. `<EOH:0>` ends a header too.
- Joined files: a later `<EOH>` after records puts the fields before it into `header`
  (existing keys kept) with a warning; if those fields hold a QSO field, a record lost its
  `<EOR>`: it is kept as a record (header fields to the header) with its own warning.
- Field names: plain names (`[A-Za-z0-9_][A-Za-z0-9_-]*`) and ADIF user-defined names with
  spaces and other characters (anything but `, : < > { }` and control characters, no space
  at either end), uppercased: xlog's `<Seq (S):3>001` gives `SEQ (S)`. A user-defined name
  needs a length and is read only when its value fits: it holds no tag and ends cleanly (as
  characters or as UTF-8 bytes); otherwise it is text (`<at 10:15>` in free text), reported
  between records as an invalid tag. The checks are linear (sorted tag positions + bisect;
  the checked values do not overlap). `format_record` accepts every name the parser reads.
- Tolerances: LoTW `value // comment` is not a length problem; lengthless `APP_*` markers
  (`<APP_LoTW_EOF>`) are ignored silently; other lengthless tags (`<NAME>x`) and invalid tags
  (`<NAME:x>`, `<CALL :5>`) are skipped with a warning; text right after a value (also a `<`
  that starts no tag, `tnx 73<3`) gives a length warning. Warnings capped at 100 per
  document plus "Further warnings not listed: {count}" (same text/translation as `core_cty.json`).
- Field helpers and writer as before: `parse_latlon` (`XDDD MM.MMM`, 1-3 degree digits,
  lowercase hemisphere, decimal comma, `-0.0` -> `0.0`), `parse_freq` (`<= 0` -> `None`, also
  int/float), `parse_qso_datetime` (dates before 1930 -> `None`), `dedup_key` (contract format),
  `format_document` (`HamQ ADIF export`, `ADIF_VER` 3.1.4, `PROGRAMID` HamQ, header overrides).
- `tests/core/test_adif.py`: 408 tests (406 pass, 2 strict `xfail` for requested contract changes).
- `tests/fixtures/adif/` (15 files + `README.md` + `.gitattributes` `*.adi -text`):
  `wsjtx_log.adi`, `n1mm.adi`, `log4om.adi`, `qrz_export.adi`, `lotw.adi`, `utf8_name.adi`,
  `utf8_bytes.adi`, `no_eoh.adi`, `missing_eor.adi`, `wrong_length.adi`, `latin1.adi`,
  `cp1250.adi` (Windows-1250), `bom_crlf.adi`, and new in the fix round `xlog.adi`
  (user-defined `Seq (S)`/`Seq (R)` fields, `<e-mail>` in header text) and
  `mixed_encoding.adi` (UTF-8 log joined with a Latin-1 log).
- `hamq/i18n/sr_Latn/core_adif.json`: 15 messages (unchanged in the fix round).

**Fix round after review (2026-09-30)**; each regression test failed before its fix
(42 tests of the final file fail against the reviewed `adif.py`; the other new ones guard
the new code):
1. (must) Only `<EOH>` inside a header value: the header is no longer lost; the value is cut
   at the `<EOH>` with a warning. Broader than the suggested patch: any header field read
   before counts (not only the current field), and a later `<EOH>` of a joined file no
   longer hides it (the header is read a second time with the first `<EOH>` as its end when
   records follow). Tests: `test_header_value_too_long_for_the_eoh_is_cut_at_the_eoh`,
   `test_only_eoh_inside_a_value_after_a_header_field_ends_the_header` (6 fields),
   `test_eoh_inside_a_header_value_followed_by_records_ends_the_header`,
   `test_eoh_inside_a_record_value_of_a_file_joined_with_one_that_has_a_header`.
2. (should) User-defined field names: read (see above). Differences from the suggested fix,
   each with a test: the "does the byte reading swallow a tag" check stays strict, as
   suggested, but the trigger for trying the byte reading also sees user-defined tags
   (`<NAME:7>Đorđe <Seq (S):3>001` is read right); and a guard against free text: without it
   `Exported <at 10:15>` swallowed `<ADIF_VER:5>` and `note <at 10:15> x` a whole QSO (found
   while testing, never released). Real xlog file: 24 warnings -> 0, 12 QSOs keep `SEQ (S)`/`SEQ (R)`.
3. (should) Task file rewritten (this file); cp1250 `xfail` reason now points to the contract
   change request below, which exists.
4. (should) Mixed encodings: contract-level, so no code change. Added `mixed_encoding.adi`, a
   test that pins what the contract guarantees (no QSO lost, the Latin-1 part right, warning),
   a strict `xfail` test with the wanted result, and contract change request 1 (Notes), checked
   with a prototype.
5. (should) Test gaps: tests for each surviving mutant (`<b>` in a byte-length value, lengths of
   4-6 digits, CALL-less record before the first `<EOH>`, header fields ended by `<EOH:0>`).
6. (nit) `<EOR>` in header text: an `<EOR>` means "records first" only after a field that is no
   header field (broader than suggested: `ADIF_VER ... Records end with <EOR>` also works).
7. (nit) `TypeError` for non-bytes-like input; `parse_document(bytes)` adds the Latin-1 warning.
8. (nit) Fixture positions made consistent: own station `MY_GRIDSQUARE` `KN04fs` -> `KN04ft`
   (where `N044 48.750 E020 27.672` lies) in 4 files, QRZ `VK3XYZ` `LAT`/`LON` moved inside its
   `QF22oc` (it was on the edge of `QF22md`) and its `DISTANCE` 15820 -> 15429 km (the great-circle
   value). `test_fixture_positions_agree_with_their_locators` checks all fixtures.
- Also: user-defined tags are checked in linear time (tests with a time ratio; a first version
  was quadratic on crafted input). The literal U+FEFF characters in the source became `﻿`.

**Commands run (final state)**
- `python3 -m pytest -p no:cacheprovider tests/core/test_adif.py -q` -> 408 tests: 406 passed, 2 xfailed
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 2821 passed, 8 xfailed (run after the
  main fixes); the last runs failed only in `test_i18n_catalog.py` for `hamq/gui/dock.py`,
  `hamq/gui/language.py`, `hamq/net/cty_download.py` (1 to 4 failures while other agents were editing
  those files; the 7 catalog checks for `adif.py` pass)
- Docker `python:3.9-slim` (Python 3.9.25, `:ro` mount): `tests/core/test_adif.py tests/core/test_architecture.py`
  -> 479 passed, 4 xfailed; `tests/core` -> the same state as on the host (only the `test_i18n_catalog.py`
  failures above)
- `ruff check` and `ruff format --check` on `hamq/core/adif.py tests/core/test_adif.py` -> clean
- Mutation check (scratch copy, 69 mutants: the reviewer's list adapted to the current code plus
  mutants for each new rule) -> all killed
- Fuzzing: the reviewer's scripts (3000 header/free-text documents; 20 000 mixed char/byte-counted
  documents; 4 x 20 000 byte-rule documents) -> 0 wrong; own user-defined-name fuzzer
  (3 x 20 000 documents with byte-/char-counted user fields, tag-like header and gap text, 3000
  round trips) -> 0 wrong
- Pathological input (1 MB of `<a b:2000000000>`, crafted lengths, 2M `<`, lengthless tags):
  linear, about 0.2-0.5 s per MB (the slowest, lengthless tags, as before the fix round)
- Timing: 10 000 records (14 fields) 0.37-0.39 s (before the fix round 0.33-0.34 s on the same machine)
- Real third-party files (not committed; 16 files, 767 records: LoTW, QRZ, WSJT-X, JS8Call,
  xlog, ADIF spec sample): unchanged except xlog (24 -> 0 warnings); only two genuinely
  malformed files warn (`<mycall>` without length; missing final `<EOR>`)

**Manual checks still needed**
- None for this core module (no GUI). Import of real user logs happens in the *Import ADIF* task.

## Notes
- `CHANGELOG.md` not updated (outside this task's scope). Suggested line under `## Unreleased`:
  "ADIF parser: tolerant .adi reading (UTF-8/latin-1, byte-counted lengths, LoTW comments,
  user-defined field names, joined files), writer, fixtures."
- Contract change requests (for the orchestrator; not made here):
  1. `decode_bytes`: decode UTF-8 and fall back only for the bytes that are not UTF-8 (a codecs
     error handler), instead of reading the whole file as latin-1; keep the warning. Optionally
     use Windows-1250 instead of latin-1 for those bytes when any of them is 0x80-0x9F (C1
     control codes in latin-1, `Š š Ž ž ...` in Windows-1250); that needs its own warning text
     ("... read as Windows-1250"). Trade-off: a Western Windows-1252 file with typographic quotes
     would then read some accented letters as Central European ones (`è` as `č`). Checked with a
     prototype on the 15 fixtures and 16 real files: per-byte latin-1 changes only
     `mixed_encoding.adi` (all names right, 6 -> 2 warnings); with the Windows-1250 option also
     `cp1250.adi` becomes right; everything else (including `latin1.adi`) is unchanged. Tests
     `test_fixture_mixed_encoding_utf8_part_is_kept` and `test_fixture_cp1250_serbian_letters`
     are strict `xfail`s that turn green with the change (then remove the markers).
  2. Contract text for behaviour now implemented: user-defined field names with spaces and
     punctuation (`SEQ (S)`) appear as record keys, so `adif_extra` JSON keys may contain them;
     `format_record` accepts them; `decode_bytes` / `parse_document` raise `TypeError` for
     arguments that are not text/bytes-like/`None` (programmer error).
  3. Carried over from the first report: the two LoTW tolerances (`// comment` after a value,
     lengthless `APP_*` markers) belong in the adif skill/contract; `read_adi` raises `OSError`
     for a file it cannot open; `core/qso.py` should treat `""` (zero-length field) as missing.
- Known limits:
  - Byte rule (contract): a character-counted value with a well-formed tag after enough non-ASCII
    letters (`ĐĐĐĐĐ<EOR>`) is misread as byte-counted (`test_byte_rule_known_limit`).
  - A value under a user-defined name may not hold a tag (`<My Note:13><CALL:5>YU9ZZ`): such a
    tag is reported as invalid and skipped (`test_user_defined_field_limits`); plain names keep
    "take LENGTH characters".
  - Free text that is exactly a user-defined field whose value fits (`<Note to self:3>abc`) is
    read as a field, as `<NOTE:3>abc` always was; nothing else is lost.
  - Per skill ("take LENGTH characters, do not try to be clever"): a too long length that
    swallows `<EOR>` merges two records (duplicate-field warnings show it).
- For `core/qso.py`: zero-length fields come back as `""`; ADIF 3.1.4 also allows 12-character
  locators (GridSquareExt); the fixture README lists records for each qso.py case, and positions
  and locators in the fixtures agree (tested).
- Outside scope: at the end of this round `tests/core/test_i18n_catalog.py` failed for
  `hamq/gui/dock.py`, `hamq/gui/language.py` and `hamq/net/cty_download.py` (strings not yet in
  their catalogs); those files were being edited by other agents.
