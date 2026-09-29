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
- [x] `decode_bytes`: UTF-8 (BOM removed), latin-1 fallback, never raises; CRLF kept (lengths count it); UTF-16 with BOM decoded too
- [x] `parse_document` / `parse_adi`: free header text, header fields, `<EOH>` optional, case-insensitive tags, type indicators ignored, text between tags ignored
- [x] last record without `<EOR>` kept + warning; zero-length fields kept as `""`; duplicate field: last wins + warning; field names uppercase, values stripped
- [x] `<` inside a value of correct length kept (`a<b`, even `<EOR>`); `<` in free header text is fine; CRLF
- [x] length past the end: take what is there + warning; too short / too long lengths: warning, later records parse; huge lengths and leading zeros handled
- [x] narrow byte-count rule (contract) with tests for character- and byte-counted Serbian names (`Đorđe Petrović`, `Miloš Šćekić`, Cyrillic) and for when it must NOT apply
- [x] joined files (second `<EOH>`), records before the first `<EOH>`, lengthless and invalid tags
- [x] `parse_latlon`, `parse_freq`, `parse_qso_datetime`, `dedup_key`, `format_record`, `format_document`, `read_adi`
- [x] round trip `parse(format(x)) == x` for 400 random documents (Serbian letters, Cyrillic, `<`, `>`, tag-like values)
- [x] warnings through `tr()` with `{placeholders}`; Serbian catalog `core_adif.json` (sorted, `ensure_ascii=False`, indent 2)
- [x] fixtures + `README.md`; 10 000-record performance test (`@pytest.mark.slow`)

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` and `ruff format --check` pass for the changed files
- [x] Python 3.9 (Docker `python:3.9-slim`) passes `tests/core/test_adif.py`
- [x] no `qgis` / `PyQt` import in `hamq/core/adif.py` (`tests/core/test_architecture.py`)
- [x] skill vectors: `S033 52.128` -> -33.8688, `W077 02.196` -> -77.0366, `N044 48.750` -> 44.8125
- [x] parsing 10 000 records < 1 s here (measured ~0.27 s; test asserts < 2 s)

## Result
**What changed**
- `hamq/core/adif.py` (new). Public API exactly as in the contract. `AdifDocument` fields
  have default factories (so `AdifDocument()` is an empty document). Behaviour worth knowing:
  - Header = everything before the first `<EOH>`. If an `<EOR>` tag comes before it (a file
    without header joined with one that has a header), the file has no header and the
    fields before a later `<EOH>` go into `header` (existing keys kept) with a warning.
  - Lengths are characters. Byte rule, implemented narrowly: used only when the LENGTH
    characters swallow a well-formed tag (`<NAME:n>`, `<EOR>`, `<EOH>`) or run past the end,
    and LENGTH UTF-8 bytes end on a character boundary, contain no well-formed tag and are
    followed only by whitespace before the next `<`, or by the end. Otherwise LENGTH
    characters are taken (as the skill says) with a warning when the value isn't followed by a tag.
  - LoTW tolerance (found while testing real files, see below): `value // comment` is not a
    length problem, and lengthless `APP_*` markers (`<APP_LoTW_EOF>`) are ignored silently.
    Other lengthless tags (`<CALL>YU1AB`) and invalid tags (`<NAME:x>`) are skipped with a warning.
  - Warnings are capped at 100 per document plus "Further warnings not listed: {count}"
    (same English text and Serbian translation as `core_cty.json`).
  - `read_adi` adds a warning when the file was not UTF-8 and was read as latin-1; an
    unreadable or missing file raises `OSError` (content problems never raise).
  - `parse_document` also accepts bytes (decoded with `decode_bytes`) and `None`.
  - `parse_latlon`: `XDDD MM.MMM`, 1 to 3 degree digits, lowercase hemisphere, decimal comma,
    minutes without decimals; `-0.0` is returned as `0.0`. `parse_freq`: `<= 0` is `None`;
    also accepts int/float. `parse_qso_datetime`: dates before 1930 (ADIF minimum) are `None`.
  - `format_document`: free-text line `HamQ ADIF export`, `ADIF_VER` 3.1.4, `PROGRAMID` HamQ,
    then the given header fields (they may override both), `<EOH>`, one record per line.
    Invalid field names (empty, spaces, `:`, `<`, `EOR`, `EOH`, non-ASCII) raise `ValueError`.
- `tests/core/test_adif.py` (new): 293 tests.
- `tests/fixtures/adif/`: `wsjtx_log.adi`, `n1mm.adi`, `log4om.adi`, `qrz_export.adi`,
  `lotw.adi` (extra: LoTW QSL download), `utf8_name.adi`, `utf8_bytes.adi`, `no_eoh.adi`,
  `missing_eor.adi`, `wrong_length.adi`, `latin1.adi`, `bom_crlf.adi`, `README.md` (what
  each file tests + a table of records for later `qso.py` tests), `.gitattributes`
  (`*.adi -text`, so git never converts line ends of these byte-exact files).
- `hamq/i18n/sr_Latn/core_adif.json` (new): 14 messages.

**Commands run**
- `python3 -m pytest -p no:cacheprovider tests/core/test_adif.py -q` -> 293 passed (about 1 s)
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 1865 passed (whole core suite at that moment)
- Docker `python:3.9-slim`: `pytest tests/core/test_adif.py tests/core/test_architecture.py` -> 336 passed
- `ruff check` / `ruff format --check` on `hamq/core/adif.py tests/core/test_adif.py` -> clean
- Timing: 10 000 records (14 fields) 0.27 s; with byte-counted names 0.30 s; 100 000 records 2.9 s (linear)
- Mutation check (scratch copy, 25 mutants of the key rules): all caught by the tests
- Real third-party files (not committed): test data of two open-source ADIF parsers
  (LoTW old and new format, QRZ, WSJT-X, JS8Call, xlog, ADIF spec sample; 767 records).
  After the LoTW tolerance, all parse without warnings except two genuinely malformed files
  (`<mycall>` without length; missing final `<EOR>`), which warn correctly. Values match the
  skill's reference parser wherever that parser is right.

**Manual checks still needed**
- None for this core module (no GUI). Import of real user logs happens in the *Import ADIF* task.

## Notes
- `CHANGELOG.md` not updated (outside this task's scope). Suggested line under `## Unreleased`:
  "ADIF parser: tolerant .adi reading (UTF-8/latin-1, byte-counted lengths, LoTW comments), writer, fixtures."
- Two LoTW tolerances go beyond the skill text; proposed for the adif skill and contract (see report).
- Known limit, per skill ("take LENGTH characters, do not try to be clever"): if a length is too
  long and the value swallows the `<EOR>`, two records merge (the duplicate-field warnings show it).
- Known limit of the contract's byte rule: a character-counted value that contains a
  well-formed tag after enough non-ASCII letters (e.g. `ĐĐĐĐĐ<EOR>`) is misread as byte-counted.
  Real logs don't contain such values.
- For `core/qso.py`: zero-length fields come back as `""` (treat as missing); ADIF 3.1.4 also
  allows 12-character locators (GridSquareExt); the README lists records for each qso.py case.
- Serbian users of old Windows loggers may have Windows-1250 files: they decode as latin-1
  (no crash, `Đ` shows as `Ð`), with the "read as Latin-1" warning. A cp1250 heuristic
  could be a later improvement.
