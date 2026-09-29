---
name: hamlib
description: Post-MVP. Controlling radios and antenna rotators through Hamlib rigctld / rotctld over TCP - commands, responses, error codes, async QTcpSocket client. Read only for tasks after v0.1.0.
---

# Hamlib rigctld / rotctld

HamQ never talks to the radio directly. The user runs Hamlib daemons,
HamQ connects over TCP. This covers hundreds of radios with zero dependencies.

```bash
rigctld -m <model> -r /dev/ttyUSB0 -s 19200 -t 4532   # radio
rotctld -m <model> -r /dev/ttyUSB1 -t 4533             # rotator
rigctl -l                                              # list models
```

Testing without hardware: `rigctld -m 1` and `rotctld -m 1` are dummy devices.

## Protocol

Line-based ASCII, one command per line, `\n` terminated.

### rigctld (default port 4532)

| Command | Meaning | Response |
|---|---|---|
| `f` | get frequency | `14074000` (Hz) |
| `F 14074000` | set frequency | `RPRT 0` |
| `m` | get mode | two lines: `USB`, `2400` (passband) |
| `M USB 0` | set mode, 0 = default passband | `RPRT 0` |
| `t` | get PTT | `0` or `1` |

### rotctld (default port 4533)

| Command | Meaning | Response |
|---|---|---|
| `p` | get position | two lines: azimuth, elevation |
| `P 123.0 0.0` | set position | `RPRT 0` |
| `S` | stop | `RPRT 0` |

`RPRT 0` is success, negative values are Hamlib error codes.
Extended response mode (prefix command with `+`) returns labeled lines ending in
`RPRT n`, which is easier to parse reliably. Prefer `+f`, `+m`, `+p`.

## Client design

- `QTcpSocket`, async, one command in flight at a time, queue the rest.
- Poll `+f` and `+m` every 1 s while connected. Stop polling on disconnect.
- Timeout 2 s per command; on timeout, mark disconnected, retry connect every 5 s.
- Parse in `core/hamlib.py` (pure functions on response text); socket code in `net/`.
- Rotator: map tool click -> `bearing_deg(my_qth, clicked)` -> `+P <az> 0`.
  Ask for confirmation the first time. Clamp azimuth to the rotator range
  the user configured (e.g. 0-360 or 0-450).
