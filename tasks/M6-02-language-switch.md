# M6-02: Language switch (manager, menu, toolbar button)

**Milestone:** M6
**Status:** done
**Skills:** pyqgis-plugin

## Goal
Switch the HamQ interface between English, Srpski (latinica) and Српски (ћирилица) at
run time, without restarting QGIS: a `LanguageManager` that owns the setting and applies
it, a language menu and a toolbar button `EN` / `SR` / `СР`.

## Scope
- `hamq/gui/language.py`
- `hamq/i18n/sr_Latn/gui_language.json`
- `tests/qgis/test_language.py`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: the shared block
  "M6-02 / M0-03 / M3-03" (`TOOLBUTTON_MENU_BUTTON_POPUP`)

## Out of scope
- Wiring into `plugin.py` (integration; the tested wiring is in the Notes).
- The language choice of the settings dialog (M0-03, `tasks/M0-03-settings-dialog.md`).
- `CHANGELOG.md` (not to be edited by this task).

## Checklist
- [x] `LanguageManager(settings=None, parent=None)`: `setting()`, `language()`,
      `set_setting(value) -> str`, `toggle() -> str`, `apply_from_settings() -> str`,
      `cleanup()`, signal `settingChanged(str)`
- [x] `set_setting` persists `settings.language`, updates `settings.last_serbian` when a
      Serbian script is applied, calls `core.i18n.set_language(resolved)`, emits
      `events().languageChanged(resolved)` only when the resolved language changes;
      `ValueError` (and no change at all) for an unknown value
- [x] `qgis_ui_locale()`: QGIS locale override, `qgis --lang`, else the system locale
      with its script (checked against QGIS `src/app/main.cpp` 3.34 and master)
- [x] `build_language_menu(manager, parent) -> LanguageMenu(QMenu)`: exclusive checkable
      Auto (QGIS language) / English / Srpski (latinica) / Српски (ћирилица); native
      names never translated, only "Auto" is; kept in sync with the manager
- [x] `LanguageSwitchButton(QToolButton)`: `EN` / `SR` / `СР`, click toggles EN <-> last
      Serbian script, arrow opens the menu (`MenuButtonPopup`), tooltip always
      "Language / Jezik"
- [x] Slots never raise into Qt (logged as `Critical` with the traceback); `cleanup()`
      everywhere, safe to call twice
- [x] Catalog `gui_language.json`

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` pass on the files of this task
- [x] `scripts/test_qgis.sh all -k test_language.py` passes on local 4.2, 3.44, 4.0, 3.34
- [x] Switching emits the signals and changes the core translation; menu and button
      follow every change, whoever makes it

## Result

### What changed
- `hamq/gui/language.py` (new). Public names (`__all__`): `CHOICES`, `SWITCH_TITLE`,
  `BUTTON_TEXTS`, `qgis_ui_locale`, `LanguageManager`, `LanguageMenu`,
  `build_language_menu`, `LanguageSwitchButton`.
  - `LanguageManager.setting()` reads `HamQSettings.language`; `language()` is the
    language in effect, i.e. the HamQ translator's (`core.i18n.current_language()`).
    `languageChanged` is decided against the translator too, so it stays correct even
    if something else called `core.i18n.set_language`.
  - `set_setting(" SR-latn ")` is accepted (case, `-`/`_`, spaces); anything else raises
    `ValueError` before anything is stored.
  - `toggle()`: English -> `settings.last_serbian`, Serbian -> English, always as an
    explicit choice (so "Auto" becomes the chosen language).
  - `last_serbian` is updated whenever a Serbian script is applied, also through
    "Auto" (a QGIS in Cyrillic makes the toggle go back to Cyrillic).
  - `settingChanged(str)` reports the stored choice (Auto -> English with an English
    QGIS changes the choice, not the language). The menu syncs its check from it.
  - The manager re-applies the stored choice on `events().settingsChanged`.
  - It logs `core.i18n.load_problems()` (broken catalog files) once, as warnings, when it
    first applies Serbian; English never loads the catalogs.
- `qgis_ui_locale()`, in the order QGIS uses for its own interface
  (`src/app/main.cpp`, identical in 3.34 and master):
  1. "Override system locale" on (`locale/overrideFlag`, bool) with
     `locale/userLocale`: that locale;
  2. `qgis --lang xx`: `QgsApplication.translation()` (3.22+) when it differs from the
     system locale name (QGIS writes `--lang` into `locale/userLocale` but leaves the
     flag off);
  3. else the system locale with its script: `QLocale.bcp47Name()` (`sr-Latn`), plus the
     POSIX modifier (`LANG=sr_RS.UTF-8@latin`) that Qt ignores.
  `locale/userLocale` alone is not used: without the override QGIS rewrites it with
  `QLocale().name()` at every start, and `QLocale("sr_Latn_RS").name() == "sr_RS"`
  (Cyrillic). The test `test_qgis_stores_the_locale_override_under_these_keys` checks
  the keys in `QgsSettingsTree.node("locale")` and that `QgsApplication.locale()` reads
  them, on all four versions.
- `LanguageMenu` (title "Language / Jezik", icon `language.svg`): Auto, separator, the
  three native names; a failed switch puts the check back on the real choice.
- `LanguageSwitchButton(manager, parent=None, menu=None)`: shares a given menu (left to
  its owner) or makes and cleans up its own.
- `hamq/i18n/sr_Latn/gui_language.json`: 1 string (the slot error message). The
  "Auto (QGIS language)" text comes from `core.i18n.language_name` (`core_i18n.json`).
- `tests/qgis/test_language.py`: 40 tests (locale sources incl. `--lang` and the POSIX
  modifier, manager signals and persistence, invalid values, toggle, Auto, translator as
  the truth, catalog problems logged once, menu, button, shared menu, cleanup leaves no
  receivers on `events()`).

### Commands and outcomes
```
scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_language.py or test_settings_dialog.py
    or test_locator_search.py or test_azimuthal.py or test_compat.py"
  local  QGIS 4.2.1 (Qt 6.10)        PASS  285 passed  (test_language.py: 40 of them)
  3.44   qgis/qgis:3.44-trixie       PASS  285 passed
  4.0    qgis/qgis:4.0-trixie        PASS  285 passed
  3.34   camptocamp/qgis-server:3.34 PASS  285 passed  (no skips: qgis.gui is available)
whole tests/qgis on the host QGIS 4.2 (python3 -m pytest tests/qgis -q)
                                                      -> 712 passed, 2 skipped (the skips need
                                                         live Hamlib daemons)
python3 -m pytest tests/core -q                       -> 3172 passed, 8 xfailed
ruff check / ruff format --check (my 8 files + compat.py, test_compat.py) -> clean
```

### Manual checks still needed
- In QGIS desktop (3.34 and 4.x): the button in the HamQ toolbar (arrow, width of
  `EN`/`SR`/`СР`), the menu under Plugins > HamQ, switching while the dock, the settings
  dialog and Processing dialogs are open.
- "Auto" with QGIS set to Serbian (Settings > Options > General > Override system
  locale, `sr` and `sr@latin`) and with `qgis --lang sr@latin`.

## Notes
- Integration (plugin.py owner): wiring through the registry, checked by a scratch test
  (two load/unload rounds; `events()` and project receivers back to the start, nothing
  left in the project):
  ```python
  settings = HamQSettings()
  self.language_manager = LanguageManager(settings, self.iface.mainWindow())
  self.language_manager.apply_from_settings()      # before actions are created
  self.add_cleanup(self.language_manager.cleanup)
  menu = build_language_menu(self.language_manager, self.iface.mainWindow())
  self.iface.addPluginToMenu(MENU_TITLE, menu.menuAction())
  self.add_cleanup(menu.deleteLater)
  self.add_cleanup(lambda: self.iface.removePluginMenu(MENU_TITLE, menu.menuAction()))
  self.add_cleanup(menu.cleanup)
  button = LanguageSwitchButton(self.language_manager, menu=menu)
  self.toolbar.addWidget(button)                   # deleted with the toolbar
  self.add_cleanup(button.cleanup)
  ```
  The manager already logs catalog load problems; plugin.py should not log them again.
- While "Auto" is chosen, a QGIS locale override changed in the options takes effect in
  HamQ at its next apply (settings saved, plugin start), while QGIS's own interface
  changes only after a restart.
