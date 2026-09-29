"""Hamlib ``rigctld`` / ``rotctld`` protocol: commands, response parser, helpers.

HamQ never talks to a radio or rotator directly. The user runs the Hamlib
network daemons and HamQ sends them one-line commands over TCP, always in the
*extended response* form (``+`` prefix). Every reply is then a block of lines::

    get_freq:              <- header: command name, then the echoed arguments
    Frequency: 14074000    <- "Key: value" fields (zero or more)
    RPRT 0                 <- terminator: 0 = success, negative = Hamlib error code

This module is pure Python (no Qt): the socket side is ``hamq.net.hamlib_client``.
Error codes and mode names were checked against the Hamlib 4.6.2 and 4.6.5 sources
(identical there: ``include/hamlib/rig.h``, ``src/rig.c``, ``src/misc.c``,
``tests/rigctl_parse.c``) and a live Hamlib 4.6.2 ``rigctld -m 1`` / ``rotctld -m 1``.
"""

from __future__ import annotations

import codecs
import decimal
import math
import numbers
import re
from dataclasses import dataclass

from .i18n import tr, tr_noop

__all__ = [
    "MODES",
    "RIG_DEFAULT_PORT",
    "ROT_DEFAULT_PORT",
    "HamlibResponse",
    "ResponseParser",
    "cmd_get_freq",
    "cmd_get_mode",
    "cmd_get_pos",
    "cmd_set_freq",
    "cmd_set_mode",
    "cmd_set_pos",
    "cmd_stop",
    "error_message",
    "expected_command",
    "parse_freq",
    "parse_mode",
    "parse_pos",
    "rotator_target",
]

RIG_DEFAULT_PORT = 4532
ROT_DEFAULT_PORT = 4533

# Hamlib mode names accepted by rig_parse_mode() in Hamlib 4.6.x (src/misc.c,
# mode_str[]), common amateur modes first. Only canonical names are listed: the
# table's aliases are in _MODE_ALIASES. "None" is not a mode that can be set, and
# USBD1..3 / LSBD1..3 (Hamlib master only) are not accepted by 4.6.
MODES: tuple[str, ...] = (
    "USB",
    "LSB",
    "CW",
    "CWR",
    "AM",
    "FM",
    "WFM",
    "RTTY",
    "RTTYR",
    "PKTUSB",
    "PKTLSB",
    "PKTFM",
    "PKTAM",
    "PKTFMN",
    "AMS",
    "DSB",
    "FMN",
    "AMN",
    "CWN",
    "ECSSUSB",
    "ECSSLSB",
    "SAM",
    "SAL",
    "SAH",
    "FAX",
    "PSK",
    "PSKR",
    "C4FM",
    "D-STAR",
    "DPMR",
    "NXDN-VN",
    "NXDN-N",
    "DCR",
    "P25",
    "SPEC",
    "IQ",
    "ISBUSB",
    "ISBLSB",
)
_MODE_SET = frozenset(MODES)

# rig_strrmode() returns the first table entry of a mode, so Hamlib 4.6 reports
# PKTFM as "FM-D" and PKTAM as "AM-D"; parse_mode() maps them back to MODES names.
_MODE_ALIASES = {
    "AM-D": "PKTAM",
    "FM-D": "PKTFM",
    "CW-R": "CWR",
    "RTTY-R": "RTTYR",
    "LSB-D": "PKTLSB",
    "USB-D": "PKTUSB",
}

# enum rig_errcode_e in include/hamlib/rig.h (same in Hamlib 4.6.2, 4.6.5 and master).
# rotctld uses the same codes. Texts follow rigerror() in src/rig.c.
_ERROR_MESSAGES: dict[int, str] = {
    0: tr_noop("Command completed successfully"),  # RIG_OK
    1: tr_noop("Invalid parameter"),  # RIG_EINVAL
    2: tr_noop("Invalid configuration"),  # RIG_ECONF
    3: tr_noop("Memory shortage"),  # RIG_ENOMEM
    4: tr_noop("Feature not implemented"),  # RIG_ENIMPL
    5: tr_noop("Communication timed out"),  # RIG_ETIMEOUT
    6: tr_noop("Input/output error"),  # RIG_EIO
    7: tr_noop("Internal Hamlib error"),  # RIG_EINTERNAL
    8: tr_noop("Protocol error"),  # RIG_EPROTO
    9: tr_noop("Command rejected by the device"),  # RIG_ERJCTED
    10: tr_noop("Command performed, but argument truncated"),  # RIG_ETRUNC
    11: tr_noop("Feature not available"),  # RIG_ENAVAIL
    12: tr_noop("Target VFO not accessible"),  # RIG_ENTARGET
    13: tr_noop("Communication bus error"),  # RIG_BUSERROR
    14: tr_noop("Communication bus collision"),  # RIG_BUSBUSY
    15: tr_noop("Invalid argument"),  # RIG_EARG
    16: tr_noop("Invalid VFO"),  # RIG_EVFO
    17: tr_noop("Argument out of range"),  # RIG_EDOM
    18: tr_noop("Function deprecated"),  # RIG_EDEPRECATED
    19: tr_noop("Security error: password missing or wrong"),  # RIG_ESECURITY
    20: tr_noop("Radio is not powered on"),  # RIG_EPOWER
    21: tr_noop("Limit exceeded"),  # RIG_ELIMIT
    22: tr_noop("Access denied (the port may already be in use)"),  # RIG_EACCESS
}

# Parser limits: memory stays bounded whatever the daemon (or a wrong port) sends.
_MAX_LINE = 4096  # characters; longer lines are dropped whole
_MAX_FIELDS = 256  # fields kept per response; later new keys are ignored

_RPRT_RE = re.compile(r"RPRT\s+([+-]?\d{1,9})")
_HEADER_RE = re.compile(r"([a-z][a-z0-9_]*):(.*)")
_FIELD_RE = re.compile(r"([A-Za-z][^:]*):(.*)")
_COMMAND_RE = re.compile(r"\+(?:\\([a-z][a-z0-9_]*)|(\S))(?:\s|$)")
_INT_RE = re.compile(r"[+-]?\d{1,30}")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,4})?")

# Short commands of the builders -> header name of the reply.
_REPLY_HEADERS = {
    "f": "get_freq",
    "F": "set_freq",
    "m": "get_mode",
    "M": "set_mode",
    "p": "get_pos",
    "P": "set_pos",
    "S": "stop",
}

_DECIMAL_CONTEXT = decimal.Context(prec=400, rounding=decimal.ROUND_HALF_UP)
_HUNDREDTH = decimal.Decimal("0.01")
_EDGE_EPS = 1e-9  # degrees; absorbs float noise at rotator range edges


@dataclass
class HamlibResponse:
    """One reply of ``rigctld`` / ``rotctld`` in extended response mode."""

    command: str  # header name ("get_freq", "set_pos", ...), "" when missing
    args: str  # echoed text after "name:" on the header line, stripped
    fields: dict[str, str]  # {"Frequency": "14074000"}
    rprt: int  # 0 ok, negative = Hamlib error code

    @property
    def ok(self) -> bool:
        """``True`` when the daemon reported success (``RPRT 0``)."""
        return self.rprt == 0


class ResponseParser:
    """Incremental parser for the extended replies of ``rigctld`` / ``rotctld``.

    Feed it whatever the socket delivers; it returns every reply completed so
    far. Partial lines are kept across calls, bytes are decoded as UTF-8 (invalid
    sequences become U+FFFD), CRLF and blank lines are accepted.

    A reply is an optional header line (``get_freq:`` or ``set_freq: 14074000``),
    then ``Key: value`` field lines, terminated by ``RPRT n``:

    - an ``RPRT n`` line with no header before it gives a reply with command ``""``
      (stray lines, e.g. after a bogus command);
    - a header line always starts a new reply: an unfinished one is dropped
      (rigctld sends no ``RPRT`` after the header when the rig reports an I/O error);
    - any other line is ignored.

    Never raises on bad input. Lines longer than 4096 characters are dropped
    whole and at most 256 fields are kept per reply, so memory stays bounded.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._parts: list[str] = []  # pieces of the current, unfinished line
        self._parts_len = 0
        self._discarding = False  # inside an overlong line: skip to the next newline
        self._command: str | None = None  # None: no reply in progress
        self._args = ""
        self._fields: dict[str, str] = {}

    def reset(self) -> None:
        """Forget partial lines and any unfinished reply (e.g. after a timeout)."""
        self._decoder.reset()
        self._parts = []
        self._parts_len = 0
        self._discarding = False
        self._end_reply()

    def feed(self, data: bytes | str) -> list[HamlibResponse]:
        """Add received data; return the replies completed by it, in order.

        ``data`` is ``bytes`` (``bytearray`` and ``memoryview`` work too) or ``str``.
        Anything else is a programmer error and raises ``TypeError``.
        """
        if isinstance(data, str):
            # Bytes of an unfinished UTF-8 sequence cannot continue in a str.
            text = self._decoder.decode(b"", True) + data
            self._decoder.reset()
        elif isinstance(data, (bytes, bytearray, memoryview)):
            text = self._decoder.decode(bytes(data))
        else:
            raise TypeError(f"feed() takes bytes or str, not {type(data).__name__}")

        replies: list[HamlibResponse] = []
        pos = 0
        while pos < len(text):
            newline = text.find("\n", pos)
            if newline < 0:
                self._keep_partial(text[pos:])
                break
            piece = text[pos:newline]
            pos = newline + 1
            if self._discarding:
                self._discarding = False  # end of the overlong line
                continue
            if self._parts:
                self._parts.append(piece)
                piece = "".join(self._parts)
                self._parts = []
                self._parts_len = 0
            if len(piece) > _MAX_LINE:
                continue
            reply = self._line(piece)
            if reply is not None:
                replies.append(reply)
        return replies

    def _buffered_chars(self) -> int:
        """Number of characters held for an unfinished line (for tests)."""
        return self._parts_len

    def _keep_partial(self, piece: str) -> None:
        if self._discarding:
            return
        self._parts.append(piece)
        self._parts_len += len(piece)
        if self._parts_len > _MAX_LINE:
            self._parts = []
            self._parts_len = 0
            self._discarding = True

    def _end_reply(self) -> None:
        self._command = None
        self._args = ""
        self._fields = {}

    def _line(self, line: str) -> HamlibResponse | None:
        line = line.strip()
        if not line:
            return None
        match = _RPRT_RE.fullmatch(line)
        if match is not None:
            code = int(match.group(1))
            if self._command is None:
                return HamlibResponse("", "", {}, code)
            reply = HamlibResponse(self._command, self._args, self._fields, code)
            self._end_reply()
            return reply
        match = _HEADER_RE.fullmatch(line)
        if match is not None:
            self._command = match.group(1)
            self._args = match.group(2).strip()
            self._fields = {}
            return None
        match = _FIELD_RE.fullmatch(line)
        if match is not None:
            if self._command is None:
                self._command = ""
                self._args = ""
                self._fields = {}
            key = match.group(1).strip()
            if key in self._fields or len(self._fields) < _MAX_FIELDS:
                self._fields[key] = match.group(2).strip()
        return None


# --------------------------------------------------------------------------- commands


def cmd_get_freq() -> str:
    """``+f``: read the frequency (reply field ``Frequency``, Hz)."""
    return "+f\n"


def cmd_set_freq(hz: int) -> str:
    """``+F <hz>``: set the frequency, e.g. ``'+F 14074000\\n'``.

    A float is rounded to the nearest Hz. ``ValueError`` when the result is not
    above 0 or ``hz`` is not finite; ``TypeError`` when it is not a number.
    """
    value = _to_int(hz, "hz")
    if value <= 0:
        raise ValueError(f"hz must be > 0, got {hz!r}")
    return f"+F {value}\n"


def cmd_get_mode() -> str:
    """``+m``: read the mode (reply fields ``Mode`` and ``Passband``)."""
    return "+m\n"


def cmd_set_mode(mode: str, passband: int = 0) -> str:
    """``+M <mode> <passband>``: set the mode, e.g. ``'+M USB 0\\n'``.

    ``mode`` must be one of :data:`MODES` (exact spelling; rigctld silently
    ignores unknown names). ``passband`` in Hz: 0 = the rig's normal passband for
    the mode, -1 = keep the current one. ``ValueError`` for an unknown mode or a
    passband below -1 or not finite.
    """
    if not isinstance(mode, str):
        raise TypeError(f"mode must be str, not {type(mode).__name__}")
    if mode not in _MODE_SET:
        raise ValueError(f"unknown Hamlib mode {mode!r}, expected one of MODES")
    width = _to_int(passband, "passband")
    if width < -1:
        raise ValueError(f"passband must be >= -1, got {passband!r}")
    return f"+M {mode} {width}\n"


def cmd_get_pos() -> str:
    """``+p``: read the rotator position (reply fields ``Azimuth``, ``Elevation``)."""
    return "+p\n"


def cmd_set_pos(azimuth: float, elevation: float = 0.0) -> str:
    """``+P <az> <el>``: turn the rotator, e.g. ``'+P 123.5 0.0\\n'``.

    Degrees are written with at most two decimals (rounded half up) and at least
    one: ``123.5``, ``90.0``, ``12.35``. The range is not checked here (use
    :func:`rotator_target`); the daemon answers ``RPRT -21`` outside it.
    ``ValueError`` for non-finite values.
    """
    az = _format_degrees(_to_float(azimuth, "azimuth"))
    el = _format_degrees(_to_float(elevation, "elevation"))
    return f"+P {az} {el}\n"


def cmd_stop() -> str:
    """``+S``: stop the rotator."""
    return "+S\n"


def expected_command(cmd: str) -> str:
    """Header name the reply to ``cmd`` carries: ``'+f\\n'`` -> ``'get_freq'``.

    Knows the commands built by this module and the long form (``+\\get_freq``).
    Returns ``""`` for anything else.
    """
    match = _COMMAND_RE.match(cmd)
    if match is None:
        return ""
    if match.group(1):
        return match.group(1)
    return _REPLY_HEADERS.get(match.group(2), "")


# --------------------------------------------------------------------------- replies


def parse_freq(resp: HamlibResponse) -> int | None:
    """Frequency in Hz from a ``get_freq`` reply.

    ``None`` when the reply is an error, the ``Frequency`` field is missing or
    unparsable, or the value is not above 0. ``14074000.000000`` (some backends)
    gives ``14074000``.
    """
    if resp.rprt != 0:
        return None
    hz = _parse_int(_field(resp, "Frequency"))
    if hz is None or hz <= 0:
        return None
    return hz


def parse_mode(resp: HamlibResponse) -> tuple[str, int] | None:
    """``(mode, passband_hz)`` from a ``get_mode`` reply.

    Hamlib aliases are mapped to their :data:`MODES` name (``FM-D`` -> ``PKTFM``,
    ``AM-D`` -> ``PKTAM``, ...); a mode unknown to HamQ is returned as reported.
    ``None`` when the reply is an error or ``Mode`` / ``Passband`` is missing,
    empty or unparsable.
    """
    if resp.rprt != 0:
        return None
    mode = _field(resp, "Mode")
    passband = _parse_int(_field(resp, "Passband"))
    if mode is None or passband is None:
        return None
    mode = mode.strip()
    if not mode:
        return None
    name = mode.upper()
    if name in _MODE_SET:
        return name, passband
    if name in _MODE_ALIASES:
        return _MODE_ALIASES[name], passband
    return mode, passband


def parse_pos(resp: HamlibResponse) -> tuple[float, float] | None:
    """``(azimuth, elevation)`` in degrees from a ``get_pos`` reply.

    ``None`` when the reply is an error or either field is missing or unparsable.
    """
    if resp.rprt != 0:
        return None
    azimuth = _parse_number(_field(resp, "Azimuth"))
    elevation = _parse_number(_field(resp, "Elevation"))
    if azimuth is None or elevation is None:
        return None
    return azimuth, elevation


def error_message(code: int) -> str:
    """Translated description of a Hamlib ``RPRT`` code (sign ignored, as Hamlib does).

    Unknown codes give a generic message that contains the code.
    """
    text = _ERROR_MESSAGES.get(abs(code))
    if text is None:
        return tr("Unknown Hamlib error (code {code})").format(code=code)
    return tr(text)


# --------------------------------------------------------------------------- rotator


def rotator_target(
    bearing_deg: float,
    min_az: float,
    max_az: float,
    current_az: float | None = None,
) -> float | None:
    """Compass bearing -> azimuth to command, inside ``[min_az, max_az]``.

    Every ``bearing + k * 360`` inside the range reaches the same direction, so
    rotators with overlap (0..450, 180..540) or a south stop (-180..180) often
    have two choices. The one closest to ``current_az`` wins (least rotation);
    without a current position the one closest to the bearing itself (0..360).
    Ties go to the less wound equivalent. ``None`` when no equivalent is in the
    range (e.g. range 0..180 and bearing 270) or when ``min_az > max_az``.
    ``ValueError`` for non-finite arguments.
    """
    bearing = _normalize_bearing(_to_float(bearing_deg, "bearing_deg"))
    low = _to_float(min_az, "min_az")
    high = _to_float(max_az, "max_az")
    ref = bearing if current_az is None else _to_float(current_az, "current_az")
    if low > high:
        return None
    k_low = math.ceil((low - _EDGE_EPS - bearing) / 360.0)
    k_high = math.floor((high + _EDGE_EPS - bearing) / 360.0)
    if k_low > k_high:
        return None
    k_near = min(max(round((ref - bearing) / 360.0), k_low), k_high)
    candidates = [k for k in (k_near - 1, k_near, k_near + 1) if k_low <= k <= k_high]
    best = min(candidates, key=lambda k: (abs(bearing + 360.0 * k - ref), abs(k), k))
    return float(min(max(bearing + 360.0 * best, low), high))


# --------------------------------------------------------------------------- helpers


def _normalize_bearing(bearing: float) -> float:
    """Bearing in ``[0, 360)``; ``-0.0`` becomes ``0.0``."""
    value = math.fmod(bearing, 360.0)
    if value < 0.0:
        value += 360.0
    if value >= 360.0:  # -1e-15 + 360.0 rounds to 360.0
        value -= 360.0
    return value + 0.0


def _to_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be finite, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _to_int(value: object, name: str) -> int:
    """Integer argument; a float is rounded half up."""
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)
    return math.floor(_to_float(value, name) + 0.5)


def _format_degrees(value: float) -> str:
    """``123.5`` -> ``'123.5'``, ``90`` -> ``'90.0'``, ``12.345`` -> ``'12.35'``."""
    quantized = decimal.Decimal(repr(value)).quantize(_HUNDREDTH, context=_DECIMAL_CONTEXT)
    if quantized.is_zero():
        return "0.0"  # also for -0.0 and tiny negatives
    text = format(quantized, "f").rstrip("0")
    return text + "0" if text.endswith(".") else text


def _field(resp: HamlibResponse, name: str) -> str | None:
    """Field value by key; falls back to a case-insensitive match."""
    value = resp.fields.get(name)
    if value is not None:
        return value
    folded = name.casefold()
    for key, item in resp.fields.items():
        if key.casefold() == folded:
            return item
    return None


def _parse_number(text: str | None) -> float | None:
    """Finite float from reply text (``'123.00'``, ``'123,50'``, ``'1.4e7'``) or ``None``."""
    if text is None:
        return None
    value = text.strip()
    if "," in value and "." not in value:
        value = value.replace(",", ".")  # decimal comma of some locales
    if _FLOAT_RE.fullmatch(value) is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_int(text: str | None) -> int | None:
    """Integer from reply text; a decimal value is rounded half up."""
    if text is None:
        return None
    value = text.strip()
    if _INT_RE.fullmatch(value) is not None:
        return int(value)
    number = _parse_number(value)
    if number is None:
        return None
    return math.floor(number + 0.5)
