"""hamq.gui.language: LanguageManager, qgis_ui_locale(), language menu and switch button."""

from __future__ import annotations

import pytest
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QLocale

from hamq.core.i18n import (
    LANG_AUTO,
    LANG_EN,
    LANG_SR_CYRL,
    LANG_SR_LATN,
    current_language,
    resolve_language,
    set_language,
    tr,
)
from hamq.events import events
from hamq.gui import language
from hamq.gui.language import (
    BUTTON_TEXTS,
    CHOICES,
    SWITCH_TITLE,
    LanguageManager,
    LanguageMenu,
    LanguageSwitchButton,
    build_language_menu,
    qgis_ui_locale,
)
from hamq.qgis_io import compat
from hamq.settings import HamQSettings

LOCALE_KEYS = ("locale/overrideFlag", "locale/userLocale")
NATIVE_NAMES = ["English", "Srpski (latinica)", "Српски (ћирилица)"]
#: The real reader of QgsApplication.translation(); the fixture below replaces it.
REAL_QGIS_TRANSLATION = language._qgis_translation


def _clear_locale_keys() -> None:
    settings = QgsSettings()
    for key in LOCALE_KEYS:
        settings.remove(key)


@pytest.fixture(autouse=True)
def language_env(clean_settings, monkeypatch):
    """English system locale, no QGIS locale override or --lang, no POSIX locale
    variables; the HamQ translator is English again after the test."""
    _clear_locale_keys()
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(language, "_system_locale", lambda: QLocale("en_US"))
    monkeypatch.setattr(language, "_qgis_translation", lambda: "")
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)
    _clear_locale_keys()


@pytest.fixture
def expect_slot_errors():
    """Requested by tests that make a slot fail on purpose."""


@pytest.fixture(autouse=True)
def no_slot_errors(log_messages, request):
    """Slots log exceptions instead of raising: fail the test if one did."""
    yield
    if "expect_slot_errors" in request.fixturenames:
        return
    errors = [m for m, tag, level in log_messages if tag == "HamQ" and level == compat.MSG_CRITICAL]
    assert errors == []


class Recorder:
    """Collects the arguments of a signal until closed."""

    def __init__(self, signal):
        self.values: list = []
        self._signal = signal
        signal.connect(self._record)

    def _record(self, *args):
        self.values.append(args[0] if len(args) == 1 else args)

    def close(self):
        self._signal.disconnect(self._record)


@pytest.fixture
def language_events():
    recorder = Recorder(events().languageChanged)
    yield recorder
    recorder.close()


@pytest.fixture
def manager():
    manager = LanguageManager(HamQSettings())
    yield manager
    manager.cleanup()


def receivers() -> tuple[int, int]:
    obj = events()
    return obj.receivers(obj.languageChanged), obj.receivers(obj.settingsChanged)


# --------------------------------------------------------------------------- qgis_ui_locale


def test_qgis_stores_the_locale_override_under_these_keys():
    tree = pytest.importorskip("qgis.core").QgsSettingsTree
    node = tree.node("locale")
    keys = {entry.key() for entry in node.childrenSettings()}
    assert {"/locale/overrideFlag", "/locale/userLocale"} <= keys
    # QGIS reads the same keys for its own interface language
    QgsSettings().setValue("locale/overrideFlag", True)
    QgsSettings().setValue("locale/userLocale", "sr@latin")
    assert QgsApplication.locale() == "sr@latin"


@pytest.mark.parametrize("flag", [True, "true", 1])
def test_override_wins(flag):
    QgsSettings().setValue("locale/overrideFlag", flag)
    QgsSettings().setValue("locale/userLocale", " sr@latin ")
    assert qgis_ui_locale() == "sr@latin"
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_LATN


@pytest.mark.parametrize("flag", [False, "false", 0, None])
def test_without_override_the_system_locale_is_used(flag):
    if flag is not None:
        QgsSettings().setValue("locale/overrideFlag", flag)
    # QGIS rewrites userLocale with QLocale().name() at every start: not a choice
    QgsSettings().setValue("locale/userLocale", "sr_RS")
    assert qgis_ui_locale() == "en"


def test_override_without_a_locale_uses_the_system_locale():
    QgsSettings().setValue("locale/overrideFlag", True)
    QgsSettings().setValue("locale/userLocale", "  ")
    assert qgis_ui_locale() == "en"


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("sr_Latn_RS", LANG_SR_LATN),  # QLocale.name() would say "sr_RS": Cyrillic
        ("sr_RS", LANG_SR_CYRL),
        ("sr_ME", LANG_SR_LATN),
        ("sr_Cyrl_ME", LANG_SR_CYRL),
        ("de_DE", LANG_EN),
        ("C", LANG_EN),
    ],
)
def test_system_locale_keeps_the_script(monkeypatch, system, expected):
    monkeypatch.setattr(language, "_system_locale", lambda: QLocale(system))
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == expected


def test_posix_latin_modifier_is_kept(monkeypatch):
    # Qt reads sr_RS.UTF-8@latin as sr_RS (Cyrillic); the variable keeps "@latin"
    monkeypatch.setattr(language, "_system_locale", lambda: QLocale("sr_RS"))
    monkeypatch.setenv("LANG", "sr_RS.UTF-8@latin")
    assert qgis_ui_locale() == "sr_RS.UTF-8@latin"
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_LATN
    # LC_ALL wins over LANG
    monkeypatch.setenv("LC_ALL", "sr_RS.UTF-8")
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_CYRL


def test_posix_variable_is_ignored_for_a_non_serbian_system_locale(monkeypatch):
    monkeypatch.setenv("LANG", "sr_RS.UTF-8@latin")
    assert qgis_ui_locale() == "en"


def test_command_line_language(monkeypatch):
    # qgis --lang sr@latin: QGIS writes it to userLocale but leaves the override off
    monkeypatch.setattr(language, "_qgis_translation", lambda: "sr@latin")
    QgsSettings().setValue("locale/userLocale", "sr@latin")
    assert qgis_ui_locale() == "sr@latin"
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_LATN
    QgsSettings().setValue("locale/overrideFlag", True)  # an explicit override wins
    QgsSettings().setValue("locale/userLocale", "sr_RS")
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_CYRL


def test_the_default_translation_is_not_a_choice(monkeypatch):
    # without --lang QGIS uses QLocale().name(), which drops the script: keep ours
    monkeypatch.setattr(language, "_system_locale", lambda: QLocale("sr_Latn_RS"))
    monkeypatch.setattr(language, "_qgis_translation", lambda: QLocale("sr_Latn_RS").name())
    assert resolve_language(LANG_AUTO, qgis_ui_locale()) == LANG_SR_LATN


def test_qgis_translation_reads_the_application():
    original = QgsApplication.instance().translation()
    try:
        QgsApplication.setTranslation("xx_TEST")  # no such translation: nothing is loaded
        assert REAL_QGIS_TRANSLATION() == "xx_TEST"
    finally:
        QgsApplication.setTranslation(original)
    assert REAL_QGIS_TRANSLATION() == original.strip()


# --------------------------------------------------------------------------- manager


def test_initial_state(manager):
    assert manager.setting() == LANG_AUTO
    assert manager.language() == LANG_EN
    assert manager.apply_from_settings() == LANG_EN


def test_set_setting_applies_persists_and_emits(manager, language_events):
    settings = HamQSettings()
    settings_changes = Recorder(manager.settingChanged)
    try:
        assert manager.set_setting("sr_Latn") == LANG_SR_LATN
        assert current_language() == LANG_SR_LATN
        assert manager.language() == LANG_SR_LATN
        assert settings.language == LANG_SR_LATN
        assert settings.last_serbian == LANG_SR_LATN
        assert tr("About HamQ") == "O programu HamQ"
        assert language_events.values == [LANG_SR_LATN]

        manager.set_setting("sr_Latn")  # no change: no signal
        assert language_events.values == [LANG_SR_LATN]

        assert manager.set_setting(" SR-cyrl ") == LANG_SR_CYRL
        assert tr("About HamQ") == "О програму HamQ"
        assert settings.last_serbian == LANG_SR_CYRL

        assert manager.set_setting("en") == LANG_EN
        assert tr("About HamQ") == "About HamQ"
        assert settings.last_serbian == LANG_SR_CYRL  # English does not replace it
        assert language_events.values == [LANG_SR_LATN, LANG_SR_CYRL, LANG_EN]

        # Auto with an English QGIS: the choice changes, the language does not
        assert manager.set_setting("auto") == LANG_EN
        assert settings.language == LANG_AUTO
        assert language_events.values == [LANG_SR_LATN, LANG_SR_CYRL, LANG_EN]
        assert settings_changes.values == [LANG_SR_LATN, LANG_SR_CYRL, LANG_EN, LANG_AUTO]
    finally:
        settings_changes.close()


@pytest.mark.parametrize("value", ["klingon", "", "sr", None, 3])
def test_invalid_setting_changes_nothing(manager, language_events, value):
    manager.set_setting("sr_Latn")
    with pytest.raises(ValueError):
        manager.set_setting(value)
    assert HamQSettings().language == LANG_SR_LATN
    assert current_language() == LANG_SR_LATN
    assert language_events.values == [LANG_SR_LATN]


def test_toggle_between_english_and_the_last_serbian_script(manager, language_events):
    assert manager.toggle() == LANG_SR_LATN  # default last_serbian
    assert manager.toggle() == LANG_EN
    manager.set_setting("sr_Cyrl")
    assert manager.toggle() == LANG_EN
    assert manager.toggle() == LANG_SR_CYRL
    assert HamQSettings().language == LANG_SR_CYRL
    assert language_events.values == [
        LANG_SR_LATN,
        LANG_EN,
        LANG_SR_CYRL,
        LANG_EN,
        LANG_SR_CYRL,
    ]


def test_auto_follows_the_qgis_locale(manager, language_events):
    QgsSettings().setValue("locale/overrideFlag", True)
    QgsSettings().setValue("locale/userLocale", "sr_RS")
    assert manager.set_setting("auto") == LANG_SR_CYRL
    assert HamQSettings().language == LANG_AUTO
    # a Serbian script reached through Auto becomes the toggle target too
    assert HamQSettings().last_serbian == LANG_SR_CYRL
    assert manager.toggle() == LANG_EN
    assert manager.toggle() == LANG_SR_CYRL


def test_apply_from_settings_uses_the_stored_choice(language_events):
    HamQSettings().language = "sr_Cyrl"
    manager = LanguageManager(HamQSettings())
    try:
        assert manager.setting() == LANG_SR_CYRL
        assert manager.apply_from_settings() == LANG_SR_CYRL
        assert current_language() == LANG_SR_CYRL
        assert language_events.values == [LANG_SR_CYRL]
        assert manager.apply_from_settings() == LANG_SR_CYRL
        assert language_events.values == [LANG_SR_CYRL]
    finally:
        manager.cleanup()


def test_the_translator_is_the_truth(manager, language_events):
    set_language(LANG_SR_LATN)  # switched without the manager
    assert manager.language() == LANG_SR_LATN
    assert manager.set_setting("en") == LANG_EN
    assert language_events.values == [LANG_EN]  # the translator changed: announced
    set_language(LANG_SR_CYRL)
    assert manager.toggle() == LANG_EN  # the interface was Serbian: toggled to English


def test_catalog_problems_are_logged_once(manager, monkeypatch, log_messages):
    calls = []

    def problems():
        calls.append(1)
        return ["Translation file broken.json could not be read and was skipped: boom"]

    monkeypatch.setattr(language, "load_problems", problems)
    manager.set_setting("en")
    assert calls == []  # English needs no catalog
    manager.set_setting("sr_Latn")
    manager.set_setting("sr_Cyrl")
    manager.set_setting("en")
    manager.set_setting("sr_Latn")
    assert calls == [1]
    warnings = [
        m for m, tag, level in log_messages if tag == "HamQ" and level == compat.MSG_WARNING
    ]
    assert warnings == ["Translation file broken.json could not be read and was skipped: boom"]


def test_saved_settings_are_applied(manager, language_events):
    HamQSettings().language = "sr_Latn"  # e.g. written by code without the manager
    events().settingsChanged.emit()
    assert manager.language() == LANG_SR_LATN
    assert current_language() == LANG_SR_LATN
    assert language_events.values == [LANG_SR_LATN]


# --------------------------------------------------------------------------- menu


def test_menu_choices(manager):
    menu = build_language_menu(manager)
    try:
        assert isinstance(menu, LanguageMenu)
        assert menu.title() == SWITCH_TITLE
        actions = menu.choice_actions()
        assert [action.data() for action in actions] == list(CHOICES)
        assert [action.text() for action in actions] == ["Auto (QGIS language)", *NATIVE_NAMES]
        assert all(action.isCheckable() for action in actions)
        assert actions[0].actionGroup().isExclusive()
        assert [action.isChecked() for action in actions] == [True, False, False, False]
        assert sum(1 for action in menu.actions() if action.isSeparator()) == 1
    finally:
        menu.cleanup()
        menu.deleteLater()


def test_menu_switches_the_language_and_follows_the_manager(manager, language_events):
    menu = build_language_menu(manager)
    try:
        menu.action_for("sr_Cyrl").trigger()
        assert manager.setting() == LANG_SR_CYRL
        assert current_language() == LANG_SR_CYRL
        checked = [action.data() for action in menu.choice_actions() if action.isChecked()]
        assert checked == [LANG_SR_CYRL]
        texts = [action.text() for action in menu.choice_actions()]
        assert texts == ["Аутоматски (језик QGIS-а)", *NATIVE_NAMES]  # native names stay
        assert menu.title() == SWITCH_TITLE

        manager.set_setting("en")  # from elsewhere (button, settings dialog)
        checked = [action.data() for action in menu.choice_actions() if action.isChecked()]
        assert checked == [LANG_EN]
        assert menu.action_for("auto").text() == "Auto (QGIS language)"
        assert language_events.values == [LANG_SR_CYRL, LANG_EN]
    finally:
        menu.cleanup()
        menu.deleteLater()


def test_menu_slot_errors_are_logged_not_raised(
    manager, monkeypatch, log_messages, expect_slot_errors
):
    menu = build_language_menu(manager)
    try:

        def broken(_value):
            raise RuntimeError("boom")

        monkeypatch.setattr(manager, "set_setting", broken)
        menu.action_for("sr_Latn").trigger()  # must not raise into Qt
        errors = [m for m, tag, level in log_messages if tag == "HamQ" and "boom" in m]
        assert errors and errors[0].startswith("Unexpected error in")
        checked = [action.data() for action in menu.choice_actions() if action.isChecked()]
        assert checked == [LANG_AUTO]  # the check went back to the real choice
    finally:
        menu.cleanup()
        menu.deleteLater()


# --------------------------------------------------------------------------- button


def test_switch_button(manager, language_events):
    button = LanguageSwitchButton(manager)
    try:
        assert button.text() == "EN"
        assert button.toolTip() == SWITCH_TITLE
        assert button.popupMode() == compat.TOOLBUTTON_MENU_BUTTON_POPUP
        assert button.menu() is button.language_menu()
        assert isinstance(button.menu(), LanguageMenu)

        button.click()
        assert button.text() == "SR"
        assert current_language() == LANG_SR_LATN
        assert button.toolTip() == SWITCH_TITLE  # bilingual in every language
        button.click()
        assert button.text() == "EN"

        manager.set_setting("sr_Cyrl")
        assert button.text() == "СР"
        checked = [a.data() for a in button.language_menu().choice_actions() if a.isChecked()]
        assert checked == [LANG_SR_CYRL]
        button.click()
        assert button.text() == "EN"
        button.click()
        assert button.text() == "СР"
        assert language_events.values == [
            LANG_SR_LATN,
            LANG_EN,
            LANG_SR_CYRL,
            LANG_EN,
            LANG_SR_CYRL,
        ]
    finally:
        button.cleanup()
        button.deleteLater()


def test_button_texts():
    assert BUTTON_TEXTS == {LANG_EN: "EN", LANG_SR_LATN: "SR", LANG_SR_CYRL: "СР"}


def test_shared_menu_is_left_to_its_owner(manager):
    menu = build_language_menu(manager)
    button = LanguageSwitchButton(manager, menu=menu)
    try:
        assert button.language_menu() is menu
        button.cleanup()
        manager.set_setting("sr_Latn")
        checked = [a.data() for a in menu.choice_actions() if a.isChecked()]
        assert checked == [LANG_SR_LATN]  # the menu is still connected
    finally:
        button.cleanup()
        menu.cleanup()
        button.deleteLater()
        menu.deleteLater()


def test_cleanup_disconnects_everything(process_events):
    before = receivers()
    manager = LanguageManager(HamQSettings())
    menu = build_language_menu(manager)
    button = LanguageSwitchButton(manager)
    assert receivers() != before
    for _ in range(2):  # safe to call twice
        button.cleanup()
        menu.cleanup()
        manager.cleanup()
    assert receivers() == before
    button.deleteLater()
    menu.deleteLater()
    manager.deleteLater()
    process_events()
