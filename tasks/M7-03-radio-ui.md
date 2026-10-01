# M7-03: Radio UI (Radio tab, rotator map tool, manual QSO dialog)

**Milestone:** M7
**Status:** done
**Skills:** pyqgis-plugin, hamlib, geodesy, adif

## Goal
The user side of Hamlib control:
- the **Radio** tab of `HamQDock`: rig frequency, band, mode, passband and set
  controls; rotator heading on a compass, target, Turn / Stop, long path and
  "Point on map";
- `RotatorMapTool`: a map click gives the bearing from my QTH, then the rotator
  azimuth, and a beam line is drawn;
- `QsoDialog`: a manual QSO prefilled from the radio;
- `core/rigmode.py`: Hamlib radio modes to ADIF modes.

## Scope
- `hamq/gui/dock.py` (Radio tab), `hamq/gui/rotator_tool.py`, `hamq/gui/qso_dialog.py`
- `hamq/core/rigmode.py`, `tests/core/test_rigmode.py`
- `tests/qgis/test_dock.py`, `tests/qgis/test_rotator_tool.py`, `tests/qgis/test_qso_dialog.py`
- `hamq/i18n/sr_Latn/gui_dock.json`, `gui_rotator_tool.json`, `gui_qso_dialog.json`
  (`core_rigmode.json` is not needed: `core/rigmode.py` has no user-visible strings)
- `tasks/M7-03-radio-ui.md`

## Out of scope
- `net/hamlib_client.py` (M7-02) and the controller:
  - connecting signals;
  - activating the map tool;
  - storing a manual QSO through `core.qso.record_to_qso` + `qgis_io.gpkg.insert_qsos`.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md` (not mine in this run).

## Checklist
### Radio tab (`hamq/gui/dock.py`)
- [x] Rig group:
      - LED and state ("Radio control is off. Turn it on in Settings." / "Not connected" /
        "Connected");
      - frequency in a large font (MHz, 6 decimals);
      - band (`core.bands.band_from_freq`), mode and passband.
- [x] Set controls:
      - MHz spin box: accepts `.` and `,`, shows the HamQ separator, up to 999999 MHz;
      - mode combo filled from `core.hamlib.MODES`;
      - "Set" button -> `rigSetRequested(hz, mode)`.

      The controls follow the radio until the user edits them, and again after "Set".
- [x] Rotator group:
      - LED and state;
      - current azimuth, with the raw value for overlap ranges: `40° (400°)`;
      - elevation, shown when it is not 0;
      - target;
      - a compass widget that paints the heading (solid) and the target (dashed).
- [x] Rotator controls:
      - target spin box 0..359 (wrapping);
      - Turn -> `rotatorTurnRequested(bearing)`, with +180 when "Long path" is checked;
      - Stop -> `rotatorStopRequested()`;
      - checkable "Point on map" -> `pointOnMapToggled(bool)`.
- [x] "Log QSO..." -> `logQsoRequested()`; a Settings button -> `settingsRequested()`
- [x] `set_rig_enabled` / `set_rotator_enabled` (disabled look when not configured),
      `set_rig_connected`, `set_rig_state(dict)`, `set_rotator_connected`,
      `set_rotator_position(az, el)`, `set_rotator_target(az | None)`
- [x] No stale values: disconnecting clears the shown frequency and heading. "Point on
      map" is released (with `pointOnMapToggled(False)`) when the rotator disconnects or
      is turned off.
- [x] Additions:
      - `set_rig_error` / `set_rotator_error`: show `errorOccurred` texts of the clients;
      - `set_point_on_map_checked`: no signal;
      - `is_long_path()`.

### Rotator map tool (`hamq/gui/rotator_tool.py`)
- [x] `RotatorMapTool(canvas, get_station_latlon, get_rotator_range, on_target, confirm=None)`,
      a `QgsMapToolEmitPoint`. A left click goes through these steps:
      1. converts the point to EPSG:4326 (any canvas CRS; clicks beyond ±180 are wrapped);
      2. computes `core.geo` distance and bearing from my QTH;
      3. maps the bearing with `core.hamlib.rotator_target` (closest to the current
         position when `get_current_az` is given);
      4. calls `on_target(azimuth, bearing, distance_km, point)`.
- [x] Beam line: `core.geo.great_circle` parts in a `QgsRubberBand`, transformed to the
      canvas CRS. It is split at the antimeridian. A right click or deactivation removes it.
- [x] First-use confirmation through `confirm(message) -> bool`. The default is
      `QMessageBox.question`. A "yes" stores `HamQSettings.rot_confirmed`; a "no" sends
      nothing and asks again next time.
- [x] Translated message and no action when there is no QTH, the point is at the QTH or
      at its antipode, the point cannot be converted, the bearing is outside the rotator
      range (e.g. 0..180), or the range is invalid. The default `notify` uses the message
      bar and the HamQ log.
- [x] Additions (optional keyword arguments): `notify`, `get_current_az`, `get_long_path`,
      `settings`, `step_km`.
      - With the long path (`get_long_path=dock.is_long_path`) the antenna turns to
        bearing + 180, and the distance and beam line follow the long path. The beam is
        built by `long_path_parts()`: three great-circle thirds, split at the antimeridian.
      - `canvasClicked` is still emitted, as by every `QgsMapToolEmitPoint`.
- [x] `cleanup()` unsets the tool and removes the rubber band from the scene; twice is fine

### Manual QSO dialog (`hamq/gui/qso_dialog.py`) and `core/rigmode.py`
- [x] `QsoDialog(settings, rig_state=None, parent=None)` with these fields:
      - callsign: uppercased while typing, required, validated;
      - date and time in UTC: default now, "Now" button;
      - frequency in MHz: `.` or `,`;
      - band: follows the frequency and can be changed; a mismatch is reported;
      - mode and submode: editable combos;
      - RST sent and received: defaults per mode (59 / 599 / -10) unless typed;
      - locator: validated and normalized;
      - name and comment.
- [x] Prefill from the rig state (`RigClient.stateChanged` dict):
      - USB/LSB -> SSB, CW/CWR -> CW, AM, FM/FMN/WFM -> FM, RTTY/RTTYR -> RTTY,
        C4FM -> DIGITALVOICE/C4FM, ...;
      - PKT* -> empty mode plus a hint, because the user chooses (FT8, PSK31, ...).
- [x] `record()` returns an ADIF-like dict with `QSO_DATE`, `TIME_ON`, `CALL`, `FREQ`,
      `BAND`, `MODE`, `SUBMODE`, `RST_SENT`, `RST_RCVD`, `GRIDSQUARE`, `NAME`, `COMMENT`,
      `MY_GRIDSQUARE` and `STATION_CALLSIGN` (from the settings). Empty fields are left out.
      - FT4 / PSK31 / ... are written as ADIF 3.1 MODE + SUBMODE (`MFSK` + `FT4`).
      - It works with `core.qso.record_to_qso`: tested with the real module, including
        `adif_extra` and an FT4 dedup key equal to WSJT-X's `MODE=FT4`.
- [x] Save closes only a valid entry. Otherwise the translated problems are shown in
      the dialog, and the dialog grows to fit wrapped texts.
- [x] `retranslate()` on `events().languageChanged`. `done()` / `cleanup()` disconnect,
      with the same deletion safety net as the dock.
- [x] `core/rigmode.py` (pure Python, 3.9):
      - functions `adif_mode`, `is_data_mode`, `normalize_rig_mode` (Hamlib 4.6 aliases
        such as `FM-D`), `split_mode` and `default_rst`;
      - tables `ADIF_MODES`, `SUBMODES`, `SUBMODE_PARENTS`, `MODE_CHOICES` and
        `DATA_RIG_MODES`.

      A test makes sure every `core.hamlib.MODES` entry is classified on purpose.

## Acceptance criteria
- [x] `pytest tests/core -q` passes (also `tests/core/test_rigmode.py` on Python 3.9)
- [x] `ruff check` / `ruff format --check` clean
- [x] A click on Sydney from KN04ft gives about 91° (`aim_at` -> 91.22°; distance 15676 km ±0.5 %)
- [x] Range 0..180 with a west bearing: `None`, a translated message and nothing sent
- [x] The beam line has the `great_circle` parts and vertices and is split at the antimeridian
- [x] The QSO dialog prefill, band derivation, `record()` content and validation are tested
- [x] Works against the real `rigctld -m 1` / `rotctld -m 1` (`hamq/hamlib-dummy`)
      through `net/hamlib_client.py`
- [x] `scripts/test_qgis.sh all` passes on all four targets

## Result

### What changed
- `hamq/gui/dock.py`: Radio tab (see the checklist).
- `hamq/gui/rotator_tool.py` (new), `hamq/gui/qso_dialog.py` (new), `hamq/core/rigmode.py` (new).
- Tests:
  - `tests/qgis/test_dock.py`: 39 in all three tabs;
  - `tests/qgis/test_rotator_tool.py`: 49;
  - `tests/qgis/test_qso_dialog.py`: 33;
  - `tests/core/test_rigmode.py`: 88.
- Catalogs: `gui_rotator_tool.json` (10 strings), `gui_qso_dialog.json` (25); the Radio
  tab strings are in `gui_dock.json`.

### Commands and outcomes
- `scripts/test_qgis.sh all -q -k "test_dock or test_rotator_tool or test_qso_dialog"`
  (final run):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2 (Qt6) | PASS: 123 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 123 passed |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 123 passed |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 123 passed |

  121 of these tests are mine; the other 2 are named in M4-04. The `record_to_qso`
  tests now run against the real `core/qso.py`; earlier they were skipped while it did
  not exist.
- `scripts/test_qgis.sh local -q`: 707 passed, 2 skipped (live-daemon tests of `test_hamlib_client.py`).
- `python3 -m pytest tests/core -q`: 3172 passed, 8 xfailed. `tests/core/test_rigmode.py`:
  88 passed on host Python and on `python:3.9-slim` (Python 3.9.25).
- `ruff check` / `ruff format --check` on all 8 files: clean. Catalog test 240 passed;
  strict mode for my catalogs: 24 passed.
- **End-to-end against the Hamlib dummies.** A scratch script (not in the repo) wired
  `HamQDock`, `RotatorMapTool` and `QsoDialog` to `RigClient` / `RotatorClient` as a
  controller would, against `hamq/hamlib-dummy`:
  - QGIS 4.2: uniquely named container on free 127.0.0.1 ports;
  - QGIS 3.44 / Qt5: Docker bridge IP.

  Both containers were removed afterwards (`docker ps -a` is empty). Results, the same
  on both:
  ```
  rig connected: True 145.000000 MHz | 2m · —
  rig set via panel: True 7.074000 MHz | 40m · CW · 15 000 Hz
  rig set 2400.1 MHz: True 13cm · USB · 15 000 Hz        (qint64 signal, > 2^31 Hz)
  rotator connected: True 0°
  rotator turned via panel: True (30.0, 0.0) 30° Target: 30°
  map tool Sydney: 91.22 True (91.22, 0.0) 91°
  qso dialog prefill: True {'FREQ': '2400.1', 'BAND': '13cm', 'MODE': 'SSB', 'RST_SENT': '59', ...}
  rig lost: True — False          (daemon stopped: "Not connected", no stale frequency, Set disabled)
  rotator lost: True —
  ```
  In the first run, Turn to 123° looked like a failure after 15 s (the rotator was at
  89°). That is the dummy's simulated slew of about 6°/s, not a panel problem. With a
  closer target it passed.

### Controller wiring (suggested)
```python
rig.connectedChanged.connect(dock.set_rig_connected); rig.stateChanged.connect(dock.set_rig_state)
rig.errorOccurred.connect(dock.set_rig_error)
dock.rigSetRequested.connect(lambda hz, mode: (rig.set_frequency(hz), rig.set_mode(mode, 0)))
rot.connectedChanged.connect(dock.set_rotator_connected); rot.positionChanged.connect(dock.set_rotator_position)
rot.errorOccurred.connect(dock.set_rotator_error)
def turn(bearing):                                  # rotatorTurnRequested: compass bearing
    az = rotator_target(bearing, s.rot_min_az, s.rot_max_az, current_az)
    if az is None: message bar: out of range       # nothing sent
    else: rot.set_position(az); dock.set_rotator_target(az)
dock.rotatorStopRequested.connect(rot.stop_rotation)
tool = RotatorMapTool(iface.mapCanvas(), lambda: s.station().latlon(), lambda: (s.rot_min_az, s.rot_max_az),
                      lambda az, bearing, km, pt: (rot.set_position(az), dock.set_rotator_target(az)),
                      get_current_az=..., get_long_path=dock.is_long_path, settings=s)
dock.pointOnMapToggled -> canvas.setMapTool(tool) / canvas.unsetMapTool(tool)
tool.deactivated.connect(lambda: dock.set_point_on_map_checked(False))
dock.logQsoRequested -> QsoDialog(s, rig.state() if rig.is_connected() else None, iface.mainWindow()).exec()
                        accepted -> record_to_qso(dialog.record(), station=s.station(), cty=..., source="manual")
                        -> gpkg.insert_qsos(...) -> events().dataChanged
plugin.add_cleanup(tool.cleanup); plugin.add_cleanup(dock.cleanup)
```

### Manual checks still needed
- Desktop QGIS 3.34 / 3.40 and 4.x with the controller wired:
  - "Point on map" with the real `QMessageBox` confirmation (Yes / No);
  - the beam line on a projected and on the azimuthal map (HamQ aeqd CRS);
  - the cursor;
  - switching to another map tool releases "Point on map" (through `deactivated`).
- A real radio and rotator through `rigctld` / `rotctld`: data modes (PKTUSB) and the
  passband after "Set".
- The QSO dialog with a real `exec()`: tab order, calendar popup, Save / Cancel.

## Notes
- **Deviation: `rigSetRequested` C++ type.** It is declared `pyqtSignal("qint64", str)`,
  not `(int, str)`. A C++ `int` wraps above 2147483647 Hz (13 cm, 9 cm, 3 cm bands).
  Python slots still receive a plain `int`. Tested up to 10368.1 MHz and live with
  2400.1 MHz.
- **`rigSetRequested` carries no passband.** The controller chooses: 0 = the radio's
  default for the mode, -1 = keep. The dummy rig kept 15000 Hz after `M CW 0`.
- **`long_path_parts()` lives in `gui/rotator_tool.py`** (pure math). My file scope did
  not allow a change to `core/geo.py`. It is a candidate for `core.geo` (see the contract
  change requests in the report).
- **Single-letter compass keys** (`N`/`E`/`S`/`W` -> `S`/`I`/`J`/`Z`) are in
  `gui_dock.json`. Any other module that translates these letters must use the same
  meaning (cardinal directions); the catalog test enforces that the translations match.
- **`QsoDialog` after `done()`** no longer follows language changes: create a new
  dialog per QSO.
- **CHANGELOG.md** was not updated: it is outside my file scope. Suggested entry under
  `## Unreleased`:
  - "Radio tab: rig frequency/mode display and set, rotator compass, turn / stop / long
    path";
  - "Point on map turns the antenna (beam line, first-use confirmation)";
  - "Manual QSO entry prefilled from the radio";
  - "`core/rigmode.py`: Hamlib mode to ADIF mode mapping".
- **Release pass (2026-10-01):**
  - GUI fixer: the first-use confirmation of the rotator map tool names its buttons in
    the HamQ language through `gui/message_box.py` (`Turn` / `Cancel` -> "Okreni" /
    "Otkaži"), with Cancel as the default button and the answer for Escape (Qt has no
    Serbian translation of its standard buttons); the QSO dialog calendar shows month
    and day names in the HamQ language (`dock.hamq_locale()`); the rotator azimuth
    fields of the settings dialog accept `.` and `,` (`dock.DecimalSpinBox`).
  - Core fixer: the `core/rigmode.py` docstring now points the duplicate check to
    `modes.dedup_mode` (SSB, `SSB` + `USB` / `LSB` and `USB` / `LSB` are the same mode
    there), while `display_mode` keeps showing `SSB`.
  - Translations: "Point on map" is "Usmeri klikom na mapu" (like the menu action
    "Usmeri antenu klikom na mapu"; the long-path tooltip follows); "RST sent" / "RST
    received" are "Poslati RST" / "Primljeni RST" in the dialog and in the field aliases.
  - Still open (gui): "Set your QTH locator in Settings to turn the antenna from the
    map." keeps "Podesite ..." in `gui_rotator_tool.json`, because
    `tests/qgis/test_rotator_tool.py::test_messages_are_translated` pins that text; the
    glossary wording "Unesite svoj QTH lokator u podešavanjima da biste antenu okretali
    klikom na mapu." needs the catalog and that test changed together. The tool's own
    message-bar fallback (`_show_message`, used when no message callback is given)
    pushes its text unescaped; the texts are HamQ's own, with numbers only.
