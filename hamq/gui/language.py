"""Interface language switch: English / Srpski (latinica) / Српски (ћирилица), no restart.

:class:`LanguageManager` owns the language setting (``HamQSettings.language``:
``auto``, ``en``, ``sr_Latn`` or ``sr_Cyrl``). It resolves the setting (``auto``
follows the QGIS interface locale, see :func:`qgis_ui_locale`), applies the result
to the HamQ translator (``hamq.core.i18n.set_language``) and announces a change of
the resolved language with ``events().languageChanged(language)``. Every widget
retranslates itself from that signal, so the switch needs no restart.

:func:`build_language_menu` makes the menu with the four choices and
:class:`LanguageSwitchButton` the toolbar button (``EN`` / ``SR`` / ``СР``: a
click toggles English and the last used Serbian script, the arrow opens the
menu). Both follow the manager. The native language names are never translated
(a user looks for their own language written in it); only "Auto (QGIS
language)" is. The menu title and the button tooltip are the bilingual
:data:`SWITCH_TITLE`, readable whatever the current language is.

Widgets connect to ``events()`` (a process-wide object); call their
``cleanup()`` before the plugin unloads. ``cleanup()`` is safe to call twice.
"""

from __future__ import annotations

import contextlib
import functools
import os
import traceback
from collections.abc import Callable
from typing import Any

from qgis.core import QgsApplication, QgsMessageLog, QgsSettings
from qgis.PyQt.QtCore import QLocale, QObject, pyqtSignal
from qgis.PyQt.QtWidgets import QMenu, QToolButton, QWidget

from ..core.i18n import (
    LANG_AUTO,
    LANG_EN,
    LANG_SR_CYRL,
    LANG_SR_LATN,
    current_language,
    is_serbian,
    language_name,
    load_problems,
    resolve_language,
    set_language,
    tr,
)
from ..events import events
from ..qgis_io.compat import (
    MSG_CRITICAL,
    MSG_WARNING,
    TOOLBUTTON_MENU_BUTTON_POPUP,
    TOOLBUTTON_TEXT_ONLY,
    QAction,
    QActionGroup,
)
from ..settings import HamQSettings
from . import get_icon

__all__ = [
    "BUTTON_TEXTS",
    "CHOICES",
    "SWITCH_TITLE",
    "LanguageManager",
    "LanguageMenu",
    "LanguageSwitchButton",
    "build_language_menu",
    "qgis_ui_locale",
]

LOG_TAG = "HamQ"
#: The language choices in menu order: Auto first, then the three languages.
CHOICES: tuple[str, ...] = (LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL)
#: Title of the language menu and tooltip of the switch button. Deliberately bilingual
#: and never translated: whoever cannot read the current language must find the switch.
SWITCH_TITLE = "Language / Jezik"
#: Text of the switch button for each resolved language (codes, not translated).
BUTTON_TEXTS: dict[str, str] = {LANG_EN: "EN", LANG_SR_LATN: "SR", LANG_SR_CYRL: "СР"}

# QGIS 3.34 .. 4.2 keep Settings > Options > General > "Override system locale" in the
# settings tree node "locale": a bool (written as true/false in QGIS3.ini) and the
# chosen locale ("sr@latin", "de", "en_US").
_OVERRIDE_FLAG_KEY = "locale/overrideFlag"
_USER_LOCALE_KEY = "locale/userLocale"
# POSIX locale variables in order of precedence for messages.
_LOCALE_ENV_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")


def _log_error(where: str, exc: BaseException) -> None:
    message = tr("Unexpected error in {slot}: {error}").format(slot=where, error=exc)
    QgsMessageLog.logMessage(f"{message}\n{traceback.format_exc()}", LOG_TAG, MSG_CRITICAL)


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Qt slot: an exception is logged instead of escaping into Qt."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except Exception as exc:  # a slot must never raise into Qt
            _log_error(method.__qualname__, exc)
            return None

    return wrapper


def _disconnect(signal: Any, slot: Callable[..., Any]) -> None:
    """Disconnect ``slot``; nothing happens when it is not connected or the sender is gone."""
    with contextlib.suppress(TypeError, RuntimeError):
        signal.disconnect(slot)


# --------------------------------------------------------------------------- locale


def _to_bool(value: object) -> bool:
    """A ``QgsSettings`` value as bool: ``True``, ``1`` and ``"true"`` are true."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (bool, int)):
        return bool(value)
    return False


def _system_locale() -> QLocale:
    """The operating system locale (a function, so tests can replace it)."""
    return QLocale.system()


def _system_locale_name() -> str:
    """Name of the system locale that keeps its script: ``sr-Latn``, ``sr``, ``en``.

    ``QLocale.name()`` drops the script (``QLocale("sr_Latn_RS").name() == "sr_RS"``,
    which reads as Cyrillic); ``bcp47Name()`` keeps a script that differs from the
    default of the language and territory. Qt also ignores the POSIX modifier of
    ``sr_RS.UTF-8@latin``; when the system locale is Serbian and the locale variable
    carries a modifier, that variable is returned instead.
    """
    locale = _system_locale()
    name = locale.bcp47Name() or locale.name()
    if is_serbian(name):
        for variable in _LOCALE_ENV_VARS:
            value = os.environ.get(variable, "").strip()
            if value:
                if "@" in value and is_serbian(value):
                    return value
                break
    return name


def _qgis_translation() -> str:
    """The translation code QGIS chose for its own interface at start, ``""`` if unknown.

    ``QgsApplication.translation()`` (QGIS 3.22+): ``--lang`` from the command line,
    else ``locale/userLocale`` when the override is on, else ``QLocale().name()``.
    """
    app = QgsApplication.instance()
    try:
        code = app.translation() if app is not None else ""
    except (AttributeError, RuntimeError, TypeError):  # not a QgsApplication
        return ""
    return code.strip() if isinstance(code, str) else ""


def qgis_ui_locale() -> str:
    """Locale of the QGIS user interface, for ``resolve_language("auto", locale)``.

    In the order QGIS itself uses (``src/app/main.cpp``, 3.34 .. 4.x):

    1. Settings > Options > General > "Override system locale" on
       (``locale/overrideFlag``) with a locale (``locale/userLocale``): that locale;
    2. QGIS started with ``--lang <code>``: that code (QGIS stores it in
       ``locale/userLocale`` but does not set the flag);
    3. otherwise the system locale, with its script (see :func:`_system_locale_name`).

    ``locale/userLocale`` alone is not a choice: without the override QGIS rewrites it
    with ``QLocale().name()`` at every start, which also loses the script
    (``sr_Latn_RS`` -> ``sr_RS``, read as Cyrillic).
    """
    settings = QgsSettings()
    if _to_bool(settings.value(_OVERRIDE_FLAG_KEY, False)):
        user_locale = settings.value(_USER_LOCALE_KEY, "")
        if isinstance(user_locale, str) and user_locale.strip():
            return user_locale.strip()
    translation = _qgis_translation()
    if translation and translation != _system_locale().name():
        return translation  # --lang: QGIS's own default would be QLocale().name()
    return _system_locale_name()


def _choice(value: object) -> str:
    """Canonical language choice (``' SR-latn '`` -> ``'sr_Latn'``); ValueError otherwise."""
    if isinstance(value, str):
        text = value.strip().replace("-", "_").casefold()
        for code in CHOICES:
            if code.casefold() == text:
                return code
    raise ValueError(f"unknown language setting {value!r}; expected one of {CHOICES}")


# --------------------------------------------------------------------------- manager


class LanguageManager(QObject):
    """Owns the language setting and applies it to the HamQ translator.

    ``setting()`` is the stored choice (``auto``, ``en``, ``sr_Latn``, ``sr_Cyrl``),
    ``language()`` the resolved language in effect (never ``auto``).
    :meth:`set_setting` and :meth:`toggle` store and apply a new choice,
    :meth:`apply_from_settings` applies the stored one (at start-up). Whenever a
    Serbian script is applied it becomes ``settings.last_serbian``, the target of
    :meth:`toggle`. ``events().languageChanged`` is emitted only when the resolved
    language changes; :attr:`settingChanged` whenever the stored choice changes
    (Auto -> English with an English QGIS changes the choice, not the language).
    The manager also re-applies the stored choice on ``events().settingsChanged``.
    The Serbian catalogs load on their first use; the manager logs their problems
    (``core.i18n.load_problems``, e.g. a broken file) once, when it first applies Serbian.
    """

    #: The stored language choice changed: ``auto``, ``en``, ``sr_Latn`` or ``sr_Cyrl``.
    settingChanged = pyqtSignal(str)

    def __init__(self, settings: HamQSettings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else HamQSettings()
        self._setting = self._settings.language
        self._problems_logged = False
        self._connected = False
        events().settingsChanged.connect(self._on_settings_changed)
        self._connected = True

    def setting(self) -> str:
        """The stored choice: ``auto``, ``en``, ``sr_Latn`` or ``sr_Cyrl``."""
        return self._settings.language

    def language(self) -> str:
        """The language in effect (the HamQ translator's): ``en``, ``sr_Latn`` or ``sr_Cyrl``."""
        return current_language()

    def set_setting(self, value: str) -> str:
        """Store and apply a choice (``auto``, ``en``, ``sr_Latn``, ``sr_Cyrl``, any case).

        Returns the resolved language. Raises ``ValueError`` for anything else, and
        then changes nothing.
        """
        code = _choice(value)
        self._settings.language = code
        return self._apply(code)

    def toggle(self) -> str:
        """Switch English <-> the last used Serbian script; returns the new language."""
        target = LANG_EN if is_serbian(current_language()) else self._settings.last_serbian
        return self.set_setting(target)

    def apply_from_settings(self) -> str:
        """Apply the stored choice (``auto`` resolved now); returns the resolved language."""
        return self._apply(self._settings.language)

    def cleanup(self) -> None:
        """Disconnect from ``events()``. Safe to call twice."""
        if self._connected:
            self._connected = False
            _disconnect(events().settingsChanged, self._on_settings_changed)

    def _apply(self, setting: str) -> str:
        resolved = resolve_language(setting, qgis_ui_locale())
        setting_changed = setting != self._setting
        self._setting = setting
        if is_serbian(resolved) and self._settings.last_serbian != resolved:
            self._settings.last_serbian = resolved
        # compared with the translator itself: correct even if something else switched it
        language_changed = resolved != current_language()
        set_language(resolved)
        if is_serbian(resolved):
            self._log_catalog_problems()
        if setting_changed:
            self.settingChanged.emit(setting)
        if language_changed:
            events().languageChanged.emit(resolved)
        return resolved

    def _log_catalog_problems(self) -> None:
        """Log the problems of loading the Serbian catalogs, once per manager."""
        if self._problems_logged:
            return
        self._problems_logged = True
        try:
            problems = load_problems()  # loads the catalogs now if they were not used yet
        except Exception as exc:  # the language is applied; only the report is missing
            _log_error("LanguageManager._log_catalog_problems", exc)
            return
        for message in problems:
            QgsMessageLog.logMessage(message, LOG_TAG, MSG_WARNING)

    @_guarded
    def _on_settings_changed(self) -> None:
        self._apply(self._settings.language)


# --------------------------------------------------------------------------- menu


class LanguageMenu(QMenu):
    """Menu with exclusive checkable choices: Auto (QGIS language) and the three languages.

    The checked item follows ``manager.setting()``; choosing an item calls
    ``manager.set_setting``. Its title is :data:`SWITCH_TITLE`.
    """

    def __init__(self, manager: LanguageManager, parent: QWidget | None = None) -> None:
        super().__init__(SWITCH_TITLE, parent)
        self.setObjectName("HamQLanguageMenu")
        self.setIcon(get_icon("language.svg"))
        self._manager = manager
        self._group = QActionGroup(self)
        self._group.setExclusive(True)
        self._actions: dict[str, QAction] = {}
        for code in CHOICES:
            action = QAction(self)
            action.setObjectName(f"HamQLanguage_{code}")
            action.setCheckable(True)
            action.setData(code)
            self._group.addAction(action)
            self.addAction(action)
            self._actions[code] = action
        self.insertSeparator(self._actions[LANG_EN])
        self._group.triggered.connect(self._on_triggered)
        self._connected = False
        manager.settingChanged.connect(self._on_setting_changed)
        events().languageChanged.connect(self._on_language_changed)
        self._connected = True
        self.retranslate()
        self.sync()

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    def action_for(self, code: str) -> QAction:
        """The action of a choice (``auto``, ``en``, ``sr_Latn``, ``sr_Cyrl``)."""
        return self._actions[_choice(code)]

    def choice_actions(self) -> list[QAction]:
        """The four choice actions in menu order (without the separator)."""
        return [self._actions[code] for code in CHOICES]

    def retranslate(self) -> None:
        """Re-set the texts: the native names stay, "Auto (QGIS language)" is translated."""
        self.setTitle(SWITCH_TITLE)
        for code, action in self._actions.items():
            action.setText(language_name(code))

    def sync(self) -> None:
        """Check the item of the manager's current choice."""
        action = self._actions.get(self._manager.setting())
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def cleanup(self) -> None:
        """Disconnect from the manager and ``events()``. Safe to call twice."""
        if self._connected:
            self._connected = False
            _disconnect(self._manager.settingChanged, self._on_setting_changed)
            _disconnect(events().languageChanged, self._on_language_changed)

    @_guarded
    def _on_triggered(self, action: QAction) -> None:
        try:
            self._manager.set_setting(action.data())
        finally:
            self.sync()  # the group checked the item already; undo that if the switch failed

    @_guarded
    def _on_setting_changed(self, _setting: str) -> None:
        self.sync()

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()


def build_language_menu(manager: LanguageManager, parent: QWidget | None = None) -> LanguageMenu:
    """Return the language menu (:class:`LanguageMenu`) kept in sync with ``manager``.

    Add it to a menu with ``addMenu(menu)`` or to a tool button with ``setMenu(menu)``;
    call its ``cleanup()`` before the plugin unloads.
    """
    return LanguageMenu(manager, parent)


# --------------------------------------------------------------------------- button


class LanguageSwitchButton(QToolButton):
    """Toolbar button ``EN`` / ``SR`` / ``СР`` showing the current language.

    A click toggles English and the last used Serbian script
    (:meth:`LanguageManager.toggle`); the arrow opens the language menu. The tooltip
    is always the bilingual :data:`SWITCH_TITLE`. Without ``menu`` the button makes
    its own and cleans it up in :meth:`cleanup`.
    """

    def __init__(
        self,
        manager: LanguageManager,
        parent: QWidget | None = None,
        menu: LanguageMenu | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HamQLanguageButton")
        self._manager = manager
        self._own_menu = menu is None
        self._menu = menu if menu is not None else LanguageMenu(manager, self)
        self.setMenu(self._menu)
        self.setPopupMode(TOOLBUTTON_MENU_BUTTON_POPUP)
        self.setToolButtonStyle(TOOLBUTTON_TEXT_ONLY)
        self.setAutoRaise(True)
        self.setAccessibleName(SWITCH_TITLE)
        self.clicked.connect(self._on_clicked)
        self._connected = False
        events().languageChanged.connect(self._on_language_changed)
        self._connected = True
        self.retranslate()

    def language_menu(self) -> LanguageMenu:
        """The menu opened by the arrow."""
        return self._menu

    def retranslate(self) -> None:
        """Show the code of the current language; the tooltip stays bilingual."""
        self.setText(BUTTON_TEXTS.get(current_language(), BUTTON_TEXTS[LANG_EN]))
        self.setToolTip(SWITCH_TITLE)

    def cleanup(self) -> None:
        """Disconnect from ``events()`` (and clean up an own menu). Safe to call twice."""
        if self._connected:
            self._connected = False
            _disconnect(events().languageChanged, self._on_language_changed)
            if self._own_menu:
                self._menu.cleanup()

    @_guarded
    def _on_clicked(self, _checked: bool = False) -> None:
        self._manager.toggle()

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()
