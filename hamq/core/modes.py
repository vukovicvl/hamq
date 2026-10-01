"""Operating mode helpers: the mode shown to the user and the mode of the duplicate key.

ADIF 3.1 writes many modes as a submode of a parent mode, and loggers differ in how much
of that they write. One SSB contact is ``MODE=SSB`` in LoTW and in HamQ's radio mapping,
``MODE=SSB SUBMODE=USB`` in loggers that keep the sideband, and ``MODE=USB`` in some older
programs; FT4 is ``MODE=MFSK SUBMODE=FT4`` in ADIF 3.1 and ``MODE=FT4`` in older logs.

- :func:`display_mode` is what the user sees (statistics, map, panel): the submode when
  there is one, else the mode (``FT4``, ``USB``, ``PSK31``).
- :func:`dedup_mode` is the mode in the duplicate key: the same contact gets the same value
  however the logger wrote it.

Pure Python: no ``qgis`` or ``PyQt`` imports.
"""

from __future__ import annotations

__all__ = ["dedup_mode", "display_mode"]

# ADIF 3.1.7 modes whose submodes are variants of the mode (sideband, speed, tones), from
# the Mode and Submode enumerations of the specification. Many loggers write only the mode
# for these, others the submode, older ones the submode as MODE (``MODE=PSK31``).
# MFSK and DIGITALVOICE are left out on purpose: they are umbrella modes whose submodes are
# modes of their own (FT4, JS8, Q65, FST4, C4FM, DMR, DSTAR) that loggers name directly.
_VARIANT_SUBMODES: dict[str, tuple[str, ...]] = {
    "CHIP": ("CHIP64", "CHIP128"),
    "CW": ("PCW",),
    "DOMINO": (
        "DOM-M",
        "DOM4",
        "DOM5",
        "DOM8",
        "DOM11",
        "DOM16",
        "DOM22",
        "DOM44",
        "DOM88",
        "DOMINOEX",
        "DOMINOF",
    ),
    "DYNAMIC": ("FREEDATA", "VARA HF", "VARA SATELLITE", "VARA FM 1200", "VARA FM 9600"),
    "FSK": ("SCAMP_FAST", "SCAMP_SLOW", "SCAMP_VSLOW"),
    "HELL": (
        "FMHELL",
        "FSKH105",
        "FSKH245",
        "FSKHELL",
        "HELL80",
        "HELLX5",
        "HELLX9",
        "HFSK",
        "PSKHELL",
        "SLOWHELL",
    ),
    "ISCAT": ("ISCAT-A", "ISCAT-B"),
    "JT4": ("JT4A", "JT4B", "JT4C", "JT4D", "JT4E", "JT4F", "JT4G"),
    "JT65": ("JT65A", "JT65B", "JT65B2", "JT65C", "JT65C2"),
    "JT9": (
        "JT9-1",
        "JT9-2",
        "JT9-5",
        "JT9-10",
        "JT9-30",
        "JT9A",
        "JT9B",
        "JT9C",
        "JT9D",
        "JT9E",
        "JT9E FAST",
        "JT9F",
        "JT9F FAST",
        "JT9G",
        "JT9G FAST",
        "JT9H",
        "JT9H FAST",
    ),
    "MTONE": ("SCAMP_OO", "SCAMP_OO_SLW"),
    "OFDM": ("RIBBIT_PIX", "RIBBIT_SMS"),
    "OLIVIA": (
        "OLIVIA 4/125",
        "OLIVIA 4/250",
        "OLIVIA 8/250",
        "OLIVIA 8/500",
        "OLIVIA 16/500",
        "OLIVIA 16/1000",
        "OLIVIA 32/1000",
    ),
    "OPERA": ("OPERA-BEACON", "OPERA-QSO"),
    "PAC": ("PAC2", "PAC3", "PAC4"),
    "PAX": ("PAX2",),
    "PSK": (
        "8PSK125",
        "8PSK125F",
        "8PSK125FL",
        "8PSK250",
        "8PSK250F",
        "8PSK250FL",
        "8PSK500",
        "8PSK500F",
        "8PSK1000",
        "8PSK1000F",
        "8PSK1200F",
        "FSK31",
        "PSK10",
        "PSK31",
        "PSK63",
        "PSK63F",
        "PSK63RC4",
        "PSK63RC5",
        "PSK63RC10",
        "PSK63RC20",
        "PSK63RC32",
        "PSK125",
        "PSK125C12",
        "PSK125R",
        "PSK125RC10",
        "PSK125RC12",
        "PSK125RC16",
        "PSK125RC4",
        "PSK125RC5",
        "PSK250",
        "PSK250C6",
        "PSK250R",
        "PSK250RC2",
        "PSK250RC3",
        "PSK250RC5",
        "PSK250RC6",
        "PSK250RC7",
        "PSK500",
        "PSK500C2",
        "PSK500C4",
        "PSK500R",
        "PSK500RC2",
        "PSK500RC3",
        "PSK500RC4",
        "PSK800C2",
        "PSK800RC2",
        "PSK1000",
        "PSK1000C2",
        "PSK1000R",
        "PSK1000RC2",
        "PSKAM10",
        "PSKAM31",
        "PSKAM50",
        "PSKFEC31",
        "QPSK31",
        "QPSK63",
        "QPSK125",
        "QPSK250",
        "QPSK500",
        "SIM31",
    ),
    "QRA64": ("QRA64A", "QRA64B", "QRA64C", "QRA64D", "QRA64E"),
    "ROS": ("ROS-EME", "ROS-HF", "ROS-MF"),
    "RTTY": ("ASCI",),
    "SSB": ("LSB", "USB"),
    "THOR": (
        "THOR-M",
        "THOR4",
        "THOR5",
        "THOR8",
        "THOR11",
        "THOR16",
        "THOR22",
        "THOR25X4",
        "THOR50X1",
        "THOR50X2",
        "THOR100",
    ),
    "THRB": ("THRBX", "THRBX1", "THRBX2", "THRBX4", "THROB1", "THROB2", "THROB4"),
    "TOR": ("AMTORFEC", "GTOR", "NAVTEX", "SITORB"),
}

# Mode or submode name -> the mode of its family ("USB" -> "SSB", "SSB" -> "SSB").
_FAMILY: dict[str, str] = {
    name: mode for mode, submodes in _VARIANT_SUBMODES.items() for name in (mode, *submodes)
}


def _clean(value: object) -> str:
    """Stripped, uppercased text; ``""`` for ``None``, blank text or a value that is not text."""
    return value.strip().upper() if isinstance(value, str) else ""


def display_mode(mode: str | None, submode: str | None) -> str:
    """Mode shown to the user: SUBMODE if present, else MODE, uppercased; ``""`` if neither.

    ADIF 3.1 logs FT4 as ``MODE=MFSK SUBMODE=FT4`` while older loggers write
    ``MODE=FT4``; both give ``"FT4"`` here. The duplicate key uses :func:`dedup_mode`.
    """
    for value in (submode, mode):
        if value and value.strip():
            return value.strip().upper()
    return ""


def dedup_mode(mode: str | None, submode: str | None) -> str:
    """Mode in the duplicate key: one value for every way loggers write the same contact.

    - A mode whose ADIF submodes are only variants of it (sideband, speed, tones) is one
      value: ``SSB``, ``SSB`` + ``USB``, ``SSB`` + ``LSB`` and the older ``MODE=USB`` all
      give ``"SSB"``; ``PSK``, ``PSK`` + ``PSK31`` and ``MODE=PSK31`` give ``"PSK"``;
      ``JT65`` + ``JT65B`` and ``MODE=JT65B`` give ``"JT65"``; also CW, RTTY, JT4, JT9,
      QRA64, OLIVIA, HELL, DOMINO, THOR and the other ADIF 3.1.7 modes with submodes. The
      family of ``MODE`` decides, whatever the submode (a sideband written as the submode of
      ``PSK`` or ``RTTY`` does not turn the contact into SSB); without ``MODE``, the family
      of ``SUBMODE``.
    - MFSK and DIGITALVOICE are umbrella modes whose submodes are modes of their own (FT4,
      JS8, Q65, FST4, C4FM, DMR, DSTAR), so these and every other mode give
      :func:`display_mode`: ``MFSK`` + ``FT4`` and ``MODE=FT4`` give ``"FT4"``, ``FT8``
      gives ``"FT8"``.

    Case and surrounding spaces do not matter; values that are not text count as missing;
    ``""`` when neither is given.
    """
    main, sub = _clean(mode), _clean(submode)
    if main:
        return _FAMILY.get(main) or sub or main
    return _FAMILY.get(sub, sub)
