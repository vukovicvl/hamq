"""QSO log statistics for the HamQ statistics panel.

:func:`compute_stats` reads QSO rows (attribute mappings keyed by the ``QSO_FIELDS``
names of ``core/qso.py``, as ``qgis_io.gpkg.read_qso_rows`` returns them) in one pass
and returns the panel's numbers: the QSO count, DXCC entities, unique calls, grid
squares, QSOs per continent, band and mode, the longest QSO and the first and last
QSO times.

Rows are untrusted data. Missing keys, ``None``, QGIS ``NULL``, blank text and values
of the wrong type count as missing and never raise. ``"?"`` is the key for a missing
continent, band or mode. The dock translates the labels, so this module has no
user-visible text.

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .bands import band_from_freq, band_sort_key, normalize_band
from .modes import display_mode

__all__ = ["CONTINENTS", "LongestQso", "QsoStats", "compute_stats"]

CONTINENTS = ("EU", "AS", "AF", "NA", "SA", "OC", "AN")

_UNKNOWN = "?"  # key for a missing continent, band or mode
_UTC = timezone.utc
_INF = math.inf
_fromisoformat = datetime.fromisoformat
_QT_LOCAL_TIME, _QT_UTC = 0, 1  # Qt.TimeSpec values, the same in Qt5 and Qt6
_CONTINENT_RANK = {name: rank for rank, name in enumerate(CONTINENTS)}
# The 4-character square at the start of a locator: field A-R, square 0-9, any case.
# Explicit ASCII classes without IGNORECASE: look-alikes such as KELVIN SIGN never match.
_SQUARE_RE = re.compile(r"[A-Ra-r]{2}[0-9]{2}")
# ISO 8601 date with an optional time and UTC offset, in the extended
# (2026-09-15T18:45:00Z) or the basic format (20260915T184500Z). The date and the time
# each keep one format throughout, but the date, the time and the offset may use
# different ones (2026-09-15T1845). A time needs hours and minutes; "T", "t" or a space
# separates it from the date; seconds may have a fraction.
# Groups: year, date separator, month, day, hour, time separator, minute, second,
# fraction, offset sign, offset hours, offset minutes.
_ISO_RE = re.compile(
    r"([0-9]{4})(-?)([0-9]{2})\2([0-9]{2})"
    r"(?:[Tt ]([0-9]{2})(:?)([0-9]{2})(?:\6([0-9]{2})(?:[.,]([0-9]+))?)?"
    r"(?:[Zz]|([+-])([0-9]{2})(?::?([0-9]{2}))?)?)?"
)
# The common layouts (GeoPackage, QGIS and Python output) that every supported Python
# version's datetime.fromisoformat() reads the same way once a "Z" is cut off: extended
# format, "T" or space, hours 00-23, minutes and seconds 00-59, a 3 or 6 digit fraction,
# offset +HH:MM. Anything else goes through _ISO_RE, which defines the accepted syntax.
_FAST_ISO_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ](?:[01][0-9]|2[0-3]):[0-5][0-9]"
    r"(?::[0-5][0-9](?:\.[0-9]{3}(?:[0-9]{3})?)?)?"
    r"(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?"
)


@dataclass
class LongestQso:
    """The QSO with the largest distance, see :func:`compute_stats`."""

    call: str  # uppercase and stripped; "" when the row has no call
    distance_km: float
    country: str | None  # as logged, stripped
    band: str | None  # normalized band, or the band of freq_mhz when the band is missing
    mode: str | None  # display mode: SUBMODE if present, else MODE, uppercase
    qso_datetime: datetime | None  # UTC aware


@dataclass
class QsoStats:
    """Statistics of a QSO log, see :func:`compute_stats`."""

    total: int
    dxcc_count: int  # distinct codes > 0, plus names of code-less rows never seen with a code
    unique_calls: int
    grid_count: int  # distinct 4-char squares of the other station
    by_continent: dict[str, int]  # CONTINENTS order, then others / "?" last; zeros omitted
    by_band: dict[str, int]  # band_sort_key order, "?" last; band from freq_mhz when missing
    by_mode: dict[str, int]  # display_mode, descending count then name
    longest: LongestQso | None
    first_qso: datetime | None
    last_qso: datetime | None


def compute_stats(rows: Iterable[Mapping[str, object]]) -> QsoStats:
    """Compute the statistics of a QSO log in one pass over ``rows``.

    Each row is a mapping keyed by ``QSO_FIELDS`` names: a dict, another ``Mapping``,
    or an object with ``keys()`` such as ``sqlite3.Row``. Other items are skipped, and
    ``rows=None`` means no rows. A value that is missing, ``None``, QGIS ``NULL``,
    blank or of the wrong type counts as missing. Numbers may also be numeric strings
    (``"296"``, ``"1234.5"``). Bad values never raise.

    - ``total``: the number of rows.
    - ``dxcc_count``: distinct ADIF DXCC codes (``dxcc`` as an int, an integral float
      or numeric text). A row without a usable code counts by its ``country`` name
      instead. Names are compared case-insensitively (casefold), with whitespace
      stripped and collapsed. Code 0 means "not in any DXCC entity" in ADIF (``/MM``,
      ``/AM``), so such rows do not count. A name that also appears on a row with a
      code does not count on its own: that entity is already counted under its code,
      or, next to code 0, it is not an entity. Rows with neither a code nor a name do
      not count.

      Caveat for logs that mix sources: without an entity table, a name that never
      appears next to a code cannot be matched to that code. The same entity can then
      count twice: code 230 from one logger, plus the cty.dat name "Germany" (ADIF
      says "Fed. Rep. of Germany") on WSJT-X QSOs logged before cty.csv was
      available. Spelling variants of one name count separately for the same reason.
    - ``unique_calls``: distinct calls, case-insensitive and stripped, compared as
      logged (``YU1AB`` and ``YU1AB/P`` are two calls).
    - ``grid_count``: distinct 4-character squares (``KN04``) of ``gridsquare``. Its
      first four characters must be two letters A-R and two digits, in any case.
      ``my_gridsquare`` and positions from LAT/LON or cty.dat do not count.
    - ``by_continent``: ``cont`` uppercased. Keys follow ``CONTINENTS`` order, then
      other values alphabetically, then ``"?"`` for a missing continent. Only
      continents with QSOs are present.
    - ``by_band``: ``band`` normalized (``" 20M"`` -> ``"20m"``), or the band of
      ``freq_mhz`` when ``band`` is missing. Keys follow ``band_sort_key`` order
      (unknown bands after the known ones, alphabetically), then ``"?"``.
    - ``by_mode``: ``display_mode(mode, submode)`` (``MFSK`` + ``FT4`` -> ``"FT4"``),
      ``"?"`` when both are missing. Sorted by count descending, then by name.
    - ``longest``: the row with the largest ``distance_km`` (a finite number >= 0;
      anything else is ignored). On a tie the earliest QSO wins; a row with a time
      beats one without, and otherwise the first row wins. ``None`` when no row has
      a distance.
    - ``first_qso``, ``last_qso``: the earliest and latest ``qso_datetime`` (UTC aware),
      ``None`` when no row has a readable time.

    ``qso_datetime`` accepts:

    - a ``datetime``: naive means UTC, aware ones are converted to UTC;
    - a ``date``: midnight UTC;
    - a ``QDateTime`` (duck-typed; QGIS returns these for GeoPackage datetime
      fields): converted to UTC when it has an offset or a time zone. A LocalTime
      value, which is how QGIS represents a datetime stored without a zone, is read
      as UTC like a naive ``datetime``;
    - ISO 8601 text, parsed the same way on every Python version (Python 3.9's
      ``datetime.fromisoformat`` rejects ``Z``). Examples: ``2026-09-15T18:45:00Z``,
      offsets ``+02:00``, ``+0200`` or ``+02``, no seconds, a fraction of any length
      (cut to microseconds), ``t`` or a space instead of ``T``, the basic format
      ``20260915T184500Z``, or a date alone (midnight UTC). Text without an offset
      is UTC.

    Anything else, including impossible dates and times, is ignored.
    """
    # The loop only collects raw values; text is normalized once per distinct value
    # afterwards, because a log repeats the same calls, bands, modes and countries.
    # All keys below are str, None, int or tuples of those, so they are hashable.
    total = 0
    call_texts: set[str] = set()
    grid_texts: set[str] = set()
    entities: set[tuple[int | None, str | None]] = set()  # (DXCC code, raw country)
    cont_counts: dict[str | None, int] = {}  # raw continent
    band_counts: dict[str | None, int] = {}  # raw band text, else the band of freq_mhz
    mode_counts: dict[tuple[str | None, str | None], int] = {}  # raw (mode, submode)
    first: datetime | None = None
    last: datetime | None = None
    # (distance, time, call, country, band, (mode, submode)) of the longest QSO so far
    best: tuple | None = None

    for item in rows if rows is not None else ():
        if type(item) is dict:
            get = item.get
        else:
            get = _getter(item)
            if get is None:
                continue
        total += 1

        call = get("call")
        if isinstance(call, str):
            call_texts.add(call)
        else:
            call = None

        grid = get("gridsquare")
        if isinstance(grid, str):
            grid_texts.add(grid)

        code = get("dxcc")
        if type(code) is int:
            if code < 0:
                code = None
        elif code is not None:
            code = _dxcc_code(code)
        country = get("country")
        if not isinstance(country, str):
            country = None
        entities.add((code, country))

        cont = get("cont")
        if not isinstance(cont, str):
            cont = None
        cont_counts[cont] = cont_counts.get(cont, 0) + 1

        band = get("band")
        if not isinstance(band, str) or not band.strip():
            band = _freq_band(get("freq_mhz"))
        band_counts[band] = band_counts.get(band, 0) + 1

        mode = get("mode")
        submode = get("submode")
        mode_key = (
            mode if isinstance(mode, str) else None,
            submode if isinstance(submode, str) else None,
        )
        mode_counts[mode_key] = mode_counts.get(mode_key, 0) + 1

        when = get("qso_datetime")
        if type(when) is not datetime or when.tzinfo is not _UTC:
            when = _to_utc(when)
        if when is not None:
            if first is None or when < first:
                first = when
            if last is None or when > last:
                last = when

        distance = get("distance_km")
        if type(distance) is not float:
            distance = _number(distance)
        # NaN fails both comparisons; on a tie the earlier QSO wins
        if distance is not None and 0.0 <= distance < _INF:
            if (
                best is None
                or distance > best[0]
                or (distance == best[0] and _earlier(when, best[1]))
            ):
                # + 0.0 turns -0.0 into 0.0
                best = (distance + 0.0, when, call, country, band, mode_key)

    return QsoStats(
        total=total,
        dxcc_count=_count_entities(entities),
        unique_calls=len({text.strip().upper() for text in call_texts} - {""}),
        grid_count=len(_squares(grid_texts)),
        by_continent=_continent_counts(cont_counts),
        by_band=_band_counts(band_counts),
        by_mode=_mode_counts(mode_counts),
        longest=_longest(best),
        first_qso=first,
        last_qso=last,
    )


# --- row values -------------------------------------------------------------------------------


def _getter(item: object) -> Callable[[str], object] | None:
    """The ``get`` of a row, or None when the item is not a row."""
    if isinstance(item, Mapping):
        return item.get
    if callable(getattr(item, "keys", None)):  # sqlite3.Row and other keyed records
        try:
            return dict(item).get  # type: ignore[call-overload]
        except (TypeError, ValueError, KeyError, IndexError):
            return None
    return None


def _number(value: object) -> float | None:
    """A finite float from a number or numeric text, else None (bool and bytes too)."""
    if value is None or isinstance(value, (bool, bytes, bytearray, memoryview)):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _dxcc_code(value: object) -> int | None:
    """ADIF DXCC code (>= 0) from an int, integral float or numeric text; None if unusable."""
    if value is None or isinstance(value, bool):
        return None
    code: int | None = None
    if isinstance(value, int):
        code = value
    else:
        if isinstance(value, str):
            try:
                code = int(value)
            except ValueError:
                pass
        if code is None:
            number = _number(value)
            if number is None or not number.is_integer():
                return None
            code = int(number)
    return code if code >= 0 else None


def _freq_band(value: object) -> str | None:
    """The band of a ``freq_mhz`` value, or None."""
    freq = _number(value)
    return None if freq is None else band_from_freq(freq)


def _band_name(text: str | None) -> str | None:
    """A normalized band (``" 20M"`` -> ``"20m"``), None when missing or blank."""
    return None if text is None else normalize_band(text)


def _country_key(text: str | None) -> str:
    """A country name for comparison: whitespace collapsed, casefolded; "" when blank."""
    return " ".join(text.split()).casefold() if text else ""


# --- totals --------------------------------------------------------------------------------------


def _count_entities(entities: set[tuple[int | None, str | None]]) -> int:
    """Distinct codes > 0, plus names never seen next to a code (see compute_stats)."""
    codes: set[int] = set()
    coded_names: set[str] = set()
    uncoded_names: set[str] = set()
    for code, country in entities:
        name = _country_key(country)
        if code is None:
            if name:
                uncoded_names.add(name)
            continue
        if code > 0:  # 0: not a DXCC entity
            codes.add(code)
        if name:  # counted under its code, or (code 0) not an entity
            coded_names.add(name)
    return len(codes) + len(uncoded_names - coded_names)


def _squares(grid_texts: set[str]) -> set[str]:
    """Distinct uppercase 4-character squares of the valid-looking locators."""
    squares = set()
    for text in grid_texts:
        text = text.strip()
        if _SQUARE_RE.match(text):
            squares.add(text[:4].upper())
    return squares


def _continent_counts(raw: dict[str | None, int]) -> dict[str, int]:
    """CONTINENTS first in their order, then other values alphabetically, "?" last."""
    counts: dict[str, int] = {}
    for text, count in raw.items():
        key = (text.strip().upper() if text else "") or _UNKNOWN
        counts[key] = counts.get(key, 0) + count
    return {key: counts[key] for key in sorted(counts, key=_continent_order)}


def _continent_order(name: str) -> tuple[int, int, str]:
    if name == _UNKNOWN:
        return (2, 0, "")
    rank = _CONTINENT_RANK.get(name)
    return (1, 0, name) if rank is None else (0, rank, "")


def _band_counts(raw: dict[str | None, int]) -> dict[str, int]:
    """``band_sort_key`` order with "?" last (as text it would sort before letters)."""
    counts: dict[str, int] = {}
    for text, count in raw.items():
        key = _band_name(text) or _UNKNOWN
        counts[key] = counts.get(key, 0) + count
    return {key: counts[key] for key in sorted(counts, key=_band_order)}


def _band_order(band: str) -> tuple[bool, tuple[int, str]]:
    return (band == _UNKNOWN, band_sort_key(band))


def _mode_counts(raw: dict[tuple[str | None, str | None], int]) -> dict[str, int]:
    """Display modes by count descending, then name."""
    counts: dict[str, int] = {}
    for (mode, submode), count in raw.items():
        key = display_mode(mode, submode) or _UNKNOWN
        counts[key] = counts.get(key, 0) + count
    return dict(sorted(counts.items(), key=_mode_order))


def _mode_order(item: tuple[str, int]) -> tuple[int, str]:
    return (-item[1], item[0])


def _earlier(when: datetime | None, other: datetime | None) -> bool:
    """True if ``when`` is a time before ``other``; any time is before no time."""
    return when is not None and (other is None or when < other)


def _longest(best: tuple | None) -> LongestQso | None:
    """The LongestQso of the raw values kept by compute_stats."""
    if best is None:
        return None
    distance, when, call, country, band, (mode, submode) = best
    return LongestQso(
        call=call.strip().upper() if call else "",
        distance_km=distance,
        country=(country.strip() or None) if country else None,
        band=_band_name(band),
        mode=display_mode(mode, submode) or None,
        qso_datetime=when,
    )


# --- times -------------------------------------------------------------------------------------


def _to_utc(value: object) -> datetime | None:
    """A UTC-aware datetime from a ``qso_datetime`` value, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return _parse_iso(value)
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=_UTC)
    return _from_qdatetime(value)


def _as_utc(value: datetime) -> datetime | None:
    """``value`` in UTC; naive (no UTC offset) means UTC. None when out of range."""
    if value.tzinfo is _UTC:
        return value
    try:
        if value.utcoffset() is None:
            return value.replace(tzinfo=_UTC)
        return value.astimezone(_UTC)
    except (OverflowError, TypeError, ValueError):  # TypeError: a broken tzinfo
        return None


def _from_qdatetime(value: object) -> datetime | None:
    """A duck-typed ``QDateTime``: QGIS returns one for datetime fields.

    ``toPyDateTime()`` gives the naive wall-clock fields in the value's own time spec
    (PyQt5 and PyQt6). A value with a UTC offset or a time zone is converted with
    ``toUTC()`` first. QGIS gives a LocalTime value for a datetime stored without a
    zone, and PyQt makes one from a Python datetime, so its fields are read as UTC,
    like a naive ``datetime``: ``toUTC()`` would shift them by the computer's time
    zone. Invalid values are ignored.
    """
    if not hasattr(value, "toPyDateTime"):
        return None
    try:
        is_valid = getattr(value, "isValid", None)
        if is_valid is not None and not is_valid():
            return None
        # a UTC value needs no conversion, and LocalTime fields are read as UTC
        if _time_spec(value) not in (_QT_LOCAL_TIME, _QT_UTC) and hasattr(value, "toUTC"):
            value = value.toUTC()  # type: ignore[attr-defined]
        result = value.toPyDateTime()  # type: ignore[attr-defined]
    except (AttributeError, OverflowError, RuntimeError, TypeError, ValueError):
        return None
    return _as_utc(result) if isinstance(result, datetime) else None


def _time_spec(value: object) -> int | None:
    """``Qt.TimeSpec`` of a QDateTime as an int, None when unknown.

    PyQt6 returns an enum member with ``.value``; PyQt5 returns an int subclass.
    """
    time_spec = getattr(value, "timeSpec", None)
    if time_spec is None:
        return None
    spec = time_spec()
    spec = getattr(spec, "value", spec)
    return spec if isinstance(spec, int) else None


def _parse_iso(text: str) -> datetime | None:
    """ISO 8601 text (see :func:`compute_stats`) as a UTC-aware datetime, or None.

    Common layouts take the fast ``datetime.fromisoformat`` path; the result is the
    same as :func:`_parse_iso_general` gives (a test checks this).
    """
    match = _FAST_ISO_RE.fullmatch(text)
    if match is None:
        return _parse_iso_general(text)
    zone = match.group(1)
    try:
        # A zero offset gives the timezone.utc singleton, and appending it is much
        # faster than .replace(tzinfo=...) on Python 3.9.
        if zone is None:
            return _fromisoformat(text + "+00:00")
        if zone == "Z":
            return _fromisoformat(text[:-1] + "+00:00")
        return _fromisoformat(text).astimezone(_UTC)
    except (OverflowError, ValueError):
        return None


def _parse_iso_general(text: str) -> datetime | None:
    """ISO 8601 text as a UTC-aware datetime, or None: the full syntax of ``_ISO_RE``."""
    match = _ISO_RE.fullmatch(text.strip())
    if match is None:
        return None
    year, _, month, day, hour, _, minute, second, fraction, sign, off_h, off_m = match.groups()
    try:
        result = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            int(fraction[:6].ljust(6, "0")) if fraction else 0,
            tzinfo=_UTC,
        )
        if sign:
            hours, minutes = int(off_h), int(off_m or 0)
            if hours > 23 or minutes > 59:
                return None
            offset = timedelta(hours=hours, minutes=minutes)
            result = result - offset if sign == "+" else result + offset
    except (OverflowError, ValueError):
        return None
    return result
