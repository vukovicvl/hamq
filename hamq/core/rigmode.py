"""Radio (Hamlib) modes to ADIF modes, and ADIF mode helpers for manual QSO entry.

A radio reports how it demodulates (Hamlib ``USB``, ``CWR``, ``PKTUSB``, ...), while
a log records the ADIF mode of the contact (``SSB``, ``CW``, ``FT8``, ...). Most
radio modes map to exactly one ADIF mode; the data modes (``PKTUSB``, ``PKTFM``,
...) carry FT8, PSK31, RTTY and many others, so only the operator knows the ADIF
mode there and :func:`adif_mode` returns ``None`` for them.

ADIF 3.1 writes some modes as a submode of a parent mode: FT4 is
``MODE=MFSK SUBMODE=FT4``, PSK31 is ``MODE=PSK SUBMODE=PSK31``. :func:`split_mode`
turns what an operator types (``FT4``) into that pair, and :func:`default_rst` gives
the usual signal report for a mode (``59`` phone, ``599`` CW and keyboard modes,
``-10`` for the WSJT modes that report in dB).

SSB is logged as ``MODE=SSB`` without a submode: that is how most loggers write it,
and :func:`hamq.core.modes.display_mode` (statistics, duplicate check) then shows
the same ``SSB`` for every SSB contact.

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

__all__ = [
    "ADIF_MODES",
    "DATA_RIG_MODES",
    "MODE_CHOICES",
    "SUBMODES",
    "SUBMODE_PARENTS",
    "adif_mode",
    "default_rst",
    "is_data_mode",
    "normalize_rig_mode",
    "split_mode",
]

# Hamlib mode (core.hamlib.MODES name) -> (ADIF MODE, ADIF SUBMODE). Modes missing
# here have no single ADIF mode (data modes, IQ, spectrum, ISB, DSB, ECSS).
_RIG_TO_ADIF: dict[str, tuple[str, str]] = {
    "USB": ("SSB", ""),
    "LSB": ("SSB", ""),
    "CW": ("CW", ""),
    "CWR": ("CW", ""),
    "CWN": ("CW", ""),
    "AM": ("AM", ""),
    "AMN": ("AM", ""),
    "AMS": ("AM", ""),
    "SAM": ("AM", ""),
    "SAL": ("AM", ""),
    "SAH": ("AM", ""),
    "FM": ("FM", ""),
    "FMN": ("FM", ""),
    "WFM": ("FM", ""),
    "RTTY": ("RTTY", ""),
    "RTTYR": ("RTTY", ""),
    "PSK": ("PSK", ""),
    "PSKR": ("PSK", ""),
    "FAX": ("FAX", ""),
    "C4FM": ("DIGITALVOICE", "C4FM"),
    "D-STAR": ("DIGITALVOICE", "DSTAR"),
    "DPMR": ("DIGITALVOICE", ""),
    "NXDN-VN": ("DIGITALVOICE", ""),
    "NXDN-N": ("DIGITALVOICE", ""),
    "DCR": ("DIGITALVOICE", ""),
    "P25": ("DIGITALVOICE", ""),
}

#: Hamlib data modes: the radio passes audio or data for a digital mode that only the
#: operator knows (FT8, PSK31, ...), so :func:`adif_mode` returns ``None`` for them.
DATA_RIG_MODES: frozenset[str] = frozenset({"PKTUSB", "PKTLSB", "PKTFM", "PKTAM", "PKTFMN"})

# Spellings Hamlib 4.6 reports for some modes (rig_strrmode), as core.hamlib.parse_mode
# maps them; accepted here too for callers that pass the raw daemon text.
_RIG_ALIASES = {
    "AM-D": "PKTAM",
    "FM-D": "PKTFM",
    "CW-R": "CWR",
    "RTTY-R": "RTTYR",
    "LSB-D": "PKTLSB",
    "USB-D": "PKTUSB",
    "DSTAR": "D-STAR",
}

#: Common ADIF 3.1 modes, most used first.
ADIF_MODES: tuple[str, ...] = (
    "SSB",
    "CW",
    "FM",
    "AM",
    "FT8",
    "MFSK",
    "RTTY",
    "PSK",
    "JT65",
    "JT9",
    "MSK144",
    "OLIVIA",
    "SSTV",
    "DIGITALVOICE",
    "HELL",
    "CONTESTI",
    "DOMINO",
    "MT63",
    "THOR",
    "PKT",
    "FAX",
    "ATV",
)

#: Common ADIF 3.1 submodes of a mode.
SUBMODES: dict[str, tuple[str, ...]] = {
    "SSB": ("USB", "LSB"),
    "MFSK": ("FT4", "FST4", "JS8", "Q65", "MFSK16"),
    "PSK": ("PSK31", "PSK63", "PSK125", "QPSK31"),
    "DIGITALVOICE": ("C4FM", "DMR", "DSTAR", "FREEDV", "M17"),
    "JT65": ("JT65A", "JT65B", "JT65C"),
    "OLIVIA": ("OLIVIA 4/250", "OLIVIA 8/250", "OLIVIA 8/500", "OLIVIA 16/500", "OLIVIA 32/1000"),
    "HELL": ("FMHELL", "FSKHELL", "HELL80"),
}

#: ADIF submode -> its mode (``FT4`` -> ``MFSK``), for every submode in :data:`SUBMODES`.
SUBMODE_PARENTS: dict[str, str] = {
    submode: mode for mode, submodes in SUBMODES.items() for submode in submodes
}

#: What a manual QSO dialog offers as "mode": the common ADIF modes, with the popular
#: submodes (FT4, PSK31, C4FM, ...) listed by their own name. :func:`split_mode` turns a
#: choice into the ADIF ``(MODE, SUBMODE)`` pair.
MODE_CHOICES: tuple[str, ...] = (
    "SSB",
    "CW",
    "FM",
    "AM",
    "FT8",
    "FT4",
    "RTTY",
    "PSK31",
    "JT65",
    "JT9",
    "MSK144",
    "Q65",
    "FST4",
    "JS8",
    "OLIVIA",
    "SSTV",
    "C4FM",
    "DMR",
    "DSTAR",
    "FREEDV",
    "MFSK",
    "PSK",
    "DIGITALVOICE",
    "HELL",
    "CONTESTI",
    "DOMINO",
    "MT63",
    "THOR",
    "PKT",
    "FAX",
    "ATV",
)

# Modes whose signal report is RS (readability, strength): voice.
_PHONE_MODES = frozenset(
    {"SSB", "USB", "LSB", "AM", "FM", "DIGITALVOICE", "C4FM", "DMR", "DSTAR", "FREEDV", "M17"}
)
# WSJT-style modes that report the signal-to-noise ratio in dB.
_DB_MODES = frozenset(
    {
        "FT8",
        "FT4",
        "FST4",
        "JS8",
        "Q65",
        "JT65",
        "JT65A",
        "JT65B",
        "JT65C",
        "JT9",
        "JT4",
        "MSK144",
        "QRA64",
    }
)


def _clean(text: object) -> str:
    """Stripped, uppercased text; ``""`` for anything that is not a string."""
    return text.strip().upper() if isinstance(text, str) else ""


def normalize_rig_mode(rig_mode: str | None) -> str:
    """Hamlib mode name as :data:`hamq.core.hamlib.MODES` spells it (``'fm-d'`` -> ``'PKTFM'``).

    Strips and uppercases; maps the aliases Hamlib 4.6 reports (``FM-D``, ``CW-R``,
    ...). ``""`` for ``None``, blank text or a value that is not a string.
    """
    name = _clean(rig_mode)
    return _RIG_ALIASES.get(name, name)


def is_data_mode(rig_mode: str | None) -> bool:
    """True when the radio is in a data mode (``PKTUSB``, ``PKTLSB``, ``PKTFM``, ...)."""
    return normalize_rig_mode(rig_mode) in DATA_RIG_MODES


def adif_mode(rig_mode: str | None) -> tuple[str, str] | None:
    """ADIF ``(MODE, SUBMODE)`` for a Hamlib radio mode; ``SUBMODE`` is ``""`` when none.

    ``USB`` / ``LSB`` -> ``("SSB", "")``, ``CW`` / ``CWR`` -> ``("CW", "")``, ``AM`` ->
    ``("AM", "")``, ``FM`` / ``FMN`` / ``WFM`` -> ``("FM", "")``, ``RTTY`` / ``RTTYR`` ->
    ``("RTTY", "")``, ``C4FM`` -> ``("DIGITALVOICE", "C4FM")``. Case and surrounding
    spaces do not matter and Hamlib aliases (``CW-R``) are accepted.

    ``None`` when the operator has to choose: the data modes (``PKTUSB``, ``PKTFM``,
    ...), modes without an ADIF equivalent, unknown text, blank text or ``None``.
    """
    return _RIG_TO_ADIF.get(normalize_rig_mode(rig_mode))


def split_mode(text: str | None) -> tuple[str, str]:
    """ADIF ``(MODE, SUBMODE)`` for a mode an operator typed or chose.

    A known submode is written under its mode: ``'ft4'`` -> ``("MFSK", "FT4")``,
    ``'PSK31'`` -> ``("PSK", "PSK31")``, ``'USB'`` -> ``("SSB", "USB")``. Anything else
    is a mode without a submode, uppercased and stripped: ``' ft8 '`` -> ``("FT8", "")``.
    Blank text or ``None`` gives ``("", "")``.
    """
    name = _clean(text)
    parent = SUBMODE_PARENTS.get(name)
    if parent is not None:
        return parent, name
    return name, ""


def default_rst(mode: str | None, submode: str | None = None) -> str:
    """Usual signal report for a mode: ``"59"``, ``"599"``, ``"-10"`` or ``""``.

    - ``"59"`` (RS) for voice: SSB, AM, FM and digital voice (C4FM, DMR, D-STAR, ...);
    - ``"-10"`` (dB) for the WSJT modes: FT8, FT4, JT65, JT9, Q65, FST4, MSK144, JS8;
    - ``"599"`` (RST) for CW and every other mode (RTTY, PSK31, OLIVIA, ...);
    - ``""`` when no mode is given.

    ``mode`` may also be a submode (``"FT4"``); an explicit ``submode`` decides first.
    """
    main, implied = split_mode(mode)
    sub = _clean(submode) or implied
    for name in (sub, main):
        if name in _DB_MODES:
            return "-10"
        if name in _PHONE_MODES:
            return "59"
    return "599" if (sub or main) else ""
