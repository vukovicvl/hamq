# M5-02: WSJT-X UDP listener

**Milestone:** M5
**Status:** done
**Skills:** pyqgis-plugin, wsjtx-udp

## Goal
Receive WSJT-X / JTDX UDP messages in QGIS without blocking the UI. A `QUdpSocket`
decodes every datagram with `core.wsjtx.decode` and turns heartbeats, status updates,
logged QSOs (Logged ADIF), closes and the client connection state into Qt signals. It
supports unicast and multicast, and reports a busy port clearly. This is the
`WsjtxListener` described in `docs/ARCHITECTURE.md` under "net".

## Scope
- `hamq/net/wsjtx_listener.py`
- `tests/qgis/test_wsjtx_listener.py`
- `hamq/i18n/sr_Latn/net_wsjtx_listener.json`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: only the appended block
  "M4-03 / M5-02" (shared with M4-03)
- `tasks/M5-02-wsjtx-listener.md`

## Out of scope
- Writing logged QSOs to the GeoPackage, refreshing layers, the dock's WSJT-X tab, the
  start/stop button and autostart. These belong to the controller, dock and plugin
  owners.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md` (not mine in this run).

## Checklist
- [x] `WsjtxListener(QObject)` with the contract API: `heartbeatReceived(dict)`,
      `statusReceived(dict)`, `adifLogged(str, str)`, `clientClosed(str)`,
      `connectionChanged(bool)`, `errorOccurred(str)`, `start()`, `stop()`, `is_running()`,
      `is_connected()`
- [x] Bind `QHostAddress.SpecialAddress.AnyIPv4` with `ShareAddress | ReuseAddressHint`
      (compat constants)
- [x] Multicast address (224.0.0.0/4): bind AnyIPv4 and join the group on every interface
      that is up and can multicast, plus loopback; fall back to the default interface;
      leave the group on `stop()`
- [x] Bind failure: a translated `errorOccurred` message. When the port is in use, it
      suggests multicast, because another program probably holds the port.
- [x] `readyRead`: `core.wsjtx.decode` for every datagram; `None` is ignored (logged once
      per start); at most `MAX_DATAGRAMS_PER_PASS` per event-loop pass
- [x] Any message marks its client alive (QTimer); a client is gone after 45 s without
      traffic; `connectionChanged(False)` when the last client is gone
- [x] heartbeat -> `heartbeatReceived`; status -> `statusReceived`; close -> `clientClosed`
      plus disconnected (when it was the last client); logged_adif -> `adifLogged(client,
      adif)`; type 5 (QSO Logged) and other types are ignored, as the skill says
- [x] `stop()`: leaves the group, closes the socket, stops the timers; safe twice, before
      `start()` and after the Qt object was deleted; never raises
- [x] `start()` while running restarts; it never raises (returns `False` plus
      `errorOccurred`), and an unexpected error never leaves a bound socket behind
- [x] Slots never let exceptions escape into Qt; a slot connected to any signal may call
      `stop()` / `start()`
- [x] Translated messages and the catalog `net_wsjtx_listener.json` (15 strings, glossary)
- [x] Compat constants `NETIF_IS_UP`, `NETIF_IS_LOOPBACK`, `NETIF_CAN_MULTICAST` with a test
- [x] Tests with real UDP datagrams from a Python socket
- [x] Self-review (contract, AGENTS.md, skills, Qt5/Qt6, 3.34 API, leaks, slots, strings)

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes (includes the i18n catalog guard)
- [x] `ruff check` and `ruff format --check` pass for the files in scope
- [x] Real datagrams (encoded and captured fixtures) produce the right signals on QGIS
      4.2, 3.44, 4.0 and 3.34
- [x] Port conflict, multicast join, garbage, start/stop cycles and connection timeout
      are tested on all four targets

## Result

### What changed
- `hamq/net/wsjtx_listener.py` (new): `WsjtxListener`. The contract names and signatures
  are unchanged; the additions are listed below.
- `tests/qgis/test_wsjtx_listener.py` (new): 53 tests. 19 of them run one fixture
  datagram each: the 11 in `tests/fixtures/wsjtx` and the 8 captured from WSJT-X 2.7.0.
- `hamq/i18n/sr_Latn/net_wsjtx_listener.json` (new): 15 strings. The Cyrillic
  transliteration was checked by eye (`WSJT-X`, `UDP`, `multicast`, `JTAlert`,
  `GridTracker`, `HamQ` and IP addresses are kept).
- `hamq/qgis_io/compat.py` / `tests/qgis/test_compat.py`: `NETIF_IS_UP`,
  `NETIF_IS_LOOPBACK`, `NETIF_CAN_MULTICAST` in block "M4-03 / M5-02", tested by
  `test_network_interface_flags`.

### Behaviour the controller / dock can rely on
- `WsjtxListener(parent=None, *, client_timeout_ms=None)`. Use it from the UI thread.
- `start(address, port) -> bool` stops first, so calling it while running restarts.
  - `address` is the address set in WSJT-X:
    - `""` means any address, and `localhost` means 127.0.0.1.
    - Any IPv4 unicast address listens on all interfaces.
    - An IPv4 multicast address (224.0.0.0/4) joins that group.
    - IPv6, host names and garbage give `False` plus a translated `errorOccurred`.
  - `port` must be an `int` from 1 to 65535; `bool`, `float` and `str` are rejected.
  - `address()` and `port()` return the normalized values while running, `""` / `0`
    otherwise.
- Error messages (translated, also logged as warnings):
  - Port in use (`AddressInUseError`): `UDP port 2237 is already in use, probably by
    another program that receives WSJT-X messages (a logger, JTAlert, GridTracker). ...
    set the same multicast address, for example 224.0.0.1, in WSJT-X (File > Settings >
    Reporting > UDP Server) and in the HamQ settings.`
  - Other bind errors: `Cannot listen on UDP port {port}: {Qt error}. If another program
    ...`, with the same hint.
  - Join failure: `Could not join the multicast group 224.0.0.1: {Qt error}`.
- Signals, in the order they are emitted:
  - Every decoded message marks its client id alive, including QSO Logged (type 5) and
    other types, which emit nothing else. The first live client emits
    `connectionChanged(True)` before that message's own signal.
  - `heartbeatReceived(dict)` / `statusReceived(dict)` carry the `core.wsjtx.decode` dict
    unchanged. Its `client` may be `None` (a null string on the wire).
  - `adifLogged(client, adif)`:
    - `client` is `""` for a null id.
    - An empty or whitespace-only ADIF text is not emitted.
    - The same text from the same client within `DUPLICATE_WINDOW_S = 2.0` is emitted
      once. WSJT-X sends a multicast datagram once per outgoing interface selected in its
      settings.
  - `clientClosed(client)` is emitted for every Close, also from a client never seen.
    When that was the last live client, `connectionChanged(False)` follows.
  - A client expires after `CLIENT_TIMEOUT_MS = 45_000` without messages (WSJT-X sends a
    heartbeat every 15 s); `connectionChanged(False)` follows when it was the last one.
  - `stop()` emits `connectionChanged(False)` when a client was connected.
- Datagrams that do not decode are ignored. The first one per `start()` is logged at Info
  with the sender address.
- Flood safety: at most `MAX_DATAGRAMS_PER_PASS = 100` datagrams per event-loop pass; the
  rest continue in the next pass through a zero-interval timer.
- Re-entrancy: a slot may call `stop()` or `start()` from any signal. Handling of the old
  socket stops at once, and no further signals come from it.
- Cleanup: register `plugin.add_cleanup(listener.stop)`. `stop()` releases the port at
  once; the tests bind it exclusively right after.

### Commands and outcomes
- `scripts/test_qgis.sh all -k "test_cty_download or test_wsjtx_listener or test_compat" -q -rs`
  (final run, after the self-review fixes):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2 (Qt6) | PASS: 232 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 231 passed, 1 skipped |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 231 passed, 1 skipped |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 231 passed, 1 skipped |

  The run covers 53 wsjtx_listener tests, 30 cty_download tests and all compat tests.
  - The multicast test (`test_multicast_group`, 224.0.0.1 shared with a plain
    `SO_REUSEADDR` socket) ran and passed on all four targets.
  - The only skip is `test_other_bind_errors` in Docker, where a non-root user may bind
    UDP port 1. It runs and passes on the host.
- `python3 -m pytest tests/core -q`: 2996 passed, 8 xfailed (other modules' documented
  xfails). In the last run, collection stopped at `tests/core/test_qso.py`. Another agent
  had written that test first, and its `hamq/core/qso.py` did not exist yet. With
  `--ignore=tests/core/test_qso.py` the result was the same 2996 passed, 8 xfailed, and
  `tests/core/test_i18n_catalog.py` passed 233 of 233.
- `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -k "cty_download or wsjtx_listener"`:
  14 passed.
- `ruff check` / `ruff format --check` on the four files and compat / test_compat: clean.

### Manual checks still needed
- Real WSJT-X 2.7 / JTDX on the same PC, sending unicast to 127.0.0.1:2237. Then the same
  with multicast 224.0.0.1 next to another listener (JTAlert or GridTracker). The PLAN M5
  "QSO on the map in under 2 s" also needs the controller.
- Windows: the port-in-use message appears only when the other program bound the port
  exclusively. A program that used `SO_REUSEADDR` lets HamQ bind too (see Notes).
- macOS multicast. Qt maps `ShareAddress` to `SO_REUSEPORT` for UDP there.

## Notes
- With `ShareAddress`, two programs can bind the same unicast port when reuse is allowed.
  On Linux both need `SO_REUSEADDR`. On Windows the second program's `SO_REUSEADDR` is
  enough unless the first used `SO_EXCLUSIVEADDRUSE`. The kernel
  then gives each unicast datagram to only one of them, and neither gets an error. Two
  QGIS instances, or HamQ next to a reusing logger, therefore "share" silently. Multicast,
  which the error message recommends, is the real fix. The task requires these bind
  flags.
- Host names are rejected as the WSJT-X address: the listener only needs to know whether
  the address is multicast, and a host name cannot tell that without a blocking lookup.
  The settings dialog currently accepts any non-empty text for `wsjtx_addr`, so a host
  name surfaces as a clear error at `start()`.
- `connectionChanged` counts any message, not only heartbeats; WSJT-X also sends Status
  on every change. The contract comment says "heartbeat seen within timeout"; the task
  text says "any message marks the client alive", which is what is implemented.
- Any program that sends WSJT-X-format messages to the group would count as a client.
  Servers normally send replies to the client's own port, not to 2237.
