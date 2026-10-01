# Datagrams captured from a real WSJT-X 2.7.0

These eight files are unmodified UDP datagrams sent by a real WSJT-X binary. They
check `hamq.core.wsjtx` against the program itself, not only against the upstream
source code. `tests/core/test_wsjtx.py` decodes each file and re-encodes it byte for
byte.

## How they were captured (2026-09-29)

- WSJT-X 2.7.0, Debian trixie package `2.7.0+repack-1` (Qt 5.15.15), running under
  Xvfb in a `debian:trixie-slim` container. No radio (rig "None") and no sound card.
- `~/.config/WSJT-X.ini`: `MyCall=YU1ABC`, `MyGrid=KN04ft`, UDP server
  `127.0.0.1:2237`, `AcceptUDPRequests=true`. The Log QSO comment was retained
  from the settings (`[LogQSO] SaveComments=true`,
  `LogComments=\x110or\x111\x65 73 J\xfcrgen`, which is how Qt 5 stores
  "Đorđe 73 Jürgen" in an INI file).
- A Python UDP server on port 2237 recorded every datagram. It used
  `hamq.core.wsjtx.Writer` to talk back to WSJT-X:
  1. It waited for the first Heartbeat and Status without replying (schema 2, which
     is what a logger that only listens receives).
  2. It sent a Configure message (type 15) that set the DX call to YU7ABC and the
     DX grid to JN95.
  3. It opened the Log QSO dialog with Alt+Q and accepted it with Enter (xdotool).
     WSJT-X sent QSO Logged (type 5) and Logged ADIF (type 12).
  4. It replied with a schema-3 Heartbeat, as `UDPExamples/MessageServer.cpp` does,
     and sent another Configure (DX call DL1ABC, grid JO62). WSJT-X then sent
     schema 3.
  5. It sent Close (type 6). WSJT-X shut down and sent Close.

No QSO was made on the air, so the reports are empty. With no rig, the first Status
packets report frequency 0; later ones report 14 074 000 Hz.

| File | Bytes | Content |
|---|---|---|
| `wsjtx-2.7.0_heartbeat.bin` | 39 | Heartbeat, schema 2, max schema 3, version `2.7.0`, revision `''`. Byte-identical to the synthetic `../heartbeat_schema2.bin` |
| `wsjtx-2.7.0_status_startup.bin` | 120 | First Status after start: frequency 0, and **null** strings (length `ff ff ff ff`) for DX call, DX grid, `sub_mode` and `tx_message` |
| `wsjtx-2.7.0_status.bin` | 130 | Status, 14 074 000 Hz FT8, DX YU7ABC in JN95, all 21 fields |
| `wsjtx-2.7.0_qso_logged.bin` | 155 | QSO Logged (type 5): two UTC `QDateTime`s (Julian day 2461313, 66 700 012 ms, spec 1), comment in UTF-8, null propagation mode |
| `wsjtx-2.7.0_logged_adif.bin` | 340 | Logged ADIF (type 12): see below |
| `wsjtx-2.7.0_status_schema3.bin` | 130 | Status after schema 3 was negotiated, DX DL1ABC in JO62 |
| `wsjtx-2.7.0_heartbeat_schema3.bin` | 39 | Heartbeat with schema 3 in the header |
| `wsjtx-2.7.0_close_schema3.bin` | 22 | Close, schema 3 |

## What the Logged ADIF capture shows

```
<EOH> ... <rst_sent:0> <rst_rcvd:0> ... <band:3>20m <freq:9>14.075500 ...
<comment:15>?or?e 73 J\xfcrgen <EOR>
```

- WSJT-X encodes the ADIF record with `QString::toLatin1()`: `ü` is the single byte
  `0xFC`, so the text is not valid UTF-8. Serbian `Đ` and `đ` (not in Latin-1)
  arrived as `?` and cannot be recovered. The same comment arrived intact, in UTF-8,
  in the QSO Logged message.
- ADIF lengths count characters (`<comment:15>`), not bytes. More exactly they are
  `QString::size()`, in UTF-16 code units, so a character above U+FFFF (an emoji)
  would count 2 and arrive as `??`.
- Empty fields are written with length 0 (`<rst_sent:0>`).
- `FREQ` is the dial frequency plus the Tx audio offset (14 074 000 + 1500 Hz), in
  MHz with 6 decimals.

`decode()` reads the text as UTF-8 and falls back to Latin-1, so `Jürgen` arrives
correctly.
