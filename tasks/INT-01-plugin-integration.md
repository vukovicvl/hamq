# INT-01: Integration — HamQController, plugin wiring, runtime language switch, end-to-end tests

**Milestone:** M5 / M6 / M7 (integration of all milestones)
**Status:** done
**Skills:** pyqgis-plugin, wsjtx-udp, hamlib

## Goal
Wire the finished components into a working plugin: a `HamQController` that owns the
settings, the language manager, cty.dat, the WSJT-X listener, the Hamlib clients, the
azimuthal map, the rotator map tool and the panel, and a `plugin.py` with every menu and
toolbar action. The language switch updates the whole interface at once, and unloading
leaves nothing behind.

## Scope
- `hamq/controller.py` (new)
- `hamq/plugin.py` (scaffold extended, registry structure kept)
- `hamq/i18n/sr_Latn/controller.json` (new), `hamq/i18n/sr_Latn/plugin.json`
- `tests/qgis/test_integration.py` (new), `tests/qgis/test_plugin_load.py` (extended)
- `hamq/qgis_io/compat.py` + `tests/qgis/test_compat.py`: small appended block "INT-01"
  (`TASK_CANCEL_WITHOUT_PROMPT`, `TASK_HIDDEN`, `TASK_SILENT`)
- this task file

## Out of scope
- The components themselves (dock, dialogs, listener, clients, storage, algorithms, core).
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md`, `README.md` (see Notes).
- `hamq/settings.py`, `hamq/events.py`: nothing was missing; unchanged.

## Checklist
- [x] `HamQController(QObject)` owns `HamQSettings`, `LanguageManager`, `CtyManager`,
      `WsjtxListener`, `RigClient`, `RotatorClient`, `AzimuthalMap`, `RotatorMapTool` and
      `HamQDock`; `start()` once, `cleanup()` (safe twice) releases everything.
- [x] `ensure_storage()`, `load_layers()`, `refresh()`: `read_qso_rows` +
      `compute_stats` in a hidden, cancelable `QgsTask` (requests while one runs are merged
      into one more), result to `dock.set_stats`; `refresh_now()` is the synchronous variant.
- [x] Live WSJT-X: `adifLogged` -> `parse_adi` -> `record_to_qso(station, cty.database(),
      source "wsjtx")` -> `insert_qsos` (duplicates skipped by `dedup_key`) -> QSO layers
      added to the project when missing -> `events().dataChanged` (layers and statistics
      refresh) -> message bar "New QSO: {call} {band} {mode} ({distance} km)" (without
      distance when unknown) + `dock.set_last_qso`; "QSO already in the log: ..." for a
      duplicate; unreadable records and save failures reported in the message bar.
- [x] Dock WSJT-X tab: start/stop both ways (button <-> action <-> controller), heartbeat,
      status, connection, client close, listener errors (dock + message bar).
- [x] Hamlib: clients follow `rig_*` / `rot_*` settings; dock Radio tab wired both ways
      (state, errors, Set = frequency + mode only when it differs, Turn via
      `core.hamlib.rotator_target` with an out-of-range warning, Stop); rotator map tool ->
      `RotatorClient.set_position` + dock target; the tool is released when the rotator
      disconnects or another map tool takes over (the previous tool comes back when the
      user turns it off).
- [x] Manual QSO: dialog prefilled from the rig when connected, saved through
      `record_to_qso` + `insert_qsos` (source `manual`).
- [x] Settings changes: listener restarts on a new address/port, Hamlib clients restart or
      stop, statistics follow `gpkg_path`, hints close when fixed.
- [x] First run: no cty.dat -> message bar hint with a "Download" button; no valid locator ->
      hint with a "Settings..." button; both retranslated, removed on unload.
- [x] `plugin.py`: Show HamQ panel (checkable, follows the dock), Import ADIF..., Listen to
      WSJT-X (checkable), Log QSO..., Point antenna on map (checkable), Azimuthal map
      (checkable), Maidenhead grid..., Locator to point..., Recalculate distances and
      DXCC data... (named like the algorithm since the release pass; "Recalculate
      distances and paths..." before), Download cty.dat, Settings..., Language submenu,
      About; toolbar: panel,
      import, listen, point, azimuthal, settings | locator search | EN/SR/СР switch.
      Processing dialogs through `processing.execAlgorithmDialog` (imported lazily).
- [x] Language switch updates actions, menus, dock, toolbar widgets, open dialogs, hints,
      `provider.refreshAlgorithms()`, field aliases and unrenamed layer names (also after
      a project is read and once at start).
- [x] `unload()`: listener, clients, timers, task, map tool, azimuthal map (CRS restored),
      hints, dock, toolbar (widgets), menu, provider, every connection (also `events()` and
      project signals); load/unload/load/unload leaves nothing; a failing `initGui()`
      undoes itself.
- [x] Translations in `controller.json` / `plugin.json` (natural Serbian, glossary).
- [x] Tests on all four QGIS targets; real QGIS smoke runs on 4.2 and 3.44.

## Acceptance criteria
- [x] `pytest tests/core -q` passes (3244 passed, 8 xfailed)
- [x] `ruff check hamq tests`, `ruff format --check hamq tests` clean
- [x] `scripts/test_qgis.sh all -k "test_integration or test_plugin_load or test_compat"`
      passes on local 4.2, 3.44, 4.0 and 3.34
- [x] A WSJT-X QSO is on the map in under 2 s (tests: ~0.1 s; real QGIS 4.2: 0.12 s; real
      QGIS 3.44 in Docker: 1.08 s for the first QSO, 0.09 s later, see Notes)
- [x] 10 000 QSOs refresh in under 300 ms without blocking the UI (~100 ms, in a QgsTask)
- [x] load/unload twice: no leftovers, no warnings in the log

## Result

### What changed
- `hamq/controller.py`: `HamQController` (see the module docstring for the flows).
  Public API: `HamQController(iface, settings=None, parent=None, *, cty_manager=None)`;
  attributes `settings`, `language_manager`, `cty_manager`, `listener`, `rig`,
  `rotator`, `azimuthal`, `dock`, `rotator_tool`, `stats`; signals
  `listeningChanged(bool)`, `pointOnMapChanged(bool)`, `statsChanged(object)`; methods
  `start`, `cleanup`, `is_started`, `gpkg_path`, `ensure_storage`, `load_layers`,
  `refresh`, `refresh_now`, `is_refreshing`, `set_listening`, `is_listening`,
  `handle_logged_adif`, `log_qso`, `save_records`, `set_rig`, `turn_rotator`,
  `stop_rotator`, `set_point_on_map`, `is_pointing_on_map`, `show_settings`,
  `download_cty`, `open_algorithm_dialog`, `import_adif`, `hint_keys`; constants
  `ALG_*`, `SOURCE_WSJTX` / `SOURCE_MANUAL`, `MESSAGE_TITLE`.
- `hamq/plugin.py`: `initGui()` creates the controller (lazy import, so `qgis_process`
  never loads the GUI), provider, toolbar, dock, actions, language menu, toolbar widgets,
  `layers.connect_events()`, `readProject` -> `layers.retranslate_layers()`, then
  `controller.start()`; a failing `initGui()` calls `unload()` and re-raises. New public
  attributes: `controller`, `panel_action`, `import_action`, `listen_action`,
  `log_qso_action`, `point_action`, `azimuthal_action`, `grid_action`, `locator_action`,
  `recalculate_action`, `cty_action`, `settings_action`, `language_menu`,
  `language_button`, `locator_search`. Every slot is guarded (exceptions are logged).
- `hamq/qgis_io/compat.py` / `tests/qgis/test_compat.py`: block "INT-01" with three
  `QgsTask` flags (verified on 3.34, 3.44, 4.0, 4.2) and `test_task_flags_combine`.
- Catalogs: `controller.json` (24 strings), `plugin.json` (+18 strings, 29 in all).
- Tests: `tests/qgis/test_integration.py` (21 end-to-end tests),
  `tests/qgis/test_plugin_load.py` (stale M0 assumptions fixed — 4 algorithms, the full
  menu, receivers of every process-wide signal — plus 12 wiring tests).

### Commands and outcomes
```
python3 -m pytest tests/core -q                                  -> 3244 passed, 8 xfailed
HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -k "controller or plugin"
                                                                  -> 14 passed (whole file 303 passed)
ruff check hamq tests / ruff format --check hamq tests           -> clean
scripts/test_qgis.sh local -q -p no:cacheprovider               -> whole tests/qgis: 972 passed,
                                                                     2 skipped (live Hamlib only)
scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_integration or test_plugin_load or test_compat"
```
Final results per target are in the table under "Final run" below.

### Real QGIS smoke (desktop binary, offscreen, temporary profile)
Scratch scripts (not in the repo): `smoke.py` run with
`qgis --profiles-path <tmp> --nologo --noversioncheck --code smoke.py`, the plugin
symlinked into `<tmp>/profiles/default/python/plugins/hamq`, enabled through
`PythonPlugins/hamq=true` and the `hamq/` settings pre-seeded in `QGIS4.ini` /
`QGIS3.ini`; Hamlib 4.6.2 `hamq/hamlib-dummy` container (unique name, free 127.0.0.1
ports on the host, bridge IP from the 3.44 container; removed afterwards). 27 steps, all
PASS on both:

| step | QGIS 4.2.1 (host) | QGIS 3.44.15 (`qgis/qgis:3.44-trixie`, `DISPLAY=:0` + offscreen) |
|---|---|---|
| plugin active, dock, toolbar, menu (13 entries), provider (4 algorithms), cty hint | PASS | PASS |
| panel action off/on | PASS | PASS |
| listen action, heartbeat/status -> "Connected: WSJT-X 2.7.0" | PASS | PASS |
| first live QSO on the map | 0.12 s | 1.08 s (HamQ part 0.10 s, see Notes) |
| second live QSO on the map | 0.05 s | 0.09 s |
| "New QSO: VK2XYZ 20m FT8 (15 642 km)", statistics | PASS | PASS |
| EN -> SR -> СР -> EN: action, algorithm name + parameter, layer name, alias, button | PASS | PASS |
| azimuthal map on/off restores the CRS | PASS | PASS |
| point antenna without rotator: warning, unchecked | PASS | PASS |
| 7 modal dialogs open and close (settings, log QSO, 4 Processing, About) | PASS | PASS |
| real rigctld/rotctld: connected, Set 7.074 MHz CW, map aim -> rotator at ~91° | PASS | PASS |
| real cty.dat download (version 2026-09-15, 346 entities), hint closed | PASS | PASS |
| unloadPlugin: menu, dock, toolbar, provider, map tool gone; UDP port free | PASS | PASS |
| loadPlugin + startPlugin again, unload again | PASS | PASS |

The only HamQ warning in the log was the expected "The rotator is not connected ...".

### Measurements
- Statistics of 10 000 QSOs (scratch script, 5 refreshes): host 4.2: 96-146 ms, longest
  event-loop gap 12-15 ms; 3.44 container: 100-119 ms, gap 13-21 ms (synchronous
  `refresh_now`: 92 ms). The test asserts < 300 ms and gaps < 100 ms on every target.
- Live QSO in the tests: well under 1 s on every target (asserted < 2 s).

### Manual checks still needed
- QGIS desktop on screen (3.34 / 3.40 LTR and 4.x, Windows / macOS): toolbar layout with
  the locator field and the EN/SR/СР button, dock restore between sessions, the message
  bar hints, a real WSJT-X / JTDX and a real radio / rotator.
- Language switch while a Processing dialog is open: QGIS does not retranslate an open
  Processing dialog (its texts change the next time it is opened).

## Final run
`scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_integration or test_plugin_load or test_compat"`:

| target | environment | result | pytest |
|---|---|---|---|
| local | host QGIS 4.2.1 (Qt6) | PASS | 239 passed in 8.3 s |
| 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS | 239 passed in 76.9 s |
| 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS | 239 passed in 38.4 s |
| 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS | 226 passed, 13 skipped in 30.8 s |

The 13 skips on 3.34 are the existing SVG icon checks of `test_plugin_load.py` (QtSvg is
not in that image); every INT-01 test ran on every target. The two earlier `all` rounds
failed on 3.34 only, in `test_load_and_unload_twice`: objects of earlier tests that waited
for Python's garbage collector were still counted as receivers of
`QgsProject.readProject` and left during the test. A test fix: the receiver counts are now
taken after `gc.collect()` and the deferred deletes.

## Notes
- **First live QSO in the 3.44 container takes ~1.1 s**: HamQ's own handling is 0.10 s
  (`handle_logged_adif` incl. creating the layers, measured inside the run); the rest is
  one event-loop pass of ~1 s right after the layers were added, during which QGIS 3.44
  installed the missing "Open Sans" font ("Installed font Open Sans" in the message bar),
  most likely for its first render. Later QSOs take 0.09 s; on the host 0.12 s.
- **`rig_poll_ms` upper bound**: `settings.py` only checks `> 0`; `RigClient` rejects more
  than 2^31-1 ms, so a hand-edited value would have stopped the plugin from loading. The
  controller clamps the interval to the dialog's 100..60000 ms. A matching validation in
  `settings.py` would change its contract, so it was left as is (contract change request).
- **QSO source `manual`** for QSOs entered in the dialog (PLAN.md lists `adif:<file>` and
  `wsjtx` only).
- **Hints are shown at every start** while cty.dat or the locator is missing (closing one
  hides it for the session).
- **Set on the Radio tab** sends the mode only when it differs from the radio's mode, so a
  frequency change keeps the radio's passband; a new mode gets the radio's default
  passband (`+M <mode> 0`).
- Dynamic texts that the listener / clients / cty manager produced before a language switch
  (errors in the dock, transient message bar items) stay in the old language (M5-03 note).
  Since the release pass the listener and the Hamlib clients offer `current_error()` (the
  problem translated again); `_on_language_changed` does not call it yet (open, see M5-03).
- **Release pass (2026-10-01)** (plugin / controller fixer; the "after" column of the
  smoke below was measured on the fixed code in the docs pass):
  - **Full unload:** `classFactory` imports every subpackage by name (`core`, `gui`, `net`,
    `processing`, `qgis_io`), so QGIS records them and `unloadPlugin` removes all HamQ
    modules. Before, `hamq.processing` stayed in `sys.modules`, kept the modules of the old
    load alive and was reused by the next load (a plugin upgrade ran stale code).
  - **Tooltips follow the language:** at startup QGIS's shortcuts manager turns the tooltip
    of every main-window action into fixed text (`<b>Log QSO</b>`). The six actions without
    a tooltip of their own (Log QSO, Maidenhead grid, Locator to point, Recalculate,
    Download cty.dat, About) kept that English text after a switch; `_apply_texts` now
    always sets the tooltip (an empty one lets Qt show the current text).
  - **Plain text:** texts pushed to the message bar are HTML-escaped (calls, client ids
    and errors from files or the network never become links); controller and plugin log
    lines are escaped where `compat.LOG_PANEL_SHOWS_HTML` (QGIS 3.34 to 3.40.6, 3.42.0 /
    3.42.1). Still open (net, qgis_io, gui): their own log lines and the three
    `pushMessage` calls of `gui/azimuthal.py`, `gui/locator_search.py` and the rotator
    tool's fallback.
  - A QSO that cannot be saved is reported once, with the warning of `insert_qsos` (it
    names the QSO and the cause), not wrapped in a second "could not be saved".
  - The menu entry is "Recalculate distances and DXCC data...", the algorithm's name.
  - Real desktop smoke, host QGIS 4.2.1 (`qgis --code`, offscreen, temporary profile; a
    scratch script switches to Serbian, sends a WSJT-X record whose CALL is an `<a href>`
    link and a client id with a link, then unloads and loads the plugin three times):

    | check | before the fixes | after |
    |---|---|---|
    | Serbian tooltips still `<b>English</b>` | 6 of 12 | 0 of 12 |
    | live links in HamQ message bar items | 2 | 0 (markup shown as text) |
    | HamQ modules in `sys.modules` after each unload | `hamq.processing` | none |
    | modules of the old load still alive after each unload | 23 | 0 |
    | load + start after each unload | OK | OK |
  - Test runs at the end of the release pass (docs pass, all fixes in):
    `scripts/test_qgis.sh all -q -p no:cacheprovider -k "language or dock or
    settings_dialog or processing or integration or rotator_tool"`: local 4.2.1 339
    passed, 3.44 339 passed, 4.0 339 passed, 3.34 (`camptocamp/qgis-server`) 337 passed
    and 2 skipped (QtSvg, `qgis_process` lacks a library in that image). Whole
    `tests/qgis`: host 4.2.1 1086 passed, 2 skipped; `qgis/qgis:3.34` (3.34.15),
    `qgis/qgis:3.40` (3.40.15) and `qgis/qgis:4.2-trixie` (4.2.3, after the
    `test_fields.py` fix of M0-02) 1085 passed, 3 skipped each.
- **CHANGELOG.md** (not edited): "Plugin wiring: HamQ menu and toolbar with every action,
  panel, live WSJT-X QSOs on the map with a message, Hamlib radio and rotator from the
  panel and the map, manual QSO entry, first-run hints for cty.dat and the QTH locator,
  language switch of the whole interface without restart (INT-01)."
