"""WSJT-X UDP protocol: decode the messages HamQ listens for, encode them for tests and tools.

WSJT-X (and JTDX, MSHV) send UDP datagrams to a logger, by default to 127.0.0.1:2237.
Each datagram is a Qt ``QDataStream`` in big-endian byte order::

    header        quint32 magic 0xADBCCBDA, quint32 schema, quint32 type, utf8 id (client)
    Heartbeat 0   quint32 maximum schema, utf8 version, utf8 revision
    Status 1      quint64 dial frequency (Hz), utf8 mode, utf8 DX call, then the fields
                  listed in _STATUS_OPTIONAL (appended over the years, in that order);
                  JTDX ends with other fields, see below
    QSO Logged 5  structured QSO; HamQ uses Logged ADIF instead, so it decodes as "other"
    Close 6       no payload
    Logged ADIF 12  utf8 ADIF text: a header, one record, <EOR>

``utf8`` is a ``QByteArray``: a quint32 length and that many bytes. Length 0xFFFFFFFF is
a *null* string (``None`` here), length 0 an empty one. ``bool`` and ``quint8`` take one
byte. ``QDateTime`` (only in messages HamQ does not decode) is a qint64 Julian day, a
quint32 count of milliseconds since midnight and a quint8 time spec (1 = UTC), followed
by a qint32 offset only when the spec is 2.

The layout was checked against the WSJT-X sources (``Network/NetworkMessage.hpp``,
``Network/NetworkMessage.cpp``, ``Network/MessageClient.cpp`` and
``UDPExamples/MessageServer.cpp``), against Qt's own ``QDataStream`` and against
datagrams captured from WSJT-X 2.7.0; see ``tests/fixtures/wsjtx/README.md``.

:func:`decode` never raises. It returns ``None`` for:

- a wrong magic number or an incomplete header;
- a schema other than 2 (``QDataStream::Qt_5_2``) or 3 (``Qt_5_4``). Clients send
  schema 2 until a server negotiates 3, and HamQ only listens, so it normally sees 2.
  Schema 1 used the broken ``Qt_5_0`` format and is not sent by current clients;
  WSJT-X's own reader rejects anything above 3;
- a missing mandatory field. A field is missing when it does not fit in the remaining
  bytes, including a string whose declared length is larger than what is left.

Mandatory fields: the header for every type, the ADIF text for Logged ADIF, maximum
schema and version for Heartbeat, and frequency, mode and DX call for Status. The
Heartbeat revision is optional (JTDX does not send it): ``None`` when missing. The
other Status fields are read in wire order until the first one that is missing, and
the result holds the ones read before it; WSJT-X's reference server also accepts such
a short Status. Bytes after the last known field are ignored, because the protocol
only appends fields. Types other than Heartbeat, Status, Close and Logged ADIF give
``{"type": "other", ...}``.

JTDX forked the protocol before WSJT-X 2.0 and kept its own Status: the WSJT-X fields
up to ``fast_mode``, then a ``bool`` ``tx_first`` (Tx in the first, even period) and
nothing after it (``MessageClient::status_update`` in jtdx-project/jtdx). The layout
is chosen by the client id. An id that starts with ``JTDX`` (JTDX sends ``JTDX`` or
``JTDX - <rig name>``) gives ``tx_first`` and never ``special_op_mode`` or the fields
after it; any other id, MSHV included, gives the WSJT-X layout. JTDX writes the two
DFs as ``qint32``; they are audio offsets, never negative, so reading them as
``quint32`` gives the same numbers.

Strings are decoded as UTF-8, and bytes that are not valid UTF-8 as Latin-1. WSJT-X
builds the Logged ADIF record with ``QString::toLatin1()`` (``LogBook::QSOToADIF``), so
``Jürgen`` arrives as Latin-1 and letters outside Latin-1, such as Serbian
``č ć š ž đ``, arrive as ``?`` (seen in a WSJT-X 2.7.0 capture). JTDX sends UTF-8.
Both write each ADIF field length as ``QString::size()``, in UTF-16 code units: one
per character, but two for a character above U+FFFF such as an emoji, which WSJT-X
sends as ``??``. WSJT-X also sends null strings, for example for an empty DX call.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "CLOSE",
    "HEARTBEAT",
    "LOGGED_ADIF",
    "MAGIC",
    "QSO_LOGGED",
    "STATUS",
    "Writer",
    "decode",
    "encode_close",
    "encode_heartbeat",
    "encode_logged_adif",
    "encode_status",
]

MAGIC = 0xADBCCBDA
HEARTBEAT, STATUS, QSO_LOGGED, CLOSE, LOGGED_ADIF = 0, 1, 5, 6, 12

_SCHEMAS = (2, 3)
_NULL_LENGTH = 0xFFFFFFFF  # QByteArray length that marks a null string
_QUINT32_MAX = 0xFFFFFFFF  # "not applicable" / "unknown" in quint32 Status fields
_JULIAN_DAY_AT_ORDINAL_0 = 1721425  # QDate Julian day = date.toordinal() + this
_TIMESPEC_UTC = 1

_U8 = struct.Struct(">B")
_U32 = struct.Struct(">I")
_I32 = struct.Struct(">i")
_U64 = struct.Struct(">Q")
_I64 = struct.Struct(">q")
_DOUBLE = struct.Struct(">d")

# Status fields after frequency, mode and DX call, in wire order (NetworkMessage.hpp,
# MessageClient::status_update). The kind names a method of both _Reader and Writer.
# The default is what encode_status() writes for a field that is not given but comes
# before one that is: an empty string, False, 0 for special_op_mode, and for the
# quint32 fields 0xFFFFFFFF, the value WSJT-X's reference server assumes when missing.
_STATUS_OPTIONAL: tuple[tuple[str, str, object], ...] = (
    ("report", "utf8", ""),
    ("tx_mode", "utf8", ""),
    ("tx_enabled", "boolean", False),
    ("transmitting", "boolean", False),
    ("decoding", "boolean", False),
    ("rx_df", "u32", _QUINT32_MAX),
    ("tx_df", "u32", _QUINT32_MAX),
    ("de_call", "utf8", ""),
    ("de_grid", "utf8", ""),
    ("dx_grid", "utf8", ""),
    ("tx_watchdog", "boolean", False),
    ("sub_mode", "utf8", ""),
    ("fast_mode", "boolean", False),
    ("special_op_mode", "u8", 0),
    ("frequency_tolerance", "u32", _QUINT32_MAX),
    ("tr_period", "u32", _QUINT32_MAX),
    ("configuration_name", "utf8", ""),
    ("tx_message", "utf8", ""),
)
# JTDX's Status (jtdx-project/jtdx MessageClient::status_update): the fields up to
# fast_mode, then a bool tx_first, and nothing else.
_JTDX_STATUS_OPTIONAL: tuple[tuple[str, str, object], ...] = _STATUS_OPTIONAL[:13] + (
    ("tx_first", "boolean", False),
)
_JTDX_ID_PREFIX = "JTDX"  # JTDX's id is QApplication::applicationName(): "JTDX[ - <rig>]"


def _status_fields(client: object) -> tuple[tuple[str, str, object], ...]:
    """The Status fields after the DX call, in wire order, for this client id."""
    if isinstance(client, str) and client.startswith(_JTDX_ID_PREFIX):
        return _JTDX_STATUS_OPTIONAL
    return _STATUS_OPTIONAL


def _text(raw: bytes) -> str:
    """UTF-8, or Latin-1 when the bytes are not valid UTF-8 (never fails)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _datagram_bytes(data: object) -> bytes | None:
    """``data`` as plain ``bytes``, or ``None`` when it is not a bytes-like datagram.

    Only the built-in code of ``bytes``, ``bytearray`` and ``memoryview`` runs. The type
    comes from ``type()``, so an object that only claims to be bytes through
    ``__class__`` is refused. A subclass is copied by its base class, so none of its own
    methods (``__bytes__``, ``__buffer__`` in Python 3.12+, ``__getitem__`` ...) is
    called. Never raises: a released ``memoryview`` gives ``None``.
    """
    kind = type(data)
    if kind is bytes:
        return data  # the usual case, no copy
    try:
        if kind is bytearray:
            return bytes(data)
        if kind is memoryview:  # memoryview cannot be subclassed
            return data.tobytes()
        if issubclass(kind, bytes):
            return bytes.__getitem__(data, slice(None))
        if issubclass(kind, bytearray):
            return bytes(bytearray.__getitem__(data, slice(None)))
    except Exception:  # a released memoryview, or anything else going wrong
        return None
    return None


class _Truncated(Exception):
    """A field does not fit in the bytes left in the datagram."""


class _Reader:
    """Sequential ``QDataStream`` reader that never reads past the end of the data."""

    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def _unpack(self, fmt: struct.Struct) -> Any:
        end = self._pos + fmt.size
        if end > len(self._data):
            raise _Truncated
        value = fmt.unpack_from(self._data, self._pos)[0]
        self._pos = end
        return value

    def u8(self) -> int:
        return self._unpack(_U8)

    def boolean(self) -> bool:
        return self._unpack(_U8) != 0  # QDataStream reads any non-zero byte as true

    def u32(self) -> int:
        return self._unpack(_U32)

    def u64(self) -> int:
        return self._unpack(_U64)

    def utf8(self) -> str | None:
        length = self.u32()
        if length == _NULL_LENGTH:
            return None
        end = self._pos + length
        if end > len(self._data):  # checked before slicing: a huge length allocates nothing
            raise _Truncated
        raw = self._data[self._pos : end]
        self._pos = end
        return _text(raw)


def decode(data: bytes) -> dict[str, Any] | None:
    """Decode one WSJT-X datagram; never raises.

    Returns ``None`` for garbage, a wrong magic number, an unsupported schema or a
    missing mandatory field (see the module docstring), otherwise one of::

        {"type": "heartbeat", "client", "schema", "max_schema", "version", "revision"}
        {"type": "status", "client", "schema", "freq_hz", "mode", "dx_call",
         ...the optional fields present: "report", "tx_mode", "tx_enabled",
         "transmitting", "decoding", "rx_df", "tx_df", "de_call", "de_grid", "dx_grid",
         "tx_watchdog", "sub_mode", "fast_mode", "special_op_mode",
         "frequency_tolerance", "tr_period", "configuration_name", "tx_message"}
        {"type": "close", "client", "schema"}
        {"type": "logged_adif", "client", "schema", "adif"}
        {"type": "other", "code", "client", "schema"}

    A JTDX Status (client id starting with ``JTDX``) has ``"tx_first"`` after
    ``"fast_mode"`` instead of ``"special_op_mode"`` and the keys after it.

    String values are ``str`` or ``None`` (a null string on the wire, or a missing
    Heartbeat revision). ``frequency_tolerance`` and ``tr_period`` are 0xFFFFFFFF when
    they do not apply to the mode. ``data`` may be ``bytes``, ``bytearray`` (or a
    subclass of either; none of its own methods is called) or ``memoryview``; anything
    else, or a released ``memoryview``, gives ``None``.
    """
    raw = _datagram_bytes(data)
    if raw is None:
        return None
    reader = _Reader(raw)
    try:
        if reader.u32() != MAGIC:
            return None
        schema = reader.u32()
        if schema not in _SCHEMAS:
            return None
        code = reader.u32()
        client = reader.utf8()
        if code == HEARTBEAT:
            return _decode_heartbeat(reader, client, schema)
        if code == STATUS:
            return _decode_status(reader, client, schema)
        if code == CLOSE:
            return {"type": "close", "client": client, "schema": schema}
        if code == LOGGED_ADIF:
            adif = reader.utf8()
            return {"type": "logged_adif", "client": client, "schema": schema, "adif": adif}
    except _Truncated:
        return None
    return {"type": "other", "code": code, "client": client, "schema": schema}


def _decode_heartbeat(reader: _Reader, client: str | None, schema: int) -> dict[str, Any]:
    max_schema = reader.u32()
    version = reader.utf8()
    try:
        revision = reader.utf8()
    except _Truncated:  # JTDX sends no revision
        revision = None
    return {
        "type": "heartbeat",
        "client": client,
        "schema": schema,
        "max_schema": max_schema,
        "version": version,
        "revision": revision,
    }


def _decode_status(reader: _Reader, client: str | None, schema: int) -> dict[str, Any]:
    freq_hz = reader.u64()
    mode = reader.utf8()
    dx_call = reader.utf8()
    message: dict[str, Any] = {
        "type": "status",
        "client": client,
        "schema": schema,
        "freq_hz": freq_hz,
        "mode": mode,
        "dx_call": dx_call,
    }
    for key, kind, _default in _status_fields(client):
        try:
            message[key] = getattr(reader, kind)()
        except _Truncated:  # older client or cut datagram: keep what was read
            break
    return message


class Writer:
    """Encoder for WSJT-X messages, byte for byte what Qt's ``QDataStream`` writes.

    Big-endian, as WSJT-X writes schema 2 (``Qt_5_2``) and 3 (``Qt_5_4``); the two do
    not differ for the types written here. Each method appends one value and returns
    the writer, so calls chain::

        packet = Writer().header(HEARTBEAT, "WSJT-X").u32(3).utf8("2.7.0").getvalue()

    Numbers outside the field's range raise ``ValueError``, values of the wrong type
    ``TypeError``. Meant for tests and tools; the plugin itself only decodes.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def getvalue(self) -> bytes:
        """Return the bytes written so far."""
        return bytes(self._buffer)

    def header(self, msg_type: int, client: str | None, schema: int = 2) -> Writer:
        """Append the message header: magic, schema, message type and client id.

        The schema is written as given (no check), so tests can build bad packets.
        """
        return self.u32(MAGIC).u32(schema).u32(msg_type).utf8(client)

    def _pack(self, fmt: struct.Struct, value: Any) -> Writer:
        try:
            self._buffer += fmt.pack(value)
        except struct.error as exc:
            raise ValueError(f"{value!r} does not fit a {fmt.format!r} field") from exc
        return self

    def _integer(self, fmt: struct.Struct, value: int) -> Writer:
        if not isinstance(value, int):
            raise TypeError(f"expected an int, got {type(value).__name__}")
        return self._pack(fmt, value)

    def u8(self, value: int) -> Writer:
        """Append a ``quint8``."""
        return self._integer(_U8, value)

    def boolean(self, value: bool) -> Writer:
        """Append a ``bool``: one byte, 1 for true and 0 for false. Only ``bool`` is taken."""
        if not isinstance(value, bool):
            raise TypeError(f"expected a bool, got {type(value).__name__}")
        return self._pack(_U8, 1 if value else 0)

    def u32(self, value: int) -> Writer:
        """Append a ``quint32``."""
        return self._integer(_U32, value)

    def i32(self, value: int) -> Writer:
        """Append a ``qint32``."""
        return self._integer(_I32, value)

    def u64(self, value: int) -> Writer:
        """Append a ``quint64``."""
        return self._integer(_U64, value)

    def i64(self, value: int) -> Writer:
        """Append a ``qint64``."""
        return self._integer(_I64, value)

    def double(self, value: float) -> Writer:
        """Append a ``double`` (IEEE 754, 8 bytes)."""
        if not isinstance(value, (int, float)):
            raise TypeError(f"expected a float, got {type(value).__name__}")
        return self._pack(_DOUBLE, value)

    def byte_array(self, value: bytes | None) -> Writer:
        """Append a ``QByteArray``: quint32 length and the bytes; ``None`` is a null array."""
        if value is None:
            return self.u32(_NULL_LENGTH)
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TypeError(f"expected bytes, got {type(value).__name__}")
        raw = bytes(value)
        if len(raw) >= _NULL_LENGTH:
            raise ValueError("byte array too long for a QByteArray")
        self.u32(len(raw))
        self._buffer += raw
        return self

    def utf8(self, value: str | None) -> Writer:
        """Append a string as ``QString::toUtf8()`` gives it; ``None`` is a null string.

        A lone surrogate (from ``surrogateescape``, for example) cannot be encoded and
        raises ``UnicodeEncodeError``, a ``ValueError``.
        """
        return self.byte_array(None if value is None else _str(value).encode("utf-8"))

    def latin1(self, value: str | None) -> Writer:
        """Append a string as ``QString::toLatin1()`` gives it; ``None`` is a null string.

        A QString holds UTF-16 and Qt converts it one code unit at a time: a character
        outside Latin-1 becomes ``?``, and one above U+FFFF (two code units, such as an
        emoji) becomes ``??``. WSJT-X sends the Logged ADIF record this way.
        """
        return self.byte_array(None if value is None else _qt_latin1(_str(value)))

    def qdatetime(self, value: datetime) -> Writer:
        """Append a ``QDateTime`` in UTC, as WSJT-X writes ``currentDateTimeUtc()``.

        qint64 Julian day, quint32 milliseconds since midnight, quint8 time spec 1 (UTC).
        ``value`` must be timezone-aware; it is converted to UTC.
        """
        if not isinstance(value, datetime):
            raise TypeError(f"expected a datetime, got {type(value).__name__}")
        if value.utcoffset() is None:
            raise ValueError("qdatetime() needs a timezone-aware datetime")
        utc = value.astimezone(timezone.utc)
        julian_day = utc.toordinal() + _JULIAN_DAY_AT_ORDINAL_0
        seconds = (utc.hour * 60 + utc.minute) * 60 + utc.second
        return self.i64(julian_day).u32(seconds * 1000 + utc.microsecond // 1000).u8(_TIMESPEC_UTC)


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected a str or None, got {type(value).__name__}")
    return value


def _qt_latin1(text: str) -> bytes:
    """``QString::toLatin1()``: one byte per UTF-16 code unit, ``?`` for a unit above 0xFF."""
    units = "".join("??" if ord(char) > 0xFFFF else char for char in text)
    return units.encode("latin-1", "replace")


def _check_schema(schema: int) -> None:
    if schema not in _SCHEMAS:
        raise ValueError(f"schema must be 2 or 3, not {schema!r}")


def encode_heartbeat(
    client: str,
    max_schema: int = 3,
    version: str = "2.7.0",
    revision: str = "",
    schema: int = 2,
) -> bytes:
    """Encode a Heartbeat as ``MessageClient`` sends it (``None`` strings become null)."""
    _check_schema(schema)
    writer = Writer().header(HEARTBEAT, client, schema)
    return writer.u32(max_schema).utf8(version).utf8(revision).getvalue()


def encode_status(
    client: str,
    freq_hz: int,
    mode: str,
    dx_call: str = "",
    *,
    schema: int = 2,
    **optional: Any,
) -> bytes:
    """Encode a Status message.

    ``optional`` takes the fields after the DX call by their :func:`decode` keys
    (``report``, ``tx_mode``, ``tx_enabled`` ... ``tx_message``); an unknown key raises
    ``TypeError``. The layout follows the client id as in :func:`decode`: for an id that
    starts with ``JTDX`` the keys are JTDX's, ``report`` ... ``fast_mode`` and then
    ``tx_first``. Fields are written in wire order up to the last one given, so with no
    optional field the packet ends after the DX call (like an old client) and with
    ``tx_message`` it has every field WSJT-X 2.7 sends. A field that is not given but
    comes before one that is gets its default: an empty string, ``False``, 0 for
    ``special_op_mode`` and 0xFFFFFFFF for the quint32 fields. ``None`` strings become
    null strings.
    """
    fields = _status_fields(client)
    unknown = sorted(set(optional).difference(key for key, _, _ in fields))
    if unknown:
        raise TypeError(
            f"encode_status() got unexpected keyword arguments for client {client!r}: "
            + ", ".join(unknown)
        )
    _check_schema(schema)
    writer = Writer().header(STATUS, client, schema).u64(freq_hz).utf8(mode).utf8(dx_call)
    count = max(
        (index + 1 for index, (key, _, _) in enumerate(fields) if key in optional),
        default=0,
    )
    for key, kind, default in fields[:count]:
        getattr(writer, kind)(optional.get(key, default))
    return writer.getvalue()


def encode_close(client: str, schema: int = 2) -> bytes:
    """Encode a Close message: the header only."""
    _check_schema(schema)
    return Writer().header(CLOSE, client, schema).getvalue()


def encode_logged_adif(client: str, adif: str, schema: int = 2, *, latin1: bool = False) -> bytes:
    """Encode a Logged ADIF message carrying ``adif`` as UTF-8, the way JTDX sends it.

    ``latin1=True`` encodes the text as WSJT-X does (``QString::toLatin1()``, so
    characters outside Latin-1 become ``?`` and characters above U+FFFF ``??``).
    """
    _check_schema(schema)
    writer = Writer().header(LOGGED_ADIF, client, schema)
    return (writer.latin1(adif) if latin1 else writer.utf8(adif)).getvalue()
