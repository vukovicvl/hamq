"""QSO rows: one ADIF record -> one row of the ``qso`` layer.

:func:`record_to_qso` checks and normalizes one record (field names and text values,
as :mod:`hamq.core.adif` returns them), fills the DXCC data and the position from
cty.dat where the log has none, and computes distance and bearing from my station.
:func:`records_to_qsos` converts a whole log. Both return :class:`Qso` objects plus
translated warnings and never raise on bad data. Duplicates are not removed here:
the GeoPackage writer skips rows whose ``dedup_key`` it already has.

Rules (docs/ARCHITECTURE.md, "core/qso.py"):

- ``CALL`` is required (stored uppercase and stripped) and ``QSO_DATE`` + ``TIME_ON``
  must give a valid UTC time; otherwise the record is skipped with a warning.
- ``band``: ``BAND`` normalized (``20M`` -> ``20m``), else the band of ``FREQ``. A
  ``BAND`` that is not an ADIF band takes the band of ``FREQ`` when there is one and is
  kept as logged otherwise, with a warning either way.
- ``mode`` / ``submode`` are uppercased and stored as logged. The dedup key
  ``CALL|YYYYMMDDHHMM|band|mode`` (minute precision) uses :func:`modes.dedup_mode`, so one
  QSO is one key however a logger wrote its mode: FT4 as ``MODE=FT4`` or ``MODE=MFSK
  SUBMODE=FT4`` (``FT4``); SSB as ``MODE=SSB``, ``MODE=SSB SUBMODE=USB`` / ``LSB`` or
  ``MODE=USB`` (``SSB``); PSK31 as ``MODE=PSK31``, ``MODE=PSK SUBMODE=PSK31`` or
  ``MODE=PSK`` (``PSK``); ``JT65B`` as ``JT65``.
- ``gridsquare`` / ``my_gridsquare`` go through :func:`maidenhead.normalize`. A
  10-character locator is cut to 8 with a warning; an invalid one is kept as logged,
  with a warning, and is not used for a position.
- Position of the other station (``loc_source``): ``LAT`` / ``LON`` (``latlon``) >
  centre of ``GRIDSQUARE`` (``grid``) > cty.dat entity (``cty``) > none (no point).
  A 2-character ``GRIDSQUARE`` is a whole 20 x 10 degree field: the cty.dat entity is
  used when its position lies in that field; otherwise the centre of the field, with a
  warning.
- My position, the start of distance, bearing and path: ``MY_LAT`` / ``MY_LON`` >
  centre of ``MY_GRIDSQUARE`` > centre of the station locator > none.
  ``my_gridsquare`` is the record's ``MY_GRIDSQUARE``, else the 6-character locator of
  ``MY_LAT`` / ``MY_LON`` (the cell the path starts in), else the station locator. A
  ``MY_GRIDSQUARE`` that is a coarser version of the station locator (``KN04`` with
  ``KN04ft`` in the settings, as WSJT-X writes its "My Grid") names the same place less
  precisely: without ``MY_LAT`` / ``MY_LON``, the station locator is used for both. A
  ``MY_GRIDSQUARE`` that names another cell (portable operation) is used as logged.
- ``dxcc``, ``country``, ``cont``, ``cq_zone``, ``itu_zone``: the record's ``DXCC``,
  ``COUNTRY``, ``CONT``, ``CQZ`` and ``ITUZ`` win; missing (or invalid) ones come from
  cty.dat. ``country`` from cty.dat is the name of the *DXCC* entity: cty.dat also lists
  entities that count only for the CQ WAE list, and those give the name of the DXCC
  entity they belong to (Sicily -> ``Italy``, European Turkey -> ``Turkey``). DXCC
  statistics count names where the code is missing (no cty.csv), so they see one
  entity, and the name agrees with ``dxcc`` (the parent's code). Position, continent and
  zones stay those of the WAE entity (Sicily's centre). When the record's DXCC code and
  the cty.dat code differ, the record wins and a warning says so.
- ``distance_km`` / ``bearing_deg``: great circle from my position to the other
  station (:mod:`hamq.core.geo`), when both are known.
- ``adif_extra``: a JSON object (sorted keys, non-ASCII text as is) of every record
  field without a column of its own, empty values included (``LAT``, ``LON``,
  ``MY_LAT``, ``MY_LON``, ``NAME``, ``COMMENT``, ``TIME_OFF``, ...), ``"{}"`` when there
  is none. The fields with a column (``CALL``, ``QSO_DATE``, ``TIME_ON``, ``BAND``,
  ``MODE``, ``SUBMODE``, ``FREQ``, ``RST_SENT``, ``RST_RCVD``, ``GRIDSQUARE``,
  ``MY_GRIDSQUARE``, ``DXCC``, ``COUNTRY``, ``CONT``, ``CQZ``, ``ITUZ``) are not repeated.

Extra checks, each with a warning: ``LAT`` must start with ``N``/``S`` and ``LON`` with
``E``/``W``, both must be present, and ``0/0`` (a placeholder some programs write) is
not a position; the same for ``MY_LAT`` / ``MY_LON``. ``DXCC`` must be 0-999 (0 means
"no DXCC entity" in ADIF), ``CQZ`` 1-40, ``ITUZ`` 1-90 and ``CONT`` one of
``EU AS AF NA SA OC AN``. A zero-length field counts as missing.

Warnings name the record: ``Record 3 (YU1XYZ, 20260915 1845): ...`` in
:func:`records_to_qsos` (the record number of the ADIF parser's warnings) and
``QSO YU1XYZ, 20260915 1845: ...`` in :func:`record_to_qso`. A single record also gets
a warning when it has no position or my position is unknown; for a whole log these
are counted in two summary lines instead, and the other warnings are capped at 100.

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from . import geo, maidenhead
from .adif import dedup_key as _adif_dedup_key
from .adif import parse_freq, parse_latlon, parse_qso_datetime
from .bands import BAND_ORDER, band_from_freq, normalize_band
from .i18n import tr
from .modes import dedup_mode, display_mode

if TYPE_CHECKING:
    from .cty import CtyDatabase

__all__ = [
    "PATH_FIELDS",
    "QSO_FIELDS",
    "Qso",
    "Station",
    "display_mode",
    "my_position",
    "record_to_qso",
    "records_to_qsos",
]

#: Columns of the ``qso`` layer as ``(name, kind)`` in PLAN.md order, without ``fid``;
#: kind is ``int``, ``real``, ``text`` or ``datetime`` (see ``qgis_io.fields``).
QSO_FIELDS: tuple[tuple[str, str], ...] = (
    ("call", "text"),
    ("qso_datetime", "datetime"),
    ("band", "text"),
    ("mode", "text"),
    ("submode", "text"),
    ("freq_mhz", "real"),
    ("rst_sent", "text"),
    ("rst_rcvd", "text"),
    ("gridsquare", "text"),
    ("my_gridsquare", "text"),
    ("dxcc", "int"),
    ("country", "text"),
    ("cont", "text"),
    ("cq_zone", "int"),
    ("itu_zone", "int"),
    ("distance_km", "real"),
    ("bearing_deg", "real"),
    ("loc_source", "text"),
    ("source", "text"),
    ("dedup_key", "text"),
    ("adif_extra", "text"),
)

#: Columns of the ``qso_path`` layer as ``(name, kind)``.
PATH_FIELDS: tuple[tuple[str, str], ...] = (
    ("qso_fid", "int"),
    ("distance_km", "real"),
    ("bearing_deg", "real"),
    ("band", "text"),
    ("mode", "text"),
)

_FIELD_NAMES = tuple(name for name, _ in QSO_FIELDS)
_LOC_LATLON, _LOC_GRID, _LOC_CTY = "latlon", "grid", "cty"

# ADIF fields stored in a column of their own; every other field goes to adif_extra.
_MAPPED = frozenset(
    {
        "CALL",
        "QSO_DATE",
        "TIME_ON",
        "BAND",
        "MODE",
        "SUBMODE",
        "FREQ",
        "RST_SENT",
        "RST_RCVD",
        "GRIDSQUARE",
        "MY_GRIDSQUARE",
        "DXCC",
        "COUNTRY",
        "CONT",
        "CQZ",
        "ITUZ",
    }
)
_KNOWN_BANDS = frozenset(BAND_ORDER)
_CONTINENTS = frozenset(("EU", "AS", "AF", "NA", "SA", "OC", "AN"))
_DIGITS = re.compile(r"[0-9]{1,6}")  # ASCII digits only: int() would also take '２' or '2_9'
_MAX_WARNINGS = 100  # per log, like the ADIF parser; summaries come on top
_SHOWN_LENGTH = 40  # longest value quoted in a warning
_MY_LOCATOR_LENGTH = maidenhead.LEVEL_SUBSQUARE  # my_gridsquare made from MY_LAT / MY_LON


@dataclass
class Station:
    """My station: callsign and Maidenhead locator, as set in the HamQ settings."""

    call: str = ""
    grid: str = ""

    def latlon(self) -> tuple[float, float] | None:
        """Centre ``(lat, lon)`` of :attr:`grid`; ``None`` when it is empty or invalid.

        Any letter case and surrounding whitespace are accepted, and a 10-character
        locator counts as its 8-character prefix (:func:`maidenhead.normalize`).
        """
        locator = _valid_locator(self.grid)
        return None if locator is None else maidenhead.to_latlon(locator)


@dataclass
class Qso:
    """One QSO, ready for the ``qso`` layer.

    The first fields are the ``QSO_FIELDS`` columns, in order (see :meth:`attributes`).
    ``lat`` / ``lon`` are the other station's position (``None``: no point on the map)
    and ``my_lat`` / ``my_lon`` the start of the path (``None``: no distance, bearing or
    path). ``qso_datetime`` is timezone-aware UTC.
    """

    call: str
    qso_datetime: datetime
    band: str | None
    mode: str | None
    submode: str | None
    freq_mhz: float | None
    rst_sent: str | None
    rst_rcvd: str | None
    gridsquare: str | None
    my_gridsquare: str | None
    dxcc: int | None
    country: str | None
    cont: str | None
    cq_zone: int | None
    itu_zone: int | None
    distance_km: float | None
    bearing_deg: float | None
    loc_source: str | None  # 'latlon' | 'grid' | 'cty' | None
    source: str  # 'adif:<file name>' | 'wsjtx' | ...
    dedup_key: str
    adif_extra: str  # JSON object text, '{}' when nothing extra
    lat: float | None
    lon: float | None
    my_lat: float | None
    my_lon: float | None

    def attributes(self) -> dict[str, object]:
        """Column values keyed by the ``QSO_FIELDS`` names, in that order (a new dict)."""
        return {name: getattr(self, name) for name in _FIELD_NAMES}

    @property
    def display_mode(self) -> str:
        """SUBMODE if present, else MODE (``FT4`` for MFSK / FT4); ``""`` if neither."""
        return display_mode(self.mode, self.submode)


def record_to_qso(
    record: Mapping[str, str],
    *,
    station: Station | None = None,
    cty: CtyDatabase | None = None,
    source: str = "",
) -> tuple[Qso | None, list[str]]:
    """Convert one ADIF record into a :class:`Qso`; returns ``(qso, warnings)``.

    ``qso`` is ``None`` when the record has no ``CALL`` or no valid ``QSO_DATE`` /
    ``TIME_ON``. ``station`` gives my locator (used when the record has no
    ``MY_LAT`` / ``MY_LON`` and no ``MY_GRIDSQUARE`` or only a coarser version of the
    station locator, such as ``KN04`` for ``KN04ft``), ``cty`` the DXCC data and the
    fallback position, ``source`` the value of the ``source`` column (``"wsjtx"``,
    ``"adif:<file name>"``). Field names may be in any case; values are stripped and
    empty ones count as missing. Warnings are translated, never raised; besides data
    problems they also say when the QSO has no position or my position is unknown.
    See the module documentation for the rules.
    """
    context = _Context(station, cty, source)
    qso, warnings = _convert(record, context, None)
    return qso, context.warnings + warnings


def my_position(record: Mapping[str, str]) -> tuple[float, float] | None:
    """``(lat, lon)`` of the record's ``MY_LAT`` / ``MY_LON`` when :func:`record_to_qso`
    starts the path there: both present and valid, not ``0/0``. ``None`` otherwise, also
    for anything that is not a mapping. Field names may be in any case; no warnings.
    """
    fields = _fields(record)
    return _position(fields.get("MY_LAT"), fields.get("MY_LON"), "MY_LAT", "MY_LON", _quiet)


def records_to_qsos(
    records: Iterable[Mapping[str, str]],
    *,
    station: Station | None = None,
    cty: CtyDatabase | None = None,
    source: str = "",
) -> tuple[list[Qso], list[str]]:
    """Convert the records of a log; returns ``(qsos, warnings)``.

    Like :func:`record_to_qso` for every record, in order; skipped records leave no
    ``Qso``. Warnings name the record by its number (1-based, as in the ADIF parser's
    warnings). At most 100 of them are listed, then a count of the rest; at the end
    come the number of QSOs without a position and of QSOs without my position (no
    distance and bearing), when there are any. Items that are not mappings count as
    records without ``CALL``; ``records=None`` gives no QSOs.
    """
    context = _Context(station, cty, source)
    qsos: list[Qso] = []
    details = _Warnings()
    without_position = without_origin = 0
    for index, record in enumerate(() if records is None else records, 1):
        qso, warnings = _convert(record, context, index)
        for message in warnings:
            details.add(message)
        if qso is None:
            continue
        qsos.append(qso)
        if qso.lat is None:
            without_position += 1
        if qso.my_lat is None:
            without_origin += 1
    result = context.warnings + details.result()
    if without_position:
        result.append(
            tr("QSOs without a position, not shown on the map: {count}").format(
                count=without_position
            )
        )
    if without_origin:
        result.append(
            tr(
                "QSOs without distance and bearing, my QTH unknown (set your locator in the HamQ settings): {count}"
            ).format(count=without_origin)
        )
    return qsos, result


# --- conversion -------------------------------------------------------------------------------


class _Context:
    """What all records of one call share: the source, my station's locator and cty.dat."""

    __slots__ = ("cty", "source", "station_grid", "station_latlon", "warnings")

    def __init__(self, station: object, cty: object, source: object) -> None:
        self.cty = cty
        self.source = source if isinstance(source, str) else "" if source is None else str(source)
        self.warnings: list[str] = []
        grid = getattr(station, "grid", "")
        self.station_grid = _valid_locator(grid)
        self.station_latlon = (
            None if self.station_grid is None else maidenhead.to_latlon(self.station_grid)
        )
        if self.station_grid is None and grid is not None and str(grid).strip():
            self.warnings.append(
                tr("My locator {locator} is invalid, ignored").format(locator=_shown(grid))
            )


def _convert(record: object, context: _Context, index: int | None) -> tuple[Qso | None, list[str]]:
    """Convert one record. ``index`` is its 1-based number in a log, ``None`` for a single
    record (which then also gets the "no position" / "my QTH unknown" warnings)."""
    fields = _fields(record)
    get = fields.get
    call = get("CALL", "").upper()
    date_text = get("QSO_DATE", "")
    time_text = get("TIME_ON", "")
    warnings: list[str] = []
    label: list[str] = []  # made on the first warning only

    def warn(template: str, **values: object) -> None:
        if not label:
            label.append(_record_label(index, call, date_text, time_text))
        warnings.append(template.format(record=label[0], **values))

    if not call:
        warn(tr("{record}: missing CALL, skipped"))
        return None, warnings
    when = parse_qso_datetime(date_text, time_text)
    if when is None:
        warn(tr("{record}: missing or invalid QSO_DATE or TIME_ON, skipped"))
        return None, warnings

    freq_text = get("FREQ")
    freq_mhz = None
    if freq_text:
        freq_mhz = parse_freq(freq_text)
        if freq_mhz is None:
            _warn_value(warn, "FREQ", freq_text)
    band = _band(get("BAND"), freq_mhz, freq_text, warn)
    mode = get("MODE")
    mode = mode.upper() if mode else None
    submode = get("SUBMODE")
    submode = submode.upper() if submode else None

    gridsquare, grid = _locator(get("GRIDSQUARE"), "GRIDSQUARE", warn)
    my_gridsquare, my_grid = _locator(get("MY_GRIDSQUARE"), "MY_GRIDSQUARE", warn)
    position = _position(get("LAT"), get("LON"), "LAT", "LON", warn)
    my_position = _position(get("MY_LAT"), get("MY_LON"), "MY_LAT", "MY_LON", warn)
    station_grid = context.station_grid
    if (
        my_position is None
        and my_grid is not None
        and station_grid is not None
        and len(my_grid) < len(station_grid)
        and station_grid.startswith(my_grid)
    ):
        # A coarser version of my own locator (WSJT-X "My Grid" KN04, settings KN04ft):
        # the same place, known more precisely from the settings.
        my_gridsquare = my_grid = station_grid
    if my_gridsquare is None and my_position is not None:
        # the cell the path starts in, so the column never names another QTH than the origin
        my_gridsquare = maidenhead.to_locator(my_position[0], my_position[1], _MY_LOCATOR_LENGTH)
    elif my_gridsquare is None:
        my_gridsquare = station_grid

    dxcc = _number(get("DXCC"), "DXCC", 0, 999, warn)
    country = get("COUNTRY") or None
    cont = _continent(get("CONT"), warn)
    cq_zone = _number(get("CQZ"), "CQZ", 1, 40, warn)
    itu_zone = _number(get("ITUZ"), "ITUZ", 1, 90, warn)

    lat = lon = loc_source = None
    field = None  # a 2-character GRIDSQUARE: used only when cty.dat has nothing better
    if position is not None:
        (lat, lon), loc_source = position, _LOC_LATLON
    elif grid is not None and len(grid) == maidenhead.LEVEL_FIELD:
        field = grid
    elif grid is not None:
        (lat, lon), loc_source = maidenhead.to_latlon(grid), _LOC_GRID
    cty = context.cty
    if cty is not None and None in (lat, dxcc, country, cont, cq_zone, itu_zone):
        match = cty.lookup(call)
        if match is not None:
            if dxcc is not None and match.dxcc is not None and match.dxcc != dxcc:
                warn(
                    tr(
                        "{record}: DXCC {dxcc} in the log does not match cty.dat ({country}, DXCC {cty_dxcc})"
                    ),
                    dxcc=dxcc,
                    country=match.dxcc_name,
                    cty_dxcc=match.dxcc,
                )
            if dxcc is None:
                dxcc = match.dxcc
            if country is None:
                country = match.dxcc_name  # the DXCC entity, also for WAE-only entities
            if cont is None:
                cont = match.continent
            if cq_zone is None:
                cq_zone = match.cq_zone
            if itu_zone is None:
                itu_zone = match.itu_zone
            if lat is None and (field is None or _inside(field, match.lat, match.lon)):
                lat, lon, loc_source = match.lat, match.lon, _LOC_CTY
    if lat is None and field is not None:
        (lat, lon), loc_source = maidenhead.to_latlon(field), _LOC_GRID
        warn(
            tr(
                "{record}: locator {value} in GRIDSQUARE is only a Maidenhead field "
                "(20° x 10°), the QSO is placed at its center"
            ),
            value=field,
        )

    if my_position is not None:
        my_lat, my_lon = my_position
    elif my_grid is not None:
        my_lat, my_lon = maidenhead.to_latlon(my_grid)
    elif context.station_latlon is not None:
        my_lat, my_lon = context.station_latlon
    else:
        my_lat = my_lon = None

    distance = bearing = None
    if lat is not None and my_lat is not None:
        distance = geo.distance_km(my_lat, my_lon, lat, lon)
        bearing = geo.bearing_deg(my_lat, my_lon, lat, lon)
    if index is None:  # a single record: say why it has no point or no path
        if lat is None:
            warn(
                tr(
                    "{record}: no position (no LAT/LON, valid locator or cty.dat entity), "
                    "the QSO is not shown on the map"
                )
            )
        if my_lat is None:
            warn(
                tr(
                    "{record}: my QTH unknown (no MY_LAT/MY_LON, MY_GRIDSQUARE or locator in "
                    "the HamQ settings), no distance and bearing"
                )
            )

    extra = {key: value for key, value in fields.items() if key not in _MAPPED}
    key = _adif_dedup_key(
        call,
        f"{when.year:04d}{when.month:02d}{when.day:02d}",
        f"{when.hour:02d}{when.minute:02d}",
        band or "",
        dedup_mode(mode, submode),
    )
    qso = Qso(
        call=call,
        qso_datetime=when,
        band=band,
        mode=mode,
        submode=submode,
        freq_mhz=freq_mhz,
        rst_sent=get("RST_SENT") or None,
        rst_rcvd=get("RST_RCVD") or None,
        gridsquare=gridsquare,
        my_gridsquare=my_gridsquare,
        dxcc=dxcc,
        country=country,
        cont=cont,
        cq_zone=cq_zone,
        itu_zone=itu_zone,
        distance_km=distance,
        bearing_deg=bearing,
        loc_source=loc_source,
        source=context.source,
        dedup_key=key,
        adif_extra=json.dumps(extra, ensure_ascii=False, sort_keys=True),
        lat=lat,
        lon=lon,
        my_lat=my_lat,
        my_lon=my_lon,
    )
    return qso, warnings


def _fields(record: object) -> dict[str, str]:
    """Fields by uppercase name with stripped text values; ``None`` values are left out.

    Anything that is not a mapping has no fields (it is then a record without CALL).
    """
    if not isinstance(record, Mapping):
        return {}
    fields: dict[str, str] = {}
    for name, value in record.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        fields[str(name).strip().upper()] = text.strip()
    return fields


_Warn = Callable[..., None]


def _quiet(template: str, **values: object) -> None:
    """A ``warn`` that drops the warning (:func:`my_position`)."""


def _band(
    text: str | None, freq_mhz: float | None, freq_text: str | None, warn: _Warn
) -> str | None:
    """``BAND`` normalized, else the band of ``FREQ``; unknown names: see the module doc."""
    freq_band = band_from_freq(freq_mhz)
    if not text:
        if freq_band is None and freq_mhz is not None:
            warn(
                tr("{record}: frequency {freq} MHz is outside the amateur bands, band unknown"),
                freq=_shown(freq_text),
            )
        return freq_band
    band = normalize_band(text)
    if band in _KNOWN_BANDS:
        return band
    if freq_band is not None:
        warn(
            tr("{record}: unknown band {value} in BAND, band {band} taken from FREQ"),
            value=_shown(text),
            band=freq_band,
        )
        return freq_band
    warn(tr("{record}: unknown band {value} in BAND, kept as logged"), value=_shown(text))
    return band


def _locator(text: str | None, field: str, warn: _Warn) -> tuple[str | None, str | None]:
    """``(column value, locator usable for a position)`` of GRIDSQUARE / MY_GRIDSQUARE."""
    if not text:
        return None, None
    locator = _valid_locator(text)
    if locator is None:
        warn(
            tr("{record}: invalid locator {value} in {field}, not used for the position"),
            value=_shown(text),
            field=field,
        )
        return text, None  # kept as logged
    if len(locator) < len(text):  # a 10-character locator, cut to 8
        warn(
            tr("{record}: locator {value} in {field} cut to {locator}"),
            value=_shown(text),
            field=field,
            locator=locator,
        )
    return locator, locator


def _position(
    lat_text: str | None, lon_text: str | None, lat_field: str, lon_field: str, warn: _Warn
) -> tuple[float, float] | None:
    """``(lat, lon)`` from an ADIF ``LAT`` / ``LON`` pair, ``None`` if not usable."""
    if not lat_text and not lon_text:
        return None
    lat = _coordinate(lat_text, "NS")
    lon = _coordinate(lon_text, "EW")
    if lat_text and lat is None:
        _warn_value(warn, lat_field, lat_text)
    if lon_text and lon is None:
        _warn_value(warn, lon_field, lon_text)
    if lat is not None and lon is not None:
        if lat == 0.0 and lon == 0.0:
            warn(
                tr("{record}: {fields} = 0/0 is not a real position, ignored"),
                fields=f"{lat_field}/{lon_field}",
            )
            return None
        return lat, lon
    if lat is not None and not lon_text:
        warn(tr("{record}: {field} without {other}, ignored"), field=lat_field, other=lon_field)
    elif lon is not None and not lat_text:
        warn(tr("{record}: {field} without {other}, ignored"), field=lon_field, other=lat_field)
    return None


def _coordinate(text: str | None, hemispheres: str) -> float | None:
    """ADIF ``XDDD MM.MMM`` in degrees when its letter is one of ``hemispheres``."""
    if not text or text[0].upper() not in hemispheres:
        return None
    return parse_latlon(text)


def _number(text: str | None, field: str, low: int, high: int, warn: _Warn) -> int | None:
    """Integer field (``DXCC``, ``CQZ``, ``ITUZ``) within ``[low, high]``; ``05`` is 5."""
    if not text:
        return None
    if _DIGITS.fullmatch(text):
        value = int(text)
        if low <= value <= high:
            return value
    _warn_value(warn, field, text)
    return None


def _continent(text: str | None, warn: _Warn) -> str | None:
    """``CONT`` uppercased if it is one of the seven continents."""
    if not text:
        return None
    value = text.upper()
    if value in _CONTINENTS:
        return value
    _warn_value(warn, "CONT", text)
    return None


def _valid_locator(value: object) -> str | None:
    """:func:`maidenhead.normalize` of ``value``; ``None`` when it is no valid locator."""
    if not isinstance(value, str):
        return None
    try:
        return maidenhead.normalize(value)
    except ValueError:
        return None


def _inside(locator: str, lat: float, lon: float) -> bool:
    """Whether ``(lat, lon)`` lies in the cell of ``locator`` (edges included)."""
    lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


# --- warnings ---------------------------------------------------------------------------------


def _warn_value(warn: _Warn, field: str, text: str) -> None:
    warn(tr("{record}: invalid value {value} in {field}, ignored"), value=_shown(text), field=field)


def _shown(value: object) -> str:
    """A value as quoted in a warning: on one line, at most ``_SHOWN_LENGTH`` characters."""
    text = " ".join(str(value).split())
    if len(text) > _SHOWN_LENGTH:
        text = text[: _SHOWN_LENGTH - 1] + "…"
    return text


def _record_label(index: int | None, call: str, date_text: str, time_text: str) -> str:
    """How a warning names its record: number (in a log), call, date and time as logged."""
    when = " ".join(_shown(part) for part in (date_text, time_text) if part)
    details = ", ".join(part for part in (_shown(call) if call else "", when) if part)
    if index is None:
        return tr("QSO {details}").format(details=details) if details else tr("QSO")
    if details:
        return tr("Record {index} ({details})").format(index=index, details=details)
    return tr("Record {index}").format(index=index)


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
