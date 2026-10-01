# M0-03: Settings dialog

**Milestone:** M0 (settings), with the M6 language choice and the M7 Hamlib settings
**Status:** done
**Skills:** pyqgis-plugin, maidenhead

## Goal
One dialog for every HamQ setting: station, storage, WSJT-X, Hamlib radio and rotator,
language and the DXCC data. OK validates and saves, Cancel changes nothing.

## Scope
- `hamq/gui/settings_dialog.py`
- `hamq/i18n/sr_Latn/gui_settings_dialog.json`
- `tests/qgis/test_settings_dialog.py`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: `FILE_DIALOG_DONT_CONFIRM_OVERWRITE`
  in the shared block "M6-02 / M0-03 / M3-03"

## Out of scope
- The "Settings" action in `plugin.py` (integration; see Notes).
- Restarting the WSJT-X listener or the Hamlib clients after a save (whoever owns them
  listens to `events().settingsChanged`).
- `CHANGELOG.md`.

## Checklist
- [x] `SettingsDialog(settings, language_manager, cty_manager=None, parent=None)`, `exec()` only
- [x] Station: my callsign (uppercased while typing; letters, digits and `/`), my QTH locator
      validated with `core.maidenhead` with live feedback (center shown / invalid / not set)
- [x] Storage: GeoPackage path, "Browse…" (an existing log can be picked without an
      overwrite question), "Use default"
- [x] WSJT-X: UDP address, port 1..65535, autostart, hint with the WSJT-X menu path and
      the multicast set-up
- [x] Radio and rotator (Hamlib): rigctld enabled / address / port / poll interval;
      rotctld enabled / address / port / min and max azimuth with presets 0..360,
      0..450, -180..180 (and Custom); "ask again before turning the antenna" (resets
      `rot_confirmed` on OK)
- [x] Language: Auto / English / Srpski (latinica) / Српски (ћирилица), applied on OK
      through the `LanguageManager`
- [x] DXCC data: date of the cty.dat download, "Download now" -> `cty_manager.download()`
      with a status label; disabled without a manager and while a download runs
- [x] OK: validate (problems listed under the tabs, first invalid field focused, its tab
      shown), save all or nothing, apply the language, emit `events().settingsChanged`
      once; Cancel / Esc / window close: nothing saved, nothing emitted
- [x] `retranslate()` while open (also the shown problems); connected to
      `events().languageChanged` and `downloadFinished` only while shown; `cleanup()`
      safe twice
- [x] Slots and Qt virtual methods (`accept`, `done`, `showEvent`) never raise into Qt
- [x] Catalog `gui_settings_dialog.json`

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` pass on the files of this task
- [x] `scripts/test_qgis.sh all -k test_settings_dialog.py` passes on local 4.2, 3.44, 4.0, 3.34
- [x] Loads and saves every field incl. the Hamlib ones; rejects an invalid locator, port
      clashes and invalid addresses; Cancel is a no-op

## Result

### What changed
- `hamq/gui/settings_dialog.py` (new): `SettingsDialog(QDialog)` plus `LANGUAGE_CHOICES`
  and `AZIMUTH_PRESETS`. Public methods: `load()`, `save() -> bool`,
  `validate() -> list[str]`, `selected_language()`, `retranslate()`, `cleanup()`;
  `accept()` / `done()` / `showEvent()` are overridden.
  - Four tabs: Station (Station and Storage groups), WSJT-X, Radio and rotator (with a
    one-line Hamlib hint), General (Language and DXCC data groups).
  - Validation on OK:
    - callsign: letters, digits and `/`;
    - locator: `maidenhead.normalize` (2/4/6/8 characters, 10 cut to 8);
    - GeoPackage: an absolute path (`~` expanded) in an existing folder, not a folder;
    - WSJT-X address: what `net/wsjtx_listener.py` accepts, i.e. an IPv4 address
      (unicast or multicast) or `localhost`. IPv6 is rejected with its own message;
    - rigctld / rotctld addresses: not empty, no spaces;
    - when both daemons are enabled, they must not share the port on the same host
      (`localhost` = `127.0.0.1` = `::1`);
    - azimuth: max > min, range at most 720°.
  - Ports are spin boxes 1..65535; the poll interval is 100..60000 ms.
  - `save()` writes all values; if a setter still refuses one (`ValueError`), every value
    is written back as before and the error is shown: all or nothing.
  - The language goes through `language_manager.set_setting()` (stored and applied) or,
    without a manager, only into `settings.language`.
  - "Download now" asks the manager first: with `CtyManager.is_downloading()` (present in
    `net/cty_download.py`) a download started elsewhere also disables the button and shows
    "Downloading…". A download that reports its end inside `download()` is handled. A
    reopened dialog is never stuck in "Downloading…".
  - OK / Cancel texts are set by HamQ (Qt's own come from the QGIS language).
- `hamq/i18n/sr_Latn/gui_settings_dialog.json`: 64 strings, all used.
- `tests/qgis/test_settings_dialog.py`: 38 tests. They cover:
  - loading every field and the defaults; OK saving every field and emitting once;
  - Cancel being a no-op; `exec()` accept and reject;
  - an invalid locator (dialog stays open, its tab is shown); live locator feedback;
    callsign upper-casing with real key clicks;
  - port limits and clashes; invalid addresses and paths; valid WSJT-X addresses;
  - all-or-nothing save; the GeoPackage buttons;
  - a real `QFileDialog` opened through the static call with `options=` (Cancel and Save
    pressed by a timer; checks `DontConfirmOverwrite`);
  - presets; resetting the map-click confirmation;
  - "Download now": normal, started elsewhere, failing at once, reopened dialog, no
    manager;
  - working without a language manager; retranslation while open (Latin and Cyrillic,
    focus and tab kept); connections only while shown; `~` expansion.

### Commands and outcomes
```
scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_language.py or test_settings_dialog.py
    or test_locator_search.py or test_azimuthal.py or test_compat.py"
  local  QGIS 4.2.1 (Qt 6.10)        PASS  285 passed  (test_settings_dialog.py: 38 of them)
  3.44   qgis/qgis:3.44-trixie       PASS  285 passed
  4.0    qgis/qgis:4.0-trixie        PASS  285 passed
  3.34   camptocamp/qgis-server:3.34 PASS  285 passed  (no skips)
whole tests/qgis on the host QGIS 4.2                -> 712 passed, 2 skipped (live Hamlib only)
python3 -m pytest tests/core -q                      -> 3172 passed, 8 xfailed
ruff check / ruff format --check                     -> clean
```

### Manual checks still needed
- In QGIS desktop (3.34 and 4.x): layout and sizes of the four tabs, the native file
  dialog on Windows / macOS (the test uses Qt's own dialog of the offscreen platform),
  "Download now" against country-files.com, and saving while the dock and the WSJT-X
  listener are running.

## Notes
- Integration (plugin.py owner):
  ```python
  def show_settings(self):
      dialog = SettingsDialog(HamQSettings(), self.language_manager, self.cty_manager,
                              self.iface.mainWindow())
      try:
          dialog.exec()
      finally:
          dialog.deleteLater()
  ```
  The dialog disconnects itself when it closes. Whoever owns the WSJT-X listener and the
  Hamlib clients restarts them on `events().settingsChanged`.
- `HamQSettings.wsjtx_addr` cannot be empty; use `0.0.0.0` to listen on every address.
