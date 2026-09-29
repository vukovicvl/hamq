# HamQ architecture and API contract

This file is the contract between modules. Public names and signatures listed
here are fixed; implementations may add private helpers and extra optional
keyword arguments, never change or remove what is listed. If a module needs a
change to the contract, record it under `## Contract changes` at the end of
this file with the reason.

Read `AGENTS.md` (rules) and `PLAN.md` (scope) first.

## Layout

```
hamq/
  __init__.py            classFactory(iface)
  plugin.py              HamQPlugin: actions, menu, toolbar, dock lifecycle (thin)
  controller.py          HamQController(QObject): glue between GUI, storage and network
  settings.py            HamQSettings: typed access to QgsSettings keys under hamq/
  events.py              events(): process-wide QObject with signals
  metadata.txt
  core/                  pure Python 3.9+, no qgis / PyQt imports
    bands.py             band table, band_from_freq, band_sort_key   (done)
    modes.py             display_mode(mode, submode)                  (done)
    maidenhead.py adif.py geo.py cty.py wsjtx.py qso.py stats.py i18n.py hamlib.py
  i18n/
    sr_Latn/*.json       translation catalogs: English source -> Serbian Latin
  qgis_io/
    compat.py            Qt5/Qt6 and QGIS 3.34..4.x API shims (enums, field types)
    fields.py            make_field / make_fields
    gpkg.py              GeoPackage schema, insert with dedup, geodesic paths, reads
    layers.py            project layers: load, refresh, field aliases
    styles.py            default styles (color by band)
  processing/
    provider.py          HamQProvider (id "hamq")
    alg_locator_to_point.py alg_grid.py alg_import_adif.py alg_recalculate.py
  gui/
    dock.py settings_dialog.py locator_search.py azimuthal.py language.py
    rotator_tool.py qso_dialog.py
  net/
    cty_download.py wsjtx_listener.py hamlib_client.py
  resources/
    icons/*.svg styles/*.qml
tests/
  core/                  pytest, no QGIS
  qgis/                  pytest inside QGIS (local 4.x or Docker), see "Testing"
  fixtures/adif/ fixtures/cty/ fixtures/wsjtx/
scripts/
  package.py             builds dist/hamq-<version>.zip
  test_qgis.sh           runs tests/qgis on local QGIS and Docker images
```

## General rules

- `from __future__ import annotations` at the top of every module. Code must
  run on Python 3.9: no `match`, no `dataclass(slots=True)`, no `zip(strict=)`,
  no runtime use of `X | Y` types (isinstance, casts, module-level aliases).
  In annotations write `X | None` (ruff UP045 enforces it; safe on 3.9 because
  annotations are not evaluated). `Optional[...]` in this document is shorthand.
- Latitude/longitude order in core (`lat, lon`), x/y order in QGIS code.
- All datetimes are timezone-aware UTC (`datetime.timezone.utc`).
- Callsigns are uppercase and stripped.
- No `print`. Core returns warnings as data; QGIS code logs with
  `QgsMessageLog.logMessage(msg, "HamQ", Qgis.MessageLevel.Warning)`.
- Core never raises on bad input from files or the network; it returns `None`
  plus a warning. `ValueError` is only for programmer errors on explicit
  conversion functions (e.g. `to_locator` with lat=200).

## Translation (i18n) rules

Every user-visible string goes through `tr()` from `hamq.core.i18n`:

```python
from hamq.core.i18n import tr, tr_noop          # core: from .i18n import tr
msg = tr("Record {index}: missing CALL, skipped").format(index=i)
LABELS = {"total": tr_noop("Total QSOs")}       # marked, translated later with tr(LABELS[k])
```

- The argument of `tr()` / `tr_noop()` / `self.tr()` must be a single plain
  string literal (no f-strings, no concatenation). Placeholders use
  `str.format` names: `{index}`, `{count}`. A test scans all `hamq/**/*.py`
  with `ast` and fails on non-literal arguments or untranslated strings.
- Qt classes may define `def tr(self, text): return tr(text)`; never use
  `QCoreApplication.translate` or `QObject.tr` directly.
- Catalogs live in `hamq/i18n/sr_Latn/<package>_<module>.json`, one file per
  source module (`core_adif.json`, `gui_dock.json`, `plugin.json`, ...), a flat
  JSON object `{"English source": "Serbian Latin translation"}`, keys sorted,
  UTF-8 (`ensure_ascii=False`), 2-space indent. One file per module means
  parallel work never edits the same catalog. Whoever writes a module's strings
  also adds them to that module's file. Serbian Cyrillic is derived at runtime
  by transliteration (see `core/i18n.py`); only Latin is stored.
- `tr(name)` with a variable is allowed only for values that were marked with
  `tr_noop("...")` elsewhere. f-strings, `+` concatenation, `%` or `.format()`
  inside the `tr(...)` argument are errors (format after translating).
- Avoid plurals in messages ("QSO: {count}" instead of "{count} QSOs").

### Serbian glossary (use consistently)

| English | Srpski |
|---|---|
| QSO / contact | veza (pl. veze) |
| log | log (dnevnik veza in long text) |
| callsign | pozivni znak |
| locator / QTH locator | lokator / QTH lokator |
| Maidenhead field / square / subsquare / extended | polje / kvadrat / podkvadrat / prošireni podkvadrat |
| band | opseg |
| mode | vrsta rada |
| frequency | frekvencija |
| distance | rastojanje |
| bearing / azimuth | azimut |
| long path / short path | dugi put / kratki put |
| continent | kontinent |
| country / DXCC entity | zemlja / DXCC entitet |
| CQ zone / ITU zone | CQ zona / ITU zona |
| path(s) (QSO lines on the map) | putanja / putanje |
| grid (layer) | mreža |
| import | uvoz (verb: uvezi) |
| settings | podešavanja |
| statistics | statistika |
| panel (dock) | panel |
| listen / start / stop | slušaj / pokreni / zaustavi |
| connected / not connected | povezan / nije povezan |
| refresh | osveži |
| download | preuzmi |
| duplicate | duplikat |
| skipped | preskočeno |
| warning / error | upozorenje / greška |
| language | jezik |
| azimuthal map | azimutalna karta |
| about | o programu |

Keep technical names untranslated: QSO, ADIF, DXCC, WSJT-X, JTDX, UDP, CQ, ITU,
FT8, FT4, GeoPackage, cty.dat, Hamlib, QGIS, UTC, QTH, EPSG:4326.

## Core API (pure Python)

### core/bands.py (done)

`BANDS`, `BAND_ORDER`, `band_from_freq(freq_mhz) -> str | None`,
`normalize_band(band) -> str | None`, `band_sort_key(band) -> tuple[int, str]`.

### core/modes.py (done)

`display_mode(mode, submode) -> str`: SUBMODE if present else MODE, uppercased, `""` if neither.

### core/maidenhead.py (M1)

```python
LEVEL_FIELD, LEVEL_SQUARE, LEVEL_SUBSQUARE, LEVEL_EXTENDED = 2, 4, 6, 8
VALID_LENGTHS = (2, 4, 6, 8)
MAX_GRID_CELLS = 200_000

def is_valid(locator: str) -> bool                 # case-insensitive, lengths 2/4/6/8 only
def normalize(locator: str) -> str                 # strip, 'kn04FT' -> 'KN04ft'; a 10-char
                                                   # locator with a valid 8-char prefix is cut
                                                   # to 8; otherwise ValueError if invalid
def to_locator(lat: float, lon: float, precision: int = 6) -> str   # precision in VALID_LENGTHS
def to_bounds(locator: str) -> tuple[float, float, float, float]    # (lat_min, lon_min, lat_max, lon_max)
def to_latlon(locator: str) -> tuple[float, float]                  # cell center (lat, lon)
def cell_size(level: int) -> tuple[float, float]                    # (dlat, dlon) degrees
def snap_extent(lat_min, lon_min, lat_max, lon_max, level) -> tuple[float, float, float, float]
                                                   # outward to cell edges, clamped to the world
def count_cells(lat_min, lon_min, lat_max, lon_max, level) -> int
def iter_cells(lat_min, lon_min, lat_max, lon_max, level)
    -> Iterator[tuple[str, tuple[float, float, float, float]]]     # (locator, bounds), lon then lat
```

`to_bounds`, `to_latlon` accept any case and raise `ValueError` on invalid input.
Use integer cell arithmetic (not repeated float `%`) so boundary values are exact.

### core/adif.py (M2)

```python
@dataclass
class AdifDocument:
    header: dict[str, str]                 # header fields (ADIF_VER, PROGRAMID, ...), may be empty
    records: list[dict[str, str]]          # field names uppercase, values stripped
    warnings: list[str]                    # translated via tr()

def decode_bytes(data: bytes) -> str                       # UTF-8 (BOM ok), fallback latin-1
def parse_adi(text: str) -> tuple[list[dict[str, str]], list[str]]   # records, warnings
def parse_document(text: str) -> AdifDocument
def read_adi(path: str | os.PathLike) -> AdifDocument      # bytes -> decode_bytes -> parse_document
def parse_latlon(value: str | None) -> float | None        # 'N044 48.750' -> 44.8125
def parse_freq(value: str | None) -> float | None          # '14.074', '14,074' -> 14.074
def parse_qso_datetime(qso_date: str | None, time_on: str | None) -> datetime | None  # UTC aware
def dedup_key(call: str, qso_date: str, time_on: str, band: str, mode: str) -> str
    # f"{CALL}|{QSO_DATE}{TIME_ON[:4]}|{band}|{MODE}"  (minute precision)
def format_record(fields: Mapping[str, str]) -> str        # '<CALL:5>YU1AB ... <EOR>\n'
def format_document(records, header: Mapping[str, str] | None = None) -> str
```

Field lengths are counted in characters of the decoded text (per the adif skill).
Narrow tolerance extension: if taking LENGTH characters would swallow a
well-formed following tag (`<NAME:n>`, `<EOR>`) and interpreting LENGTH as
UTF-8 bytes does not, use the byte interpretation and add a warning
(some loggers count bytes; Serbian names like `Đorđe` trigger this).

### core/geo.py (M3)

```python
EARTH_RADIUS_KM = 6371.0088
def distance_km(lat1, lon1, lat2, lon2) -> float                   # spherical, great circle
def bearing_deg(lat1, lon1, lat2, lon2) -> float                   # initial bearing [0, 360)
def long_path(distance_km: float, bearing_deg: float) -> tuple[float, float]
def is_antipodal(lat1, lon1, lat2, lon2, tolerance_km: float = 20.0) -> bool
def destination(lat, lon, bearing_deg, distance_km) -> tuple[float, float]
def great_circle(lat1, lon1, lat2, lon2, step_km: float = 100.0)
    -> list[list[tuple[float, float]]]      # parts of (lat, lon), split at the antimeridian
def aeqd_proj(lat: float, lon: float) -> str   # '+proj=aeqd +lat_0=... +units=km +no_defs'
```

### core/cty.py (M4)

```python
DEFAULT_CTY_URL = "..."        # verified download URL of cty.dat (AD1C, country-files.com)
DEFAULT_CTY_CSV_URL = "..."    # verified URL of cty.csv (same data plus ADIF DXCC codes)

@dataclass(frozen=True)
class Entity:
    name: str
    cq_zone: int
    itu_zone: int
    continent: str                 # EU AS AF NA SA OC AN
    lat: float
    lon: float                     # EAST positive (sign flipped from the file)
    utc_offset: float
    primary_prefix: str            # without the leading '*'
    wae_only: bool                 # primary prefix had a leading '*'
    dxcc: Optional[int] = None     # ADIF DXCC code when cty.csv was loaded

@dataclass(frozen=True)
class CtyMatch:
    entity: Entity
    cq_zone: int                   # overrides applied
    itu_zone: int
    continent: str
    lat: float
    lon: float                     # east positive
    utc_offset: float
    matched: str                   # the prefix or exact call that matched
    exact: bool
    @property
    def dxcc(self) -> Optional[int]            # entity.dxcc (parent code for WAE-only)
    @property
    def dxcc_name(self) -> str                 # DXCC entity name; parent name for WAE-only

class CtyDatabase:
    entities: list[Entity]
    @classmethod
    def from_text(cls, dat_text: str, csv_text: str | None = None) -> CtyDatabase
    @classmethod
    def from_files(cls, dat_path, csv_path=None) -> CtyDatabase
    def lookup(self, call: str) -> Optional[CtyMatch]    # None for /MM, /AM and unknown
    def __len__(self) -> int                             # number of entities

def clean_call(call: str) -> str
```

### core/wsjtx.py (M5)

```python
MAGIC = 0xADBCCBDA
HEARTBEAT, STATUS, QSO_LOGGED, CLOSE, LOGGED_ADIF = 0, 1, 5, 6, 12

def decode(data: bytes) -> Optional[dict]   # never raises; None for garbage / wrong magic
# {"type": "heartbeat", "client", "schema", "max_schema", "version", "revision"}
# {"type": "status", "client", "schema", "freq_hz", "mode", "dx_call", ... optional extra
#   keys when present: "report", "tx_mode", "tx_enabled", "transmitting", "decoding",
#   "de_call", "de_grid", "dx_grid"}
# {"type": "close", "client", "schema"}
# {"type": "logged_adif", "client", "schema", "adif"}
# {"type": "other", "code", "client", "schema"}

class Writer: ...                             # QDataStream-compatible encoder (tests, tools)
def encode_heartbeat(client: str, max_schema: int = 3, version: str = "2.7.0",
                     revision: str = "", schema: int = 2) -> bytes
def encode_status(client: str, freq_hz: int, mode: str, dx_call: str = "", *,
                  schema: int = 2, **optional) -> bytes      # full field list per NetworkMessage.hpp
def encode_close(client: str, schema: int = 2) -> bytes
def encode_logged_adif(client: str, adif: str, schema: int = 2) -> bytes
```

### core/qso.py (M2, after maidenhead, adif, geo, cty)

```python
QSO_FIELDS: tuple[tuple[str, str], ...]   # (name, kind) in PLAN.md order, without fid;
                                          # kind in {"int", "real", "text", "datetime"}
PATH_FIELDS: tuple[tuple[str, str], ...]  # qso_fid int, distance_km real, bearing_deg real,
                                          # band text, mode text

@dataclass
class Station:
    call: str = ""
    grid: str = ""
    def latlon(self) -> Optional[tuple[float, float]]   # center of grid, None if invalid/empty

@dataclass
class Qso:
    call: str
    qso_datetime: datetime               # UTC aware
    band: Optional[str]
    mode: Optional[str]
    submode: Optional[str]
    freq_mhz: Optional[float]
    rst_sent: Optional[str]
    rst_rcvd: Optional[str]
    gridsquare: Optional[str]
    my_gridsquare: Optional[str]
    dxcc: Optional[int]
    country: Optional[str]
    cont: Optional[str]
    cq_zone: Optional[int]
    itu_zone: Optional[int]
    distance_km: Optional[float]
    bearing_deg: Optional[float]
    loc_source: Optional[str]            # 'latlon' | 'grid' | 'cty' | None
    source: str                          # 'adif:<file name>' | 'wsjtx'
    dedup_key: str
    adif_extra: str                      # JSON object text, '{}' when nothing extra
    lat: Optional[float]                 # other station position (None = no geometry)
    lon: Optional[float]
    my_lat: Optional[float]              # path origin (None = no distance / path)
    my_lon: Optional[float]
    def attributes(self) -> dict[str, object]   # keys = QSO_FIELDS names, in order
    @property
    def display_mode(self) -> str               # submode or mode, '' if neither

display_mode                              # re-exported from core/modes.py
def record_to_qso(record: Mapping[str, str], *, station: Station | None = None,
                  cty: CtyDatabase | None = None, source: str = "")
    -> tuple[Optional[Qso], list[str]]
def records_to_qsos(records: Iterable[Mapping[str, str]], *, station=None, cty=None,
                    source: str = "") -> tuple[list[Qso], list[str]]
```

Rules for `record_to_qso`:

- `CALL` required (skip + warning); `QSO_DATE`+`TIME_ON` must parse (skip + warning).
- band: `BAND` normalized, else derived from `FREQ`.
- mode/submode uppercased. Dedup uses `display_mode` (FT4 logged as `MODE=FT4`
  or `MODE=MFSK SUBMODE=FT4` must dedup to the same key).
- gridsquare normalized with `maidenhead.normalize`; a 10-char locator is cut to
  8 with a warning; an invalid one is kept as-is, not used for position, warning.
- Position of the other station: `LAT`/`LON` > `GRIDSQUARE` center > cty entity > none.
- Path origin: record `MY_LAT`/`MY_LON` > record `MY_GRIDSQUARE` > `station.grid` > none.
  `my_gridsquare` = record value or `station.grid`.
- `dxcc`, `country`, `cont`, `cq_zone`, `itu_zone`: record values (`DXCC`,
  `COUNTRY`, `CONT`, `CQZ`, `ITUZ`) win; missing ones are filled from cty
  (`dxcc` from `CtyMatch.dxcc`, `country` from `CtyMatch.dxcc_name`).
- `distance_km` / `bearing_deg` from origin to position when both exist.
- `adif_extra`: JSON of every record field not stored in a dedicated column
  (LAT, LON, MY_LAT, MY_LON, NAME, COMMENT, ... are kept here).

### core/stats.py (M4)

```python
CONTINENTS = ("EU", "AS", "AF", "NA", "SA", "OC", "AN")

@dataclass
class LongestQso:
    call: str
    distance_km: float
    country: Optional[str]
    band: Optional[str]
    mode: Optional[str]
    qso_datetime: Optional[datetime]

@dataclass
class QsoStats:
    total: int
    dxcc_count: int            # distinct dxcc codes; rows without a code count by country name
    unique_calls: int
    grid_count: int            # distinct 4-char squares of the other station
    by_continent: dict[str, int]   # CONTINENTS order, then others / "?" last; zeros omitted
    by_band: dict[str, int]        # band_sort_key order
    by_mode: dict[str, int]        # display_mode, descending count then name
    longest: Optional[LongestQso]
    first_qso: Optional[datetime]
    last_qso: Optional[datetime]

def compute_stats(rows: Iterable[Mapping[str, object]]) -> QsoStats
    # rows use QSO_FIELDS names; missing keys / None values are tolerated;
    # qso_datetime may be datetime or ISO 8601 text
```

### core/i18n.py (M6)

```python
LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL = "auto", "en", "sr_Latn", "sr_Cyrl"
LANGUAGES: tuple[tuple[str, str], ...]   # (code, native name) for en, sr_Latn, sr_Cyrl:
                                         # English, Srpski (latinica), Српски (ћирилица)
def resolve_language(setting: str | None, system_locale: str | None) -> str   # never 'auto'
def is_serbian(language: str) -> bool
def latin_to_cyrillic(text: str) -> str  # Serbian transliteration; protects {placeholders},
                                         # %s, <tags>, &entities;, URLs, file names and
                                         # technical tokens (ADIF, QSO, FT8, KN04ft, 20m, ...)
def load_catalog(directory: str | os.PathLike) -> dict[str, str]   # merges *.json
class Translator:
    def __init__(self, catalog: dict[str, str] | None = None, language: str = LANG_EN)
    @property
    def language(self) -> str
    def set_language(self, language: str) -> None
    def translate(self, text: str) -> str
def get_translator() -> Translator       # singleton, lazily loads hamq/i18n/sr_Latn/
def set_language(language: str) -> None
def current_language() -> str
def tr(text: str) -> str
def tr_noop(text: str) -> str
```

`resolve_language("auto", "sr_RS")` -> `sr_Cyrl`; `"sr@latin"`, `"sr_Latn"`,
`"sr_Latn_RS"` -> `sr_Latn`; anything else -> `en`. Unknown setting values -> `en`.

### core/hamlib.py (M7)

Hamlib daemons (`rigctld`, `rotctld`) speak a line protocol over TCP. HamQ
always uses the extended form (`+` prefix). Real Hamlib 4.6.2 responses
(captured from `rigctld -m 1` / `rotctld -m 1` in the `hamq/hamlib-dummy`
Docker image; error codes and mode names are identical in 4.6.5):

```
+f            -> 'get_freq:\nFrequency: 14074000\nRPRT 0\n'
+m            -> 'get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n'
+F 14074000   -> 'set_freq: 14074000\nRPRT 0\n'
+M USB 0      -> 'set_mode: USB 0\nRPRT 0\n'
+t            -> 'get_ptt:\nRPRT -11\n'            (feature not available)
+p            -> 'get_pos:\nAzimuth: 123.00\nElevation: 0.00\nRPRT 0\n'
+P 123.5 10   -> 'set_pos: 123.5 10\nRPRT 0\n'
+S            -> 'stop:\nRPRT 0\n'
+X_bogus      -> ''  (NO response at all: the client must time out)
+F abc        -> 'set_freq: abc\nRPRT -1\n'
(after '+X_bogus' the next line is swallowed as its argument:
 '+F abc' then yields 'set_split_mode: bogus +F\nRPRT -1\nRPRT -18\nRPRT -1\n' — stray lines: resync)
+M USB        -> no reply; the NEXT line is taken as the passband (always send the passband)
```

```python
RIG_DEFAULT_PORT, ROT_DEFAULT_PORT = 4532, 4533

@dataclass
class HamlibResponse:
    command: str                 # header name ("get_freq", "set_pos", ...), "" when missing
    args: str                    # echoed text after "name:" on the header line, stripped
    fields: dict[str, str]       # {"Frequency": "14074000"}
    rprt: int                    # 0 ok, negative = Hamlib error code
    @property
    def ok(self) -> bool

class ResponseParser:            # incremental; tolerates partial lines, CRLF, stray RPRT lines
                                 # Qt callers: feed(bytes(socket.readAll())) — QByteArray is rejected
    def feed(self, data: bytes | str) -> list[HamlibResponse]
    def reset(self) -> None

def cmd_get_freq() -> str                    # '+f\n'
def cmd_set_freq(hz: int) -> str             # '+F 14074000\n'   ValueError if hz <= 0
def cmd_get_mode() -> str                    # '+m\n'
def cmd_set_mode(mode: str, passband: int = 0) -> str   # '+M USB 0\n'  ValueError if mode not in MODES
def cmd_get_pos() -> str                     # '+p\n'
def cmd_set_pos(azimuth: float, elevation: float = 0.0) -> str   # '+P 123.5 0.0\n'
def cmd_stop() -> str                        # '+S\n'
def expected_command(cmd: str) -> str        # '+f\n' -> 'get_freq' (header the reply must carry)
MODES: tuple[str, ...]                       # Hamlib mode names (USB LSB CW CWR AM FM WFM RTTY RTTYR PKTUSB PKTLSB PKTFM ...)
def parse_freq(resp: HamlibResponse) -> int | None                # Hz
def parse_mode(resp: HamlibResponse) -> tuple[str, int] | None    # (mode, passband Hz); canonical
                                                                  # MODES names (daemon 'FM-D' -> 'PKTFM')
def parse_pos(resp: HamlibResponse) -> tuple[float, float] | None # (azimuth, elevation)
def error_message(code: int) -> str          # translated text for Hamlib RPRT codes
def rotator_target(bearing_deg: float, min_az: float, max_az: float,
                   current_az: float | None = None) -> float | None
    # compass bearing -> commandable azimuth inside [min_az, max_az]; for ranges wider
    # than 360 (0..450, -180..180, 180..540) pick the equivalent closest to current_az;
    # None when the bearing is unreachable (e.g. range 0..180 and bearing 270)
```

## QGIS side API

### settings.py

```python
class HamQSettings:
    # properties with getters/setters, stored in QgsSettings under "hamq/"
    my_call: str             # hamq/my_call, default ""
    my_grid: str             # hamq/my_grid, default ""
    gpkg_path: str           # hamq/gpkg_path, default default_gpkg_path()
    wsjtx_addr: str          # hamq/wsjtx_addr, default "127.0.0.1" (multicast 224.0.0.0/4 joins group)
    wsjtx_port: int          # hamq/wsjtx_port, default 2237
    wsjtx_autostart: bool    # hamq/wsjtx_autostart, default False
    language: str            # hamq/language, default "auto"
    last_serbian: str        # hamq/last_serbian, default "sr_Latn"
    cty_downloaded: str      # hamq/cty_downloaded, ISO date or ""
    rig_enabled: bool        # hamq/rig_enabled, default False
    rig_host: str            # hamq/rig_host, default "127.0.0.1"
    rig_port: int            # hamq/rig_port, default 4532
    rig_poll_ms: int         # hamq/rig_poll_ms, default 1000
    rot_enabled: bool        # hamq/rot_enabled, default False
    rot_host: str            # hamq/rot_host, default "127.0.0.1"
    rot_port: int            # hamq/rot_port, default 4533
    rot_min_az: float        # hamq/rot_min_az, default 0.0
    rot_max_az: float        # hamq/rot_max_az, default 360.0
    rot_confirmed: bool      # hamq/rot_confirmed, first map-click confirmation done, default False
    def station(self) -> Station
def profile_dir() -> str     # <QGIS settings dir>/hamq, created on demand
def default_gpkg_path() -> str       # profile_dir()/hamq.gpkg
def cty_cache_path() -> str          # profile_dir()/cty.dat  (cty.csv next to it)
```

### events.py

```python
class HamQEvents(QObject):
    dataChanged = pyqtSignal(str)        # gpkg path whose content changed
    languageChanged = pyqtSignal(str)    # resolved language code
    settingsChanged = pyqtSignal()
def events() -> HamQEvents               # singleton; first call must happen in the main thread
```

### qgis_io

```python
# compat.py: resolved enums/constants valid on QGIS 3.34 .. 4.x, Qt5 and Qt6, e.g.
#   WKB_POINT, WKB_MULTILINESTRING, WKB_POLYGON, SOURCE_VECTOR_POLYGON, ...,
#   MSG_INFO, MSG_WARNING, MSG_CRITICAL, MSG_SUCCESS, QAction, ...
# fields.py
def make_field(name: str, kind: str) -> QgsField          # kind: int | real | text | datetime
def make_fields(spec: Iterable[tuple[str, str]]) -> QgsFields
# gpkg.py
QSO_LAYER, PATH_LAYER = "qso", "qso_path"
def ensure_gpkg(path: str) -> None                 # create file/layers/UNIQUE index if missing
def layer_uri(path: str, layer: str) -> str        # f"{path}|layername={layer}"
def existing_dedup_keys(path: str) -> set[str]
@dataclass
class InsertResult: inserted: int; duplicates: int; failed: int; fids: list[int]; warnings: list[str]
def insert_qsos(path: str, qsos: Sequence[Qso], feedback=None, chunk_size: int = 1000) -> InsertResult
    # skips duplicates (DB + within batch), writes qso points and qso_path lines
def geodesic_path(my_lat, my_lon, lat, lon) -> Optional[QgsGeometry]   # MultiLineString, None if antipodal
def recalculate(path: str, station: Station, cty: CtyDatabase | None = None, feedback=None) -> int
    # recompute distance/bearing/paths for all QSOs (new QTH), fill missing DXCC data from cty
def read_qso_rows(path: str) -> list[dict[str, object]]   # attribute dicts for compute_stats
# layers.py
def find_layers(path: str) -> tuple[Optional[QgsVectorLayer], Optional[QgsVectorLayer]]
def load_layers(path: str) -> tuple[QgsVectorLayer, QgsVectorLayer]   # into group "HamQ", styled
def refresh_layers(path: str) -> None
def apply_field_aliases(layer: QgsVectorLayer) -> None                # translated aliases
# styles.py
BAND_COLORS: dict[str, str]
def apply_default_style(layer: QgsVectorLayer, kind: str) -> None     # "qso" | "qso_path" | "grid"
```

### processing

Provider id `hamq`, name `HamQ`. Algorithm ids: `locator_to_point`,
`maidenhead_grid`, `import_adif`, `recalculate`. Algorithms get their strings
from `tr()`. After an algorithm changes the GeoPackage it emits
`events().dataChanged.emit(path)` from `postProcessAlgorithm` (main thread).
After a language change the plugin calls `provider.refreshAlgorithms()`.

### net

```python
class CtyManager(QObject):
    downloadFinished = pyqtSignal(bool, str)        # ok, translated message
    def database(self) -> Optional[CtyDatabase]     # parsed cache, None when not downloaded
    def is_available(self) -> bool
    def download(self) -> None                      # async, QgsNetworkAccessManager
def load_cached_cty() -> Optional[CtyDatabase]      # no network; safe in worker threads

class WsjtxListener(QObject):
    heartbeatReceived = pyqtSignal(dict)
    statusReceived = pyqtSignal(dict)
    adifLogged = pyqtSignal(str, str)               # client id, ADIF text
    clientClosed = pyqtSignal(str)
    connectionChanged = pyqtSignal(bool)            # heartbeat seen within timeout
    errorOccurred = pyqtSignal(str)                 # translated
    def start(self, address: str, port: int) -> bool
    def stop(self) -> None
    def is_running(self) -> bool
    def is_connected(self) -> bool

class HamlibClient(QObject):                        # net/hamlib_client.py (M7)
    # QTcpSocket, one command in flight, FIFO queue, 2 s timeout per command
    # (timeout -> reset parser, mark disconnected, reconnect every 5 s), polling.
    # rigctld closes the client socket after a hard (non-soft) rig error when it
    # cannot reopen the rig: treat a remote close like a timeout and reconnect.
    connectedChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)                 # translated
    def start(self, host: str, port: int) -> None
    def stop(self) -> None
    def is_connected(self) -> bool
class RigClient(HamlibClient):                      # polls +f and +m every rig_poll_ms
    stateChanged = pyqtSignal(dict)                 # {"freq_hz", "mode", "passband"} (None when unknown)
    def set_frequency(self, hz: int) -> None
    def set_mode(self, mode: str, passband: int = 0) -> None
class RotatorClient(HamlibClient):                  # polls +p every second
    positionChanged = pyqtSignal(float, float)      # azimuth, elevation
    def set_position(self, azimuth: float, elevation: float = 0.0) -> None
    def stop_rotation(self) -> None
```

### gui

- `language.py`: `LanguageManager(QObject)` (setting, resolved language,
  `set_setting`, `toggle` EN <-> last Serbian, applies `core.i18n.set_language`,
  emits `events().languageChanged`), `qgis_ui_locale()`, language menu and a
  toolbar switch button (EN / SR / СР).
- `dock.py`: `HamQDock(QDockWidget)` with tabs: Statistics, WSJT-X, Radio
  (rig: frequency, band, mode, set frequency/mode; rotator: current and target
  azimuth, turn, stop, "point on map"); `retranslate()`.
- `rotator_tool.py`: `RotatorMapTool` (map click -> bearing from my QTH ->
  `rotator_target` -> confirm the first time -> `RotatorClient.set_position`),
  beam line from QTH on the map.
- `qso_dialog.py`: manual QSO entry; frequency and mode prefilled from the rig
  when connected; saved through core/qso + qgis_io/gpkg like any other QSO.
- `settings_dialog.py`: `SettingsDialog(QDialog)`: call, grid, GeoPackage path,
  UDP address/port/autostart, language, cty.dat status + refresh.
- `locator_search.py`: toolbar widget, `KN04ft` + Enter centers the map.
- `azimuthal.py`: azimuthal equidistant map centered on my QTH, helper layer.

Every widget with text implements `retranslate()` and is connected to
`events().languageChanged`.

## Testing

- `python3 -m pytest tests/core -q` (no QGIS needed; default `testpaths`).
- `scripts/test_qgis.sh <target> [pytest args]` runs `tests/qgis`:
  `local` (host QGIS 4.x, Qt6), `3.44` (Docker `qgis/qgis:3.44-trixie`, Qt5),
  `4.0` (Docker `qgis/qgis:4.0-trixie`, Qt6), `3.34` (Docker
  `camptocamp/qgis-server:3.34`, Qt5, minimum supported version), `all`.
- `ruff check hamq tests` and `ruff format --check hamq tests`.

## Contract changes

(none yet)
