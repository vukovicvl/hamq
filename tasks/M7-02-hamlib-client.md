# M7-02: Hamlib rigctld/rotctld TCP clients

**Milestone:** M7
**Status:** done
**Skills:** pyqgis-plugin, hamlib

## Goal
Asynchronous TCP clients for the Hamlib daemons (`HamlibClient`, `RigClient`,
`RotatorClient` in `hamq/net/hamlib_client.py`) as in `docs/ARCHITECTURE.md` "net".
They are built on `core/hamlib.py`, never block the QGIS UI thread and recover by
themselves when the daemon goes away. Tests run against an in-process fake daemon,
plus optional real-daemon tests.

## Scope
- `hamq/net/hamlib_client.py`
- `tests/qgis/test_hamlib_client.py`
- `tests/qgis/fake_hamlib.py` (test helper)
- `hamq/i18n/sr_Latn/net_hamlib_client.json`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: the `M7-02` block only
- `scripts/test_qgis.sh`: pass `HAMQ_RIGCTLD` / `HAMQ_ROTCTLD` into the containers
- `tasks/M7-02-hamlib-client.md`

## Out of scope
- `hamq/controller.py`, which wires the clients to the settings, the dock, the rotator
  map tool and the QSO dialog (other M7 tasks)
- `hamq/core/hamlib.py` (M7-01, used unchanged)
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md`, `pyproject.toml`, `README.md`

## Checklist
- [x] `HamlibClient(QObject)`: `connectedChanged(bool)`, `errorOccurred(str)` (translated),
      `start(host, port)`, `stop()`, `is_connected()`
- [x] `QTcpSocket` with no proxy; only signals and `QTimer`, never a `waitFor*()` call
      (the longest event loop gap measured while polling the real daemon was 1.3–2.9 ms)
- [x] One command in flight. Replies are matched with `expected_command()`; stray replies
      are ignored. Data goes in as `bytes(socket.readAll())`, never a `QByteArray`
- [x] Bounded FIFO queue:
  - set commands go ahead of queued polls;
  - a poll is never queued while the same poll is waiting, and a timer poll is never
    queued while the same poll is in flight;
  - a newer set command of the same kind replaces a queued one;
  - `stop_rotation` cancels a queued turn and goes first.
- [x] 2 s timeout for connecting and for each command. A timeout, a socket error or a
      remote close all do the same: reset the parser, abort the socket, emit
      `connectedChanged(False)` and the unknown state, then reconnect every 5 s
      (`QTimer`) until `stop()`
- [x] Polling only while connected; `RigClient` polls `+f` and `+m` every `poll_ms`
      (constructor argument = `settings.rig_poll_ms`); `RotatorClient` polls `+p` every 1 s
- [x] `stateChanged(dict)` and `positionChanged(float, float)` only on change
- [x] `set_frequency` / `set_mode` (always with the passband, via `cmd_set_mode`) /
      `set_position` / `stop_rotation`, each followed by an immediate poll
- [x] `RPRT` errors -> `errorOccurred(...error_message(code))`. Poll and connection
      errors are rate limited (once per problem, again at most once a minute); set
      command errors are always reported
- [x] `stop()` cancels everything and is safe to call twice, and also after the Qt object
      was deleted with its parent. No timer, socket or reconnect is left after it
- [x] Exceptions never escape into Qt: every slot is guarded and logs a CRITICAL
      "Unexpected error" in the HamQ log tab
- [x] Survives receivers that run a nested event loop (modal dialog) inside our
      signals: no socket is deleted mid-emission, and replies that arrive meanwhile
      are still read
- [x] Translations: 18 strings in `net_hamlib_client.json` (Serbian Latin, glossary
      terms; Cyrillic checked through the runtime transliteration)
- [x] Fake daemon with switches: never answer, stray lines, replies split across
      packets, delayed replies, custom replies (header without `RPRT`, CRLF), close
      after a command, close all connections, stop / restart
- [x] Optional real-daemon tests (`HAMQ_RIGCTLD` / `HAMQ_ROTCTLD`), run against
      `hamq/hamlib-dummy`
- [x] Self-review (adversarial), ruff, all four QGIS targets

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes (3172 passed, 8 xfailed at the end of the
      run; includes the i18n catalog scan of this module, also with `HAMQ_STRICT_I18N=1`)
- [x] `scripts/test_qgis.sh all -k "hamlib or compat"` passes on local 4.2, 3.44, 4.0
      and 3.34, with the real-daemon tests enabled
- [x] `ruff check` and `ruff format --check` pass for the files in scope
- [x] Works against real `rigctld -m 1` / `rotctld -m 1` (Hamlib 4.6.2), including a
      daemon killed and restarted and a frozen daemon (`docker pause`)

## Result

This run continued an interrupted earlier run. The client, the fake and most tests
already existed. In this run I:
- reviewed all of it against the contract, AGENTS.md and the skills;
- fixed three defects, each with a test that fails without its fix;
- made three timing-sensitive tests robust (two use a fake clock now);
- added the real-daemon and outage checks;
- wrote this file.

### What changed
- `hamq/net/hamlib_client.py` (new). Contract API plus additions, all optional or extra:
  - constructor `HamlibClient(poll_ms=1000, parent=None, *, timeout_ms=2000,
    reconnect_ms=5000, error_interval_ms=60000)`, the same for `RigClient` and
    `RotatorClient`; wrong types or values raise `TypeError` / `ValueError`;
  - `is_running()`, `address()`, `poll_interval()`, `set_poll_interval(ms)`;
  - `RigClient.state() -> dict` (a copy), `RotatorClient.position() -> (az, el) | None`;
  - constants `DEFAULT_POLL_MS`, `DEFAULT_TIMEOUT_MS`, `DEFAULT_RECONNECT_MS`,
    `DEFAULT_ERROR_INTERVAL_MS`, `MAX_QUEUE`.
- `tests/qgis/fake_hamlib.py` (new): `FakeHamlib("rig" | "rot")` on a `QTcpServer` in
  the test thread. It sends the exact reply formats of the contract and answers only
  its own commands, like the real daemons: `+M <mode>` without a passband takes the
  next line as the passband, and a rotator position outside -180..450 / 0..90 gives
  `RPRT -21`. Switches: `mute`, `mute_commands`, `errors`, `replies`, `stray`,
  `chunk_size` / `chunk_gap_ms`, `delay_ms`, `close_on`, `close_clients()`, `stop()`,
  `restart()`. It also provides `wait_until`, `pump` and `drain_deleted`.
- `tests/qgis/test_hamlib_client.py` (new): 48 test functions (49 items), 2 of them
  against real daemons (skipped unless `HAMQ_RIGCTLD` / `HAMQ_ROTCTLD` are set).
- `hamq/i18n/sr_Latn/net_hamlib_client.json` (new): 18 strings.
- `hamq/qgis_io/compat.py` + `tests/qgis/test_compat.py` (M7-02 block, already in
  HEAD from the interrupted run): `SOCKET_ERROR_REMOTE_CLOSED`, `NET_PROXY_NONE`.
- `scripts/test_qgis.sh` (already in HEAD): `-e HAMQ_RIGCTLD -e HAMQ_ROTCTLD`.

Fixes made in this run (each has a test that fails without the fix):
1. **Replies lost during a nested event loop.** Qt does not emit `readyRead` again
   while a `readyRead` slot is running. Suppose a receiver of `stateChanged` or
   `errorOccurred` opens a modal dialog. The poll timer goes on sending commands, but
   their replies were only buffered, so after 2 s the client reported "did not reply"
   and reconnected. Now replies go through one re-entrant drain queue, and the poll
   timer and the watchdog read buffered data before giving up
   (`test_replies_arriving_during_a_nested_event_loop_are_read`).
2. **Stale state after a restart in a slot.** When a `connectedChanged(False)` slot
   restarted or stopped the client, the unknown state was not emitted, so the GUI could
   keep showing the old frequency
   (`test_restart_in_a_disconnect_slot_still_reports_the_unknown_state`).
3. **`stop()` after deletion.** `stop()` after the parent had deleted the client
   logged a CRITICAL "Unexpected error" (`RuntimeError` from a deleted `QTimer`)
   (`test_stop_after_the_parent_deleted_the_client`). Socket slots now also ignore
   signals that arrive while the client is being destroyed. That guard is defensive:
   PyQt already drops those connections, so no test can observe it.

Timing-sensitive tests: the two rate-limit tests now use an injected fake clock
instead of real intervals of 1 / 400 ms, so they are deterministic. The slow-daemon
bound is now computed from the elapsed time instead of a fixed count.

### Behaviour the controller / GUI can rely on
- **When it counts as connected.** `connectedChanged(True)` is emitted when the daemon
  answers the first command on a connection, not when TCP connects. A port that
  accepts connections but never answers (a wrong service, a frozen daemon) therefore
  never shows as connected and does not flicker.
- **On disconnect.** `connectedChanged(False)` comes first. `RigClient` then emits
  `stateChanged` with all `None`. `RotatorClient` cannot emit `None` through
  `positionChanged(float, float)`: clear the display on `connectedChanged(False)`;
  `position()` is `None` from then on. The first position after a reconnect is always
  emitted.
- **When values are `None`.** A failed read (`RPRT` error on `+f` / `+m`) makes that
  value `None` until the next good reply.
- **Error reporting.** `errorOccurred` texts are already logged in the HamQ log tab
  (WARNING). Show them, e.g. in the message bar, but do not log them again. A
  "Connected to rigctld at host:port" INFO line is logged at most once a minute.
- **When set commands are accepted.** While connecting, they are queued and sent
  first. While waiting to reconnect, or after `stop()`, they are refused with
  `errorOccurred("Setting the frequency failed: not connected to rigctld")`. A set
  command lost with the connection is reported the same way.
- **Invalid arguments.** `set_*` raise `ValueError` / `TypeError` for invalid arguments
  (programmer errors, from the core builders), e.g. `set_mode("usb")`: only exact
  `MODES` names. The controller passes values from the dock (`hz > 0`, a `MODES` combo).
- **Azimuth.** `set_position` sends the azimuth as given. Map a compass bearing with
  `core.hamlib.rotator_target()` first. The daemon rejects positions out of range with
  `RPRT -21`, reported as "Turning the rotator failed: Limit exceeded".
- **Calling `start()` again.** The same address keeps the running connection, or, while
  waiting to reconnect, retries at once. Another address restarts the client.
  `set_poll_interval()` takes effect at once (for `rig_poll_ms` changes).
- **Re-entrancy.** Receivers may call `stop()` / `start()` or open modal dialogs from any
  of our signals.

### Commands and outcomes
```
python3 -m pytest tests/core -q                                   -> 3172 passed, 8 xfailed
HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q -k hamlib
                                                                  -> 14 passed
ruff check  hamq/net/hamlib_client.py tests/qgis/test_hamlib_client.py tests/qgis/fake_hamlib.py \
            hamq/qgis_io/compat.py tests/qgis/test_compat.py      -> All checks passed!
ruff format --check (same files)                                  -> 5 files already formatted
scripts/test_qgis.sh local -q (whole tests/qgis, no daemon env)    -> 712 passed, 2 skipped

# all four targets, real-daemon tests enabled through the dummy container's bridge IP
HAMQ_RIGCTLD=172.17.0.3:4532 HAMQ_ROTCTLD=172.17.0.3:4533 \
  scripts/test_qgis.sh all -k "hamlib or compat" -q -p no:cacheprovider -rs
target  environment                    result    time    pytest
local   host QGIS 4.2.1 (Qt 6.10.2)    PASS      18s     198 passed, 514 deselected in 15.34s
3.44    qgis/qgis:3.44-trixie          PASS      20s     198 passed, 514 deselected in 16.47s
4.0     qgis/qgis:4.0-trixie           PASS      19s     198 passed, 514 deselected in 16.34s
3.34    camptocamp/qgis-server:3.34    PASS      22s     198 passed, 516 deselected in 16.56s
(198 = 49 items of test_hamlib_client.py (48 functions, one parametrized twice),
 including both real-daemon tests, none skipped, + 149 items of test_compat.py)
```

### Real-daemon checks (`hamq/hamlib-dummy`, Hamlib 4.6.2 `rigctld -m 1` / `rotctld -m 1`)
- Container `hamq-m702-real-<timestamp>` was published on free `127.0.0.1` ports and
  removed afterwards with `docker rm -f`. A leftover container of the interrupted run
  was removed as well.
- `test_real_rigctld` / `test_real_rotctld` passed:
  - on the host with `127.0.0.1:<port>` (3 runs in a row, about 1.9 s each);
  - on all four targets through the bridge IP (above).
  They set frequency and mode, including `PKTFM`, which the daemon reports as `FM-D`,
  then restore them. They turn the rotator, stop it, and check the limit error for 500°.
- A scratch outage script (`outage_check.py`, not in the repo) ran on the host on
  QGIS 4.2 against the same container. Result: **22 passed, 0 failed**. Condensed:
  ```
  0.14s  connected, state {freq 145000000, FM, 15000}; one connectedChanged(True), no errors
  2.14s  longest event loop gap while polling (200 ms) for 2 s: 2.1 ms
  2.21s  kill every rigctld in the container
  2.5s   connectedChanged(False), state all None,
         errorOccurred "rigctld at 127.0.0.1:<port> closed the connection; reconnecting"
 10.37s  still running, not connected; restart rigctld
 12.66s  reconnected (within the 5 s retry period), state back; outage reported once
 12.68s  docker pause (daemon frozen)
 14.72s  connectedChanged(False) after the 2 s timeout; 12 s of retries: one message only
 26.79s  docker unpause -> reconnected at once, state back
 26.91s  set_frequency(7074000) + set_mode("PKTUSB", 3000) -> state {7074000, PKTUSB, 3000}
         stop() twice -> no timer active
         rotator: position read, moving after set_position, stopped after stop_rotation,
         set_position(-200) -> "Turning the rotator failed: Limit exceeded"
         HamQ log tab: no "Unexpected error"
  RESULT: 22 passed, 0 failed
  ```

### Manual checks still needed
- In QGIS with the GUI, once the controller wires the clients to the dock, map tool and
  QSO dialog:
  - enable the rig and rotator in the settings;
  - start/stop the daemons while QGIS runs;
  - reload the plugin while connected (it must call `stop()`; no warnings in the log).
- A real radio with slow CAT and a real rotator. Some backends need more than 2 s, or
  keep `+P` busy while turning. The 2 s timeout from the plan then means reconnects.

## Notes
- **Docker hides "Connection refused".** With Docker's userland port proxy, a stopped
  daemon in a container never refuses the connection on the host. The proxy accepts it
  and closes it. Each retry is then reported (rate limited) as "closed the connection",
  not "Connection refused".
- **Several daemons can share a port.** In the dummy image, a second `rigctld` on the
  same port also starts listening, so the kernel can hand connections to either one.
  This misled the first outage-script runs; the script now kills every `rigctld`.
- **`rigctld --vfo` is not supported.** In VFO mode every command needs a VFO argument,
  so `+f` alone would make the daemon wait for the next line. The client then times
  out and keeps reconnecting, with one rate-limited message. This belongs in the user
  documentation.
- **Very small poll intervals.** `rig_poll_ms` is only validated as `> 0` in
  `settings.py`; the settings dialog limits it to 100..60000 ms. A smaller value stored
  by hand makes the client poll back to back: still one command in flight, so the UI is
  not affected, but the radio's CAT port gets no rest.
- **A hang in another agent's test.** One local run with `-k "real_"` also selected
  `tests/qgis/test_settings_dialog.py::test_browse_opens_a_real_file_dialog`
  (another agent's file) and did not finish within 600 s. Two reruns of the same
  selection, and the test alone, passed in about 2 s. That test opens a real modal
  `QFileDialog`; my tests in the same session finished.
- **CHANGELOG.md** was not updated (outside this task's file scope). Suggested entry
  under `## Unreleased`: "Hamlib TCP clients (`net/hamlib_client.py`): `RigClient`
  (frequency and mode) and `RotatorClient` (azimuth, turn, stop) for rigctld/rotctld,
  asynchronous, with timeouts, automatic reconnection and rate-limited translated errors."
