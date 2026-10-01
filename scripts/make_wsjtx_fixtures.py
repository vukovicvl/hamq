#!/usr/bin/env python3
"""Generate the WSJT-X UDP golden packets in tests/fixtures/wsjtx/ and their README.

The packets are synthetic, built field by field the way WSJT-X's C++ code builds them
(Network/MessageClient.cpp, logbook/logbook.cpp, widgets/mainwindow.cpp), and JTDX's
code for the two JTDX packets. Running the script again reproduces the committed files
byte for byte.

    python3 scripts/make_wsjtx_fixtures.py            # write *.bin and README.md
    python3 scripts/make_wsjtx_fixtures.py --check    # compare only; exit 1 on any difference
    python3 scripts/make_wsjtx_fixtures.py --out DIR  # write (or --check) another directory

    # Rebuild every packet with Qt's own QDataStream and text conversion (PyQt5 or PyQt6
    # through qgis.PyQt) and compare with the hamq encoder; also compare hamq's UTF-8 and
    # Latin-1 conversion and the ADIF lengths with Qt's for a few texts. QGIS's Python
    # directory is /usr/share/qgis/python for distribution packages and the qgis/qgis
    # images, and /usr/local/share/qgis/python for QGIS built from source (for example
    # the camptocamp/qgis-server images):
    PYTHONPATH=/usr/share/qgis/python QT_QPA_PLATFORM=offscreen \\
        python3 scripts/make_wsjtx_fixtures.py --verify-qt

With both --check and --verify-qt, each check reports on its own and the exit status is 1
when either fails.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hamq.core import wsjtx  # noqa: E402 (needs the repository root on sys.path)
from hamq.core.wsjtx import Writer  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "wsjtx"
UTC = timezone.utc

# --- scenario ---------------------------------------------------------------------------------
# YU1ABC (KN04ft, Belgrade) works YU7ABC (JN95) with FT8 on 40 m on 15 September 2026,
# 18:45:00-18:46:15 UTC, Tx audio offset 1234 Hz. The operator typed the name "Đorđe" in
# WSJT-X's Log QSO dialog; "Report in comments" is on.

CLIENT = "WSJT-X"
VERSION = "2.7.0"
REVISION = "a1b2c3d"  # synthetic, git-style short hash
DIAL_HZ = 7_074_000
TX_DF = 1234
TIME_ON = datetime(2026, 9, 15, 18, 45, 0, tzinfo=UTC)
TIME_OFF = datetime(2026, 9, 15, 18, 46, 15, tzinfo=UTC)
COMMENT = "FT8  Sent: -12  Rcvd: -09"  # LogQSO with report_in_comments()

# Status as MainWindow::statusUpdate fills it between two transmissions. sub_mode is a
# null QString for FT8; frequency tolerance and T/R period are not shown for FT8, so
# WSJT-X sends the maximum quint32 ("not applicable").
STATUS = {
    "report": "-12",
    "tx_mode": "FT8",
    "tx_enabled": True,
    "transmitting": False,
    "decoding": False,
    "rx_df": TX_DF,
    "tx_df": TX_DF,
    "de_call": "YU1ABC",
    "de_grid": "KN04ft",
    "dx_grid": "JN95",
    "tx_watchdog": False,
    "sub_mode": None,
    "fast_mode": False,
    "special_op_mode": 0,
    "frequency_tolerance": 0xFFFFFFFF,
    "tr_period": 0xFFFFFFFF,
    "configuration_name": "Default",
    "tx_message": "YU7ABC YU1ABC -12",
}
TRUNCATE_IN = "de_call"  # truncated_status.bin ends in the middle of this string


def qstring_size(value: str) -> int:
    """QString::size(): UTF-16 code units, so a character above U+FFFF counts 2."""
    return sum(2 if ord(char) > 0xFFFF else 1 for char in value)


def adif_field(name: str, value: str) -> str:
    """ADIF field as LogBook::QSOToADIF writes it: the length is QString::size()."""
    return f"<{name}:{qstring_size(value)}>{value}"


def adif_record(fields: list[tuple[str, str]]) -> str:
    return " ".join(adif_field(name, value) for name, value in fields)


def wsjtx_adif(record: str) -> str:
    """The text MessageClient::logged_ADIF sends: an ADIF header, the record, <EOR>."""
    return "\n<adif_ver:5>3.1.0\n<programid:6>WSJT-X\n<EOH>\n" + record + " <EOR>"


def mhz(hz: int) -> str:
    return f"{hz / 1e6:.6f}"  # QString::number(m_dialFreq / 1.e6, 'f', 6)


ADIF_TEXT = wsjtx_adif(
    adif_record(
        [
            ("call", "YU7ABC"),
            ("gridsquare", "JN95"),
            ("mode", "FT8"),
            ("rst_sent", "-12"),
            ("rst_rcvd", "-09"),
            ("qso_date", TIME_ON.strftime("%Y%m%d")),
            ("time_on", TIME_ON.strftime("%H%M%S")),
            ("qso_date_off", TIME_OFF.strftime("%Y%m%d")),
            ("time_off", TIME_OFF.strftime("%H%M%S")),
            ("band", "40m"),
            ("freq", mhz(DIAL_HZ + TX_DF)),
            ("station_callsign", "YU1ABC"),
            ("my_gridsquare", "KN04ft"),
            ("tx_pwr", "100"),
            ("comment", COMMENT),
            ("name", "Đorđe"),
        ]
    )
)

# The next day YU1ABC works DL1ABC (JO62) on 20 m; the name Jürgen is in Latin-1, the
# character set real WSJT-X uses for the ADIF record.
LATIN1_ADIF_TEXT = wsjtx_adif(
    adif_record(
        [
            ("call", "DL1ABC"),
            ("gridsquare", "JO62"),
            ("mode", "FT8"),
            ("rst_sent", "-15"),
            ("rst_rcvd", "-07"),
            ("qso_date", "20260916"),
            ("time_on", "061530"),
            ("qso_date_off", "20260916"),
            ("time_off", "061645"),
            ("band", "20m"),
            ("freq", mhz(14_074_000 + 1512)),
            ("station_callsign", "YU1ABC"),
            ("my_gridsquare", "KN04ft"),
            ("tx_pwr", "100"),
            ("name", "Jürgen"),
        ]
    )
)

# QSO Logged (type 5): MessageClient::qso_logged, same QSO as ADIF_TEXT.
QSO_LOGGED = [
    ("time_off", "qdatetime", TIME_OFF),
    ("dx_call", "utf8", "YU7ABC"),
    ("dx_grid", "utf8", "JN95"),
    ("tx_frequency_hz", "u64", DIAL_HZ + TX_DF),
    ("mode", "utf8", "FT8"),
    ("report_sent", "utf8", "-12"),
    ("report_received", "utf8", "-09"),
    ("tx_power", "utf8", "100"),
    ("comments", "utf8", COMMENT),
    ("name", "utf8", "Đorđe"),
    ("time_on", "qdatetime", TIME_ON),
    ("operator_call", "utf8", ""),
    ("my_call", "utf8", "YU1ABC"),
    ("my_grid", "utf8", "KN04ft"),
    ("exchange_sent", "utf8", ""),
    ("exchange_received", "utf8", ""),
    ("propagation_mode", "utf8", ""),
]

JTDX_VERSION = "2.2.159"
JTDX_CLIENT = "JTDX"  # QApplication::applicationName(); "JTDX - <rig>" with --rig-name
JTDX_DIAL_HZ = 14_074_000

# Status as JTDX's MainWindow::statusUpdate fills it (jtdx-project/jtdx): the WSJT-X
# fields up to fast_mode, then tx_first (Tx in the first, even period), and nothing
# else. JTDX always sends a null sub-mode and fast_mode false; its DFs are qint32.
JTDX_STATUS = {
    "report": "-10",
    "tx_mode": "FT8",
    "tx_enabled": True,
    "transmitting": False,
    "decoding": False,
    "rx_df": 1500,
    "tx_df": 1210,
    "de_call": "YU1ABC",
    "de_grid": "KN04ft",
    "dx_grid": "JO62",
    "tx_watchdog": False,
    "sub_mode": None,
    "fast_mode": False,
    "tx_first": True,
}

# --- packets built with the hamq encoder ------------------------------------------------------


def status_packet() -> bytes:
    return wsjtx.encode_status(CLIENT, DIAL_HZ, "FT8", "YU7ABC", **STATUS)


def truncated_status_packet() -> bytes:
    keys = list(STATUS)
    before = {key: STATUS[key] for key in keys[: keys.index(TRUNCATE_IN)]}
    end = len(wsjtx.encode_status(CLIENT, DIAL_HZ, "FT8", "YU7ABC", **before))
    return status_packet()[: end + 4 + len(STATUS[TRUNCATE_IN]) // 2]  # length + half the text


def jtdx_heartbeat_packet() -> bytes:
    """JTDX's MessageClient::impl::heartbeat: maximum schema and version, no revision."""
    return Writer().header(wsjtx.HEARTBEAT, "JTDX", 2).u32(3).utf8(JTDX_VERSION).getvalue()


def qso_logged_packet() -> bytes:
    writer = Writer().header(wsjtx.QSO_LOGGED, CLIENT, 2)
    for _name, kind, value in QSO_LOGGED:
        getattr(writer, kind)(value)
    return writer.getvalue()


def build_fixtures() -> dict[str, bytes]:
    """File name -> packet, in the order of the README table."""
    heartbeat2 = wsjtx.encode_heartbeat(CLIENT, 3, VERSION, "", schema=2)
    return {
        "heartbeat_schema2.bin": heartbeat2,
        "heartbeat_schema3.bin": wsjtx.encode_heartbeat(CLIENT, 3, VERSION, REVISION, schema=3),
        "heartbeat_jtdx.bin": jtdx_heartbeat_packet(),
        "status_ft8.bin": status_packet(),
        "truncated_status.bin": truncated_status_packet(),
        "status_jtdx.bin": wsjtx.encode_status(
            JTDX_CLIENT, JTDX_DIAL_HZ, "FT8", "DL1ABC", **JTDX_STATUS
        ),
        "close.bin": wsjtx.encode_close(CLIENT),
        "logged_adif.bin": wsjtx.encode_logged_adif(CLIENT, ADIF_TEXT),
        "logged_adif_latin1.bin": wsjtx.encode_logged_adif(CLIENT, LATIN1_ADIF_TEXT, latin1=True),
        "qso_logged.bin": qso_logged_packet(),
        "bad_magic.bin": heartbeat2[:4][::-1] + heartbeat2[4:],
    }


DESCRIPTIONS = {
    "heartbeat_schema2.bin": (
        "Heartbeat, WSJT-X 2.7.0, schema 2 (what a listening logger receives), revision empty",
        "heartbeat, `revision` `''`",
    ),
    "heartbeat_schema3.bin": (
        f"Heartbeat after a server negotiated schema 3, revision `{REVISION}`",
        "heartbeat, schema 3",
    ),
    "heartbeat_jtdx.bin": (
        f"Heartbeat as JTDX {JTDX_VERSION} sends it: no revision field",
        "heartbeat, `revision` `None`",
    ),
    "status_ft8.bin": (
        "Status with all 21 fields, FT8 on 7.074 MHz, DX YU7ABC, null `sub_mode`",
        "status with every key",
    ),
    "truncated_status.bin": (
        f"`status_ft8.bin` cut in the middle of `{TRUNCATE_IN}`",
        f"status: frequency, mode, DX call and the fields before `{TRUNCATE_IN}`",
    ),
    "status_jtdx.bin": (
        f"Status as JTDX {JTDX_VERSION} sends it: the fields up to `fast_mode`, then"
        " `tx_first` true",
        "status with `tx_first`, no `special_op_mode`",
    ),
    "close.bin": ("Close: the header only", "close"),
    "logged_adif.bin": (
        "Logged ADIF, WSJT-X record layout, UTF-8 text (as JTDX sends it), NAME `Đorđe`",
        "logged_adif",
    ),
    "logged_adif_latin1.bin": (
        "Logged ADIF in Latin-1 (as WSJT-X sends it) with NAME `Jürgen` (byte 0xFC)",
        "logged_adif, text read as Latin-1",
    ),
    "qso_logged.bin": (
        "QSO Logged (type 5) for the same QSO, two UTC QDateTimes",
        "other, code 5",
    ),
    "bad_magic.bin": ("`heartbeat_schema2.bin` with the magic written little-endian", "`None`"),
}

# --- README -----------------------------------------------------------------------------------


def status_segments() -> list[tuple[bytes, str]]:
    """status_ft8.bin split into (bytes, label) pieces for the annotated dump."""

    def num(method: str, value: object, label: str) -> tuple[bytes, str]:
        return getattr(Writer(), method)(value).getvalue(), label

    def text(value: str | None, label: str) -> list[tuple[bytes, str]]:
        raw = Writer().utf8(value).getvalue()
        if value is None:
            return [(raw, f"{label}: null string (length 0xFFFFFFFF)")]
        return [(raw[:4], f"{label}: length {len(raw) - 4}"), (raw[4:], f'  "{value}"')]

    segments = [
        num("u32", wsjtx.MAGIC, "magic 0xADBCCBDA"),
        num("u32", 2, "schema 2"),
        num("u32", wsjtx.STATUS, "message type 1 = Status"),
        *text(CLIENT, "id"),
        num("u64", DIAL_HZ, f"dial frequency {DIAL_HZ} Hz (quint64)"),
        *text("FT8", "mode"),
        *text("YU7ABC", "DX call"),
    ]
    for key, value in STATUS.items():
        if isinstance(value, str) or value is None:
            segments += text(value, key)
            continue
        if isinstance(value, bool):
            method, qt_type = "boolean", "bool"
        elif key == "special_op_mode":
            method, qt_type = "u8", "quint8"
        else:
            method, qt_type = "u32", "quint32"
        shown = "0xFFFFFFFF (not applicable)" if value == 0xFFFFFFFF else repr(value)
        segments.append(num(method, value, f"{key} = {shown} ({qt_type})"))
    return segments


def hex_dump(segments: list[tuple[bytes, str]]) -> str:
    lines = ["offset  bytes                                            field"]
    offset = 0
    for raw, label in segments:
        for start in range(0, len(raw), 16):
            chunk = raw[start : start + 16]
            lines.append(
                f"{offset + start:04x}    {chunk.hex(' '):<48} {label if start == 0 else ''}"
            )
        offset += len(raw)
    return "\n".join(line.rstrip() for line in lines)


def render_readme(fixtures: dict[str, bytes]) -> str:
    segments = status_segments()
    if b"".join(raw for raw, _ in segments) != fixtures["status_ft8.bin"]:
        raise AssertionError("annotated segments do not add up to status_ft8.bin")
    rows = "\n".join(
        f"| `{name}` | {len(data)} | {DESCRIPTIONS[name][0]} | {DESCRIPTIONS[name][1]} |"
        for name, data in fixtures.items()
    )
    return f"""# WSJT-X UDP test packets

Golden datagrams for `tests/core/test_wsjtx.py` and the listener tests. They are
**synthetic**: `scripts/make_wsjtx_fixtures.py` builds them field by field the way the
WSJT-X C++ code does (`Network/MessageClient.cpp`, `logbook/logbook.cpp`,
`widgets/mainwindow.cpp`), and the JTDX code for the two JTDX packets. They are not
captures. This README is generated by the same script; do not edit either by hand.

Real datagrams captured from WSJT-X 2.7.0 are in `captured/` (see `captured/README.md`).
`heartbeat_schema2.bin` is byte-identical to the captured heartbeat, and the hamq
encoder reproduces every captured packet byte for byte.

```sh
python3 scripts/make_wsjtx_fixtures.py            # regenerate
python3 scripts/make_wsjtx_fixtures.py --check    # verify, write nothing
PYTHONPATH=/usr/share/qgis/python QT_QPA_PLATFORM=offscreen \\
    python3 scripts/make_wsjtx_fixtures.py --verify-qt   # rebuild with Qt's QDataStream
# QGIS built from source (e.g. camptocamp/qgis-server): /usr/local/share/qgis/python
```

Scenario: YU1ABC (KN04ft, Belgrade) works YU7ABC (JN95) with FT8 on 40 m on
15 September 2026, 18:45:00 to 18:46:15 UTC, Tx audio offset {TX_DF} Hz. The operator
typed the name Đorđe in the Log QSO dialog.

| File | Bytes | Packet | `decode()` gives |
|---|---|---|---|
{rows}

## Encoding

Qt `QDataStream`, big-endian, stream version `Qt_5_2` (schema 2) or `Qt_5_4`
(schema 3); the two are identical for every type below.

| Type | Bytes |
|---|---|
| `quint8`, `bool` | 1 (`bool`: 0 or 1; any non-zero byte reads as true) |
| `quint32` / `qint32` | 4 |
| `quint64` / `qint64` | 8 |
| `utf8` (`QByteArray`) | `quint32` length + bytes; `ff ff ff ff` = null string, `00 00 00 00` = empty |
| `QDateTime` (UTC) | `qint64` Julian day, `quint32` ms since midnight, `quint8` spec `01` |

Every packet starts with `quint32` magic `0xADBCCBDA`, `quint32` schema, `quint32`
message type and the `utf8` client id. WSJT-X sends schema 2 until a server replies
with a higher one; a logger that only listens therefore sees schema 2.

## Character sets in Logged ADIF

WSJT-X builds the ADIF record with `QString::toLatin1()` (`LogBook::QSOToADIF`), so the
"utf8" field really carries Latin-1: `Jürgen` arrives as the byte `0xFC`, and letters
that are not in Latin-1, including Serbian `č ć š ž đ Č Ć Š Ž Đ`, are replaced by `?`
before sending. JTDX sends the record as UTF-8. Both write each ADIF field length as
`QString::size()`, in UTF-16 code units: one per character, but two for a character
above U+FFFF such as an emoji, which WSJT-X sends as `??`. `decode()` reads UTF-8 and
falls back to Latin-1, so both arrive intact; a Serbian name typed in WSJT-X arrives
as `?or?e` and cannot be recovered. The QSO Logged message (type 5) always uses UTF-8.
A capture from WSJT-X 2.7.0 confirms the WSJT-X part:
`captured/wsjtx-2.7.0_logged_adif.bin` carries
`<comment:15>?or?e 73 J\\xfcrgen` for the comment "Đorđe 73 Jürgen". The JTDX part comes
from the JTDX source (`logqso.cpp`: `myadif.trimmed().toUtf8()`) and was not captured.

`logged_adif.bin` keeps WSJT-X's record layout but carries UTF-8 text, as JTDX would,
so that the Serbian name reaches the ADIF parser intact. A real WSJT-X would send
`<name:5>?or?e` for it.

## JTDX

JTDX forked the protocol before WSJT-X 2.0. Its Status (`status_jtdx.bin`) has the
WSJT-X fields up to `fast_mode`, then a `bool` `tx_first` (Tx in the first, even
period), and ends there: no `special_op_mode` and none of the later fields. `rx_df` and
`tx_df` are `qint32` in JTDX (`quint32` in WSJT-X); they are audio offsets, so the bytes
are the same. JTDX always sends a null `sub_mode` and `fast_mode` false, and its
Heartbeat has no revision (`heartbeat_jtdx.bin`). `decode()` recognises JTDX by the
client id, `JTDX` or `JTDX - <rig name>`, and reads the last byte as `tx_first`; read
with the WSJT-X layout it would be `special_op_mode` 1, "NA VHF". Both JTDX packets
follow the JTDX source (`MessageClient.cpp`, GitHub `jtdx-project/jtdx`); they were not
captured.

## Annotated dump: `status_ft8.bin`

```
{hex_dump(segments)}
```
"""


# --- Qt cross-check ---------------------------------------------------------------------------

# Texts whose conversion hamq must do exactly as Qt: Latin-1, letters outside Latin-1
# (Serbian Latin and Cyrillic) and characters above U+FFFF, which are two UTF-16 code units
TEXT_PROBES = ("Jürgen", "Đorđe Ђорђе čćšž", "73 \U0001f4fb", "\U0001f4fb\U0001f4fb", "")


def qt_encode(text: str, latin1: bool = False) -> bytes:
    """QString::toUtf8() / toLatin1(), converted by Qt itself.

    Qt 5 has QTextCodec, Qt 6 QStringEncoder. For Latin-1 both work one UTF-16 code unit
    at a time, like QString::toLatin1().
    """
    try:
        from qgis.PyQt.QtCore import QTextCodec  # Qt 5
    except ImportError:
        from qgis.PyQt.QtCore import QStringConverter, QStringEncoder  # Qt 6

        encoding = QStringConverter.Encoding.Latin1 if latin1 else QStringConverter.Encoding.Utf8
        return bytes(QStringEncoder(encoding).encode(text))
    return bytes(QTextCodec.codecForName(b"ISO-8859-1" if latin1 else b"UTF-8").fromUnicode(text))


def qt_packets() -> dict[str, bytes]:
    """The same packets built with Qt's QDataStream, mirroring the WSJT-X C++ statements."""
    from qgis.PyQt.QtCore import QByteArray, QDataStream, QDate, QDateTime, QIODevice, Qt, QTime

    def ba(value: str | None, latin1: bool = False) -> QByteArray:
        # QString::toUtf8() / toLatin1(): a null QString gives a null QByteArray
        if value is None:
            return QByteArray()
        return QByteArray(qt_encode(value, latin1))

    def qdatetime(value: datetime) -> QDateTime:
        date = QDate(value.year, value.month, value.day)
        time = QTime(value.hour, value.minute, value.second, value.microsecond // 1000)
        try:  # Qt >= 6.5; the TimeSpec constructor is deprecated there
            from qgis.PyQt.QtCore import QTimeZone

            return QDateTime(date, time, QTimeZone(QTimeZone.Initialization.UTC))
        except (ImportError, AttributeError, TypeError):
            return QDateTime(date, time, Qt.TimeSpec.UTC)

    class Builder:
        """NetworkMessage::Builder::common_initialization."""

        def __init__(self, msg_type: int, client: str, schema: int) -> None:
            self.data = QByteArray()
            self.out = QDataStream(self.data, QIODevice.OpenModeFlag.WriteOnly)
            version = QDataStream.Version.Qt_5_2 if schema <= 2 else QDataStream.Version.Qt_5_4
            self.out.setVersion(version)
            self.out.writeUInt32(0xADBCCBDA)
            self.out.writeUInt32(schema)
            self.out.writeUInt32(msg_type)
            self.out << ba(client)

        def value(self) -> bytes:
            if self.out.status() != QDataStream.Status.Ok:
                raise RuntimeError("QDataStream write failed")
            return bytes(self.data)

    def heartbeat(client: str, version: str, revision: str | None, schema: int) -> bytes:
        hb = Builder(0, client, schema)  # MessageClient::impl::heartbeat
        hb.out.writeUInt32(3)  # Builder::schema_number
        hb.out << ba(version)
        if revision is not None:  # JTDX writes no revision
            hb.out << ba(revision)
        return hb.value()

    s = STATUS  # MessageClient::status_update
    st = Builder(1, CLIENT, 2)
    out = st.out
    out.writeUInt64(DIAL_HZ)
    out << ba("FT8") << ba("YU7ABC") << ba(s["report"]) << ba(s["tx_mode"])
    out.writeBool(s["tx_enabled"])
    out.writeBool(s["transmitting"])
    out.writeBool(s["decoding"])
    out.writeUInt32(s["rx_df"])
    out.writeUInt32(s["tx_df"])
    out << ba(s["de_call"]) << ba(s["de_grid"]) << ba(s["dx_grid"])
    out.writeBool(s["tx_watchdog"])
    out << ba(s["sub_mode"])
    out.writeBool(s["fast_mode"])
    out.writeUInt8(s["special_op_mode"])
    out.writeUInt32(s["frequency_tolerance"])
    out.writeUInt32(s["tr_period"])
    out << ba(s["configuration_name"]) << ba(s["tx_message"])

    def jtdx_status() -> bytes:
        j = JTDX_STATUS  # JTDX's MessageClient::status_update
        msg = Builder(1, JTDX_CLIENT, 2)
        out = msg.out
        out.writeUInt64(JTDX_DIAL_HZ)
        out << ba("FT8") << ba("DL1ABC") << ba(j["report"]) << ba(j["tx_mode"])
        out.writeBool(j["tx_enabled"])
        out.writeBool(j["transmitting"])
        out.writeBool(j["decoding"])
        out.writeInt32(j["rx_df"])  # qint32 in JTDX
        out.writeInt32(j["tx_df"])
        out << ba(j["de_call"]) << ba(j["de_grid"]) << ba(j["dx_grid"])
        out.writeBool(j["tx_watchdog"])
        out << ba(j["sub_mode"])
        out.writeBool(j["fast_mode"])
        out.writeBool(j["tx_first"])
        return msg.value()

    def logged_adif(text: str, latin1: bool) -> bytes:
        msg = Builder(12, CLIENT, 2)  # MessageClient::logged_ADIF
        msg.out << ba(text, latin1)
        return msg.value()

    ql = Builder(5, CLIENT, 2)  # MessageClient::qso_logged
    for _name, kind, value in QSO_LOGGED:
        if kind == "qdatetime":
            ql.out << qdatetime(value)
        elif kind == "u64":
            ql.out.writeUInt64(value)
        else:
            ql.out << ba(value)

    return {
        "heartbeat_schema2.bin": heartbeat(CLIENT, VERSION, "", 2),
        "heartbeat_schema3.bin": heartbeat(CLIENT, VERSION, REVISION, 3),
        "heartbeat_jtdx.bin": heartbeat("JTDX", JTDX_VERSION, None, 2),
        "status_ft8.bin": st.value(),
        "status_jtdx.bin": jtdx_status(),
        "close.bin": Builder(6, CLIENT, 2).value(),  # MessageClient::impl::closedown
        "logged_adif.bin": logged_adif(ADIF_TEXT, latin1=False),
        "logged_adif_latin1.bin": logged_adif(LATIN1_ADIF_TEXT, latin1=True),
        "qso_logged.bin": ql.value(),
    }


def verify_qt(fixtures: dict[str, bytes]) -> bool:
    try:
        from qgis.PyQt.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    except ImportError as exc:
        print(f"--verify-qt needs qgis.PyQt on PYTHONPATH: {exc}", file=sys.stderr)
        return False
    print(f"Qt {QT_VERSION_STR}, PyQt {PYQT_VERSION_STR}")
    ok = True
    for name, data in qt_packets().items():
        same = data == fixtures[name]
        ok &= same
        print(f"{'same as Qt' if same else 'DIFFERS from Qt'}  {name} ({len(data)} bytes)")
    for text in TEXT_PROBES:
        latin1 = qt_encode(text, latin1=True)
        same = (
            Writer().utf8(text).getvalue()[4:] == qt_encode(text)
            and Writer().latin1(text).getvalue()[4:] == latin1
            and qstring_size(text) == len(latin1)  # toLatin1() gives a byte per code unit
        )
        ok &= same
        print(f"{'same as Qt' if same else 'DIFFERS from Qt'}  text {text!a}")
    return ok


# --- main -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare only, write nothing")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR, help="fixture directory")
    parser.add_argument("--verify-qt", action="store_true", help="compare with Qt QDataStream")
    args = parser.parse_args(argv)

    fixtures = build_fixtures()
    files = dict(fixtures)
    files["README.md"] = render_readme(fixtures).encode("utf-8")

    qt_ok = verify_qt(fixtures) if args.verify_qt else True
    files_ok = True
    if args.check:
        for name, data in files.items():
            path = args.out / name
            current = path.read_bytes() if path.is_file() else None
            if current != data:
                files_ok = False
                print(f"{'differs' if current is not None else 'missing'}  {path}")
        print("fixtures up to date" if files_ok else "fixtures out of date: run without --check")
    elif not args.verify_qt:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (args.out / name).write_bytes(data)
            print(f"wrote  {args.out / name} ({len(data)} bytes)")
    return 0 if files_ok and qt_ok else 1


if __name__ == "__main__":
    sys.exit(main())
