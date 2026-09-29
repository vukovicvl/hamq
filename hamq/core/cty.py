"""DXCC entity, continent and zones from a callsign, using the AD1C country files.

The country files are maintained by Jim Reisert AD1C at https://www.country-files.com. HamQ
downloads them at runtime (``DEFAULT_CTY_URL``, ``DEFAULT_CTY_CSV_URL``) and never bundles them.

cty.dat, as found in the real file (Big CTY of 15 September 2026, 346 entities):

* An entity starts with a header line of 8 colon-terminated fields::

      Serbia:                   15:  28:  EU:   44.00:   -21.00:    -1.0:  YU:

  name, CQ zone, ITU zone, continent, latitude (+ north), longitude (+ WEST; flipped to
  east-positive here), UTC offset (local time = UTC - offset; kept as in the file) and the
  primary prefix. A leading ``*`` on the primary prefix marks an entity that counts only for
  the CQ WAE list (``*IT9`` Sicily, ``*TA1`` European Turkey, ...), not for DXCC.
* Indented lines follow with comma separated entries; ``;`` ends the entity. ``=CALL`` is an
  exact callsign, anything else a prefix. An entry may carry overrides in any order:
  ``(cq)``, ``[itu]``, ``<lat/lon>``, ``{continent}``, ``~utc offset~``.
* When a callsign is listed twice, the first occurrence wins (AD1C: read top to bottom).

cty.csv has one row per entity: primary prefix (with ``*``), name, ADIF DXCC code, continent,
CQ, ITU, latitude, longitude, UTC offset and the space separated entries. Only the code is
used; for a WAE-only row it is the code of the DXCC entity the row belongs to.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass

from .i18n import tr

DEFAULT_CTY_URL = "https://www.country-files.com/bigcty/cty.dat"
DEFAULT_CTY_CSV_URL = "https://www.country-files.com/bigcty/cty.csv"

_CONTINENTS = frozenset(("EU", "AS", "AF", "NA", "SA", "OC", "AN"))
_MAX_WARNINGS = 50

# WAE-only entities of cty.dat (primary prefix with '*') and the primary prefix of the DXCC
# entity they belong to. Verified against cty.dat / cty.csv of 15 September 2026, where the
# csv gives these rows the parent's ADIF code: 4U1V Vienna Intl Ctr -> OE Austria (206),
# GM/s Shetland Islands -> GM Scotland (279), IG9 African Italy -> I Italy (248), IT9 Sicily
# -> I Italy (248), JW/b Bear Island -> JW Svalbard (259), TA1 European Turkey -> TA (390).
# Used when cty.csv is not loaded or has no code for the entity.
_WAE_PARENTS = {
    "4U1V": "OE",
    "GM/s": "GM",
    "IG9": "I",
    "IT9": "I",
    "JW/b": "JW",
    "TA1": "TA",
}

# DXCC names that differ from the cty.dat name because cty.dat splits the entity for the WAE
# list: DXCC entity 390 is "Turkey"; cty.dat names its Asian part "Asiatic Turkey".
_DXCC_NAMES = {"TA": "Turkey"}

# Suffixes meaning "no DXCC entity": maritime mobile, aeronautical mobile.
_NO_ENTITY_SUFFIXES = frozenset(("MM", "AM"))
# Two-letter activity suffixes that look like a prefix but are not locations: lighthouse (LH,
# Norway), light tower and lightship (LT, LS, Argentina), WWFF flora and fauna (FF, France),
# field day (FD, France), YL operator (YL, Latvia). WSJT-X ignores LH, LT, FF and FD the same
# way. How common they are: the Big CTY of 15 September 2026 has to list CALL/LH 773 times,
# CALL/YL 65, CALL/FF 62, CALL/LS 23, CALL/LT 16 and CALL/FD 8 times as exceptions under the
# home entity, because a location rule gets them wrong.
_MODIFIER_SUFFIXES = frozenset(("FD", "FF", "LH", "LS", "LT", "YL"))

# Longer text is not a callsign (the longest call in the Big CTY has 13 characters). The limit
# keeps lookup() cheap for untrusted text: ADIF files and WSJT-X UDP do not limit the length.
_MAX_CALL_LENGTH = 64

# cty.dat maps the whole KG4 prefix to Guantanamo Bay, but only KG4 plus two letters (KG4AB)
# is Guantanamo Bay; KG4 plus one or three letters (KG4A, KG4ABC) is a US 4th call area call.
# cty.dat cannot express this, so lookup() applies it, like WSJT-X (logbook/AD1CCty.cpp).
_KG4_PREFIX = "KG4"
_KG4_US_CALL_RE = re.compile(r"KG4(?:[A-Z]|[A-Z]{3})")
_US_PRIMARY_PREFIX = "K"

_CALL_RE = re.compile(r"[A-Z0-9/]+")
_ENTRY_RE = re.compile(
    r"(=?)([A-Z0-9/]+)((?:\(\d+\)|\[\d+\]|<[^<>]*>|\{[A-Z]*\}|~[^~]*~)*)", re.ASCII
)
_OVERRIDE_RE = re.compile(r"\((\d+)\)|\[(\d+)\]|<([^<>]*)>|\{([A-Z]*)\}|~([^~]*)~", re.ASCII)


@dataclass(frozen=True)
class Entity:
    """One cty.dat entity: a DXCC entity, or a WAE-only one such as Sicily.

    ``lon`` is east-positive (cty.dat stores west-positive). ``utc_offset`` is kept as in
    cty.dat: local time = UTC - utc_offset (Serbia -1.0). ``dxcc`` is the ADIF DXCC code from
    cty.csv (for a WAE-only entity the code of its DXCC entity), ``None`` without cty.csv.
    ``dxcc_name`` is the name of the DXCC entity this entity counts for (Sicily -> "Italy");
    ``None`` means the same as ``name``.
    """

    name: str
    cq_zone: int
    itu_zone: int
    continent: str
    lat: float
    lon: float
    utc_offset: float
    primary_prefix: str
    wae_only: bool
    dxcc: int | None = None
    dxcc_name: str | None = None


@dataclass(frozen=True)
class CtyMatch:
    """Result of :meth:`CtyDatabase.lookup` with the per-entry overrides applied."""

    entity: Entity
    cq_zone: int
    itu_zone: int
    continent: str
    lat: float
    lon: float
    utc_offset: float
    matched: str
    exact: bool

    @property
    def dxcc(self) -> int | None:
        """ADIF DXCC code (the parent's code for a WAE-only entity), ``None`` without cty.csv."""
        return self.entity.dxcc

    @property
    def dxcc_name(self) -> str:
        """Name of the DXCC entity; the parent's name for a WAE-only entity."""
        return self.entity.dxcc_name or self.entity.name


def clean_call(call: str) -> str:
    """Uppercase ``call`` and remove all whitespace: ``' yu1ab / p '`` -> ``'YU1AB/P'``."""
    if not isinstance(call, str):
        return ""
    return "".join(call.split()).upper()


class CtyDatabase:
    """Parsed cty.dat (plus DXCC codes from cty.csv) with callsign lookup.

    Build it with :meth:`from_text` or :meth:`from_files`. Parsing never raises on bad
    content: broken entities or entries are skipped and described in ``warnings``
    (translated). The object is read-only after construction and safe to share between
    threads.
    """

    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.warnings: list[str] = []
        self._exact: dict[str, tuple] = {}
        self._prefix: dict[str, tuple] = {}
        self._max_prefix_len = 0
        self._us_default: tuple | None = None  # United States with its defaults (KG4 rule)

    @classmethod
    def from_text(cls, dat_text: str, csv_text: str | None = None) -> CtyDatabase:
        """Parse cty.dat text and, optionally, cty.csv text (for ADIF DXCC codes)."""
        warnings = _Warnings()
        raws = _parse_dat(dat_text if isinstance(dat_text, str) else "", warnings.add)
        codes = _parse_csv(csv_text, warnings.add) if isinstance(csv_text, str) else {}
        entities = _build_entities(raws, codes)
        exact: dict[str, tuple] = {}
        prefix: dict[str, tuple] = {}
        us_default = None
        for raw, ent in zip(raws, entities):
            default = (ent, raw.cq, raw.itu, raw.cont, raw.lat, raw.lon, raw.tz)
            if us_default is None and not raw.wae and raw.prefix == _US_PRIMARY_PREFIX:
                us_default = default
            for is_exact, key, values in raw.entries:
                target = exact if is_exact else prefix
                if key not in target:  # first occurrence wins
                    target[key] = default if values is None else (ent, *values)
        db = cls()
        db.entities = entities
        db.warnings = warnings.result(no_entities=not entities)
        db._exact = exact
        db._prefix = prefix
        db._max_prefix_len = max(map(len, prefix), default=0)
        db._us_default = us_default
        return db

    @classmethod
    def from_files(
        cls, dat_path: str | os.PathLike, csv_path: str | os.PathLike | None = None
    ) -> CtyDatabase:
        """Read and parse cty.dat and optionally cty.csv.

        An unreadable cty.dat raises ``OSError``; an unreadable cty.csv only adds a warning
        (the database then has no DXCC codes).
        """
        dat_text = _read_text(dat_path)
        csv_text = None
        csv_problem = None
        if csv_path is not None:
            try:
                csv_text = _read_text(csv_path)
            except OSError as exc:
                csv_problem = tr(
                    "cty.csv could not be read, DXCC codes are not available: {error}"
                ).format(error=exc)
        db = cls.from_text(dat_text, csv_text)
        if csv_problem:
            db.warnings.append(csv_problem)
        return db

    def __len__(self) -> int:
        return len(self.entities)

    def __repr__(self) -> str:
        return (
            f"<CtyDatabase entities={len(self.entities)} prefixes={len(self._prefix)} "
            f"calls={len(self._exact)}>"
        )

    def lookup(self, call: str) -> CtyMatch | None:
        """Resolve a callsign to its entity; ``None`` for /MM, /AM, garbage and unknown calls.

        1. Exact ``=`` entries: the whole call, then after dropping non-location suffixes
           from the end one by one (``4U1VIC/P`` -> ``4U1VIC``), then after dropping every
           non-location part after the first (``9A70DP/P/KA`` -> ``9A70DP/KA``,
           ``II0PN/P/MM`` -> ``II0PN/MM``).
        2. ``/MM`` and ``/AM`` -> ``None``.
        3. Suffixes that are not locations are dropped: digits only (``/7``), one character
           (``/P /M /A /B /R``), the activities ``/LH /LS /LT /FF /FD /YL``, and three or
           more letters (``/QRP /LGT /JOTA``).
        4. ``PREFIX/CALL`` or ``CALL/PREFIX``: the shorter part is the location
           (``YU/DL1ABC`` and ``DL1ABC/YU`` -> Serbia). On equal length the part with the
           longer prefix match is (``K1A/KL7`` and ``KL7/K1A`` -> Alaska), on a tie the first
           part. If the location matches nothing, the home call decides.
        5. Longest-prefix match, overrides of the matched entry applied.
        6. KG4 rule, which cty.dat cannot express: a home call ``KG4`` plus one or three
           letters (``KG4A``, ``KG4ABC``) is a US call, not Guantanamo Bay (``KG4AB``); the
           match then has the United States defaults and ``matched == "K"``.

        The home call must contain a digit (``YUGO``, ``N/A`` -> ``None``). Text longer than
        64 characters after removing whitespace is not a callsign (``None``).
        """
        text = clean_call(call)
        if not text or len(text) > _MAX_CALL_LENGTH or _CALL_RE.fullmatch(text) is None:
            return None
        parts = [part for part in text.split("/") if part]
        if not parts:
            return None
        exact = self._exact
        candidate = "/".join(parts)
        hit = exact.get(candidate)
        if hit is not None:
            return _match(hit, candidate, True)
        trimmed = parts[:]
        while len(trimmed) > 1 and _is_modifier(trimmed[-1]):
            trimmed.pop()
            candidate = "/".join(trimmed)
            hit = exact.get(candidate)
            if hit is not None:
                return _match(hit, candidate, True)
        rest = parts[1:]
        kept = [parts[0]] + [part for part in rest if not _is_modifier(part)]
        if len(kept) != len(trimmed):
            candidate = "/".join(kept)
            hit = exact.get(candidate)
            if hit is not None:
                return _match(hit, candidate, True)
        if any(part in _NO_ENTITY_SUFFIXES for part in rest):
            return None
        if len(kept) == 1:
            home, location = kept[0], None
        else:
            location, home = self._split_location(kept[0], kept[1])
        if home.isalpha():  # a callsign always has a digit
            return None
        if location is not None:
            key = self._prefix_key(location)
            if key is not None:
                return _match(self._prefix[key], key, False)
        key = self._prefix_key(home)
        if key is None:
            return None
        if (
            key == _KG4_PREFIX
            and self._us_default is not None
            and _KG4_US_CALL_RE.fullmatch(home) is not None
        ):
            return _match(self._us_default, _US_PRIMARY_PREFIX, False)
        return _match(self._prefix[key], key, False)

    def _split_location(self, first: str, second: str) -> tuple[str, str]:
        """Return ``(location, home call)`` for a two-part call."""
        if len(first) != len(second):
            return (first, second) if len(first) < len(second) else (second, first)
        if len(self._prefix_key(second) or "") > len(self._prefix_key(first) or ""):
            return second, first
        return first, second

    def _prefix_key(self, text: str) -> str | None:
        """The longest listed prefix that ``text`` starts with, ``None`` if there is none."""
        prefixes = self._prefix
        for size in range(min(len(text), self._max_prefix_len), 0, -1):
            key = text[:size]
            if key in prefixes:
                return key
        return None


# --- lookup helpers ---------------------------------------------------------------------------


def _match(hit: tuple, matched: str, exact: bool) -> CtyMatch:
    entity, cq, itu, cont, lat, lon, tz = hit
    return CtyMatch(entity, cq, itu, cont, lat, lon, tz, matched, exact)


def _is_modifier(part: str) -> bool:
    """True for a suffix that is not a location: ``/7``, ``/P``, ``/LH``, ``/QRP``, ..."""
    if part.isdigit() or len(part) == 1 or part in _MODIFIER_SUFFIXES:
        return True
    return len(part) > 2 and part.isalpha()


# --- parsing ----------------------------------------------------------------------------------


class _Warnings:
    """Collects warnings, keeping the first ``_MAX_WARNINGS`` and counting the rest."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.dropped = 0

    def add(self, message: str) -> None:
        if len(self.items) < _MAX_WARNINGS:
            self.items.append(message)
        else:
            self.dropped += 1

    def result(self, no_entities: bool) -> list[str]:
        out = [tr("cty.dat contains no entities")] if no_entities else []
        out.extend(self.items)
        if self.dropped:
            out.append(tr("Further warnings not listed: {count}").format(count=self.dropped))
        return out


class _RawEntity:
    """Header values and parsed entries of one cty.dat entity, before ``Entity`` is built."""

    def __init__(self, line, name, cq, itu, cont, lat, lon, tz, prefix, wae) -> None:
        self.line = line
        self.name = name
        self.cq = cq
        self.itu = itu
        self.cont = cont
        self.lat = lat
        self.lon = lon
        self.tz = tz
        self.prefix = prefix
        self.wae = wae
        # (is_exact, key, None for the entity defaults or (cq, itu, cont, lat, lon, tz))
        self.entries: list[tuple] = []


def _east(lon_west: float) -> float:
    """cty.dat longitude (+ west) -> east-positive, without a negative zero."""
    value = -lon_west
    return value if value else 0.0


def _valid(cq: int, itu: int, cont: str, lat: float, lon: float, tz: float) -> bool:
    # chained comparisons are False for NaN, so NaN and infinities are rejected too
    return (
        1 <= cq <= 40
        and 1 <= itu <= 90
        and cont in _CONTINENTS
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
        and -24.0 < tz < 24.0
    )


def _parse_header(line: str, number: int) -> tuple[_RawEntity, str] | None:
    """Parse an entity header line; return the entity and any text after the 8th ':'."""
    fields = line.split(":")
    if len(fields) < 9:
        return None
    name = fields[0].strip()
    cont = fields[3].strip().upper()
    prefix = fields[7].strip()
    try:
        cq = int(fields[1])
        itu = int(fields[2])
        lat = float(fields[4])
        lon = _east(float(fields[5]))
        tz = float(fields[6])
    except ValueError:
        return None
    wae = prefix.startswith("*")
    if wae:
        prefix = prefix[1:].strip()
    if not name or not prefix or not _valid(cq, itu, cont, lat, lon, tz):
        return None
    raw = _RawEntity(number, name, cq, itu, cont, lat, lon, tz, prefix, wae)
    return raw, ":".join(fields[8:])


def _apply_overrides(text: str, raw: _RawEntity) -> tuple | None:
    """Entity defaults with the overrides of one entry applied; ``None`` if one is invalid."""
    cq, itu, cont, lat, lon, tz = raw.cq, raw.itu, raw.cont, raw.lat, raw.lon, raw.tz
    try:
        for match in _OVERRIDE_RE.finditer(text):
            new_cq, new_itu, latlon, new_cont, new_tz = match.groups()
            if new_cq is not None:
                cq = int(new_cq)
            elif new_itu is not None:
                itu = int(new_itu)
            elif latlon is not None:
                lat_text, slash, lon_text = latlon.partition("/")
                if not slash:
                    return None
                lat = float(lat_text)
                lon = _east(float(lon_text))
            elif new_cont is not None:
                cont = new_cont
            else:
                tz = float(new_tz)
    except ValueError:
        return None
    if not _valid(cq, itu, cont, lat, lon, tz):
        return None
    return cq, itu, cont, lat, lon, tz


def _parse_entries(raw: _RawEntity, body: str, number: int, warn) -> None:
    for token in body.split(","):
        token = token.strip()
        if not token:
            continue
        found = _ENTRY_RE.fullmatch(token.upper())
        values = None
        if found is not None and found.group(3):
            values = _apply_overrides(found.group(3), raw)
        if found is None or (found.group(3) and values is None):
            warn(
                tr("cty.dat line {line}: invalid prefix or callsign skipped: {entry}").format(
                    line=number, entry=token
                )
            )
            continue
        raw.entries.append((found.group(1) == "=", found.group(2), values))


def _parse_dat(text: str, warn) -> list[_RawEntity]:
    """Parse cty.dat line by line; broken entities are skipped with a warning."""
    raws: list[_RawEntity] = []
    current: _RawEntity | None = None
    skipping = False  # inside a broken entity or stray text: ignore lines up to the next ';'

    def unterminated(raw: _RawEntity) -> None:
        warn(
            tr("cty.dat line {line}: entity {name} is not closed with ';'").format(
                line=raw.line, name=raw.name
            )
        )
        raws.append(raw)

    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped[0] == "#":
            continue
        if ":" in stripped:  # only header lines contain ':'
            if current is not None:
                unterminated(current)
                current = None
            parsed = _parse_header(stripped, number)
            if parsed is None:
                warn(
                    tr("cty.dat line {line}: invalid entity header, entity skipped").format(
                        line=number
                    )
                )
                skipping = True
                continue
            current, stripped = parsed
            skipping = False
        elif current is None:
            if not skipping:
                warn(tr("cty.dat line {line}: text outside an entity skipped").format(line=number))
            skipping = ";" not in stripped
            continue
        body, end, _ = stripped.partition(";")
        _parse_entries(current, body, number, warn)
        if end:
            raws.append(current)
            current = None
    if current is not None:
        unterminated(current)
    return raws


def _parse_csv(text: str, warn) -> dict[str, int]:
    """ADIF DXCC code by cty.csv primary prefix (with the '*' of WAE-only rows)."""
    codes: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped[0] == "#":
            continue
        try:
            row = next(csv.reader([stripped]), [])
        except csv.Error:
            row = []
        prefix = row[0].strip() if row else ""
        code = row[2].strip() if len(row) > 2 else ""
        if not prefix or not (code.isascii() and code.isdigit()) or not 0 < int(code) < 1000:
            warn(tr("cty.csv line {line}: invalid row skipped").format(line=number))
            continue
        codes.setdefault(prefix, int(code))
    return codes


def _build_entities(raws: list[_RawEntity], codes: dict[str, int]) -> list[Entity]:
    """Create the entities with DXCC codes and the DXCC name (parent for WAE-only ones)."""

    def code_of(raw: _RawEntity) -> int | None:
        return codes.get("*" + raw.prefix if raw.wae else raw.prefix)

    by_prefix: dict[str, _RawEntity] = {}
    by_code: dict[int, list[_RawEntity]] = {}
    for raw in raws:
        if raw.wae:
            continue
        by_prefix.setdefault(raw.prefix, raw)
        code = code_of(raw)
        if code is not None:
            by_code.setdefault(code, []).append(raw)

    entities = []
    for raw in raws:
        code = code_of(raw)
        if not raw.wae:
            dxcc_name = _DXCC_NAMES.get(raw.prefix, raw.name)
        else:
            same_code = by_code.get(code, []) if code is not None else []
            parent = same_code[0] if len(same_code) == 1 else None
            if parent is None:
                parent = by_prefix.get(_WAE_PARENTS.get(raw.prefix, ""))
            if parent is None:
                dxcc_name = raw.name
            else:
                dxcc_name = _DXCC_NAMES.get(parent.prefix, parent.name)
                if code is None:
                    code = code_of(parent)
        entities.append(
            Entity(
                raw.name,
                raw.cq,
                raw.itu,
                raw.cont,
                raw.lat,
                raw.lon,
                raw.tz,
                raw.prefix,
                raw.wae,
                code,
                dxcc_name,
            )
        )
    return entities


def _read_text(path: str | os.PathLike) -> str:
    """File contents as text: UTF-8 (BOM allowed), else Latin-1. ``OSError`` propagates."""
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")
