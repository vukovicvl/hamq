# M6-01: Translation engine (`hamq/core/i18n.py`) and the project-wide i18n guard

**Milestone:** M6
**Status:** done
**Skills:** pyqgis-plugin

## Goal
Replace the `tr()` stub with the real translation engine: English / Serbian Latin / Serbian
Cyrillic, language resolution from the QGIS or system locale, catalog loading that never
raises, a thread-safe lazily loaded module translator and Serbian Latin -> Cyrillic
transliteration that leaves technical text alone. Add the project-wide guard test that keeps
every translatable string of `hamq/` in the Serbian catalogs.

## Scope
- `hamq/core/i18n.py` (replaces the stub; `tr` / `tr_noop` stay the identity in English)
- `tests/core/test_i18n.py`
- `tests/core/test_i18n_catalog.py`
- `hamq/i18n/sr_Latn/core_i18n.json`
- `tasks/M6-01-i18n-core.md`

## Out of scope
- Language switch in the GUI (`hamq/gui/language.py`: `LanguageManager`, `qgis_ui_locale()`,
  menu and toolbar switch), `HamQSettings.language`, `events().languageChanged`.
- Catalogs of other modules (each module's author owns its file).
- `CHANGELOG.md`, `docs/ARCHITECTURE.md` (owned by the orchestrator in this parallel run).

## Checklist
- [x] `LANG_AUTO`, `LANG_EN`, `LANG_SR_LATN`, `LANG_SR_CYRL`; `LANGUAGES` with native names
      English, Srpski (latinica), Српски (ћирилица)
- [x] `resolve_language(setting, system_locale)`: explicit choice wins, `auto` / `None` / `''`
      use the locale; POSIX (`sr_RS.UTF-8@latin`), BCP 47 (`sr-Latn-RS`), Qt (`sr_Latn_RS`),
      Windows (`Serbian (Latin)_Serbia.1250`), `SR`, `C`, `en_US.UTF-8`, `None`, `''`; never `auto`
- [x] `is_serbian(language)`
- [x] `load_catalog(directory)`: every `*.json` merged in name order; broken file, non-object
      file and non-text entries skipped and recorded; never raises; `load_problems()` returns
      the problems of the latest load, translated when read
- [x] `Translator` (English identity, Serbian Latin with source fallback, Serbian Cyrillic
      transliterated and cached), module singleton with lazy, lock-guarded catalog loading on
      the first non-English translation; `get_translator`, `set_language`, `current_language`,
      `tr`, `tr_noop`
- [x] `latin_to_cyrillic`: all 30 letters, digraphs in every case form (also the single code
      points U+01C4..U+01CC), decomposed input, protection of placeholders, printf / Qt
      arguments, tags, entities, URLs, e-mail, paths, file names, identifiers, command-line
      arguments, tokens with digits / 2+ capitals / q w x y or other foreign letters,
      `PROTECTED_WORDS`; capitalized words transliterated; case endings after protected names
      transliterated (`QGIS-u` -> `QGIS-у`); known digraph exceptions documented (xfail tests)
- [x] Tests written first (`tests/core/test_i18n.py`), including edge cases
- [x] Guard `tests/core/test_i18n_catalog.py`, rules a-f, parametrized per source / catalog
      file with the relative path as test id, plus scanner self-tests
- [x] `hamq/i18n/sr_Latn/core_i18n.json` for the six strings of this module
- [x] Python 3.9 compatible, no `qgis` / `PyQt` imports, ruff clean

## Acceptance criteria
- [x] `pytest tests/core -q` passes (2314 passed, 4 xfailed)
- [x] `ruff check` and `ruff format --check` pass on the changed files
- [x] Task examples hold: `Uvezi ADIF fajl` -> `Увези ADIF фајл`, `Veza {call} na {band}` ->
      `Веза {call} на {band}`, `Preuzmi cty.dat sa https://www.country-files.com` keeps the file
      name and URL, `Lokator KN04ft` -> `Локатор KN04ft`, `Opseg 20m` -> `Опсег 20m`,
      `WSJT-X je povezan` -> `WSJT-X је повезан`, `Ljubljana` -> `Љубљана`, `NJEGOŠ` -> `ЊЕГОШ`,
      `Džak` -> `Џак`
- [x] Missing translations fall back to the English source; a broken catalog file never
      breaks loading
- [x] The guard passes for every file present at the end of this task and fails, under the
      owning file's test id, for each injected violation (mutation check below)

## Result

### What changed
- `hamq/core/i18n.py` (replaces the stub), public names:
  - contract: `LANG_AUTO`, `LANG_EN`, `LANG_SR_LATN`, `LANG_SR_CYRL`, `LANGUAGES`,
    `resolve_language`, `is_serbian`, `latin_to_cyrillic`, `load_catalog`, `Translator`
    (`language`, `set_language`, `translate`), `get_translator`, `set_language`,
    `current_language`, `tr`, `tr_noop`;
  - additions (see Notes, contract change requests): `PROTECTED_WORDS`, `language_name()`,
    `load_problems()`, keyword-only `Translator(..., *, loader=None)`.
- Language resolution: an explicit setting (`en`, `sr_Latn`, `sr_Cyrl`; case and `-`/`_`
  ignored) wins; `auto`, `None` or `''` use the locale; unknown settings give `en`. Serbian
  locales: script subtag (`Latn`/`Cyrl`) first, then the glibc / KDE modifier (`@latin`,
  `@ijekavianlatin`, `@cyrillic`), then the territory: `sr_ME` is Latin (CLDR; Qt reports
  `QLocale("sr_ME").script() == Latin`), everything else Cyrillic. `sr` and `srp` count as
  Serbian; `sh`, `hr`, `bs` do not.
- Catalog loading: `*.json` in name order, hidden files and non-files ignored, UTF-8 with
  optional BOM; unreadable / invalid / non-object files skipped; non-text values skipped;
  empty values left out (source shown); conflicting translations: the later file wins.
  Every problem is recorded as (English template, parameters) and translated only when
  `load_problems()` is called (no `tr()` during loading, so no lock re-entry).
- Translator: English returns the text; `sr_Latn` returns the catalog value or the source;
  `sr_Cyrl` transliterates the catalog value once (cache per source text) and returns an
  untranslated English source unchanged (never transliterated English). The module singleton
  is created on first use and reads `hamq/i18n/sr_Latn/` on the first Serbian translation,
  double-checked under a lock; a failing loader gives an empty catalog, never an exception.
- Transliteration rules are documented in the module docstring. Beyond the task list:
  `{{call}}` (a field shown as text) is kept; `/` between plain words is not a path
  (`Greška ulaza/izlaza` -> `Грешка улаза/излаза`, found in `core_hamlib.json`), but `/` next to a
  technical token keeps the whole token (`YU1AB/P`, `km/h`); in hyphen / `&` compounds with a
  technical part, capitalized parts stay (`Latin-1`, `Wi-Fi`) and lowercase parts are Serbian
  (`QGIS-u` -> `QGIS-у`, `WSJT-X-a` -> `WSJT-X-а`, `QSO-veza` -> `QSO-веза`, `20-ak` -> `20-ак`);
  all-caps words with č ć đ š ž are Serbian (`NJEGOŠ`), without them acronyms; unit symbols
  (`km`, `MHz`, `Hz`, `dB`, `ms`, ...) stay Latin as in Serbian orthography for SI symbols.
- `tests/core/test_i18n.py`: 308 tests + 4 strict xfail (the documented digraph exceptions
  `nadživeti`, `podžupan`, `injekcija`, `konjunkcija`). Every test runs with a fresh module
  translator (monkeypatched) so no language state leaks into other test modules.
- `tests/core/test_i18n_catalog.py`: the guard (141 tests with the files present now; the
  number grows with the files). Rules as requested, plus: `tr()` at import time is an error
  (the text would never follow a language switch); `tr_noop()` must get a literal (per the
  architecture rules); exactly one positional argument; `self.tr()` in a class without its own
  or an inherited HamQ `tr` is flagged as Qt's `QObject.tr`; `super().tr()` and the Qt markers
  (`QT_TR_NOOP`, `QT_TRANSLATE_NOOP`, ...) are flagged; `tr` imported under an alias is
  recognized; catalogs must be canonical (`json.dumps(..., ensure_ascii=False, indent=2,
  sort_keys=True) + "\n"`), without Cyrillic, and placeholders must survive transliteration.
- `hamq/i18n/sr_Latn/core_i18n.json`: `Auto (QGIS language)` -> `Automatski (jezik QGIS-a)`
  and the five catalog-loading messages.

### Commands and outcomes
- `python3 -m pytest -p no:cacheprovider tests/core/test_i18n.py -q` -> 308 passed, 4 xfailed.
- `python3 -m pytest -p no:cacheprovider tests/core/test_i18n_catalog.py -q` -> 141 passed
  (also with `HAMQ_STRICT_I18N=1`: no unused keys at the moment).
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 2314 passed, 4 xfailed.
- Docker `python:3.9-slim` (read-only mount): i18n tests -> 449 passed, 4 xfailed (Python 3.9.25).
- Docker `qgis/qgis:3.44-trixie` and `qgis/qgis:4.0-trixie`: i18n tests -> 449 passed,
  4 xfailed each (Python 3.13.5).
- Docker `camptocamp/qgis-server:3.34` (Python 3.10.12, no pytest): smoke script (resolve,
  switch, translate `core_i18n` and `core_hamlib` texts, `load_problems() == []`) -> ok.
- `ruff check` and `ruff format --check` on the three Python files -> clean.
- Guard mutation check (scratch copy): an injected GUI module with import-time `tr()`, an
  f-string, a missing text, `QCoreApplication.translate` and `self.tr()` without `tr`, and an
  injected catalog with a duplicate key, unsorted keys, a placeholder mismatch, a conflicting
  translation, a Cyrillic value, an unused key, plus an invalid JSON file -> every violation
  failed under the owning file's test id; the unused key only warned, and failed with
  `HAMQ_STRICT_I18N=1`.
- Implementation mutation check (scratch copy): 18 deliberate bugs (digraph case, no lock, no
  cache, `sr_ME`, transliterated fallbacks, no placeholder / printf protection, capitals rule,
  diacritic exception, dot rule, slash rule, compound rule, no NFC, unknown setting, merge
  order, accumulating problems, hidden files, option rule) -> all 18 caught.
- Worst case input (`"a." * 50000`): 14 ms after bounding the URL scheme length (the first
  version was quadratic: 0.18 s for 8000).
- Every value of the current catalogs (`core_adif`, `core_cty`, `core_hamlib`, `core_i18n`,
  `plugin`, `processing_provider`) was transliterated and reviewed; this found `Latin-1` being
  turned into `Латин-1` (fixed with the compound rule).

### Manual checks still needed
- None for this core module. The visible switch (menu, toolbar, settings) belongs to the GUI
  task; there, check that switching EN / SR / СР relabels actions without a restart.

## Notes
- Contract change requests (additive, not written to `docs/ARCHITECTURE.md`):
  - `PROTECTED_WORDS: frozenset[str]`: words kept in Latin (program and people names,
    unit symbols, keys, network terms), compared ignoring case. Authors whose Serbian texts
    contain other foreign words written like Serbian words (no capitals, digits or q w x y)
    should request additions; names in the About box (`Jim Reisert`, `Joe Taylor`,
    `WSJT Development Group`) are already there.
  - `language_name(language) -> str`: native name for `en` / `sr_Latn` / `sr_Cyrl`, translated
    `Auto (QGIS language)` for `auto`. The GUI should use it instead of adding the same key to
    its own catalog: the guard requires one translation per English text across all catalogs.
  - `load_problems() -> list[str]`: translated problems of the latest `load_catalog()` call.
    The module translator's lazy load is such a call, so the plugin should call it after the
    first Serbian translation (for example after applying the language) and log the result.
  - `Translator(catalog=None, language="en", *, loader=None)`: `loader` supplies the catalog
    lazily (used by the singleton).
  - Documented behaviour: `resolve_language(None or "", locale)` means `auto`; `set_language` /
    `Translator` accept codes in any case with `-` or `_`, anything else selects English;
    `is_serbian` also accepts Serbian locale names (`sr_RS`).
- For `gui/language.py` `qgis_ui_locale()` (checked on the host QGIS 4.2 / Qt6):
  `QgsApplication.locale()` returns only `'sr'` for `LANG=sr_RS.UTF-8`, `sr_RS@latin` and
  `sr_ME.UTF-8` (the script is lost); `QLocale.system()` ignores the glibc `@latin` modifier
  (reports Cyrillic for `sr_RS@latin`); `QLocale("sr_Latn_RS").name()` is `'sr_RS'` while
  `bcp47Name()` is `'sr-Latn'`; `QLocale("sr_ME")` is Latin. Suggested source: the QGIS
  `locale/userLocale` setting when `locale/overrideFlag` is true, otherwise
  `QLocale.system().bcp47Name()`, and on Linux the POSIX variables (`LC_ALL`, `LC_MESSAGES`,
  `LANG`) when they carry `@latin`. `resolve_language` accepts all of these spellings. The host
  QGIS install has no `qgis_sr*.qm`, so Serbian users often run QGIS itself in English;
  `auto` then follows the system locale only if the GUI passes it.
- Known transliteration limits (documented, out of scope): digraph pairs across a morpheme
  boundary (`nadživeti`, `podžupan`, `injekcija`, `konjunkcija`; xfail tests); all-caps words
  without č ć đ š ž stay Latin (`UPOZORENJE`); case endings glued to a protected name without a
  hyphen (`Hamliba`) are transliterated with the name.
- The guard scans only `hamq/**/*.py`, so the stray `w/` copy at the repository root (already
  reported in M4-01) does not affect it; it should still be removed before committing.
- `CHANGELOG.md` was not updated (outside this task's file scope in the parallel run).
