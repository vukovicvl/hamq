"""Tests for hamq.core.i18n: languages, catalogs, the translator and Serbian transliteration.

Every test starts with a fresh module translator (English, catalogs not loaded) and the
process-wide one is restored afterwards, so language changes here never leak into other
test modules that expect English texts.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from hamq.core import i18n
from hamq.core.i18n import (
    LANG_AUTO,
    LANG_EN,
    LANG_SR_CYRL,
    LANG_SR_LATN,
    LANGUAGES,
    PROTECTED_WORDS,
    Translator,
    current_language,
    get_translator,
    is_serbian,
    language_name,
    latin_to_cyrillic,
    load_catalog,
    load_problems,
    resolve_language,
    set_language,
    tr,
    tr_noop,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = ROOT / "hamq" / "i18n" / "sr_Latn"
OWN_CATALOG = CATALOG_DIR / "core_i18n.json"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """New module translator and no recorded load problems for each test (restored afterwards)."""
    monkeypatch.setattr(i18n, "_translator", None)
    monkeypatch.setattr(i18n, "_problems", None)
    monkeypatch.setattr(i18n, "_translator_problems", None, raising=False)


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- languages


def test_language_codes():
    assert (LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL) == ("auto", "en", "sr_Latn", "sr_Cyrl")


def test_languages_have_native_names():
    assert LANGUAGES == (
        ("en", "English"),
        ("sr_Latn", "Srpski (latinica)"),
        ("sr_Cyrl", "Српски (ћирилица)"),
    )


@pytest.mark.parametrize(
    ("setting", "system_locale", "expected"),
    [
        # auto: from the QGIS / system locale
        ("auto", "sr_RS", "sr_Cyrl"),
        ("auto", "sr-RS", "sr_Cyrl"),
        ("auto", "sr", "sr_Cyrl"),
        ("auto", "SR", "sr_Cyrl"),
        ("auto", "sr_RS.UTF-8", "sr_Cyrl"),
        ("auto", "sr_Cyrl_RS", "sr_Cyrl"),
        ("auto", "sr-Cyrl", "sr_Cyrl"),
        ("auto", "sr_BA", "sr_Cyrl"),
        ("auto", "sr_Cyrl_ME", "sr_Cyrl"),
        ("auto", "srp", "sr_Cyrl"),
        ("auto", "sr_RS@cyrillic", "sr_Cyrl"),
        ("auto", "sr@ijekavian", "sr_Cyrl"),
        ("auto", "sr@latin", "sr_Latn"),
        ("auto", "sr_RS@latin", "sr_Latn"),
        ("auto", "sr_RS.UTF-8@latin", "sr_Latn"),
        ("auto", "sr_Latn", "sr_Latn"),
        ("auto", "sr_Latn_RS", "sr_Latn"),
        ("auto", "sr-Latn-RS", "sr_Latn"),
        ("auto", "sr-Latn", "sr_Latn"),
        ("auto", "SR_LATN_RS", "sr_Latn"),
        ("auto", "sr@ijekavianlatin", "sr_Latn"),
        ("auto", "sr_ME", "sr_Latn"),  # Qt / CLDR: Serbian in Montenegro is Latin by default
        ("auto", "sr_ME.UTF-8", "sr_Latn"),
        ("auto", "Serbian (Latin)_Serbia.1250", "sr_Latn"),  # Windows locale names
        ("auto", "Serbian_Serbia.1251", "sr_Cyrl"),
        # BCP 47 extensions (-u-, -t-) and private use (-x-) never carry the script or region
        ("auto", "sr-RS-u-nu-latn", "sr_Cyrl"),  # Latin digits only; the script stays Cyrillic
        ("auto", "sr-RS-x-latn", "sr_Cyrl"),
        ("auto", "sr-x-ME", "sr_Cyrl"),
        ("auto", "sr-Latn-RS-u-nu-latn", "sr_Latn"),
        ("auto", "sr-ME-u-ca-gregory", "sr_Latn"),
        ("auto", " sr_RS ", "sr_Cyrl"),
        ("auto", "en_US.UTF-8", "en"),
        ("auto", "en", "en"),
        ("auto", "de_DE", "en"),
        ("auto", "hr_HR", "en"),
        ("auto", "bs_BA", "en"),
        ("auto", "ru_RU", "en"),
        ("auto", "C", "en"),
        ("auto", "POSIX", "en"),
        ("auto", "", "en"),
        ("auto", "   ", "en"),
        ("auto", None, "en"),
        ("auto", "_", "en"),
        ("AUTO", "sr_RS", "sr_Cyrl"),
        # a missing or empty setting means the default, auto
        (None, "sr_RS", "sr_Cyrl"),
        ("", "sr_RS@latin", "sr_Latn"),
        (None, None, "en"),
        # an explicit choice wins over the locale
        ("en", "sr_RS", "en"),
        ("sr_Latn", "en_US", "sr_Latn"),
        ("sr_Cyrl", None, "sr_Cyrl"),
        ("sr_Latn", "sr_RS", "sr_Latn"),
        ("sr_Cyrl", "sr@latin", "sr_Cyrl"),
        ("sr-latn", None, "sr_Latn"),
        (" SR_CYRL ", "en", "sr_Cyrl"),
        ("EN", "sr_RS", "en"),
        # unknown setting values -> English
        ("de", "sr_RS", "en"),
        ("sr", "sr_RS", "en"),
        ("sr_RS", "sr_RS", "en"),
        ("garbage", None, "en"),
    ],
)
def test_resolve_language(setting, system_locale, expected):
    assert resolve_language(setting, system_locale) == expected


def test_resolve_language_never_returns_auto_and_tolerates_non_text():
    for setting in ("auto", None, "", 5, ["sr"]):
        for system_locale in ("sr_RS", "sr@latin", "en", None, 3.5, b"sr"):
            result = resolve_language(setting, system_locale)
            assert result in (LANG_EN, LANG_SR_LATN, LANG_SR_CYRL)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("sr_Latn", True),
        ("sr_Cyrl", True),
        ("SR-LATN", True),
        ("sr_RS", True),
        ("sr", True),
        ("sr@latin", True),
        ("en", False),
        ("auto", False),
        ("de_DE", False),
        ("", False),
        (None, False),
        (42, False),
    ],
)
def test_is_serbian(language, expected):
    assert is_serbian(language) is expected


def test_language_name_native_and_auto():
    assert language_name(LANG_EN) == "English"
    assert language_name(LANG_SR_LATN) == "Srpski (latinica)"
    assert language_name(LANG_SR_CYRL) == "Српски (ћирилица)"
    assert language_name(LANG_AUTO) == "Auto (QGIS language)"
    assert language_name("xx") == "xx"
    set_language(LANG_SR_LATN)
    assert language_name(LANG_AUTO) == "Automatski (jezik QGIS-a)"
    assert language_name(LANG_EN) == "English"  # native names never change
    set_language(LANG_SR_CYRL)
    assert language_name(LANG_AUTO) == "Аутоматски (језик QGIS-а)"
    assert language_name(LANG_SR_LATN) == "Srpski (latinica)"


# --------------------------------------------------------------------------- Translator

CATALOG = {
    "Import": "Uvoz",
    "QSO: {count}": "Veza: {count}",
    "Import ADIF file": "Uvezi ADIF fajl",
    "Untranslated": "",
}


def test_translator_defaults_to_english_identity():
    translator = Translator(CATALOG)
    assert translator.language == LANG_EN
    assert translator.translate("Import") == "Import"
    assert translator.translate("Anything at all") == "Anything at all"


def test_translator_serbian_latin():
    translator = Translator(CATALOG, LANG_SR_LATN)
    assert translator.language == "sr_Latn"
    assert translator.translate("Import") == "Uvoz"
    assert translator.translate("QSO: {count}").format(count=5) == "Veza: 5"


def test_translator_serbian_cyrillic():
    translator = Translator(CATALOG, LANG_SR_CYRL)
    assert translator.translate("Import") == "Увоз"
    assert translator.translate("QSO: {count}").format(count=5) == "Веза: 5"
    assert translator.translate("Import ADIF file") == "Увези ADIF фајл"


@pytest.mark.parametrize("language", [LANG_SR_LATN, LANG_SR_CYRL])
def test_missing_or_empty_translation_falls_back_to_the_english_source(language):
    translator = Translator(CATALOG, language)
    assert translator.translate("Not in the catalog") == "Not in the catalog"
    assert translator.translate("Untranslated") == "Untranslated"
    assert translator.translate("") == ""


def test_translator_switches_language_at_run_time():
    translator = Translator(CATALOG)
    translator.set_language(LANG_SR_CYRL)
    assert translator.translate("Import") == "Увоз"
    translator.set_language(LANG_SR_LATN)
    assert translator.translate("Import") == "Uvoz"
    translator.set_language(LANG_EN)
    assert translator.translate("Import") == "Import"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("sr_Cyrl", "sr_Cyrl"),
        ("SR-CYRL", "sr_Cyrl"),
        (" sr_latn ", "sr_Latn"),
        ("en", "en"),
        ("auto", "en"),  # resolve 'auto' with resolve_language() first
        ("de", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_translator_language_codes_are_normalized(language, expected):
    translator = Translator(CATALOG, language)
    assert translator.language == expected
    translator.set_language(LANG_SR_LATN)
    translator.set_language(language)
    assert translator.language == expected


def test_translator_copies_the_catalog():
    catalog = dict(CATALOG)
    translator = Translator(catalog, LANG_SR_LATN)
    catalog["Import"] = "Changed"
    assert translator.translate("Import") == "Uvoz"


def test_translator_ignores_non_text():
    translator = Translator({"Import": "Uvoz", "Bad": 5}, LANG_SR_CYRL)
    assert translator.translate(None) is None
    assert translator.translate("Bad") == "Bad"


def test_cyrillic_translations_are_cached(monkeypatch):
    calls = []
    real = i18n.latin_to_cyrillic

    def counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(i18n, "latin_to_cyrillic", counting)
    translator = Translator(CATALOG, LANG_SR_CYRL)
    first = translator.translate("Import")
    second = translator.translate("Import")
    assert first == "Увоз"
    assert second is first
    translator.set_language(LANG_SR_LATN)
    assert translator.translate("Import") == "Uvoz"
    translator.set_language(LANG_SR_CYRL)
    assert translator.translate("Import") == "Увоз"
    assert calls == ["Uvoz"]
    translator.translate("Not in the catalog")
    translator.translate("Not in the catalog")
    assert calls == ["Uvoz"]  # English fallbacks are not transliterated


def test_loader_runs_lazily_on_the_first_serbian_translation():
    calls = []

    def loader():
        calls.append(1)
        return {"Import": "Uvoz"}

    translator = Translator(loader=loader)
    assert translator.translate("Import") == "Import"
    translator.set_language(LANG_SR_LATN)
    assert calls == []
    assert translator.translate("Import") == "Uvoz"
    assert translator.translate("Import") == "Uvoz"
    translator.set_language(LANG_SR_CYRL)
    assert translator.translate("Import") == "Увоз"
    assert calls == [1]


def test_explicit_catalog_wins_over_loader():
    translator = Translator({"Import": "Uvoz"}, LANG_SR_LATN, loader=lambda: {"Import": "X"})
    assert translator.translate("Import") == "Uvoz"


def test_failing_loader_gives_english_fallbacks():
    def loader():
        raise OSError("disk on fire")

    translator = Translator(language=LANG_SR_CYRL, loader=loader)
    assert translator.translate("Import") == "Import"


def test_concurrent_first_use_loads_once():
    calls = []

    def slow_loader():
        calls.append(1)
        time.sleep(0.05)
        return {"Import": "Uvoz"}

    translator = Translator(language=LANG_SR_CYRL, loader=slow_loader)
    barrier = threading.Barrier(16)
    results = []

    def worker():
        barrier.wait()
        for _ in range(50):
            results.append(translator.translate("Import"))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [1]
    assert results == ["Увоз"] * 800


# --------------------------------------------------------------------------- module singleton


def test_singleton_is_shared_and_english_by_default():
    assert get_translator() is get_translator()
    assert current_language() == LANG_EN
    assert tr("Auto (QGIS language)") == "Auto (QGIS language)"


def test_singleton_loads_the_plugin_catalogs_lazily(monkeypatch):
    loads = []
    real = i18n._read_catalogs

    def counting(directory):
        loads.append(Path(directory))
        return real(directory)

    monkeypatch.setattr(i18n, "_read_catalogs", counting)
    assert tr("Auto (QGIS language)") == "Auto (QGIS language)"
    set_language(LANG_SR_LATN)
    assert loads == []  # switching alone does not read files
    assert tr("Auto (QGIS language)") == "Automatski (jezik QGIS-a)"
    set_language(LANG_SR_CYRL)
    assert current_language() == "sr_Cyrl"
    assert tr("Auto (QGIS language)") == "Аутоматски (језик QGIS-а)"
    assert loads == [CATALOG_DIR]


def test_set_language_normalizes():
    set_language("SR-LATN")
    assert current_language() == LANG_SR_LATN
    set_language("auto")
    assert current_language() == LANG_EN


def test_tr_noop_returns_its_argument():
    text = "Total QSOs"
    assert tr_noop(text) is text
    set_language(LANG_SR_CYRL)
    assert tr_noop(text) is text


def test_tr_after_switch_back_to_english_is_identity():
    set_language(LANG_SR_CYRL)
    assert tr("Auto (QGIS language)") != "Auto (QGIS language)"
    set_language(LANG_EN)
    assert tr("Auto (QGIS language)") == "Auto (QGIS language)"


def test_own_catalog_matches_the_module_strings():
    catalog = json.loads(OWN_CATALOG.read_text(encoding="utf-8"))
    assert catalog["Auto (QGIS language)"] == "Automatski (jezik QGIS-a)"
    for source, serbian in catalog.items():
        assert serbian.strip() and serbian != source


# --------------------------------------------------------------------------- load_catalog


def test_load_catalog_merges_files_sorted_by_name(tmp_path):
    write_json(tmp_path / "b_module.json", {"Band": "Opseg", "Same": "Isto"})
    write_json(tmp_path / "a_module.json", {"Mode": "Vrsta rada", "Same": "Isto"})
    catalog = load_catalog(tmp_path)
    assert catalog == {"Band": "Opseg", "Mode": "Vrsta rada", "Same": "Isto"}
    assert load_problems() == []
    assert load_catalog(str(tmp_path)) == catalog  # str or os.PathLike


def test_load_catalog_conflict_later_file_wins_and_is_reported(tmp_path):
    write_json(tmp_path / "b.json", {"Band": "Druga"})
    write_json(tmp_path / "a.json", {"Band": "Opseg"})
    assert load_catalog(tmp_path) == {"Band": "Druga"}
    problems = load_problems()
    assert len(problems) == 1
    assert "a.json" in problems[0] and "b.json" in problems[0] and "Band" in problems[0]


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b'{"Band": "Opseg",}',
        b"",
        '{"Band": "Đak"}'.encode("latin-1", "replace") + b"\xff",
        '{"Band": "Opseg"}'.encode("utf-16"),
    ],
)
def test_broken_file_is_skipped_and_reported(tmp_path, content):
    write_json(tmp_path / "good.json", {"Mode": "Vrsta rada"})
    (tmp_path / "broken.json").write_bytes(content)
    assert load_catalog(tmp_path) == {"Mode": "Vrsta rada"}
    problems = load_problems()
    assert len(problems) == 1
    assert "broken.json" in problems[0]


@pytest.mark.parametrize("data", [[1, 2], "text", 5, None])
def test_non_object_file_is_skipped_and_reported(tmp_path, data):
    write_json(tmp_path / "list.json", data)
    assert load_catalog(tmp_path) == {}
    problems = load_problems()
    assert len(problems) == 1 and "list.json" in problems[0]


def test_invalid_entries_are_skipped_and_reported(tmp_path):
    write_json(
        tmp_path / "mixed.json",
        {"Band": "Opseg", "Count": 5, "Nothing": None, "List": ["a"], "Empty": "", "Blank": "  "},
    )
    assert load_catalog(tmp_path) == {"Band": "Opseg"}
    problems = load_problems()
    assert len(problems) == 3  # empty translations are "not translated yet", not problems
    assert all("mixed.json" in problem for problem in problems)


def test_bom_and_unrelated_files(tmp_path):
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + b'{"Band": "Opseg"}')
    (tmp_path / "._bom.json").write_bytes(b"\x00\x05\x16\x07 macOS resource fork")
    (tmp_path / ".hidden.json").write_text('{"Hidden": "Skriven"}', encoding="utf-8")
    (tmp_path / "notes.txt").write_text('{"Notes": "Beleške"}', encoding="utf-8")
    (tmp_path / "folder.json").mkdir()
    assert load_catalog(tmp_path) == {"Band": "Opseg"}
    assert load_problems() == []


def test_catalog_file_names_ignore_case_of_the_extension(tmp_path):
    (tmp_path / "b.json").write_text('{"B": "Be"}', encoding="utf-8")
    (tmp_path / "A_UPPER.JSON").write_text('{"Upper": "Gornji"}', encoding="utf-8")
    (tmp_path / ".hidden.JSON").write_text('{"Hidden": "Skriven"}', encoding="utf-8")
    assert i18n._catalog_file_names(tmp_path) == ["A_UPPER.JSON", "b.json"]
    assert load_catalog(tmp_path) == {"B": "Be", "Upper": "Gornji"}


def test_missing_directory_gives_empty_catalog_and_a_problem(tmp_path):
    assert load_catalog(tmp_path / "missing") == {}
    problems = load_problems()
    assert len(problems) == 1 and "missing" in problems[0]
    assert load_catalog(None) == {}
    assert len(load_problems()) == 1


def test_problems_belong_to_the_latest_load(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.json").write_text("{", encoding="utf-8")
    load_catalog(bad)
    assert len(load_problems()) == 1
    load_catalog(CATALOG_DIR)
    assert load_problems() == []


def test_problems_are_translated_when_read(tmp_path):
    set_language(LANG_SR_LATN)
    assert tr("Auto (QGIS language)") == "Automatski (jezik QGIS-a)"
    assert load_problems() == []  # the plugin catalogs load cleanly
    (tmp_path / "x.json").write_text("[]", encoding="utf-8")
    load_catalog(tmp_path)
    assert load_problems() == ["Fajl sa prevodima x.json nije JSON objekat, preskočen je"]
    set_language(LANG_SR_CYRL)
    assert load_problems() == ["Фајл са преводима x.json није JSON објекат, прескочен је"]
    set_language(LANG_EN)
    assert load_problems() == ["Translation file x.json is not a JSON object and was skipped"]


def test_reading_problems_never_changes_them(tmp_path):
    # Review finding: translating the messages triggered the module translator's lazy load,
    # which replaced the problems that were just reported.
    set_language(LANG_SR_LATN)  # the module translator has not read its catalogs yet
    (tmp_path / "x.json").write_text("[]", encoding="utf-8")
    load_catalog(tmp_path)
    expected = ["Fajl sa prevodima x.json nije JSON objekat, preskočen je"]
    assert load_problems() == expected  # translating them loads the plugin catalogs ...
    assert load_problems() == expected  # ... and that load keeps its problems apart
    assert get_translator().translate("Auto (QGIS language)") == "Automatski (jezik QGIS-a)"


def test_problems_of_the_module_translator_load(tmp_path, monkeypatch):
    # Until code calls load_catalog() itself, load_problems() reports the problems of the
    # module translator's own lazy load (the plugin logs them after applying the language).
    (tmp_path / OWN_CATALOG.name).write_bytes(OWN_CATALOG.read_bytes())
    (tmp_path / "x.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(i18n, "_CATALOG_DIR", str(tmp_path))
    set_language(LANG_SR_LATN)
    assert load_problems() == []  # nothing loaded yet, and reading loads nothing
    assert tr("Auto (QGIS language)") == "Automatski (jezik QGIS-a)"
    expected = ["Fajl sa prevodima x.json nije JSON objekat, preskočen je"]
    assert load_problems() == expected
    assert load_problems() == expected
    set_language(LANG_EN)
    assert load_problems() == ["Translation file x.json is not a JSON object and was skipped"]
    load_catalog(CATALOG_DIR)  # an explicit load is the latest one from now on
    assert load_problems() == []


def test_problem_messages_keep_their_parameters(tmp_path):
    write_json(tmp_path / "a.json", {"Band": "Opseg"})
    write_json(tmp_path / "b.json", {"Band": "Traka", "Mode": 7})
    (tmp_path / "c.json").write_text("{", encoding="utf-8")
    load_catalog(tmp_path)
    problems = load_problems()
    assert problems[0] == "Translation file b.json: the entry Mode is not text and was skipped"
    assert problems[1] == (
        "The translation of Band differs between a.json and b.json; the one from b.json is used"
    )
    assert problems[2].startswith("Translation file c.json could not be read and was skipped: ")
    assert len(problems) == 3


def test_real_catalogs_load():
    catalog = load_catalog(CATALOG_DIR)
    assert catalog["Auto (QGIS language)"] == "Automatski (jezik QGIS-a)"
    assert all(isinstance(value, str) and value for value in catalog.values())


# --------------------------------------------------------------------------- transliteration

LETTERS = [
    ("A", "А"),
    ("B", "Б"),
    ("C", "Ц"),
    ("Č", "Ч"),
    ("Ć", "Ћ"),
    ("D", "Д"),
    ("Dž", "Џ"),
    ("Đ", "Ђ"),
    ("E", "Е"),
    ("F", "Ф"),
    ("G", "Г"),
    ("H", "Х"),
    ("I", "И"),
    ("J", "Ј"),
    ("K", "К"),
    ("L", "Л"),
    ("Lj", "Љ"),
    ("M", "М"),
    ("N", "Н"),
    ("Nj", "Њ"),
    ("O", "О"),
    ("P", "П"),
    ("R", "Р"),
    ("S", "С"),
    ("Š", "Ш"),
    ("T", "Т"),
    ("U", "У"),
    ("V", "В"),
    ("Z", "З"),
    ("Ž", "Ж"),
]


def test_alphabet_has_thirty_letters():
    assert len(LETTERS) == 30
    assert len({cyrillic for _, cyrillic in LETTERS}) == 30


@pytest.mark.parametrize(("latin", "cyrillic"), LETTERS, ids=[latin for latin, _ in LETTERS])
def test_every_letter(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic
    assert latin_to_cyrillic(latin.lower()) == cyrillic.lower()


def test_whole_alphabet_in_one_text():
    latin = " ".join(letter for letter, _ in LETTERS)
    cyrillic = " ".join(letter for _, letter in LETTERS)
    assert latin_to_cyrillic(latin) == cyrillic
    assert latin_to_cyrillic(latin.lower()) == cyrillic.lower()


def test_pangram():
    assert latin_to_cyrillic(
        "Fijuče vetar u šiblju, ledi pasaže i kuće iza njih i gunđa u odžacima."
    ) == ("Фијуче ветар у шибљу, леди пасаже и куће иза њих и гунђа у оџацима.")


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        ("Ljubljana", "Љубљана"),
        ("NJEGOŠ", "ЊЕГОШ"),
        ("Džak", "Џак"),
        ("ljubav", "љубав"),
        ("Njiva", "Њива"),
        ("njiva", "њива"),
        ("džep", "џеп"),
        ("DŽEP", "ЏЕП"),
        ("LJUBIČAST", "ЉУБИЧАСТ"),
        ("Njegoš", "Његош"),
        ("kuća", "кућа"),
        ("Đorđe", "Ђорђе"),
        ("ĐAČKI", "ЂАЧКИ"),
        ("Ðorđe", "Ђорђе"),  # U+00D0 (eth), often typed for Đ
        ("nijedan", "ниједан"),  # n-i-j: no digraph
        ("gunđa", "гунђа"),
    ],
)
def test_digraphs_and_special_letters(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        ("\u01c7UBAV", "ЉУБАВ"),  # Ǉ
        ("\u01c8ubav", "Љубав"),  # ǈ
        ("\u01c9ubav", "љубав"),  # ǉ
        ("\u01caIVA", "ЊИВА"),  # Ǌ
        ("\u01cbiva", "Њива"),  # ǋ
        ("\u01cciva", "њива"),  # ǌ
        ("\u01c4EP", "ЏЕП"),  # Ǆ
        ("\u01c5ep", "Џеп"),  # ǅ
        ("\u01c6ep", "џеп"),  # ǆ
    ],
)
def test_single_code_point_digraphs(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


def test_decomposed_letters_are_composed_first():
    decomposed = unicodedata.normalize("NFD", "Ključ i kuća, Đurđevdan, Žabljak")
    assert decomposed != "Ključ i kuća, Đurđevdan, Žabljak"
    assert latin_to_cyrillic(decomposed) == "Кључ и кућа, Ђурђевдан, Жабљак"


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        # examples from the task
        ("Uvezi ADIF fajl", "Увези ADIF фајл"),
        ("Veza {call} na {band}", "Веза {call} на {band}"),
        (
            "Preuzmi cty.dat sa https://www.country-files.com",
            "Преузми cty.dat са https://www.country-files.com",
        ),
        ("Lokator KN04ft", "Локатор KN04ft"),
        ("Opseg 20m", "Опсег 20m"),
        ("WSJT-X je povezan", "WSJT-X је повезан"),
        # capitalized and ordinary words are transliterated
        ("Veza je upisana. Sledeća veza.", "Веза је уписана. Следећа веза."),
        ("Srbija i Crna Gora", "Србија и Црна Гора"),
        # two or more capitals: acronyms and product names
        ("GeoPackage fajl", "GeoPackage фајл"),
        ("HamQ podešavanja", "HamQ подешавања"),
        ("Otvori QGIS", "Отвори QGIS"),
        ("DXCC entitet, CQ zona i ITU zona", "DXCC ентитет, CQ зона и ITU зона"),
        ("Veza (QSO) u UTC", "Веза (QSO) у UTC"),
        ("XX vek", "XX век"),
        # digits
        ("UDP port 2237", "UDP порт 2237"),
        ("FT8 i FT4", "FT8 и FT4"),
        ("Koordinate u EPSG:4326", "Координате у EPSG:4326"),
        ("Pozivni znak YU1ABC", "Позивни знак YU1ABC"),
        ("Verzija v0.1.0", "Верзија v0.1.0"),
        ("Opsezi 2m i 70cm", "Опсези 2m и 70cm"),
        ("20-ak veza", "20-ак веза"),
        # case endings after protected tokens stay Serbian
        ("veze uživo iz WSJT-X-a", "везе уживо из WSJT-X-а"),
        ("u QGIS-u", "у QGIS-у"),
        ("preko Hamlib-a", "преко Hamlib-а"),
        ("iz FT8-a", "из FT8-а"),
        ("Veza sa {call}-om", "Веза са {call}-ом"),
        ("radio-amateri", "радио-аматери"),
        ("Beograd–Sidnej", "Београд–Сиднеј"),
        # compounds: capitalized parts next to a technical part belong to the name
        ("pročitan kao Latin-1 (ISO 8859-1)", "прочитан као Latin-1 (ISO 8859-1)"),
        ("Wi-Fi i AT&T", "Wi-Fi и AT&T"),
        ("radio-QSO i QSO-veza", "радио-QSO и QSO-веза"),
        ("DXCC-entitet", "DXCC-ентитет"),
        ("veze(QSO) i Veza,QSO", "везе(QSO) и Веза,QSO"),
        # q, w, x, y and other non-Serbian letters
        ("Linux i Windows", "Linux и Windows"),
        ("Joe Taylor, K1JT", "Joe Taylor, K1JT"),
        ("Müller i Nowak", "Müller и Nowak"),
        ("osa X", "оса X"),
        # protected words
        ("Maidenhead lokator", "Maidenhead локатор"),
        ("Hamlib greška", "Hamlib грешка"),
        ("Processing algoritmi", "Processing алгоритми"),
        ("Python greška", "Python грешка"),
        ("Pokreni rigctld ili rotctld", "Покрени rigctld или rotctld"),
        ("Jim Reisert, AD1C", "Jim Reisert, AD1C"),
        ("WSJT Development Group", "WSJT Development Group"),
        ("Pritisni Enter", "Притисни Enter"),
        # units
        ("Frekvencija (MHz)", "Фреквенција (MHz)"),
        ("Rastojanje (km)", "Растојање (km)"),
        ("Propusni opseg (Hz)", "Пропусни опсег (Hz)"),
        ("Pomeraj (kHz)", "Померај (kHz)"),
        ("Osvežavanje (ms)", "Освежавање (ms)"),
        ("Odnos signal/šum (dB)", "Однос сигнал/шум (dB)"),
        ("Brzina (km/h)", "Брзина (km/h)"),
        # placeholders and printf / Qt style arguments
        ("Veza {0} od {1}", "Веза {0} од {1}"),
        ("Ukupno {count:,} veza", "Укупно {count:,} веза"),
        ("Uvezeno {} veza", "Увезено {} веза"),
        ("{{doslovno}} i {name!r}", "{{doslovno}} и {name!r}"),
        (
            "Naziv sme da sadrži {{call}} i {{{count}}}",
            "Назив сме да садржи {{call}} и {{{count}}}",
        ),
        ("Zagrade {{ i }} same", "Заграде {{ и }} саме"),
        ("Širina {value:{width}}", "Ширина {value:{width}}"),
        ("Veza %s na %d MHz", "Веза %s на %d MHz"),
        ("Snaga %5.1f W i %(name)s", "Снага %5.1f W и %(name)s"),
        ("Veza %1 od %2, %L3 i %n", "Веза %1 од %2, %L3 и %n"),
        ("Uspeh 50% veza, 100 % sigurno", "Успех 50% веза, 100 % сигурно"),
        # markup
        ("<b>Veza</b> je <i>nova</i>", "<b>Веза</b> је <i>нова</i>"),
        (
            '<a href="https://github.com/vukovicvl/hamq">Izvorni kod</a>',
            '<a href="https://github.com/vukovicvl/hamq">Изворни код</a>',
        ),
        ("Veza&nbsp;&amp; log &#169; &#x2014;", "Веза&nbsp;&amp; лог &#169; &#x2014;"),
        ("<br/>Nova linija<!-- komentar -->", "<br/>Нова линија<!-- komentar -->"),
        # URLs, e-mail, files and paths
        ("Pišite na yu1abc@example.rs", "Пишите на yu1abc@example.rs"),
        ("Sajt: http://example.rs/putanja?a=b.", "Сајт: http://example.rs/putanja?a=b."),
        ("Otvori www.qrz.com sada", "Отвори www.qrz.com сада"),
        ("Log je u /home/pera/hamq/log.adi", "Лог је у /home/pera/hamq/log.adi"),
        ("Fajl C:\\Users\\Pera\\log.adi", "Фајл C:\\Users\\Pera\\log.adi"),
        ("Sačuvano u ~/hamq i ./logovi", "Сачувано у ~/hamq и ./logovi"),
        ("Fascikla (/usr/share/qgis)", "Фасцикла (/usr/share/qgis)"),
        ("Fajl hamq.gpkg i cty.csv", "Фајл hamq.gpkg и cty.csv"),
        ("Izvor: (country-files.com).", "Извор: (country-files.com)."),
        ("Algoritam hamq:import_adif", "Алгоритам hamq:import_adif"),
        ("Ključ my_call", "Кључ my_call"),
        ("Pokreni rigctld -m 1 i pošalji +f", "Покрени rigctld -m 1 и пошаљи +f"),
        # slashes between plain words, and between technical tokens
        ("Greška ulaza/izlaza", "Грешка улаза/излаза"),
        ("da/ne", "да/не"),
        ("YU1AB/P radi portabl", "YU1AB/P ради портабл"),
        ("TX/RX", "TX/RX"),
        # punctuation, accelerators, quotes
        ("Podešavanja...", "Подешавања..."),
        ("&Podešavanja i Po&moć", "&Подешавања и По&моћ"),
        ("„Uvezi“ i »Osveži«", "„Увези“ и »Освежи«"),
        ("Veza br. {index}.", "Веза бр. {index}."),
        ("Greška: {error}", "Грешка: {error}"),
    ],
)
def test_transliteration_protects_technical_text(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        # a Serbian case ending after a file name, identifier or other technical token
        (
            "Greška pri preuzimanju cty.dat-a: {error}",
            "Грешка при преузимању cty.dat-а: {error}",
        ),
        ("Greška u cty.dat-u", "Грешка у cty.dat-у"),
        ("sa cty.dat-om i cty.csv-om", "са cty.dat-ом и cty.csv-ом"),
        ("u hamq.gpkg-u", "у hamq.gpkg-у"),
        ("(iz cty.dat-a).", "(из cty.dat-а)."),
        ("vrednost my_call-a", "вредност my_call-а"),
        ("algoritmom hamq:import_adif-om", "алгоритмом hamq:import_adif-ом"),
        ("u EPSG:4326-u", "у EPSG:4326-у"),
        ("sa YU1AB/P-om", "са YU1AB/P-ом"),
        ("stanica/QTH-a", "станица/QTH-а"),
        # hyphenated tails that are no case ending stay with their token
        ("Fajlovi hamq.gpkg-wal i hamq.gpkg-shm", "Фајлови hamq.gpkg-wal и hamq.gpkg-shm"),
        ("Paket hamq-0.1.0.zip", "Пакет hamq-0.1.0.zip"),
        ("Slika python:3.9-slim", "Слика python:3.9-slim"),
        ("Fajl cty.dat-A", "Фајл cty.dat-A"),
    ],
)
def test_case_endings_after_technical_tokens(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        # unit symbols stay Latin like km, ms or MHz; m, s, h and min only where they are units
        (
            "Visina (m), vreme (s), osvežavanje (ms), rastojanje (km)",
            "Висина (m), време (s), освежавање (ms), растојање (km)",
        ),
        ("Brzina (m/s) i (km/h)", "Брзина (m/s) и (km/h)"),
        (
            "Opseg 20 m, pauza 5 s, zatim 2 h i 30 min.",
            "Опсег 20 m, пауза 5 s, затим 2 h и 30 min.",
        ),
        ("Pauza 1,5 s, od 10 do 20 s", "Пауза 1,5 s, од 10 до 20 s"),
        ("Ponovo za {seconds} s", "Поново за {seconds} s"),
        ("Čekaj %d s i [min]", "Чекај %d s и [min]"),
        ("Visina (cm), masa (kg) i korak 5 mm", "Висина (cm), маса (kg) и корак 5 mm"),
        # elsewhere they are Serbian: the preposition s, the abbreviation min.
        ("Veza s YU1AB i s njim", "Веза с YU1AB и с њим"),
        ("s obzirom na to", "с обзиром на то"),
        ("Veza na FT8 s YU1AB", "Веза на FT8 с YU1AB"),
        ("min. azimut", "мин. азимут"),
        # keyboard keys
        ("Pritisni Delete ili Tab", "Притисни Delete или Tab"),
        (
            "Tasteri Home, End, Insert, Backspace, Space i Return",
            "Тастери Home, End, Insert, Backspace, Space и Return",
        ),
        ("Ctrl+Alt+Del", "Ctrl+Alt+Del"),
    ],
)
def test_unit_symbols_and_keys(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        # words joined by '/' or '+' are judged one by one
        ("Moja stanica/QTH", "Моја станица/QTH"),
        ("veza/QSO", "веза/QSO"),
        ("QSO/sat", "QSO/сат"),
        ("AM/FM/Ostalo", "AM/FM/Остало"),
        ("Ctrl+klik na mapu", "Ctrl+клик на мапу"),
        ("Shift+Enter ili Ctrl+S", "Shift+Enter или Ctrl+S"),
        # technical parts and one-letter parts next to them stay, so do units after '/'
        ("YU1AB/P i YU1AB/M", "YU1AB/P и YU1AB/M"),
        ("km/h, m/s, TX/RX i dB/km", "km/h, m/s, TX/RX и dB/km"),
        ("Brzina: {rate} veza/h ili veza/min", "Брзина: {rate} веза/h или веза/min"),
        # plain words stay words; '+' between single letters is a formula
        ("ulaza/izlaza, da/ne/možda", "улаза/излаза, да/не/можда"),
        ("Klik+prevuci", "Клик+превуци"),
        ("a+b i x+y", "a+b и x+y"),
        # a lowercase token with a technical part and two or more '/' is a relative path
        ("Fascikla python/plugins/hamq", "Фасцикла python/plugins/hamq"),
    ],
)
def test_words_joined_by_slash_or_plus(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.slow
@pytest.mark.parametrize(
    ("prefix", "unit"),
    [
        ("", "<!--a"),
        ("<!-- closed -->", "<!--a"),
        ("", "a."),
        ("", "{a"),
        ("", "{{a"),
        ("", "<a "),
        ("", "&a"),
        ("", "a/"),
        ("", "a+"),
        ("", "cty.dat-a "),
        ("", "(s) "),
    ],
)
def test_transliteration_time_is_linear(prefix, unit):
    # Review finding: unclosed '<!--' made the old comment pattern quadratic (100 KB took
    # about 11 s); linear patterns need well under a second for 100 KB.
    text = prefix + unit * (100_000 // len(unit))
    started = time.perf_counter()
    latin_to_cyrillic(text)
    assert time.perf_counter() - started < 3.0


def test_html_comments_are_kept_and_unclosed_ones_are_text():
    assert latin_to_cyrillic("a<!-- b -->c<!-- d") == "а<!-- b -->ц<!-- д"
    assert latin_to_cyrillic("<!---->veza<!--x-->") == "<!---->веза<!--x-->"


def test_all_caps_words_with_serbian_letters_are_transliterated():
    assert latin_to_cyrillic("GREŠKA U ČITANJU") == "ГРЕШКА У ЧИТАЊУ"


def test_all_caps_word_without_serbian_letters_is_treated_as_an_acronym():
    # Documented limitation: 'UPOZORENJE' cannot be told apart from an acronym.
    assert latin_to_cyrillic("UPOZORENJE") == "UPOZORENJE"


@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        ("profil/podaci", "профил/подаци"),  # a relative path without a technical part
        ("verzija v0.1.0-alfa", "верзија v0.1.0-алфа"),  # a vowel-initial word: an ending
        ("preko Hamliba", "преко Хамлиба"),  # an ending glued to a protected name
        ("u my_call-u", "у my_call-у"),
    ],
)
def test_documented_limitations_and_examples(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize("word", sorted(PROTECTED_WORDS))
def test_protected_words_are_kept(word):
    assert latin_to_cyrillic(word) == word
    assert latin_to_cyrillic(f"i {word} i") == f"и {word} и"


def test_whitespace_and_empty_text_are_kept():
    assert latin_to_cyrillic("") == ""
    assert latin_to_cyrillic("  Veza\n\tlog\u00a0 ") == "  Веза\n\tлог\u00a0 "
    assert latin_to_cyrillic("123 {x} %s <b></b>") == "123 {x} %s <b></b>"


def test_cyrillic_and_mixed_text():
    assert latin_to_cyrillic("Веза је у реду") == "Веза је у реду"
    assert latin_to_cyrillic("Веза je u redu") == "Веза је у реду"


@pytest.mark.parametrize(
    "text",
    [
        "Veze uživo iz WSJT-X-a u QGIS-u: {count}",
        "Preuzmi cty.dat sa https://www.country-files.com",
        "Wi-Fi i YU1AB/P, ulaza/izlaza, 20-ak {call}-om",
        "Fijuče vetar u šiblju, ledi pasaže i kuće iza njih i gunđa u odžacima.",
        "Greška u cty.dat-u, stanica/QTH, Ctrl+klik, pauza 5 s (m/s)",
        # found by fuzzing: a case ending must be recognized in Cyrillic too
        ".L+ž-i; ljubav",
        "„udx+H-u“ i xg+/Š-opseg",
    ],
)
def test_transliteration_is_idempotent(text):
    once = latin_to_cyrillic(text)
    assert latin_to_cyrillic(once) == once


def test_non_text_is_returned_unchanged():
    assert latin_to_cyrillic(None) is None


@pytest.mark.xfail(
    strict=True,
    reason="out of scope: digraph letters across a prefix boundary need a dictionary",
)
@pytest.mark.parametrize(
    ("latin", "cyrillic"),
    [
        ("nadživeti", "надживети"),  # nad + živeti: d-ž, not dž
        ("podžupan", "поджупан"),
        ("injekcija", "инјекција"),  # in + jekcija: n-j, not nj
        ("konjunkcija", "конјункција"),
    ],
)
def test_known_digraph_exceptions(latin, cyrillic):
    assert latin_to_cyrillic(latin) == cyrillic
