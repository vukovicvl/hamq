"""Operating mode helpers."""

from __future__ import annotations


def display_mode(mode: str | None, submode: str | None) -> str:
    """Mode shown to the user and used for dedup: SUBMODE if present, else MODE.

    ADIF 3.1 logs FT4 as ``MODE=MFSK SUBMODE=FT4`` while older loggers write
    ``MODE=FT4``; both give ``"FT4"`` here.
    """
    for value in (submode, mode):
        if value and value.strip():
            return value.strip().upper()
    return ""
