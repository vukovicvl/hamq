---
name: hamlib
description: Part of v0.1.0 (PLAN.md M7). Controlling radios and antenna rotators through Hamlib rigctld / rotctld over TCP - extended commands and real replies, quirks, error codes, mode names, rotator ranges, async QTcpSocket client, tests with dummy daemons. Read for hamq/core/hamlib.py, core/rigmode.py, net/hamlib_client.py, the Radio tab and the rotator map tool.
---

# Hamlib rigctld / rotctld

HamQ never talks to the radio directly. The user runs Hamlib daemons,
HamQ connects over TCP. This covers hundreds of radios with zero dependencies.
Part of v0.1.0 (milestone M7), checked with the Hamlib 4.6 dummy devices, not yet
with a real radio or rotator. Where this skill and `docs/ARCHITECTURE.md` differ,
the contract and the code win.

```bash
rigctld -m <model> -r /dev/ttyUSB0 -s 19200 -T 127.0.0.1 -t 4532   # radio
rotctld -m <model> -r /dev/ttyUSB1 -T 127.0.0.1 -t 4533             # rotator
rigctl -l                                                           # list models
```

`-T 127.0.0.1` lets only this computer connect. `rigctld --vfo` is not supported:
every command then needs a VFO argument, so the client only times out and reconnects.

Testing without hardware: `rigctld -m 1` and `rotctld -m 1` are dummy devices. The
dummy rig starts at 145000000 Hz, FM, passband 15000; the dummy rotator has the range
-180..450 azimuth, 0..90 elevation and turns about 6°/s (right after `+P`, `+p` still
reports the old position).

## Protocol

Line-based ASCII, one command per line, `\n` terminated.

### rigctld (default port 4532)

| Command | Meaning | Response |
|---|---|---|
| `f` | get frequency | `14074000` (Hz) |
| `F 14074000` | set frequency | `RPRT 0` |
| `m` | get mode | two lines: `USB`, `2400` (passband) |
| `M USB 0` | set mode, 0 = default passband, -1 = keep it | `RPRT 0` |
| `t` | get PTT (not used by HamQ) | `0` or `1` |

### rotctld (default port 4533)

| Command | Meaning | Response |
|---|---|---|
| `p` | get position | two lines: azimuth, elevation |
| `P 123.0 0.0` | set position | `RPRT 0` |
| `S` | stop | `RPRT 0` |

`RPRT 0` is success, negative values are Hamlib error codes.
Extended response mode (prefix command with `+`) returns labeled lines ending in
`RPRT n`, which is easier to parse reliably. HamQ always uses it (`+f`, `+m`, `+F`,
`+M`, `+p`, `+P`, `+S`). Real Hamlib 4.6.2 replies (`docs/ARCHITECTURE.md`):

```
+f            -> 'get_freq:\nFrequency: 14074000\nRPRT 0\n'
+m            -> 'get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n'
+F 14074000   -> 'set_freq: 14074000\nRPRT 0\n'
+M USB 0      -> 'set_mode: USB 0\nRPRT 0\n'
+t            -> 'get_ptt:\nRPRT -11\n'            (dummy: feature not available)
+p            -> 'get_pos:\nAzimuth: 123.00\nElevation: 0.00\nRPRT 0\n'
+P 123.5 10   -> 'set_pos: 123.5 10\nRPRT 0\n'
+S            -> 'stop:\nRPRT 0\n'
+F abc        -> 'set_freq: abc\nRPRT -1\n'
```

Quirks the code is built around:

- An unknown command (`+X_bogus`) gets no reply at all: the client must time out.
  rigctld even takes the next line as its argument (`+F abc` then gives
  `set_split_mode: bogus +F`, `RPRT -1`, `RPRT -18`, `RPRT -1`). A rig client pointed
  at rotctld (or the reverse) gets no reply either. Accept only the reply whose header
  is `expected_command(cmd)` (`get_freq` for `+f`); stray `RPRT` lines and other
  headers are ignored, so the client resynchronizes.
- `+M USB` without a passband gets no reply: the next line becomes the passband.
  Always send it (`cmd_set_mode`: 0 = default, -1 = keep).
- `+M usb 0` or an unknown mode name gets `RPRT 0` and changes nothing:
  `cmd_set_mode` takes only exact `MODES` names (`ValueError` otherwise).
- Hamlib 4.6 reports PKTFM as `FM-D` and PKTAM as `AM-D` (also `USB-D`, `LSB-D`,
  `CW-R`, `RTTY-R`): `parse_mode` returns the canonical `MODES` names (`FM-D` ->
  `PKTFM`).
- After a hard rig error (I/O) rigctld sends the header without `RPRT`, and closes the
  socket when it cannot reopen the rig: a header always starts a new reply, and a
  remote close is handled like a timeout (reconnect).
- The dummy accepts `+F 0`: `cmd_set_freq` needs hz > 0, `parse_freq` gives `None` for
  0 and reads `14074000.000000` (some backends) as 14074000.
- Qt: `ResponseParser.feed(bytes(socket.readAll()))`; a `QByteArray` raises `TypeError`.

Error codes: `enum rig_errcode_e` 0..22 (the same in 4.6.2, 4.6.5 and for rotctld).
`error_message(code)` translates them (sign ignored); an unknown code gives a text
with the code. Common: `-1` invalid parameter (`+F abc`), `-5` communication timed
out, `-11` feature not available (`+t` on the dummy), `-21` limit exceeded (rotator
position outside its range).

## Client design

- `QTcpSocket` with no proxy (`QNetworkProxy.ProxyType.NoProxy`), async (signals and
  `QTimer`, never `waitFor*()`), one command in flight at a time, queue the rest. Set
  commands go ahead of queued polls, a newer set command replaces a queued one of the
  same kind, a poll is never queued twice, `stop_rotation` cancels a queued turn and
  goes first; at most `MAX_QUEUE = 16` wait.
- Poll `+f` and `+m` every 1 s while connected (`rig_poll_ms`, default 1000; the
  settings dialog allows 100 to 60000), `+p` every 1 s. Stop polling on disconnect.
  After a set command the values are read back at once.
- Timeout 2 s for connecting and per command; on timeout, a socket error or a remote
  close: reset the parser, drop the queue, mark disconnected, retry connect every 5 s.
  Connected means the daemon answered a command on this connection, not only TCP.
- A GUI thread that was blocked is no timeout (no false disconnect): commands are
  flushed to the socket at once (`flush()`), and before a timeout is reported the event
  loop gets one more short pass (100 ms) that delivers a reply or connection that
  arrived meanwhile (again when the watchdog fired late once more). Replies left
  unread during a nested event loop (a modal dialog) are read by the poll timer and
  the watchdog.
- Errors are translated, logged once in the HamQ log tab and emitted with
  `errorOccurred` (the GUI shows them and does not log them again). Connection errors
  say what to do (start rigctld / rotctld, check the address and port). Poll and
  connection errors are rate limited (reported when they start, again at most once a
  minute); failed set commands always. `current_error()` repeats the last problem in
  the current language.
- Parse in `core/hamlib.py` (`ResponseParser` and pure functions on response text);
  socket code in `net/hamlib_client.py`.
- Rotator: map tool click -> `bearing_deg(my_qth, clicked)` (+180 with *Long path*) ->
  `rotator_target(bearing, rot_min_az, rot_max_az, current_az)` -> `+P <az> 0.0`.
  Ask for confirmation the first time (*Turn* / *Cancel*, stored in `rot_confirmed`).
  Never clamp to the range: `rotator_target` returns the equivalent `bearing + k * 360`
  inside the range the user configured (0..360, 0..450, -180..180, 180..540, ...;
  where two fit, the one closest to the current azimuth) or `None` when the range does
  not reach the bearing (0..180 and 270): then nothing is sent and the user is told
  why. The daemon answers `RPRT -21` outside its own range.
- Manual QSO: `core/rigmode.py` turns the radio mode into ADIF (`USB`/`LSB` -> `SSB`,
  `CW`/`CWR` -> `CW`, `C4FM` -> `DIGITALVOICE` + `C4FM`); for the data modes (`PKTUSB`,
  `PKTFM`, ...) the operator picks the mode (FT8, PSK31, ...).

## Tests

- `tests/core/test_hamlib.py`: the captured replies, whole and split at every byte.
- `tests/qgis/test_hamlib_client.py` runs against `tests/qgis/fake_hamlib.py`, an
  in-process fake with the 4.6.2 reply formats and switches for silence, stray lines,
  split or late replies and closed connections.
- Live tests `test_real_rigctld` / `test_real_rotctld` run when `HAMQ_RIGCTLD` /
  `HAMQ_ROTCTLD` are set to `host:port`. They change frequency, mode and rotator
  position: dummy devices only. `scripts/test_qgis.sh` passes both into its Docker
  containers (use an address the container can reach, e.g. the dummy container's IP).
- `hamq/hamlib-dummy` is a local Docker image, not in this repository: Hamlib 4.6.2,
  `rigctld -m 1 -T 0.0.0.0 -t 4532` and `rotctld -m 1 -T 0.0.0.0 -t 4533`.
