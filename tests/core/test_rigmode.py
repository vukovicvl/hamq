"""hamq.core.rigmode: Hamlib radio modes -> ADIF modes, submodes and default reports."""

from __future__ import annotations

import pytest

from hamq.core import hamlib
from hamq.core.modes import display_mode
from hamq.core.rigmode import (
    ADIF_MODES,
    DATA_RIG_MODES,
    MODE_CHOICES,
    SUBMODE_PARENTS,
    SUBMODES,
    adif_mode,
    default_rst,
    is_data_mode,
    normalize_rig_mode,
    split_mode,
)


@pytest.mark.parametrize(
    ("rig_mode", "expected"),
    [
        ("USB", ("SSB", "")),
        ("LSB", ("SSB", "")),
        ("CW", ("CW", "")),
        ("CWR", ("CW", "")),
        ("CWN", ("CW", "")),
        ("AM", ("AM", "")),
        ("SAM", ("AM", "")),
        ("FM", ("FM", "")),
        ("FMN", ("FM", "")),
        ("WFM", ("FM", "")),
        ("RTTY", ("RTTY", "")),
        ("RTTYR", ("RTTY", "")),
        ("PSK", ("PSK", "")),
        ("C4FM", ("DIGITALVOICE", "C4FM")),
        ("D-STAR", ("DIGITALVOICE", "DSTAR")),
        ("P25", ("DIGITALVOICE", "")),
    ],
)
def test_adif_mode_of_rig_modes(rig_mode, expected):
    assert adif_mode(rig_mode) == expected


@pytest.mark.parametrize("rig_mode", ["PKTUSB", "PKTLSB", "PKTFM", "PKTAM", "PKTFMN"])
def test_data_modes_leave_the_choice_to_the_operator(rig_mode):
    assert adif_mode(rig_mode) is None
    assert is_data_mode(rig_mode)
    assert rig_mode in DATA_RIG_MODES


@pytest.mark.parametrize(
    "rig_mode", ["", "   ", None, "IQ", "SPEC", "DSB", "ECSSUSB", "ISBLSB", "BOGUS", 14, b"USB"]
)
def test_modes_without_an_adif_equivalent_give_none(rig_mode):
    assert adif_mode(rig_mode) is None
    assert not is_data_mode(rig_mode)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" usb ", ("SSB", "")),
        ("cw-r", ("CW", "")),  # Hamlib 4.6 spelling of CWR
        ("RTTY-R", ("RTTY", "")),
        ("DSTAR", ("DIGITALVOICE", "DSTAR")),
    ],
)
def test_case_spaces_and_hamlib_aliases(raw, expected):
    assert adif_mode(raw) == expected


@pytest.mark.parametrize("alias", ["FM-D", "fm-d", "AM-D", "USB-D", "LSB-D"])
def test_hamlib_data_aliases_are_data_modes(alias):
    assert is_data_mode(alias)
    assert adif_mode(alias) is None


def test_normalize_rig_mode():
    assert normalize_rig_mode(" fm-d ") == "PKTFM"
    assert normalize_rig_mode("usb") == "USB"
    assert normalize_rig_mode(None) == ""
    assert normalize_rig_mode(12) == ""


def test_every_hamlib_mode_is_mapped_or_a_deliberate_choice():
    # A new Hamlib mode must be classified on purpose: mapped, data, or no equivalent.
    no_equivalent = {"DSB", "ECSSUSB", "ECSSLSB", "SPEC", "IQ", "ISBUSB", "ISBLSB"}
    for mode in hamlib.MODES:
        mapped = adif_mode(mode)
        if mode in DATA_RIG_MODES:
            assert mapped is None, mode
        elif mapped is None:
            assert mode in no_equivalent, mode
        else:
            assert mapped[0] in ADIF_MODES, mode
            assert not mapped[1] or SUBMODE_PARENTS[mapped[1]] == mapped[0], mode


def test_rig_ssb_is_logged_as_plain_ssb():
    # display_mode (statistics, dedup) must show SSB, as for SSB QSOs from other loggers
    assert display_mode(*adif_mode("USB")) == "SSB"
    assert display_mode(*adif_mode("LSB")) == "SSB"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FT8", ("FT8", "")),
        (" ft8 ", ("FT8", "")),
        ("FT4", ("MFSK", "FT4")),
        ("ft4", ("MFSK", "FT4")),
        ("JS8", ("MFSK", "JS8")),
        ("Q65", ("MFSK", "Q65")),
        ("PSK31", ("PSK", "PSK31")),
        ("C4FM", ("DIGITALVOICE", "C4FM")),
        ("USB", ("SSB", "USB")),
        ("SSB", ("SSB", "")),
        ("MFSK", ("MFSK", "")),
        ("olivia 8/250", ("OLIVIA", "OLIVIA 8/250")),
        ("", ("", "")),
        ("  ", ("", "")),
        (None, ("", "")),
        (42, ("", "")),
    ],
)
def test_split_mode(text, expected):
    assert split_mode(text) == expected


def test_submode_parents_cover_every_submode():
    for mode, submodes in SUBMODES.items():
        assert mode in ADIF_MODES
        for submode in submodes:
            assert SUBMODE_PARENTS[submode] == mode
            assert split_mode(submode) == (mode, submode)


def test_mode_choices_split_into_known_modes():
    assert len(set(MODE_CHOICES)) == len(MODE_CHOICES)
    for choice in MODE_CHOICES:
        mode, submode = split_mode(choice)
        assert mode in ADIF_MODES, choice
        assert not submode or submode in SUBMODES[mode], choice
    # the dialog offers every common ADIF mode
    assert set(ADIF_MODES) <= {split_mode(choice)[0] for choice in MODE_CHOICES}


@pytest.mark.parametrize(
    ("mode", "submode", "expected"),
    [
        ("SSB", None, "59"),
        ("SSB", "USB", "59"),
        ("AM", "", "59"),
        ("FM", None, "59"),
        ("DIGITALVOICE", "DMR", "59"),
        ("C4FM", None, "59"),
        ("CW", None, "599"),
        ("RTTY", None, "599"),
        ("PSK", "PSK31", "599"),
        ("PSK31", None, "599"),
        ("OLIVIA", None, "599"),
        ("MFSK", None, "599"),  # keyboard MFSK16 & co.
        ("SSTV", None, "599"),
        ("FT8", None, "-10"),
        ("ft8", None, "-10"),
        ("FT4", None, "-10"),
        ("MFSK", "FT4", "-10"),
        ("MFSK", "q65", "-10"),
        ("JT65", "JT65B", "-10"),
        ("JT9", None, "-10"),
        ("MSK144", None, "-10"),
        ("", None, ""),
        (None, None, ""),
        ("  ", "  ", ""),
        ("", "FT4", "-10"),
    ],
)
def test_default_rst(mode, submode, expected):
    assert default_rst(mode, submode) == expected


def test_submode_decides_before_the_mode():
    # an explicit submode wins over what the mode text implies
    assert default_rst("SSB", "FT4") == "-10"
    assert default_rst("FT8", "USB") == "59"
