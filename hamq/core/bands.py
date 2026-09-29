"""Amateur radio bands: ADIF 3.1 band enumeration, derivation from frequency, ordering."""

from __future__ import annotations

# (band, lower MHz, upper MHz) in frequency order, per the ADIF 3.1 "Band" enumeration.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("2190m", 0.1357, 0.1378),
    ("630m", 0.472, 0.479),
    ("560m", 0.501, 0.504),
    ("160m", 1.8, 2.0),
    ("80m", 3.5, 4.0),
    ("60m", 5.06, 5.45),
    ("40m", 7.0, 7.3),
    ("30m", 10.1, 10.15),
    ("20m", 14.0, 14.35),
    ("17m", 18.068, 18.168),
    ("15m", 21.0, 21.45),
    ("12m", 24.89, 24.99),
    ("10m", 28.0, 29.7),
    ("8m", 40.0, 45.0),
    ("6m", 50.0, 54.0),
    ("5m", 54.000001, 69.9),
    ("4m", 70.0, 71.0),
    ("2m", 144.0, 148.0),
    ("1.25m", 222.0, 225.0),
    ("70cm", 420.0, 450.0),
    ("33cm", 902.0, 928.0),
    ("23cm", 1240.0, 1300.0),
    ("13cm", 2300.0, 2450.0),
    ("9cm", 3300.0, 3500.0),
    ("6cm", 5650.0, 5925.0),
    ("3cm", 10000.0, 10500.0),
    ("1.25cm", 24000.0, 24250.0),
    ("6mm", 47000.0, 47200.0),
    ("4mm", 75500.0, 81000.0),
    ("2.5mm", 119980.0, 123000.0),
    ("2mm", 134000.0, 149000.0),
    ("1mm", 241000.0, 250000.0),
    ("submm", 300000.0, 7500000.0),
)

BAND_ORDER: tuple[str, ...] = tuple(name for name, _, _ in BANDS)
_BAND_INDEX = {name: i for i, name in enumerate(BAND_ORDER)}


def band_from_freq(freq_mhz: float | None) -> str | None:
    """Return the ADIF band for a frequency in MHz, or ``None`` if it is outside every band."""
    if freq_mhz is None:
        return None
    for name, low, high in BANDS:
        if low <= freq_mhz <= high:
            return name
    return None


def normalize_band(band: str | None) -> str | None:
    """Normalize a band name: ``' 20M '`` -> ``'20m'``. Empty input gives ``None``."""
    if band is None:
        return None
    value = band.strip().lower()
    return value or None


def band_sort_key(band: str | None) -> tuple[int, str]:
    """Sort key: known bands in frequency order, then unknown bands alphabetically."""
    value = normalize_band(band) or ""
    return (_BAND_INDEX.get(value, len(BAND_ORDER)), value)
