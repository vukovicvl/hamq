# M4-04: Statistics dock (HamQ panel)

**Milestone:** M4
**Status:** done
**Skills:** pyqgis-plugin, adif, geodesy

## Goal
The HamQ dock panel `HamQDock(QDockWidget)` (objectName `HamQDock`) and its
**Statistics** tab. The tab shows a `core.stats.QsoStats`, or a friendly empty state
with an "Import ADIF..." button. The panel has no business logic: the controller feeds
it through `set_*` methods and reacts to its signals. The WSJT-X tab (M5-03) and the
Radio tab (M7-03) live in the same widget.

## Scope
- `hamq/gui/dock.py` (whole panel; the WSJT-X and Radio tabs are described in M5-03 / M7-03)
- `tests/qgis/test_dock.py`
- `hamq/i18n/sr_Latn/gui_dock.json`
- `tasks/M4-04-stats-dock.md`

## Out of scope
- Controller / plugin wiring: creating the dock with `add_dock_widget`, computing
  `compute_stats(read_qso_rows(path))` (in a `QgsTask` for big logs) on
  `events().dataChanged`, the import and settings actions behind the signals.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md`, `README.md` (not mine in this run).

## Checklist
- [x] `HamQDock(parent=None, settings=None)`, objectName `HamQDock`, all dock areas,
      tabs Statistics / WSJT-X / Radio with icons from `gui.get_icon`
- [x] Header: my callsign and locator (`HamQSettings`, re-read on `events().settingsChanged`;
      `set_station(call, grid)` overrides). A hint to set them in Settings when the
      callsign is missing or the locator is not a valid Maidenhead locator.
      Refresh / Import ADIF / Settings tool buttons.
- [x] Tiles: Total QSOs, DXCC entities, Unique callsigns, Grid squares, Longest QSO
      (call, distance in km, country; band · mode · UTC time as tooltip)
- [x] Compact tables By continent / By band / By mode with count and share %. The
      continent names are translated, and "Unknown" (`"?"`) is always the last row.
      Each table is exactly as tall as its rows, so the tab scrolls and the tables don't.
- [x] First / last QSO date (UTC)
- [x] `set_stats(QsoStats | None)`: `None`, or a log with 0 QSOs, gives the empty state
      ("No QSOs yet", "Import ADIF..." button -> `importRequested`)
- [x] Signals `refreshRequested`, `importRequested`, `settingsRequested`. Setters never
      emit the signals.
- [x] Numbers in the HamQ language: decimal comma in Serbian, thousands grouped with a
      no-break space from 5 digits (`15 676 km`)
- [x] `retranslate()` re-applies every text from the last known state: tab titles,
      tiles, table headers and rows, state texts, tooltips, the compass letters and the
      frequency spin box. It is connected to `events().languageChanged`.
- [x] Every slot, setter and Qt virtual is guarded: an exception is logged
      (`QgsMessageLog`, tag HamQ, Critical) and never reaches Qt
- [x] `cleanup()` disconnects from `events()` and is safe to call twice. Safety net: deleting
      the dock without `cleanup()` also drops those connections. PyQt does not do this by
      itself; checked on Qt5 and Qt6.
- [x] Serbian (Latin) catalog with the glossary terms; Cyrillic is derived at runtime
- [x] Tests on QGIS 4.2 (host), 3.44, 4.0 and 3.34 (Docker)

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` clean for the files in scope
- [x] The dock shows a realistic `QsoStats` and the empty state for `None`
- [x] Switching to `sr_Latn` and `sr_Cyrl` changes every visible text. The test checks
      that no English text is left except names, numbers and units.
- [x] `scripts/test_qgis.sh all` passes on all four targets

## Result

### What changed
- `hamq/gui/dock.py` (new). Besides the panel, it has formatting helpers that the
  rotator tool and the QSO dialog reuse: `format_number`, `format_mhz`, `format_distance`,
  `format_azimuth`, `format_bearing`, `format_utc` and `decimal_separator`.
- `tests/qgis/test_dock.py` (new): 39 tests covering all three tabs.
- `hamq/i18n/sr_Latn/gui_dock.json` (new): 70 strings.

### Panel API (Statistics part)
| Member | Meaning |
|---|---|
| `set_stats(stats)` | `QsoStats` or `None` (empty state) |
| `set_station(call, grid)` | header text; normally read from `HamQSettings` |
| `refreshRequested()` / `importRequested()` / `settingsRequested()` | header buttons and the empty-state button |
| `retranslate()` | also runs on `events().languageChanged` |
| `cleanup()` | before deleting the dock (twice is fine) |
| `current_tab()` / `show_tab(index)` | `TAB_STATISTICS`, `TAB_WSJTX`, `TAB_RADIO` |

### Commands and outcomes
- `scripts/test_qgis.sh all -q -k "test_dock or test_rotator_tool or test_qso_dialog"` (final
  run, after the self-review fixes):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2 (Qt6) | PASS: 123 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 123 passed |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 123 passed |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 123 passed |

  The run covers my 121 tests (dock 39, rotator tool 49, QSO dialog 33). The other 2
  match the filter: `test_compat.py::test_dock_areas` and
  `test_plugin_load.py::test_dock_widget_is_removed_on_unload`.
- `scripts/test_qgis.sh local -q`: whole `tests/qgis` suite, 707 passed, 2 skipped. The
  skips are live-daemon tests in `test_hamlib_client.py` that need `HAMQ_RIGCTLD` /
  `HAMQ_ROTCTLD`.
- `python3 -m pytest tests/core -q`: 3172 passed, 8 xfailed (other modules' documented xfails).
- `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -k "dock or rotator_tool or qso_dialog or rigmode"`:
  24 passed. The whole catalog test: 240 passed.
- `ruff check` / `ruff format --check` on the 8 files of M4-04 / M5-03 / M7-03: clean.
- Screenshots of every tab in English and Serbian, with an offscreen `grab()` (scratch
  script, not in the repo). They were checked by eye and showed two defects, both fixed:
  - the disabled look left "Target", "Elevation" and the "MHz" unit enabled;
  - "Unknown" sat in the middle of the By mode table.

### Manual checks still needed
- In desktop QGIS 3.34 / 3.40 and 4.x, once the controller adds the dock:
  - dock, undock and resize it;
  - check that QGIS restores its position (objectName `HamQDock`);
  - check the dark theme: LED colors and the red error lines are fixed colors.
- The empty state's "Import ADIF..." button with the real import action.

## Notes
- **core.stats ordering.** `QsoStats.by_mode` sorts by count, then name, so `"?"` can
  come before letters. This follows the contract. The panel moves the unknown row last
  in all three tables. `core/stats.py` is unchanged.
- **Station hint.** It also appears when `my_grid` is not a valid locator. In that case
  `Station.latlon()` is `None`, so there are no distances or bearings.
- **Controller wiring** (suggested):
  - `plugin.add_dock_widget(dock)` and `plugin.add_cleanup(dock.cleanup)`.
  - `events().dataChanged` -> recompute the stats (`QgsTask` for big logs) ->
    `dock.set_stats(stats)`.
  - Connect `dock.refreshRequested`, `importRequested` and `settingsRequested` to the
    actions.
- **CHANGELOG.md** was not updated: it is outside my file scope. Suggested entry under
  `## Unreleased`: "HamQ panel: statistics tab (QSOs, DXCC entities, unique calls, grid
  squares, longest QSO, by continent / band / mode, first and last QSO), WSJT-X tab,
  Radio tab with rig and rotator control."
