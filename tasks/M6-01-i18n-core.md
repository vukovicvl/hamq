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

Review fix round (2026-09-30), each with a regression test that failed before the fix:
- [x] should: a case ending after a file name / technical token is Serbian (`cty.dat-a` ->
      `cty.dat-а`, `hamq.gpkg-u` -> `hamq.gpkg-у`)
- [x] should: guard rule e catches `QgsApplication.translate`, aliased imports,
      `<App>.instance().translate`, `qApp`, `QtCore.QT_TRANSLATE_NOOP`
- [x] nit: `tr(("Yes", "No")[flag])` (an indexed literal container) fails rule a
- [x] nit: the guard selects catalog files exactly as the runtime loader (shared helper)
- [x] nit: `load_problems()` never changes what it reports (the lazy load keeps its problems apart)
- [x] nit: unclosed `<!--` no longer takes quadratic time
- [x] nit: one unit convention (`m`, `s`, `h`, `min` Latin where they are units; `cm`, `mm`,
      `kg` and the missing key names protected)
- [x] nit: Serbian words joined by `/` or `+` to a technical token are transliterated
- [x] nit: BCP 47 extension / private use subtags are not read as a script
- [x] nit: stale note about a `w/` folder removed

## Acceptance criteria
- [x] `pytest tests/core -q` passes (2821 passed, 8 xfailed after the fix round)
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
- Catalog loading: `*.json` (extension in any case) in name order, hidden files and non-files
  ignored, UTF-8 with
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
  (`Greška ulaza/izlaza` -> `Грешка улаза/излаза`, found in `core_hamlib.json`); words joined
  by `/` or `+` are judged one by one (fix round, below); in hyphen / `&` compounds with a
  technical part, capitalized parts stay (`Latin-1`, `Wi-Fi`) and lowercase parts are Serbian
  (`QGIS-u` -> `QGIS-у`, `WSJT-X-a` -> `WSJT-X-а`, `QSO-veza` -> `QSO-веза`, `20-ak` -> `20-ак`);
  all-caps words with č ć đ š ž are Serbian (`NJEGOŠ`), without them acronyms; unit symbols
  (`km`, `MHz`, `Hz`, `dB`, `ms`, ...) stay Latin as in Serbian orthography for SI symbols.
- `tests/core/test_i18n.py`: 388 tests + 4 strict xfail (the documented digraph exceptions
  `nadživeti`, `podžupan`, `injekcija`, `konjunkcija`). Every test runs with a fresh module
  translator and no recorded load problems (monkeypatched), so no state leaks into other
  test modules.
- `tests/core/test_i18n_catalog.py`: the guard (160 tests with the files present now; the
  number grows with the files). Rules as requested, plus: `tr()` at import time is an error
  (the text would never follow a language switch); `tr_noop()` must get a literal (per the
  architecture rules); exactly one positional argument; `self.tr()` in a class without its own
  or an inherited HamQ `tr` is flagged as Qt's `QObject.tr`; `super().tr()` and the Qt markers
  (`QT_TR_NOOP`, `QT_TRANSLATE_NOOP`, ...) are flagged; `tr` imported under an alias is
  recognized; catalogs must be canonical (`json.dumps(..., ensure_ascii=False, indent=2,
  sort_keys=True) + "\n"`), without Cyrillic, and placeholders must survive transliteration.
- `hamq/i18n/sr_Latn/core_i18n.json`: `Auto (QGIS language)` -> `Automatski (jezik QGIS-a)`
  and the five catalog-loading messages.

### Review fix round (2026-09-30)
The reviewer's ten findings were reproduced first (scratch script); for each, a regression
test was added and seen failing against the unfixed code (48 failures), then fixed:
- Case endings after technical tokens (should): `_split_ending` splits a trailing hyphen plus
  a lowercase ending that starts with a vowel or `j` + vowel off a token that has technical
  characters, a dot/colon join, `/` or `+`; the token is converted on its own (usually kept)
  and the ending transliterated: `cty.dat-a` -> `cty.dat-а`, `cty.dat-om`, `hamq.gpkg-u`,
  `my_call-a`, `EPSG:4326-u`, `YU1AB/P-om`. Technical tails stay: `hamq.gpkg-shm`,
  `hamq.gpkg-wal`, `python:3.9-slim`, `hamq-0.1.0.zip`, `cty.dat-A`. Cyrillic endings are
  recognized too, so a second conversion changes nothing (a fuzz run found this; tests added).
- Guard rule e (should): `QgsApplication` added to the application classes; aliases from
  `from ... import QCoreApplication as QCA` / `QObject as Base` and from assignments
  (`app = QgsApplication.instance()`); `<App>.instance().translate/.tr`, `qApp.translate`;
  markers as attributes (`QtCore.QT_TRANSLATE_NOOP`). Nine new scanner self-tests (flagged)
  and three (allowed: `instance().processingRegistry()`, an aliased `App.locale()`,
  `app.translate(table)` on an unrelated object).
- Indexed literal (nit): `tr()` of a subscript must index a name or attribute (a table marked
  with `tr_noop`); `("Yes", "No")[flag]`, `{...}[key]`, `"Yes"[:2]`, `labels()[key]` fail
  rule a with a hint. Nested `LABELS[key][i]` and `self.LABELS[key]` stay allowed.
- Catalog file selection (nit): `i18n._catalog_file_names()` (private) is the one rule
  (`*.json` in any case, no hidden files, regular files, name order); `load_catalog` and the
  guard's `catalog_files()` use it. Regression test with `A_UPPER.JSON`, `c.Json`,
  `.hidden.json`, `notes.txt`, `folder.json/`.
- `load_problems()` (nit): problems are kept per source: the latest `load_catalog()` call, and
  the module translator's own lazy load (private `_read_catalogs`, never touches the call's
  problems). `load_problems()` returns the call's problems, or the translator's while no call
  was made; reading them (which may trigger the lazy load) returns the same list every time.
- Unclosed comments (nit): the span pattern matches only `<!--`; the end is found with
  `str.find`, and after the first unclosed `<!--` no further search is made: 100 KB of
  `<!--a` 11.6 s -> 0.016 s. A timing test (marker `slow`, 3 s bound for 100 KB) covers 11
  pathological inputs; all take <= 0.31 s here.
- Units and keys (nit): one convention, unit symbols stay Latin. `cm`, `mm`, `kg` and the keys
  `Return`, `Tab`, `Space`, `Backspace`, `Delete`, `Del`, `Insert`, `Home`, `End` were added to
  `PROTECTED_WORDS`. `m`, `s`, `h`, `min` are also Serbian words or letters (`s` is a
  preposition, `min.` an abbreviation), so they stay Latin only after a number or a value
  placeholder (`20 m`, `1,5 h`, `{seconds} s`, `%d s`), alone in brackets (`(s)`, `[min]`) and
  in units (`m/s`, `veza/h`); elsewhere they are transliterated (`s njim` -> `с њим`,
  `min. azimut` -> `мин. азимут`).
- `/` and `+` (nit): parts are judged one by one: technical parts, one-letter parts next to
  them and units after `/` stay; Serbian words are transliterated (`stanica/QTH` ->
  `станица/QTH`, `veza/QSO`, `AM/FM/Ostalo` -> `AM/FM/Остало`, `Ctrl+klik` -> `Ctrl+клик`,
  `Klik+prevuci` -> `Клик+превуци`). Kept whole: `YU1AB/P`, `km/h`, `TX/RX`, `Ctrl+S`,
  `a+b` (single letters: a formula) and `python/plugins/hamq` (lowercase, a technical part and
  two or more `/`: a relative path, as before the change).
- BCP 47 (nit): subtag scanning stops at the first singleton: `sr-RS-u-nu-latn`,
  `sr-RS-x-latn`, `sr-x-ME` -> `sr_Cyrl`; `sr-Latn-RS-u-nu-latn` -> `sr_Latn`.
- Task file (nit): the stale note about a `w/` folder was removed.
- Old vs new transliteration on all 66 current catalog values: no difference. Of 86 probe
  phrases, 34 differ, all in the classes above (reviewed one by one).
- Fuzz (3 seeds x 200k random texts from Serbian words, technical tokens, units, placeholders
  and joiners): never raises, idempotent, `str.format` fields unchanged, worst case < 2 ms.
  One seed hit a degenerate template whose format spec contains `{{call}}`
  (`[Ž{Delete; :{{call}}...}`): not usable with `str.format` (ValueError), and the old code
  changes it as well; left as is (real catalogs are covered by guard rule c).

### Commands and outcomes
- `python3 -m pytest -p no:cacheprovider tests/core/test_i18n.py -q` -> 388 passed, 4 xfailed.
- `python3 -m pytest -p no:cacheprovider tests/core/test_i18n_catalog.py -q` -> 160 passed
  (also with `HAMQ_STRICT_I18N=1`: no unused keys at the moment).
- `python3 -m pytest -p no:cacheprovider tests/core -q` -> 2821 passed, 8 xfailed.
- Docker `python:3.9-slim` (read-only mount): i18n tests -> 548 passed, 4 xfailed (Python 3.9.25).
- Docker `qgis/qgis:3.44-trixie` and `qgis/qgis:4.0-trixie`: i18n tests -> 548 passed,
  4 xfailed each (Python 3.13.5).
- Docker `camptocamp/qgis-server:3.34` (Python 3.10.12, no pytest): smoke script (resolve incl.
  `sr-RS-u-nu-latn`, switch, translate `core_i18n` and `core_hamlib` texts, `load_problems()`
  stable across two reads, endings / units / slash / keys, unclosed comments fast) -> ok.
- `ruff check` and `ruff format --check` on the three Python files -> clean.
- Guard mutation check after the fix round (scratch copy): an injected `hamq/gui/zz_mut.py`
  with `QgsApplication.translate`, an aliased `QCA.translate`, `QCA.instance().translate`,
  `QtCore.QT_TRANSLATE_NOOP` and `tr(("Yes", "No")[flag])`, and an injected `ZZ_UPPER.JSON`
  (unsorted, Cyrillic value, not canonical, placeholder mismatch) -> each failed under the
  owning file's test id (`test_no_qt_translation_api`, `test_tr_arguments_are_plain_literals`,
  `test_catalog_file_is_well_formed`, `test_catalog_placeholders_match`).

Before the fix round:
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
    `WSJT Development Group`) are already there. The fix round added `cm`, `mm`, `kg`,
    `Return`, `Tab`, `Space`, `Backspace`, `Delete`, `Del`, `Insert`, `Home`, `End`.
  - `language_name(language) -> str`: native name for `en` / `sr_Latn` / `sr_Cyrl`, translated
    `Auto (QGIS language)` for `auto`. The GUI should use it instead of adding the same key to
    its own catalog: the guard requires one translation per English text across all catalogs.
  - `load_problems() -> list[str]` (semantics refined in the fix round): translated problems
    of the latest `load_catalog()` call; while no such call was made, those of the module
    translator's own lazy load of `hamq/i18n/sr_Latn/`. That load never replaces a call's
    problems, so repeated reads return the same list. In production nothing else calls
    `load_catalog()`, so the plugin should call `load_problems()` after the first Serbian
    translation (for example after applying the language) and log the result.
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
- Known transliteration limits (documented and tested, out of scope): digraph pairs across a
  morpheme boundary (`nadživeti`, `podžupan`, `injekcija`, `konjunkcija`; xfail tests);
  all-caps words without č ć đ š ž stay Latin (`UPOZORENJE`); case endings glued to a
  protected name without a hyphen (`Hamliba`) are transliterated with the name; a relative
  path without a technical part or with a single `/` is read as words (`profil/podaci`: pass
  paths as `{path}`); a vowel-initial word after a hyphen that follows a technical token is
  taken for a case ending (`v0.1.0-alfa` -> `v0.1.0-алфа`); after a number or a placeholder
  `s` is the unit (`5 s`, `{seconds} s`), so translators should write the preposition as `sa`
  there.
- For translators (catalog authors): write unit symbols as usual (`20 m`, `5 s`, `(km)`,
  `m/s`); they stay Latin in Cyrillic, like `km` and `MHz`.
- The guard calls the private helper `i18n._catalog_file_names()` so that it checks exactly
  the files the runtime loads; renaming that helper needs the guard updated too.
- `CHANGELOG.md` was not updated (outside this task's file scope in the parallel run).
