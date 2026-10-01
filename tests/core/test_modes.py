"""Tests for hamq.core.modes: the mode shown to the user and the mode of the duplicate key.

The expected families were transcribed by hand from the ADIF 3.1.7 specification
(https://adif.org/317/ADIF_317.htm, Mode and Submode enumerations), independently of
the table in the module: every "import-only" mode (an older ADIF mode that is now a
submode) is listed below with the mode it belongs to.
"""

from __future__ import annotations

import pytest

from hamq.core import modes, rigmode
from hamq.core.modes import dedup_mode, display_mode

# ADIF 3.1.7 import-only modes -> the ADIF mode they are a submode of now.
IMPORT_ONLY = {
    "AMTORFEC": "TOR",
    "ASCI": "RTTY",
    "C4FM": "DIGITALVOICE",
    "CHIP64": "CHIP",
    "CHIP128": "CHIP",
    "DOMINOF": "DOMINO",
    "DSTAR": "DIGITALVOICE",
    "FMHELL": "HELL",
    "FSK31": "PSK",
    "GTOR": "TOR",
    "HELL80": "HELL",
    "HFSK": "HELL",
    "JT4A": "JT4",
    "JT4B": "JT4",
    "JT4C": "JT4",
    "JT4D": "JT4",
    "JT4E": "JT4",
    "JT4F": "JT4",
    "JT4G": "JT4",
    "JT65A": "JT65",
    "JT65B": "JT65",
    "JT65C": "JT65",
    "MFSK8": "MFSK",
    "MFSK16": "MFSK",
    "PAC2": "PAC",
    "PAC3": "PAC",
    "PAX2": "PAX",
    "PCW": "CW",
    "PSK10": "PSK",
    "PSK31": "PSK",
    "PSK63": "PSK",
    "PSK63F": "PSK",
    "PSK125": "PSK",
    "PSKAM10": "PSK",
    "PSKAM31": "PSK",
    "PSKAM50": "PSK",
    "PSKFEC31": "PSK",
    "PSKHELL": "HELL",
    "QPSK31": "PSK",
    "QPSK63": "PSK",
    "QPSK125": "PSK",
    "THRBX": "THRB",
}
UMBRELLA = frozenset({"MFSK", "DIGITALVOICE"})


@pytest.mark.parametrize(
    ("mode", "submode", "expected"),
    [
        ("FT8", None, "FT8"),
        ("ft8", "", "FT8"),
        ("MFSK", "FT4", "FT4"),
        ("SSB", "USB", "USB"),
        (" ssb ", None, "SSB"),
        (None, "JS8", "JS8"),
        (None, None, ""),
        ("  ", " ", ""),
    ],
)
def test_display_mode(mode, submode, expected):
    assert display_mode(mode, submode) == expected


@pytest.mark.parametrize(
    ("mode", "submode"),
    [
        # one SSB contact as different loggers write it
        ("SSB", None),  # LoTW, HamQ's radio mapping (USB -> SSB)
        ("SSB", "USB"),  # loggers that keep the sideband, HamQ's dialog with submode USB
        ("SSB", "LSB"),
        ("USB", None),  # older programs
        ("LSB", None),
        ("usb", ""),
        (" ssb ", " lsb "),
        (None, "USB"),
    ],
)
def test_every_way_of_writing_ssb_gives_ssb(mode, submode):
    assert dedup_mode(mode, submode) == "SSB"


@pytest.mark.parametrize(
    ("mode", "submode", "expected"),
    [
        ("PSK", None, "PSK"),  # HamQ's radio mapping of the Hamlib PSK mode
        ("PSK", "PSK31", "PSK"),  # ADIF 3
        ("PSK31", None, "PSK"),  # ADIF 2 (xlog)
        ("psk", "qpsk63", "PSK"),
        ("8PSK125", None, "PSK"),
        ("PSK", "BPSK31", "PSK"),  # an unknown submode of PSK is still PSK
        ("JT65", None, "JT65"),  # WSJT-X
        ("JT65", "JT65B", "JT65"),
        ("JT65B", None, "JT65"),
        ("JT65", "JT65C2", "JT65"),
        ("JT9", "JT9A", "JT9"),
        ("JT4F", None, "JT4"),
        ("QRA64", "QRA64B", "QRA64"),
        ("OLIVIA", "OLIVIA 8/250", "OLIVIA"),
        ("OLIVIA", "olivia 32/1000", "OLIVIA"),
        ("CW", "PCW", "CW"),
        ("PCW", None, "CW"),
        ("RTTY", "ASCI", "RTTY"),
        ("HELL", "FMHELL", "HELL"),
        ("FMHELL", None, "HELL"),
        ("DOMINO", "DOMINOEX", "DOMINO"),
        ("DYNAMIC", "VARA HF", "DYNAMIC"),
    ],
)
def test_variant_submodes_give_their_mode(mode, submode, expected):
    assert dedup_mode(mode, submode) == expected


@pytest.mark.parametrize(
    ("mode", "submode", "expected"),
    [
        ("MFSK", "FT4", "FT4"),  # WSJT-X, ADIF 3.1
        ("FT4", None, "FT4"),  # older loggers, LoTW fixture
        ("mfsk", "ft4", "FT4"),
        ("MFSK", "JS8", "JS8"),
        ("MFSK", "Q65", "Q65"),
        ("MFSK", "FST4", "FST4"),
        ("MFSK", "MFSK16", "MFSK16"),
        ("MFSK16", None, "MFSK16"),
        ("MFSK", None, "MFSK"),
        ("DIGITALVOICE", "C4FM", "C4FM"),
        ("C4FM", None, "C4FM"),
        ("DIGITALVOICE", "DSTAR", "DSTAR"),
        ("DSTAR", None, "DSTAR"),
        ("DIGITALVOICE", None, "DIGITALVOICE"),
    ],
)
def test_umbrella_modes_keep_the_submode(mode, submode, expected):
    assert dedup_mode(mode, submode) == expected == display_mode(mode, submode)


@pytest.mark.parametrize(
    ("mode", "submode", "expected"),
    [
        # the family of MODE decides: a sideband in SUBMODE does not make the contact SSB
        ("PSK", "USB", "PSK"),
        ("RTTY", "LSB", "RTTY"),
        ("CW", "USB", "CW"),
        ("SSB", "FT4", "SSB"),
        # MODE without a family: as display_mode, the SUBMODE's family is not used
        ("FT8", "USB", "USB"),
        ("DATA", "PSK31", "PSK31"),
        ("MFSK", "USB", "USB"),
        ("FOO", "BAR", "BAR"),
        ("FOO", None, "FOO"),
        # no MODE: the family of SUBMODE
        (None, "PSK31", "PSK"),
        ("", "LSB", "SSB"),
        (None, "FT4", "FT4"),
        (None, "BAR", "BAR"),
    ],
)
def test_mode_and_submode_that_do_not_belong_together(mode, submode, expected):
    assert dedup_mode(mode, submode) == expected


@pytest.mark.parametrize("value", [None, "", "   ", 42, 1.5, ("SSB",)])
def test_missing_or_non_text_values(value):
    assert dedup_mode(value, None) == ""
    assert dedup_mode(None, value) == ""
    assert dedup_mode(value, "USB") == "SSB"
    assert dedup_mode("FT8", value) == "FT8"


@pytest.mark.parametrize("mode", ["FT8", "FM", "AM", "MSK144", "SSTV", "WSPR", "FSK441", "PKT"])
def test_modes_without_submodes_are_unchanged(mode):
    assert dedup_mode(mode, None) == mode == display_mode(mode, None)
    assert dedup_mode(mode.lower(), "") == mode


@pytest.mark.parametrize(("legacy", "mode"), sorted(IMPORT_ONLY.items()))
def test_older_modes_dedup_like_their_adif3_form(legacy, mode):
    """``MODE=PSK31`` (ADIF 2) and ``MODE=PSK SUBMODE=PSK31`` (ADIF 3) are one contact."""
    assert dedup_mode(legacy, None) == dedup_mode(mode, legacy)
    expected = legacy if mode in UMBRELLA else mode
    assert dedup_mode(legacy, None) == expected
    if mode not in UMBRELLA:  # a logger that writes only the mode
        assert dedup_mode(mode, None) == expected


def test_table_is_consistent():
    table = modes._VARIANT_SUBMODES
    assert UMBRELLA.isdisjoint(table)
    names = [name for submodes in table.values() for name in submodes]
    assert len(names) == len(set(names))  # no submode in two modes
    assert set(names).isdisjoint(table)  # no submode is also a mode
    assert all(name == name.strip().upper() for name in [*names, *table])
    # ADIF 3.1.7: 26 modes with 197 submodes, without MFSK (19) and DIGITALVOICE (5)
    assert len(table) == 24 and len(names) == 173


@pytest.mark.parametrize(
    ("mode", "submode"),
    [(mode, submode) for mode, submodes in rigmode.SUBMODES.items() for submode in submodes],
)
def test_submodes_offered_by_the_qso_dialog(mode, submode):
    """Every submode of the manual QSO dialog dedups like the other loggers write it."""
    key = dedup_mode(mode, submode)
    assert dedup_mode(submode, None) == key  # older loggers: the submode as MODE
    if mode in UMBRELLA:
        assert key == submode
    else:
        assert key == mode == dedup_mode(mode, None)  # loggers that write only the mode


@pytest.mark.parametrize("rig_mode", ["USB", "LSB", "CW", "CWR", "AM", "FM", "RTTY", "PSK", "C4FM"])
def test_radio_modes_dedup_like_other_loggers(rig_mode):
    """A manual QSO with the mode from the radio matches the ADIF 3 form of that mode."""
    mode, submode = rigmode.adif_mode(rig_mode)
    expected = {"USB": "SSB", "LSB": "SSB", "CWR": "CW", "C4FM": "C4FM"}.get(rig_mode, rig_mode)
    assert dedup_mode(mode, submode) == expected
