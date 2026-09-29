---
name: wsjtx-udp
description: WSJT-X / JTDX UDP network protocol - QDataStream encoding, message header, the message types HamQ needs (Heartbeat, Status, QSO Logged, Logged ADIF), a reference decoder and QUdpSocket listener setup. Read for hamq/core/wsjtx.py and net/wsjtx_listener.py.
---

# WSJT-X UDP protocol

Reference: `Network/NetworkMessage.hpp` in the WSJT-X source. JTDX and
MSHV use the same protocol.

## Transport

- WSJT-X **sends** to a configured address, default `127.0.0.1:2237`
  (Settings -> Reporting -> UDP Server).
- The plugin **listens**. Bind `QUdpSocket` to `QHostAddress.SpecialAddress.AnyIPv4`, port 2237.
- Only one program can bind a unicast port. If another logger already listens,
  the user should switch WSJT-X to a multicast address (e.g. `224.0.0.1`) and
  all listeners join the group (`joinMulticastGroup`). Support both.
- Bind with `QAbstractSocket.BindFlag.ShareAddress | ReuseAddressHint`.

## Encoding (Qt QDataStream, big-endian)

| Type | Encoding |
|---|---|
| quint32 / qint32 | 4 bytes BE |
| quint64 / qint64 | 8 bytes BE |
| quint8 / bool | 1 byte |
| double | 8 bytes IEEE BE |
| utf8 string | quint32 length + bytes; length `0xFFFFFFFF` = null string |
| QDateTime | qint64 Julian day, quint32 ms since midnight, quint8 timespec (+ qint32 offset if timespec == 2) |

## Header (every message)

| Field | Type |
|---|---|
| magic | quint32 = `0xADBCCBDA` |
| schema | quint32 (2 or 3) |
| type | quint32 |
| id | utf8 (client name, e.g. `WSJT-X`) |

Ignore packets with wrong magic. Accept schema 2 and 3.

## Messages HamQ uses

| Type | Name | Use |
|---|---|---|
| 0 | Heartbeat | mark client as connected; payload: max schema (quint32), version (utf8), revision (utf8) |
| 1 | Status | dial frequency (quint64 Hz), mode (utf8), dx call (utf8), ... read only these first three after the header |
| 5 | QSO Logged | structured QSO, skip in MVP |
| 6 | Close | mark client disconnected |
| 12 | Logged ADIF | **use this**: one utf8 field with a full ADIF record (`<EOH>` + record + `<EOR>`) |

Using type 12 means HamQ reuses the ADIF parser and avoids decoding
QDateTime. Types 5 and 12 are both sent for each logged QSO; handle only 12.

Unknown types: ignore silently. Trailing bytes after the fields you read:
ignore (newer versions append fields).

## Reference decoder

```python
import struct

MAGIC = 0xADBCCBDA

class Reader:
    def __init__(self, data: bytes):
        self.b, self.i = data, 0
    def u32(self):
        v = struct.unpack_from(">I", self.b, self.i)[0]; self.i += 4; return v
    def u64(self):
        v = struct.unpack_from(">Q", self.b, self.i)[0]; self.i += 8; return v
    def utf8(self):
        n = self.u32()
        if n == 0xFFFFFFFF:
            return None
        v = self.b[self.i:self.i + n].decode("utf-8", "replace"); self.i += n; return v

def decode(data: bytes):
    r = Reader(data)
    if len(data) < 12 or r.u32() != MAGIC:
        return None
    schema, mtype, client = r.u32(), r.u32(), r.utf8()
    if mtype == 0:
        return {"type": "heartbeat", "client": client,
                "max_schema": r.u32(), "version": r.utf8()}
    if mtype == 1:
        return {"type": "status", "client": client,
                "freq_hz": r.u64(), "mode": r.utf8(), "dx_call": r.utf8()}
    if mtype == 6:
        return {"type": "close", "client": client}
    if mtype == 12:
        return {"type": "logged_adif", "client": client, "adif": r.utf8()}
    return {"type": "other", "code": mtype, "client": client}
```

Wrap `decode` in try/except `struct.error` -> return None. A truncated packet
must never raise into Qt.

## Listener (QGIS side)

```python
from qgis.PyQt.QtNetwork import QUdpSocket, QHostAddress, QAbstractSocket

self.sock = QUdpSocket()
ok = self.sock.bind(QHostAddress(QHostAddress.SpecialAddress.AnyIPv4), port,
                    QAbstractSocket.BindFlag.ShareAddress | QAbstractSocket.BindFlag.ReuseAddressHint)
self.sock.readyRead.connect(self._on_ready)

def _on_ready(self):
    while self.sock.hasPendingDatagrams():
        dg = self.sock.receiveDatagram()
        msg = decode(bytes(dg.data()))
        ...
```

On unload: disconnect `readyRead`, `close()` the socket.

## Test fixtures

Build packets in tests with a small encoder (mirror of `Reader`), and also
commit a few real captured datagrams as `tests/fixtures/wsjtx/*.bin`
(capture with `socat -u UDP-RECV:2237 - > pkt.bin` or Wireshark).
