# M5-03: WSJT-X tab of the HamQ panel

**Milestone:** M5
**Status:** done
**Skills:** pyqgis-plugin, wsjtx-udp, adif

## Goal
The **WSJT-X** tab of `HamQDock` (`hamq/gui/dock.py`, see M4-04). It has:
- a Start/Stop listening toggle;
- a state LED and text: not listening / listening on address:port, waiting for WSJT-X /
  connected to client and version;
- the last Status message: dial frequency, band, mode and DX call;
- the last logged QSO.

The tab only shows what the controller passes from `WsjtxListener` (M5-02) and reports
the user's toggle.

## Scope
- `hamq/gui/dock.py` (WSJT-X tab)
- `tests/qgis/test_dock.py` (WSJT-X tests)
- `hamq/i18n/sr_Latn/gui_dock.json`
- `tasks/M5-03-wsjtx-dock.md`

## Out of scope
- `net/wsjtx_listener.py` (M5-02) and the controller:
  - starting and stopping the listener;
  - autostart;
  - writing logged QSOs to the GeoPackage.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md` (not mine in this run).

## Checklist
- [x] Checkable "Start listening" / "Stop listening" button -> `listenToggled(bool)`, emitted
      on user clicks only. If the controller cannot start, `set_listening(False)` unchecks
      the button again.
- [x] LED + state text. The texts:
      - "Not listening"
      - "Listening on {address}:{port}, waiting for WSJT-X" (IPv6 addresses in brackets)
      - "Connected: {client} {version}"

      LED colors: grey = off, amber = waiting, green = connected, red = not listening
      after an error.
- [x] `set_listening(listening, address="", port=0)`, `set_wsjtx_connected(connected, client=None,
      version=None)`. `None` keeps the client and version from earlier heartbeats or Status
      messages.
- [x] `set_wsjtx_status(dict | None)`: shows a `core.wsjtx.decode` Status dict.
      - Frequency: dial frequency in MHz with 6 decimals and the HamQ decimal separator.
      - Band: `core.bands.band_from_freq`.
      - Mode and DX call.
      - "—" for 0 Hz or null strings.
- [x] `set_last_qso(dict | None)` accepts either form, with any key case:
      - an ADIF record (`CALL`, `QSO_DATE`, `TIME_ON`, `FREQ`, `MODE` / `SUBMODE`, ...);
      - `Qso.attributes()` (`qso_datetime` as `datetime` / `QDateTime` / ISO text, plus
        `distance_km` and `country`).

      It shows the call, then "band · mode · UTC time · grid · distance · country".
- [x] `set_wsjtx_error(message | None)` (an addition): shows `WsjtxListener.errorOccurred`
      texts, for example the long "UDP port in use, use multicast" advice, as a
      word-wrapped line under the state. `set_listening(True, ...)` clears it.
- [x] Every text is re-applied by `retranslate()` (`events().languageChanged`)
- [x] Tests for every setter, the toggle, IPv6, unknown values, translation and errors

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` clean
- [x] Status decoded from `core.wsjtx.encode_status("WSJT-X", 14074000, "FT8", "YU1AB")`
      shows `14.074000 MHz`, `20m`, `FT8`, `YU1AB` (Serbian: `14,074000 MHz`)
- [x] `scripts/test_qgis.sh all` passes on all four targets

## Result

### What changed
- WSJT-X tab in `hamq/gui/dock.py`; its tests are part of `tests/qgis/test_dock.py`
  (39 tests in all). Its strings are in `hamq/i18n/sr_Latn/gui_dock.json`.

### Commands and outcomes
See M4-04. They are the same runs: `scripts/test_qgis.sh all -q -k "test_dock or
test_rotator_tool or test_qso_dialog"` passed on local 4.2, 3.44, 4.0 and 3.34, with
123 passed on each target. Ruff and the catalog checks are clean.

### Controller wiring (suggested)
```python
dock.listenToggled.connect(lambda on: start_or_stop(on))   # start(): set_listening(ok, addr, port)
listener.errorOccurred.connect(dock.set_wsjtx_error)
listener.heartbeatReceived.connect(lambda hb: dock.set_wsjtx_connected(True, hb.get("client"), hb.get("version")))
listener.connectionChanged.connect(dock.set_wsjtx_connected)
listener.statusReceived.connect(dock.set_wsjtx_status)
listener.clientClosed.connect(lambda _client: dock.set_wsjtx_connected(False))
# after a logged QSO is parsed (and written): dock.set_last_qso(record or qso.attributes())
```
Autostart should call `set_listening(True, settings.wsjtx_addr, settings.wsjtx_port)` once
the listener runs.

### Manual checks still needed
- A real WSJT-X or JTDX sending to 127.0.0.1:2237 and to a multicast group, with the
  controller wired. Check the state text, the status values and the last QSO, and that
  a new QSO appears on the map within 2 s (PLAN.md M5).

## Notes
- **Error texts are dynamic.** The `set_*_error` texts come translated from the listener
  or the clients. `retranslate()` cannot translate them again, so after a language
  switch an error shown earlier stays in the old language until it changes or clears.
  Release pass: the listener and the Hamlib clients now offer `current_error()` (the
  problem translated again in the current language), but the controller does not call it
  on a language switch yet, so the limitation stays (open, controller: show
  `rig.current_error()`, `rotator.current_error()` and `listener.current_error()` again in
  `_on_language_changed`, only while the dock shows an error from that client).
- **Release pass (2026-10-01)** (GUI fixer): every label of the panel shows plain text
  (`compat.TEXT_PLAIN`), and tooltips that contain outside text (client names, DX calls,
  errors) are escaped, so markup in a callsign or a client id from the network is shown
  as it is, never rendered or turned into a link.
- The tab never stops or starts anything itself. The button only emits
  `listenToggled`, and the controller confirms with `set_listening`.
