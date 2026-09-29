"""ADIF (.adi) reading and writing.

A tolerant parser for logs written by real loggers (WSJT-X, N1MM Logger+,
Log4OM, QRZ.com, ...) and a small writer. Pure Python, no QGIS imports.

Parsing never raises on bad content: problems come back as translated
warnings and the parser carries on with the next tag. Record-level checks
(missing CALL, bad date, invalid locator) belong to ``core/qso.py``.

Field lengths are counted in characters of the decoded text. The only
exception is the narrow byte-count rule of the contract (see
``docs/ARCHITECTURE.md``): when LENGTH characters would swallow a following
well-formed tag and LENGTH UTF-8 bytes would not, the byte reading is used
and a warning is added. Some loggers count bytes, and Serbian names such as
``Đorđe`` expose it.

Two tolerances for LoTW downloads, which are otherwise flagged on every
record: a ``// comment`` after a value (``<MY_STATE:2>CO // Colorado``) is not
treated as a length problem, and lengthless ``APP_*`` markers such as
``<APP_LoTW_EOF>`` are ignored silently.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .bands import normalize_band
from .i18n import tr

__all__ = [
    "AdifDocument",
    "decode_bytes",
    "dedup_key",
    "format_document",
    "format_record",
    "parse_adi",
    "parse_document",
    "parse_freq",
    "parse_latlon",
    "parse_qso_datetime",
    "read_adi",
]

_ADIF_VERSION = "3.1.4"
_PROGRAM_ID = "HamQ"
_HEADER_TEXT = "HamQ ADIF export"
_MAX_WARNINGS = 100
_MAX_LENGTH_DIGITS = 15  # longer lengths are "past the end" anyway; avoids huge int parsing
_END_TAGS = ("EOR", "EOH")
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

# <NAME>, <NAME:LENGTH> or <NAME:LENGTH:TYPE>; the type indicator (letters) is ignored.
_NAME = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
_TAG = re.compile(r"<(" + _NAME + r")(?::([0-9]+)(?::[A-Za-z]*)?)?>")
_EOH = re.compile(r"<eoh(?::[0-9]+(?::[a-z]*)?)?>", re.IGNORECASE)
_BAD_TAG = re.compile(r"<" + _NAME + r":[^<>\r\n]{0,20}>")
_FIELD_NAME = re.compile(_NAME)
_NON_SPACE = re.compile(r"\S")

_LATLON = re.compile(r"\s*([NSEWnsew])\s*([0-9]{1,3})\s+([0-9]{1,2}(?:[.,][0-9]*)?)\s*")
_FREQ = re.compile(r"\s*\+?([0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)\s*")
_DATE = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")
_TIME = re.compile(r"([0-9]{2})([0-9]{2})([0-9]{2})?")


@dataclass
class AdifDocument:
    """A parsed ADIF document.

    ``header`` holds the header fields (``ADIF_VER``, ``PROGRAMID``, ...) and may
    be empty. ``records`` are dicts with uppercase field names and stripped
    values; a zero-length field is kept as ``""``. ``warnings`` are translated,
    user-visible messages.
    """

    header: dict[str, str] = field(default_factory=dict)
    records: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- decoding -------------------------------------------------------------------------------


def _decode(data: object) -> tuple[str, bool]:
    """Decode file content; the flag tells whether the latin-1 fallback was used."""
    if data is None:
        return "", False
    if isinstance(data, str):
        return (data[1:] if data.startswith("﻿") else data), False
    raw = bytes(data)  # bytes, bytearray, memoryview
    if raw.startswith(_UTF8_BOM):
        raw = raw[len(_UTF8_BOM) :]
    elif raw.startswith(_UTF16_BOMS):
        return raw.decode("utf-16", "replace"), False
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("latin-1"), True


def decode_bytes(data: bytes) -> str:
    """Decode ADIF file bytes: UTF-8 (a BOM is removed), else latin-1. Never raises.

    Line ends are kept as they are, because field lengths count them. A file
    with a UTF-16 byte order mark is decoded as UTF-16.
    """
    return _decode(data)[0]


# --- warnings -------------------------------------------------------------------------------


def _msg_bytes(index: int | None, name: str) -> str:
    if index is None:
        return tr(
            "Header: length of field {field} is given in bytes instead of characters, value corrected"
        ).format(field=name)
    return tr(
        "Record {index}: length of field {field} is given in bytes instead of characters, value corrected"
    ).format(index=index, field=name)


def _msg_mismatch(index: int | None, name: str) -> str:
    if index is None:
        return tr(
            "Header: length of field {field} does not match its data, the value may be wrong"
        ).format(field=name)
    return tr(
        "Record {index}: length of field {field} does not match its data, this or the next field may be wrong"
    ).format(index=index, field=name)


def _msg_past_end(index: int | None, name: str) -> str:
    if index is None:
        return tr(
            "Header: field {field} runs past the end of the header, value may be incomplete"
        ).format(field=name)
    return tr(
        "Record {index}: field {field} runs past the end of the file, value may be incomplete"
    ).format(index=index, field=name)


def _msg_duplicate(index: int | None, name: str) -> str:
    if index is None:
        return tr("Header: duplicate field {field}, the last value is used").format(field=name)
    return tr("Record {index}: duplicate field {field}, the last value is used").format(
        index=index, field=name
    )


class _Warnings:
    """Collects warnings, keeping the first ``_MAX_WARNINGS`` and counting the rest."""

    __slots__ = ("items", "dropped")

    def __init__(self) -> None:
        self.items: list[str] = []
        self.dropped = 0

    def add(self, message: str) -> None:
        if len(self.items) < _MAX_WARNINGS:
            self.items.append(message)
        else:
            self.dropped += 1

    def result(self) -> list[str]:
        out = list(self.items)
        if self.dropped:
            out.append(tr("Further warnings not listed: {count}").format(count=self.dropped))
        return out


# --- parser ---------------------------------------------------------------------------------


def _length(digits: str) -> int:
    """Declared field length (leading zeros allowed on import); absurdly long numbers
    become 'longer than any file' without parsing thousands of digits."""
    significant = digits.lstrip("0")
    if len(significant) > _MAX_LENGTH_DIGITS:
        return 10**_MAX_LENGTH_DIGITS
    return int(significant or "0")


class _Parser:
    """One pass over the text: optional header up to the first ``<EOH>``, then records."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.header: dict[str, str] = {}
        self.records: list[dict[str, str]] = []
        self.warnings = _Warnings()

    def run(self) -> AdifDocument:
        start = 0
        eoh = _EOH.search(self.text)
        if eoh is not None:
            header_warnings: list[str] = []
            header = self._parse_header(eoh.start(), header_warnings)
            if header is not None:
                self.header = header
                for message in header_warnings:
                    self.warnings.add(message)
                start = eoh.end()
        self._parse_records(start)
        return AdifDocument(self.header, self.records, self.warnings.result())

    # -- tags and values

    def _is_tag(self, pos: int) -> bool:
        """True if a well-formed tag (``<NAME:n>``, ``<EOR>``, ``<EOH>``) starts at ``pos``."""
        m = _TAG.match(self.text, pos)
        return m is not None and (m.group(2) is not None or m.group(1).upper() in _END_TAGS)

    def _has_tag(self, start: int, stop: int) -> bool:
        """True if a well-formed tag starts in ``[start, stop)`` (it may end after ``stop``)."""
        text = self.text
        pos = text.find("<", start, stop)
        while pos != -1:
            if self._is_tag(pos):
                return True
            pos = text.find("<", pos + 1, stop)
        return False

    def _ends_cleanly(self, pos: int, end: int) -> bool:
        """True if only whitespace separates ``pos`` from the next ``<`` or from ``end``.

        A ``//`` comment after whitespace also counts as a clean end: LoTW writes
        ``<MY_STATE:2>CO // Colorado``.
        """
        m = _NON_SPACE.search(self.text, pos, end)
        if m is None:
            return True
        found = m.start()
        return self.text[found] == "<" or (found > pos and self.text.startswith("//", found))

    def _byte_reading(self, start: int, length: int, end: int) -> int | None:
        """Number of characters in ``length`` UTF-8 bytes from ``start``, when that is a
        clean alternative reading: whole characters, no swallowed tag, followed by a tag."""
        chunk = self.text[start : min(start + length, end)]
        data = chunk.encode("utf-8", "surrogatepass")
        if len(data) < length:
            return None
        try:
            count = len(data[:length].decode("utf-8", "surrogatepass"))
        except UnicodeDecodeError:
            return None  # the byte count ends inside a character
        if count == length:
            return None  # only ASCII: same as the character reading
        stop = start + count
        if self._has_tag(start, stop) or not self._ends_cleanly(stop, end):
            return None
        return count

    def _read_value(
        self,
        name: str,
        start: int,
        length: int,
        end: int,
        index: int | None,
        warn: Callable[[str], None],
    ) -> tuple[str, int]:
        """Read one field value; returns ``(raw value, position after it)``."""
        text = self.text
        stop = start + length
        if stop <= end and not self._has_tag(start, stop):
            if not self._ends_cleanly(stop, end):
                warn(_msg_mismatch(index, name))
            return text[start:stop], stop
        count = self._byte_reading(start, length, end)
        if count is not None:
            warn(_msg_bytes(index, name))
            return text[start : start + count], start + count
        if stop > end:
            warn(_msg_past_end(index, name))
            return text[start:end], end
        # swallows a tag and bytes do not help: take LENGTH characters (adif skill)
        if not self._ends_cleanly(stop, end):
            warn(_msg_mismatch(index, name))
        return text[start:stop], stop

    # -- header and records

    def _parse_header(self, end: int, warnings: list[str]) -> dict[str, str] | None:
        """Header fields before the first ``<EOH>``; ``None`` if records come first."""
        text = self.text
        header: dict[str, str] = {}
        pos = 0
        while True:
            m = _TAG.search(text, pos, end)
            if m is None:
                return header
            name = m.group(1).upper()
            pos = m.end()
            if name == "EOR":
                return None  # records before the first <EOH>: no header (joined files)
            if m.group(2) is None:
                continue  # <b> and similar in the free header text
            value, pos = self._read_value(
                name, pos, _length(m.group(2)), end, None, warnings.append
            )
            if name in header:
                warnings.append(_msg_duplicate(None, name))
            header[name] = value.strip()

    def _check_gap(self, start: int, stop: int, index: int) -> None:
        """Report tag-like text between tags that is not a valid tag (e.g. ``<CALL:x>``)."""
        if self.text.find("<", start, stop) == -1:
            return
        for m in _BAD_TAG.finditer(self.text, start, stop):
            self.warnings.add(
                tr("Record {index}: invalid tag {tag} skipped").format(index=index, tag=m.group())
            )

    def _parse_records(self, pos: int) -> None:
        text = self.text
        end = len(text)
        records = self.records
        add = self.warnings.add
        search = _TAG.search
        current: dict[str, str] = {}
        while True:
            m = search(text, pos)
            if m is None:
                self._check_gap(pos, end, len(records) + 1)
                break
            if m.start() > pos:
                self._check_gap(pos, m.start(), len(records) + 1)
            name = m.group(1).upper()
            digits = m.group(2)
            pos = m.end()
            if name == "EOR":
                if current:
                    records.append(current)
                    current = {}
                continue
            if name == "EOH":
                if current:  # header of a second document appended to the first
                    for key, value in current.items():
                        self.header.setdefault(key, value)
                    add(
                        tr(
                            "Record {index}: another <EOH> found (joined files?), the fields before it were treated as header"
                        ).format(index=len(records) + 1)
                    )
                    current = {}
                continue
            if digits is None:
                if not name.startswith("APP_"):  # e.g. LoTW ends files with <APP_LoTW_EOF>
                    add(
                        tr("Record {index}: field {field} has no length, skipped").format(
                            index=len(records) + 1, field=name
                        )
                    )
                continue
            value, pos = self._read_value(name, pos, _length(digits), end, len(records) + 1, add)
            if name in current:
                add(_msg_duplicate(len(records) + 1, name))
            current[name] = value.strip()
        if current:
            add(
                tr("Record {index}: missing <EOR> at the end of the file, record kept").format(
                    index=len(records) + 1
                )
            )
            records.append(current)


def parse_document(text: str) -> AdifDocument:
    """Parse ADIF text into header fields, records and warnings. Never raises.

    Tags are case-insensitive and may carry a type indicator (``<FREQ:9:N>``).
    Text between tags is ignored; a warning is added when a value is directly
    followed by text other than whitespace, a tag or a ``//`` comment (a sign of a
    wrong length). Without ``<EOH>`` the whole text is records.
    A last record without ``<EOR>`` is kept with a warning. A repeated field in
    one record keeps the last value with a warning. Bytes are decoded with
    :func:`decode_bytes` first.
    """
    if not isinstance(text, str):
        text = decode_bytes(text)
    return _Parser(text).run()


def parse_adi(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Parse ADIF text; returns ``(records, warnings)``. See :func:`parse_document`."""
    doc = parse_document(text)
    return doc.records, doc.warnings


def read_adi(path: str | os.PathLike) -> AdifDocument:
    """Read and parse an .adi file (bytes -> :func:`decode_bytes` -> :func:`parse_document`).

    Content problems never raise; they are returned as warnings, including a
    note when the file was not valid UTF-8 and was read as latin-1. A file
    that cannot be opened raises ``OSError``.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    text, fallback = _decode(data)
    doc = parse_document(text)
    if fallback:
        doc.warnings.insert(0, tr("File is not valid UTF-8, it was read as Latin-1 (ISO 8859-1)"))
    return doc


# --- field values ---------------------------------------------------------------------------


def parse_latlon(value: str | None) -> float | None:
    """ADIF location ``XDDD MM.MMM`` to signed decimal degrees, ``None`` if invalid.

    ``'N044 48.750'`` -> 44.8125, ``'S033 52.128'`` -> -33.8688. Accepts 1 to 3
    degree digits, a lowercase hemisphere and a decimal comma. Minutes of 60 or
    more, latitude above 90 and longitude above 180 give ``None``.
    """
    if not isinstance(value, str):
        return None
    m = _LATLON.fullmatch(value)
    if m is None:
        return None
    hemisphere = m.group(1).upper()
    minutes = float(m.group(3).replace(",", "."))
    if minutes >= 60.0:
        return None
    degrees = int(m.group(2)) + minutes / 60.0
    if degrees > (90.0 if hemisphere in "NS" else 180.0):
        return None
    if degrees == 0.0:
        return 0.0
    return -degrees if hemisphere in "SW" else degrees


def parse_freq(value: str | None) -> float | None:
    """ADIF frequency in MHz: ``'14.074'``, ``' 14.07400 '``, ``'14,074'`` -> 14.074.

    Empty, non-numeric, zero or negative values give ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        m = _FREQ.fullmatch(value)
        if m is None:
            return None
        number = float(m.group(1).replace(",", "."))
    else:
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def parse_qso_datetime(qso_date: str | None, time_on: str | None) -> datetime | None:
    """``QSO_DATE`` (YYYYMMDD) and ``TIME_ON`` (HHMM or HHMMSS) to an aware UTC datetime.

    Invalid dates or times (``20260230``, ``2460``, letters, empty) and dates
    before 1930 (the ADIF minimum) give ``None``.
    """
    if not isinstance(qso_date, str) or not isinstance(time_on, str):
        return None
    d = _DATE.fullmatch(qso_date.strip())
    t = _TIME.fullmatch(time_on.strip())
    if d is None or t is None:
        return None
    year, month, day = int(d.group(1)), int(d.group(2)), int(d.group(3))
    if year < 1930:
        return None
    try:
        return datetime(
            year,
            month,
            day,
            int(t.group(1)),
            int(t.group(2)),
            int(t.group(3) or 0),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def dedup_key(call: str, qso_date: str, time_on: str, band: str, mode: str) -> str:
    """Duplicate key ``CALL|YYYYMMDDHHMM|band|MODE`` (minute precision).

    The call and mode are uppercased, the band lowercased, seconds dropped
    (some loggers round them). Missing parts become empty strings.
    """
    call_text = (call or "").strip().upper()
    date_text = (qso_date or "").strip()
    time_text = (time_on or "").strip()[:4]
    band_text = normalize_band(band) or ""
    mode_text = (mode or "").strip().upper()
    return f"{call_text}|{date_text}{time_text}|{band_text}|{mode_text}"


# --- writing --------------------------------------------------------------------------------


def _field_name(name: object) -> str:
    text = str(name).strip().upper()
    if not _FIELD_NAME.fullmatch(text) or text in _END_TAGS:
        raise ValueError(f"invalid ADIF field name: {name!r}")
    return text


def _field(name: object, value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return f"<{_field_name(name)}:{len(text)}>{text}"


def format_record(fields: Mapping[str, str]) -> str:
    """One ADIF record on one line: ``'<CALL:5>YU1AB <BAND:3>20m <EOR>\\n'``.

    Field names are uppercased; lengths count characters. ``None`` values are
    left out; other non-string values are written with ``str()``. An invalid
    field name raises ``ValueError``.
    """
    parts = [_field(name, value) for name, value in fields.items() if value is not None]
    parts.append("<EOR>")
    return " ".join(parts) + "\n"


def format_document(
    records: Iterable[Mapping[str, str]], header: Mapping[str, str] | None = None
) -> str:
    """A complete .adi document: header (``ADIF_VER`` 3.1.4, ``PROGRAMID`` HamQ, then
    ``header`` fields, which may override them), ``<EOH>`` and one record per line."""
    fields = {"ADIF_VER": _ADIF_VERSION, "PROGRAMID": _PROGRAM_ID}
    for name, value in (header or {}).items():
        if value is not None:
            fields[_field_name(name)] = value if isinstance(value, str) else str(value)
    lines = [_HEADER_TEXT]
    lines.extend(_field(name, value) for name, value in fields.items())
    lines.append("<EOH>")
    return "\n".join(lines) + "\n" + "".join(format_record(record) for record in records)
