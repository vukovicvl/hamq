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

The header ends at the first ``<EOH>`` tag; an ``<EOH>`` inside a value of the
declared length is data, in the header as in records (unless it is the only one
and header fields came first: then the length is wrong). QSO fields (``CALL``,
``QSO_DATE``, ``TIME_ON``) before an ``<EOH>`` mean that a record lost its
``<EOR>`` (joined files); they are kept as a record, with a warning.

Field names follow ADIF: besides the usual ``NAME``, user-defined names may hold
spaces and other characters (xlog writes ``<Seq (S):3>001``). Such a tag is read
like any field (name uppercased) when its value fits: it holds no tag and ends
cleanly, as characters or as UTF-8 bytes. Otherwise it is text (``<at 10:15>``),
reported between records as an invalid tag.

Two tolerances for LoTW downloads, which are otherwise flagged on every
record: a ``// comment`` after a value (``<MY_STATE:2>CO // Colorado``) is not
treated as a length problem, and lengthless ``APP_*`` markers such as
``<APP_LoTW_EOF>`` are ignored silently.
"""

from __future__ import annotations

import bisect
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
# Groups: 1 NAME, 2 user-defined name, 3 LENGTH. NAME is letters, digits, '_' and '-'
# (APP_WSJT-X_...). ADIF also allows user-defined field names with other characters, e.g.
# xlog's <Seq (S):3>001: anything but , : < > { }, not beginning or ending with a space
# (control characters are not accepted either). Such a name needs a length, and the parser
# reads it only when the value fits (see _Parser._next_tag): free text like <a href=x> or
# <at 10:15> stays text.
_NAME = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
_USER_CHAR = r"[^,:<>{}\x00-\x1f\x7f-\x9f]"
_USER_EDGE = r"[^\s,:<>{}\x00-\x1f\x7f-\x9f]"  # first and last character: no space
_USER_NAME = _USER_EDGE + r"(?:" + _USER_CHAR + r"*" + _USER_EDGE + r")?"
_TAG = re.compile(
    r"<(?:(" + _NAME + r")|(" + _USER_NAME + r")(?=:[0-9]))(?::([0-9]+)(?::[A-Za-z]*)?)?>"
)
_EOH = re.compile(r"<eoh(?::[0-9]+(?::[a-z]*)?)?>", re.IGNORECASE)
# Tag-like text that is no valid tag and is skipped with a warning: a bad length
# (<NAME:x>, <NAME: 5>) or a bad name (<CALL :5>: a space at the end). Links such as
# <http://...> are not tags.
_BAD_TAG = re.compile(r"<[^<>:,{}\r\n]+:(?!//)[^<>\r\n]{0,20}>")
_FIELD_NAME = re.compile(_NAME + r"|" + _USER_NAME)  # the writer accepts what the parser reads
_NON_SPACE = re.compile(r"\S")
# Fields that only a QSO record has; before an <EOH> they mean a record lost its <EOR>.
_QSO_FIELDS = frozenset({"CALL", "QSO_DATE", "TIME_ON"})
_HEADER_FIELD = re.compile(r"ADIF_VER|CREATED_TIMESTAMP|PROGRAMID|PROGRAMVERSION|USERDEF[0-9]+")

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
    """Decode file content; the flag tells whether the latin-1 fallback was used.

    Accepts ``str``, ``None`` and bytes-like objects (``bytes``, ``bytearray``,
    ``memoryview``, ...); anything else raises ``TypeError`` (a programmer error:
    ``bytes(3)`` would be three NUL characters).
    """
    if data is None:
        return "", False
    if isinstance(data, str):
        return (data[1:] if data.startswith("\ufeff") else data), False
    # bytes as they are; other bytes-like objects copied; TypeError for an int, a list, ...
    raw = data if isinstance(data, bytes) else memoryview(data).tobytes()
    if raw.startswith(_UTF8_BOM):
        bom_length = len(_UTF8_BOM)
        raw = raw[bom_length:]
    elif raw.startswith(_UTF16_BOMS):
        return raw.decode("utf-16", "replace"), False
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("latin-1"), True


def decode_bytes(data: bytes) -> str:
    """Decode ADIF file bytes: UTF-8 (a BOM is removed), else latin-1.

    Never raises for bytes-like input (``bytes``, ``bytearray``, ``memoryview``);
    ``None`` gives ``""`` and ``str`` is returned without a BOM. Other types raise
    ``TypeError``. Line ends are kept as they are, because field lengths count
    them. A file with a UTF-16 byte order mark is decoded as UTF-16.
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
        self._tags: list[int] | None = None  # starts of all tags, made when first needed

    def run(self) -> AdifDocument:
        start = 0
        header_warnings: list[str] = []
        found = self._parse_header(header_warnings)
        if found is not None:
            self.header, start = found
            for message in header_warnings:
                self.warnings.add(message)
        self._parse_records(start)
        return AdifDocument(self.header, self.records, self.warnings.result())

    # -- tags and values

    def _is_tag(self, pos: int, user: bool = True) -> bool:
        """True if a well-formed tag starts at ``pos``: a field (``<NAME:n>``; if ``user``,
        also with a user-defined name such as ``<Seq (S):3>``), ``<EOR>`` or ``<EOH>``."""
        m = _TAG.match(self.text, pos)
        if m is None or (m.group(1) is None and not user):
            return False
        return m.group(3) is not None or m.group(1).upper() in _END_TAGS

    def _next_tag(self, pos: int, end: int) -> re.Match[str] | None:
        """The next tag in ``[pos, end)``. A user-defined name is only a guess (free text
        may look like ``<at 10:15>``), so such a tag counts only when its value fits: it
        holds no tag (:meth:`_is_tag`) and ends cleanly, as characters or as UTF-8 bytes.
        Otherwise it is text. Linear time: a value that holds no tag ends before the next
        tag, so the values checked here do not overlap."""
        text = self.text
        m = _TAG.search(text, pos, end)
        while m is not None and m.group(2) is not None:
            start, length = m.end(), _length(m.group(3))
            stop = start + length
            limit = min(end, self._tag_from(start))
            if stop <= limit and self._ends_cleanly(stop, end):
                break
            if self._byte_reading(start, length, end, limit) is not None:
                break
            m = _TAG.search(text, m.start() + 1, end)
        return m

    def _tag_from(self, pos: int) -> int:
        """Start of the first tag (:meth:`_is_tag`) at or after ``pos``; the text length
        if none."""
        if self._tags is None:
            self._tags = [
                m.start()
                for m in _TAG.finditer(self.text)
                if m.group(3) is not None or m.group(1).upper() in _END_TAGS
            ]
        i = bisect.bisect_left(self._tags, pos)
        return self._tags[i] if i < len(self._tags) else len(self.text)

    def _has_tag(self, start: int, stop: int, user: bool = True) -> bool:
        """True if a well-formed tag starts in ``[start, stop)`` (it may end after ``stop``);
        ``user`` as in :meth:`_is_tag`."""
        text = self.text
        pos = text.find("<", start, stop)
        while pos != -1:
            if self._is_tag(pos, user):
                return True
            pos = text.find("<", pos + 1, stop)
        return False

    def _ends_cleanly(self, pos: int, end: int) -> bool:
        """True if only whitespace separates ``pos`` from the next tag or from ``end``.

        A tag is what the parser reads as one: a valid tag, or tag-like text that is
        reported as an invalid tag. A ``//`` comment after whitespace also counts as a
        clean end: LoTW writes ``<MY_STATE:2>CO // Colorado``. Other text, also a ``<``
        that starts no tag (``tnx 73<3``), would be skipped silently: not a clean end.
        """
        text = self.text
        m = _NON_SPACE.search(text, pos, end)
        if m is None:
            return True
        found = m.start()
        if text[found] == "<":
            return _TAG.match(text, found) is not None or _BAD_TAG.match(text, found) is not None
        return found > pos and text.startswith("//", found)

    def _byte_reading(
        self, start: int, length: int, end: int, limit: int | None = None
    ) -> int | None:
        """Number of characters in ``length`` UTF-8 bytes from ``start``, when that is a
        clean alternative reading: whole characters, no swallowed tag, followed by a tag.
        The reading must end by ``limit`` (default ``end``). Text that only looks like a
        tag with a user-defined name (``<Hvala lepo:3>``) is data inside the reading."""
        chunk_end = min(start + length, end if limit is None else limit)
        chunk = self.text[start:chunk_end]
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
        if self._has_tag(start, stop, user=False) or not self._ends_cleanly(stop, end):
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
            byte_stop = start + count
            return text[start:byte_stop], byte_stop
        if stop > end:
            warn(_msg_past_end(index, name))
            return text[start:end], end
        # swallows a tag and bytes do not help: take LENGTH characters (adif skill)
        if not self._ends_cleanly(stop, end):
            warn(_msg_mismatch(index, name))
        return text[start:stop], stop

    # -- header and records

    def _eoh_in_value(self, start: int, length: int, end: int) -> bool:
        """True if the ``<EOH>`` at ``end`` is data inside a header value of ``length``
        characters from ``start`` (``<PROGRAMVERSION:7>x<EOH>y``): the value ends cleanly
        after it, and it is no byte-counted value that ends before it (the byte rule
        wins, as in records). Otherwise the length is wrong and the ``<EOH>`` is a tag."""
        stop = start + length
        size = len(self.text)
        if stop > size or self._byte_reading(start, length, end) is not None:
            return False
        return self._ends_cleanly(stop, size)

    def _parse_header(self, warnings: list[str]) -> tuple[dict[str, str], int] | None:
        """Header fields and the position after the ``<EOH>`` tag; ``None`` without header.

        The header ends at the first ``<EOH>`` that is a tag, not data inside a value of
        the declared length. Such data needs a later ``<EOH>`` that ends the header, or no
        header field before it (then it is a record value in a file without header); if
        header fields (``ADIF_VER``, ``PROGRAMID``, ...) came first and no other ``<EOH>``
        ends the header, the length is wrong and the first ``<EOH>`` ends the header.
        There is no header if no ``<EOH>`` exists, or if records come first: before it, a
        QSO field (``CALL``, ``QSO_DATE``, ``TIME_ON``), or an ``<EOR>`` after a field that
        is no header field (a file without header, maybe joined with one that has a
        header). An ``<EOR>`` before such a field is text (``Records end with <EOR>``).
        """
        found, skipped = self._read_header(warnings, data_eoh=True)
        if found is None and skipped:  # records follow the <EOH> read as data: it is the end
            del warnings[:]
            found, _ = self._read_header(warnings, data_eoh=False)
        return found

    def _read_header(
        self, warnings: list[str], data_eoh: bool
    ) -> tuple[tuple[dict[str, str], int] | None, bool]:
        """One try of :meth:`_parse_header`. With ``data_eoh`` an ``<EOH>`` inside a value
        of the declared length may be data; the flag returned tells whether that was taken
        for an ``<EOH>`` after a header field."""
        text = self.text
        eoh = _EOH.search(text)
        header: dict[str, str] = {}
        header_field = record_field = False  # read so far: a header field, another field
        skipped = False
        pos = 0
        while eoh is not None:
            end = eoh.start()
            m = self._next_tag(pos, end)
            if m is None:
                return (header, eoh.end()), skipped
            name = (m.group(1) or m.group(2)).upper()
            pos = m.end()
            if name == "EOR":
                if record_field:
                    return None, skipped
                continue  # '<EOR>' mentioned in the free header text
            if m.group(3) is None:
                continue  # <b> and similar in the free header text
            if name in _QSO_FIELDS:
                return None, skipped
            if _HEADER_FIELD.fullmatch(name):
                header_field = True
            else:
                record_field = True
            length = _length(m.group(3))
            if data_eoh and pos + length > end and self._eoh_in_value(pos, length, end):
                later = _EOH.search(text, pos + length)
                if later is not None:
                    eoh, end = later, later.start()
                    skipped = skipped or header_field
                elif not header_field:
                    return None, skipped  # '<EOH>' in a record value of a file without header
            value, pos = self._read_value(name, pos, length, end, None, warnings.append)
            if name in header:
                warnings.append(_msg_duplicate(None, name))
            header[name] = value.strip()
        return None, skipped

    def _header_after_records(self, fields: dict[str, str], index: int) -> None:
        """Fields before an ``<EOH>`` that comes after records (joined files).

        Normally they are the header of the appended file (existing header keys are
        kept). If they hold a QSO field, a record lost its ``<EOR>``: it is kept as a
        record, only the ADIF header fields go to the header.
        """
        if _QSO_FIELDS.isdisjoint(fields):
            for key, value in fields.items():
                self.header.setdefault(key, value)
            self.warnings.add(
                tr(
                    "Record {index}: another <EOH> found (joined files?), the fields before it were treated as header"
                ).format(index=index)
            )
            return
        record: dict[str, str] = {}
        for key, value in fields.items():
            if _HEADER_FIELD.fullmatch(key):
                self.header.setdefault(key, value)
            else:
                record[key] = value
        self.warnings.add(
            tr("Record {index}: missing <EOR> before <EOH> (joined files?), record kept").format(
                index=index
            )
        )
        self.records.append(record)

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
        next_tag = self._next_tag
        current: dict[str, str] = {}
        while True:
            m = next_tag(pos, end)
            if m is None:
                self._check_gap(pos, end, len(records) + 1)
                break
            if m.start() > pos:
                self._check_gap(pos, m.start(), len(records) + 1)
            plain, user, digits = m.groups()
            name = (plain or user).upper()
            pos = m.end()
            if name == "EOR":
                if current:
                    records.append(current)
                    current = {}
                continue
            if name == "EOH":
                if current:
                    self._header_after_records(current, len(records) + 1)
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
    """Parse ADIF text into header fields, records and warnings.

    Never raises on bad content. ``text`` may also be bytes (decoded with
    :func:`decode_bytes`, with a warning when they were read as latin-1, as
    :func:`read_adi` does) or ``None``; other types raise ``TypeError``.

    Tags are case-insensitive and may carry a type indicator (``<FREQ:9:N>``).
    Field names are uppercased; user-defined names with spaces and other
    characters (``<Seq (S):3>001``) are read when their value fits. Text between
    tags is ignored; a warning is added when a value is directly followed by text
    other than whitespace, a tag or a ``//`` comment (a sign of a wrong length),
    and for tag-like text that is no valid tag (``<NAME:x>``, ``<CALL :5>``), which
    is skipped. The header ends at the first ``<EOH>`` tag (an ``<EOH>`` inside a
    value of the declared length is data); without one, or when QSO fields come
    before it, the whole text is records. A last record without ``<EOR>`` is kept
    with a warning. A repeated field in one record keeps the last value with a
    warning.
    """
    return _parse(text)


def _parse(data: object) -> AdifDocument:
    """Decode (text, bytes or ``None``) and parse; the latin-1 fallback is the first warning."""
    text, fallback = _decode(data)
    parser = _Parser(text)
    if fallback:
        parser.warnings.add(tr("File is not valid UTF-8, it was read as Latin-1 (ISO 8859-1)"))
    return parser.run()


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
    return _parse(data)


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
    left out; other non-string values are written with ``str()``. Every name the
    parser reads is accepted, also user-defined names such as ``SEQ (S)``; an
    empty name, ``EOR``, ``EOH``, or one with ``, : < > { }`` or control
    characters raises ``ValueError``.
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
