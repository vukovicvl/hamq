"""Typed access to the HamQ settings stored in ``QgsSettings`` under ``hamq/``.

Every property reads and writes ``QgsSettings`` directly, so several
:class:`HamQSettings` instances always see the same values. Invalid stored
values (a port that is not a number, an unknown choice) read back as the
default. Setters only store; whoever saves settings from the UI emits
``events().settingsChanged`` once afterwards.

Files that belong to the plugin (GeoPackage, cty.dat cache) live in
:func:`profile_dir`, inside the active QGIS profile.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from qgis.core import QgsApplication, QgsSettings

if TYPE_CHECKING:
    from .core.qso import Station

#: Prefix of every HamQ key in ``QgsSettings``.
SETTINGS_PREFIX = "hamq/"

_TRUE_TEXT = frozenset({"1", "true", "yes", "on"})
_FALSE_TEXT = frozenset({"0", "false", "no", "off", ""})
# A Maidenhead locator of 2, 4, 6 or 8 characters (case-insensitive).
_LOCATOR_RE = re.compile(r"[A-Ra-r]{2}(?:[0-9]{2}(?:[A-Xa-x]{2}(?:[0-9]{2})?)?)?")


def profile_dir() -> str:
    """Return ``<QGIS settings dir>/hamq`` of the active profile, created on demand."""
    path = os.path.join(os.path.normpath(QgsApplication.qgisSettingsDirPath()), "hamq")
    os.makedirs(path, exist_ok=True)
    return path


def default_gpkg_path() -> str:
    """Default GeoPackage with the QSO log: ``profile_dir()/hamq.gpkg``."""
    return os.path.join(profile_dir(), "hamq.gpkg")


def cty_cache_path() -> str:
    """Local cty.dat cache: ``profile_dir()/cty.dat`` (cty.csv is stored next to it)."""
    return os.path.join(profile_dir(), "cty.dat")


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


_CONVERTERS: dict[type, Callable[[Any], Any]] = {
    bool: _to_bool,
    int: _to_int,
    float: _to_float,
    str: _to_str,
}


def _normalize_call(value: str) -> str:
    return value.strip().upper()


def _normalize_grid(value: str) -> str:
    """``' kn04FT '`` -> ``'KN04ft'``; text that is not a locator is only stripped."""
    text = value.strip()
    if _LOCATOR_RE.fullmatch(text):
        return text[:2].upper() + text[2:4] + text[4:6].lower() + text[6:8]
    return text


def _port(value: int) -> bool:
    return 0 < value < 65536


class _Setting:
    """Descriptor for one typed ``QgsSettings`` key under ``hamq/``."""

    def __init__(
        self,
        key: str,
        kind: type,
        default: Any,
        *,
        validate: Callable[[Any], bool] | None = None,
        normalize: Callable[[Any], Any] | None = None,
    ) -> None:
        self.key = SETTINGS_PREFIX + key
        self.kind = kind
        self._default = default
        self._validate = validate
        self._normalize = normalize

    def default(self) -> Any:
        return self._default() if callable(self._default) else self._default

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self
        raw = QgsSettings().value(self.key, None)
        value = None if raw is None else _CONVERTERS[self.kind](raw)
        if value is None or (self._validate is not None and not self._validate(value)):
            return self.default()
        return value

    def __set__(self, obj: Any, value: Any) -> None:
        converted = _CONVERTERS[self.kind](value)
        if converted is not None and self._normalize is not None:
            converted = self._normalize(converted)
        if converted is None or (self._validate is not None and not self._validate(converted)):
            raise ValueError(f"invalid value {value!r} for setting {self.key}")
        QgsSettings().setValue(self.key, converted)


class HamQSettings:
    """Typed HamQ settings (``QgsSettings`` keys under ``hamq/``).

    Reading never fails: a missing or invalid value gives the default. Setting a
    value that cannot be converted to the property type, or that is out of range
    (a port outside 1..65535), raises ``ValueError`` (a programmer error: dialogs
    validate their input first).
    """

    #: My callsign, stored uppercase and stripped.
    my_call = _Setting("my_call", str, "", normalize=_normalize_call)
    #: My QTH locator, e.g. ``KN04ft`` (Maidenhead case applied when it looks like one).
    my_grid = _Setting("my_grid", str, "", normalize=_normalize_grid)
    #: GeoPackage with the QSO log.
    gpkg_path = _Setting("gpkg_path", str, default_gpkg_path, normalize=str.strip)
    #: Address the WSJT-X listener binds to; a multicast address (224.0.0.0/4) joins the group.
    wsjtx_addr = _Setting("wsjtx_addr", str, "127.0.0.1", normalize=str.strip)
    #: UDP port of the WSJT-X listener.
    wsjtx_port = _Setting("wsjtx_port", int, 2237, validate=_port)
    #: Start the WSJT-X listener when QGIS starts.
    wsjtx_autostart = _Setting("wsjtx_autostart", bool, False)
    #: Interface language setting: ``auto``, ``en``, ``sr_Latn`` or ``sr_Cyrl``.
    language = _Setting("language", str, "auto", normalize=str.strip)
    #: Serbian script used by the EN <-> SR toggle: ``sr_Latn`` or ``sr_Cyrl``.
    last_serbian = _Setting(
        "last_serbian", str, "sr_Latn", validate=lambda v: v in ("sr_Latn", "sr_Cyrl")
    )
    #: Date of the last cty.dat download (ISO 8601) or ``""``.
    cty_downloaded = _Setting("cty_downloaded", str, "", normalize=str.strip)
    #: Connect to Hamlib ``rigctld``.
    rig_enabled = _Setting("rig_enabled", bool, False)
    #: ``rigctld`` host.
    rig_host = _Setting("rig_host", str, "127.0.0.1", normalize=str.strip)
    #: ``rigctld`` TCP port.
    rig_port = _Setting("rig_port", int, 4532, validate=_port)
    #: Rig polling interval in milliseconds.
    rig_poll_ms = _Setting("rig_poll_ms", int, 1000, validate=lambda v: v > 0)
    #: Connect to Hamlib ``rotctld``.
    rot_enabled = _Setting("rot_enabled", bool, False)
    #: ``rotctld`` host.
    rot_host = _Setting("rot_host", str, "127.0.0.1", normalize=str.strip)
    #: ``rotctld`` TCP port.
    rot_port = _Setting("rot_port", int, 4533, validate=_port)
    #: Lowest azimuth the rotator accepts (degrees).
    rot_min_az = _Setting("rot_min_az", float, 0.0)
    #: Highest azimuth the rotator accepts (degrees), e.g. 450 for overlap rotators.
    rot_max_az = _Setting("rot_max_az", float, 360.0)
    #: The user confirmed the first "turn the antenna from a map click".
    rot_confirmed = _Setting("rot_confirmed", bool, False)

    def station(self) -> Station:
        """Return my station (callsign and locator) as a ``core.qso.Station``."""
        from .core.qso import Station

        return Station(call=self.my_call, grid=self.my_grid)
