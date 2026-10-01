---
name: wsjtx-udp
description: WSJT-X / JTDX UDP network protocol - QDataStream encoding, message header, the message types HamQ needs (Heartbeat, Status, QSO Logged, Logged ADIF), a reference decoder, QUdpSocket listener setup (bind address, multicast, no proxy) and how a logged QSO is saved. Read for hamq/core/wsjtx.py and net/wsjtx_listener.py.
---

# WSJT-X UDP protocol

Reference: `Network/NetworkMessage.hpp` in the WSJT-X source. JTDX and
MSHV use the same protocol; JTDX forked it before WSJT-X 2.0 and has its own
Status (below). Where this skill and `docs/ARCHITECTURE.md` differ, the contract
and the code win.

## Transport

- WSJT-X **sends** to a configured address, default `127.0.0.1:2237`
  (File > Settings > Reporting > UDP Server).
- The plugin **listens** on the address and port of the HamQ settings
  (`wsjtx_addr`, default `127.0.0.1`; `wsjtx_port`, default 2237) and binds that
  address, so only datagrams sent to it arrive: with `127.0.0.1` only programs on
  this computer can add QSOs to the log. Bind `QHostAddress.SpecialAddress.AnyIPv4`
  only for `0.0.0.0` (or an empty address), a multicast group and a broadcast
  address; never for a unicast address (any host on the network could log QSOs).
  `localhost` is 127.0.0.1. IPv6 and host names are refused, and an address that is
  not one of this computer gives a translated error.
- Only one program receives the unicast datagrams of a port. If another logger
  already listens, the user should switch WSJT-X to a multicast address (e.g.
  `224.0.0.1`) and all listeners join the group (`joinMulticastGroup`). Support both.
  HamQ joins on every interface that is up and can multicast, plus loopback (else the
  default interface). A busy port gives a translated error that suggests multicast.
- Bind with `QAbstractSocket.BindFlag.ShareAddress | ReuseAddressHint`.
- `setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))` on the socket: without
  it Qt asks for the system proxy configuration, synchronously in the GUI thread.

## Encoding (Qt QDataStream, big-endian)

| Type | Encoding |
|---|---|
| quint32 / qint32 | 4 bytes BE |
| quint64 / qint64 | 8 bytes BE |
| quint8 / bool | 1 byte (bool: any non-zero byte is true) |
| double | 8 bytes IEEE BE |
| utf8 string | quint32 length + bytes; length `0xFFFFFFFF` = null string, `0` = empty |
| QDateTime | qint64 Julian day, quint32 ms since midnight, quint8 timespec (+ qint32 offset if timespec == 2) |

WSJT-X really sends null strings (an empty DX call, DX grid, sub-mode): every string
value, also `client` and `adif`, can be `None`. Text is UTF-8, but WSJT-X builds the
Logged ADIF record with `QString::toLatin1()`: `Jürgen` arrives as the byte `0xFC`,
and Serbian `č ć š ž đ` arrive as `?` (lost). JTDX sends UTF-8. So decode UTF-8 and
fall back to Latin-1. ADIF lengths in the record are `QString::size()`, UTF-16 code
units (an emoji counts 2).

## Header (every message)

| Field | Type |
|---|---|
| magic | quint32 = `0xADBCCBDA` |
| schema | quint32 (2 or 3) |
| type | quint32 |
| id | utf8 (client name, e.g. `WSJT-X`) |

Ignore packets with wrong magic. Accept schema 2 and 3 (anything else: ignore).
Clients send schema 2 until a server replies with a higher one, so a listener that
never replies sees 2.

## Messages HamQ uses

| Type | Name | Use |
|---|---|---|
| 0 | Heartbeat | mark client as connected; payload: max schema (quint32), version (utf8), revision (utf8; JTDX sends none: `None`) |
| 1 | Status | dial frequency (quint64 Hz, 0 without a rig), mode (utf8), dx call (utf8), then 18 more fields (below) |
| 5 | QSO Logged | structured QSO, decoded as "other" and not used |
| 6 | Close | mark client disconnected |
| 12 | Logged ADIF | **use this**: one utf8 field with a full ADIF record (`<EOH>` + record + `<EOR>`) |

Status after the DX call, in wire order: report, tx_mode, tx_enabled, transmitting,
decoding, rx_df, tx_df, de_call, de_grid, dx_grid, tx_watchdog, sub_mode, fast_mode,
special_op_mode, frequency_tolerance, tr_period (both 0xFFFFFFFF when they do not
apply), configuration_name, tx_message. Read them until the first one that is
missing (older clients send fewer). JTDX (client id `JTDX` or `JTDX - <rig name>`)
sends the fields up to `fast_mode`, then a bool `tx_first`, and nothing after it;
read with the WSJT-X layout that byte would be `special_op_mode` 1. Choose the layout
by the client id.

Using type 12 means HamQ reuses the ADIF parser and avoids decoding
QDateTime. Types 5 and 12 are both sent for each logged QSO; handle only 12.
(Type 5 carries NAME and COMMENT in UTF-8, where WSJT-X's type 12 has lost Serbian
letters; HamQ does not use that yet.)

Every decoded message marks its client alive; a client is gone after 45 s without
messages (WSJT-X sends a heartbeat every 15 s) or when it sends Close. WSJT-X sends a
multicast datagram once per selected interface, so the same Logged ADIF text from the
same client within 2 s is reported once.

Unknown types: ignore silently. Trailing bytes after the fields you read:
ignore (newer versions append fields).

## Reference decoder

```python
import struct

MAGIC = 0xADBCCBDA

class Truncated(Exception):
    pass

class Reader:
    def __init__(self, data: bytes):
        self.b, self.i = data, 0
    def take(self, n):
        if self.i + n > len(self.b):  # check first: a 4 GiB length allocates nothing
            raise Truncated
        v = self.b[self.i:self.i + n]; self.i += n; return v
    def u32(self):
        return struct.unpack(">I", self.take(4))[0]
    def u64(self):
        return struct.unpack(">Q", self.take(8))[0]
    def utf8(self):
        n = self.u32()
        if n == 0xFFFFFFFF:
            return None
        raw = self.take(n)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:  # WSJT-X sends Logged ADIF as Latin-1
            return raw.decode("latin-1")

def decode(data: bytes):
    r = Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        schema = r.u32()
        if schema not in (2, 3):
            return None
        mtype, client = r.u32(), r.utf8()
        if mtype == 0:
            msg = {"type": "heartbeat", "client": client, "schema": schema,
                   "max_schema": r.u32(), "version": r.utf8(), "revision": None}
            try:
                msg["revision"] = r.utf8()  # JTDX sends none
            except Truncated:
                pass
            return msg
        if mtype == 1:  # core/wsjtx.py then reads the other fields (see the table)
            return {"type": "status", "client": client, "schema": schema,
                    "freq_hz": r.u64(), "mode": r.utf8(), "dx_call": r.utf8()}
        if mtype == 6:
            return {"type": "close", "client": client, "schema": schema}
        if mtype == 12:
            return {"type": "logged_adif", "client": client, "schema": schema,
                    "adif": r.utf8()}
        return {"type": "other", "code": mtype, "client": client, "schema": schema}
    except Truncated:
        return None
```

Check every length against the bytes left before reading, as above: a truncated
packet returns None and must never raise into Qt. `hamq/core/wsjtx.py` is the full
version (all Status fields, the JTDX layout, `bytearray` / `memoryview` input) and
has the encoder (`Writer`, `encode_*`) for tests and tools.

## Listener (QGIS side)

```python
from qgis.PyQt.QtNetwork import QHostAddress, QNetworkProxy, QUdpSocket
from hamq.qgis_io.compat import (BIND_REUSE_ADDRESS_HINT, BIND_SHARE_ADDRESS,
                                 HOST_ANY_IPV4, NET_PROXY_NONE)

self.sock = QUdpSocket(self)
self.sock.setProxy(QNetworkProxy(NET_PROXY_NONE))
# the configured address; QHostAddress(HOST_ANY_IPV4) only for 0.0.0.0, multicast, broadcast
host = QHostAddress(address)
ok = self.sock.bind(host, port, BIND_SHARE_ADDRESS | BIND_REUSE_ADDRESS_HINT)
self.sock.readyRead.connect(self._on_ready)

def _on_ready(self):
    for _ in range(100):  # MAX_DATAGRAMS_PER_PASS; a 0 ms timer goes on in the next pass
        if not self.sock.hasPendingDatagrams():
            return
        dg = self.sock.receiveDatagram()
        msg = decode(bytes(dg.data()))
        ...
```

Slots never let an exception reach Qt. On stop and unload (`WsjtxListener.stop()`,
registered with `plugin.add_cleanup`): disconnect `readyRead`, leave the multicast
group, `close()` the socket (frees the port at once), stop the timers.

## Logged QSOs

`HamQController.handle_logged_adif(client, adif)` parses the text with
`core.adif.parse_adi`, converts each record with `core.qso.record_to_qso` (my station
from the settings, the cached cty.dat, `source = "wsjtx"`) and saves it with
`gpkg.insert_qsos` in the main thread. The dedup key makes a QSO that is already in
the log a duplicate. Then `events().dataChanged(path)` refreshes the layers and the
statistics, and the message bar says "New QSO: ..." or "QSO already in the log: ...".
Live QSOs get no `APP_HAMQ_STATION_GRID` mark: they keep the locator of the moment
they were logged.

## Test fixtures

Build packets in tests with the encoder of `hamq/core/wsjtx.py` (`Writer`,
`encode_*`). The golden packets `tests/fixtures/wsjtx/*.bin` and their `README.md`
are synthetic, generated by `scripts/make_wsjtx_fixtures.py` field by field after the
upstream C++ code (JTDX's for the two JTDX packets); never edit them by hand. `--check`
compares, `--verify-qt` rebuilds them with Qt's own `QDataStream`.
`tests/fixtures/wsjtx/captured/` holds 8 datagrams captured from a real WSJT-X 2.7.0
(how: its `README.md`); the encoder reproduces each one byte for byte. JTDX packets
were not captured. `tests/qgis/test_wsjtx_listener.py` sends every fixture as a real
UDP datagram.
