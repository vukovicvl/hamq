"""HamQ translations: English source texts in Serbian, Latin or Cyrillic script.

Languages
---------
The interface language is :data:`LANG_EN` (the English source texts),
:data:`LANG_SR_LATN` (Serbian, Latin script) or :data:`LANG_SR_CYRL` (Serbian,
Cyrillic script). The stored setting may also be :data:`LANG_AUTO`, which
:func:`resolve_language` turns into one of the three from the QGIS or system
locale: ``sr``, ``sr_RS``, ``sr-Cyrl`` -> Cyrillic; ``sr@latin``, ``sr_Latn``,
``sr_Latn_RS``, ``sr-Latn``, ``sr_RS.UTF-8@latin`` and ``sr_ME`` (Qt and CLDR
write Serbian in Montenegro in Latin) -> Latin; anything else -> English.

Usage
-----
::

    from hamq.core.i18n import tr, tr_noop          # inside core: from .i18n import ...
    message = tr("Record {index}: missing CALL, skipped").format(index=i)   # format AFTER tr
    LABELS = {"total": tr_noop("Total QSOs")}       # marked now, tr(LABELS[key]) later

A text missing from the catalog falls back to the English source.

Catalogs
--------
Only Serbian Latin is stored: ``hamq/i18n/sr_Latn/<package>_<module>.json``, one
flat JSON object ``{"English source": "Serbian Latin"}`` per source module.
:func:`load_catalog` merges every ``*.json`` of a folder in file-name order.
Loading never raises: an unreadable or malformed file is skipped and the problem
is kept for :func:`load_problems` (the module translator's own load keeps its
problems apart, so it never replaces those of a :func:`load_catalog` call).
Serbian Cyrillic is derived from the Latin text by :func:`latin_to_cyrillic` and
cached per text.

The module translator (:func:`get_translator`, used by :func:`tr`) starts in
English and reads the catalogs lazily, on the first translation into Serbian.
It may be used from Processing worker threads: loading is guarded by a lock, a
language switch is a single attribute assignment and the Cyrillic cache only
ever gains identical values.

Transliteration
---------------
Serbian Latin maps one to one onto Serbian Cyrillic: the digraphs ``lj``,
``nj`` and ``dž`` in every case form (``Lj LJ lj``, ...) become ``љ њ џ``, ``đ``
becomes ``ђ``, ``č ć š ž`` become ``ч ћ ш ж`` and the other letters map one by
one. Technical text inside a translation stays in Latin script:

* ``{placeholders}`` (also ``{0}``, ``{count:,}``, ``{{call}}``), ``%s`` /
  ``%(name)s`` / ``%1`` / ``%n`` arguments, ``<html tags>``, ``<!-- comments -->``,
  ``&entities;``;
* URLs, e-mail addresses, paths (starting with ``/``, ``~/``, ``./`` or a drive,
  or containing ``\\``), file names and domains (a dot between letters:
  ``cty.dat``, ``www.qrz.com``), identifiers (``my_call``, ``hamq:import_adif``)
  and command-line arguments (``-m``, ``+f``);
* words with a digit (``FT8``, ``KN04ft``, ``20m``, ``EPSG:4326``), with two or
  more capital letters (``ADIF``, ``QSO``, ``QGIS``, ``WSJT-X``, ``GeoPackage``,
  ``HamQ``; Roman numerals like ``XX`` too), with ``q``, ``w``, ``x``, ``y`` or any
  other letter outside the Serbian alphabet (``Linux``, ``Müller``), and the words
  in :data:`PROTECTED_WORDS` (names such as ``Maidenhead`` or ``Hamlib``, unit
  symbols such as ``km``, ``MHz`` or ``kg``, keys such as ``Ctrl`` or ``Delete``);
* the unit symbols ``m``, ``s``, ``h`` and ``min`` where they are units: after a
  number or a value placeholder (``20 m``, ``1,5 h``, ``{seconds} s``, ``%d s``),
  alone in brackets (``(s)``, ``[min]``) and in units such as ``m/s`` or ``veza/h``.
  Elsewhere they are Serbian (``s njim`` -> ``с њим``, ``min. azimut`` ->
  ``мин. азимут``); after a number or a placeholder write the preposition as ``sa``.

Words joined by ``/`` or ``+`` are judged one by one: technical parts and one-letter
parts next to them stay (``YU1AB/P``, ``km/h``, ``TX/RX``, ``Ctrl+S``) and Serbian
words are transliterated (``stanica/QTH`` -> ``станица/QTH``, ``Ctrl+klik`` ->
``Ctrl+клик``, ``ulaza/izlaza`` -> ``улаза/излаза``). Single letters joined by ``+``
are a formula (``a+b``) and a lowercase token with a technical part and two or more
``/`` is a relative path (``python/plugins/hamq``): both stay whole.

Everything else is transliterated, including capitalized words (``Veza`` ->
``Веза``). In a compound joined by hyphens or ``&``, capitalized parts next to a
technical part belong to the name (``Latin-1``, ``Wi-Fi``) and lowercase parts are
Serbian case endings or words (``QGIS-u`` -> ``QGIS-у``, ``WSJT-X-a`` ->
``WSJT-X-а``, ``QSO-veza`` -> ``QSO-веза``). A case ending after a file name,
identifier or other technical token is Serbian too (``cty.dat-a`` -> ``cty.dat-а``,
``my_call-u`` -> ``my_call-у``): lowercase letters after the last hyphen that start
with a vowel or with ``j`` and a vowel, so ``hamq.gpkg-shm`` and ``python:3.9-slim``
stay whole. Write case endings of protected names after a hyphen (``Hamlib-a``);
glued to the name (``Hamliba``) the whole word is transliterated.

Converting a converted text changes nothing, and the time is linear in the length
of the text.

Known limitations (out of scope, they need a dictionary):

* a digraph pair that belongs to two morphemes is taken as a digraph:
  ``nadživeti`` -> ``наџивети`` (correct: ``надживети``), likewise ``podžupan``,
  ``injekcija`` and ``konjunkcija`` (correct: ``инјекција``, ``конјункција``);
* an all-capitals word without ``č ć đ š ž`` counts as an acronym and stays in
  Latin (``UPOZORENJE``); ``GREŠKA`` is transliterated. Write words in normal case;
* a relative path without a technical part or with a single ``/`` is read as
  words (``profil/podaci``); pass paths as placeholders (``{path}``);
* a vowel-initial word after a hyphen that follows a technical token is taken for a
  case ending (``v0.1.0-alfa`` -> ``v0.1.0-алфа``).
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from collections.abc import Callable, Iterator, Mapping

__all__ = [
    "LANGUAGES",
    "LANG_AUTO",
    "LANG_EN",
    "LANG_SR_CYRL",
    "LANG_SR_LATN",
    "PROTECTED_WORDS",
    "Translator",
    "current_language",
    "get_translator",
    "is_serbian",
    "language_name",
    "latin_to_cyrillic",
    "load_catalog",
    "load_problems",
    "resolve_language",
    "set_language",
    "tr",
    "tr_noop",
]

LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL = "auto", "en", "sr_Latn", "sr_Cyrl"

#: Interface languages as ``(code, native name)``. Native names are never translated:
#: a user looks for their own language written in it.
LANGUAGES: tuple[tuple[str, str], ...] = (
    (LANG_EN, "English"),
    (LANG_SR_LATN, "Srpski (latinica)"),
    (LANG_SR_CYRL, "Српски (ћирилица)"),
)


def tr_noop(text: str) -> str:
    """Mark ``text`` for translation without translating it now; returns ``text``.

    Use it for texts defined at import time (tables, labels) and translate them
    with ``tr(value)`` when they are shown, so they follow language switches.
    """
    return text


# Source texts of this module (catalog: hamq/i18n/sr_Latn/core_i18n.json).
_AUTO_NAME = tr_noop("Auto (QGIS language)")
_MSG_FOLDER = tr_noop("Translations could not be loaded from {path}: {error}")
_MSG_UNREADABLE = tr_noop("Translation file {file} could not be read and was skipped: {error}")
_MSG_NOT_OBJECT = tr_noop("Translation file {file} is not a JSON object and was skipped")
_MSG_BAD_ENTRY = tr_noop("Translation file {file}: the entry {text} is not text and was skipped")
_MSG_CONFLICT = tr_noop(
    "The translation of {text} differs between {first} and {second}; the one from {second} is used"
)

# --------------------------------------------------------------------------- languages

_CODES = {code.casefold(): code for code in (LANG_AUTO, LANG_EN, LANG_SR_LATN, LANG_SR_CYRL)}
_SERBIAN_CODES = (LANG_SR_LATN, LANG_SR_CYRL)
_SERBIAN_LANGUAGE_TAGS = frozenset({"sr", "srp"})  # ISO 639-1 and 639-2
# Territories where plain "sr" means Latin script (CLDR likely subtags; Qt reports
# QLocale("sr_ME").script() == LatinScript). Everywhere else plain "sr" is Cyrillic.
_LATIN_TERRITORIES = frozenset({"ME"})
_SUBTAG_SPLIT_RE = re.compile(r"[-_]")


def _language_code(value: object) -> str | None:
    """Canonical HamQ code of ``value`` (``' SR-LATN '`` -> ``'sr_Latn'``), else ``None``."""
    if not isinstance(value, str):
        return None
    return _CODES.get(value.strip().replace("-", "_").casefold())


def _serbian_from_locale(locale_name: object) -> str | None:
    """Serbian language code for a locale name, ``None`` when it is not Serbian.

    Accepts POSIX (``sr_RS.UTF-8@latin``), BCP 47 (``sr-Latn-RS``), Qt
    (``sr_Latn_RS``) and Windows (``Serbian (Latin)_Serbia.1250``) spellings. BCP 47
    extensions and private use subtags (``sr-RS-u-nu-latn``: Latin digits only) are
    ignored.
    """
    if not isinstance(locale_name, str):
        return None
    text = locale_name.strip()
    folded = text.casefold()
    if folded.startswith("serbian"):
        return LANG_SR_LATN if "latin" in folded else LANG_SR_CYRL
    base, _, modifier = text.partition("@")
    base = base.split(".", 1)[0]  # drop the code set: sr_RS.UTF-8
    modifier = modifier.split(".", 1)[0].casefold()
    subtags = [subtag for subtag in _SUBTAG_SPLIT_RE.split(base) if subtag]
    if not subtags or subtags[0].casefold() not in _SERBIAN_LANGUAGE_TAGS:
        return None
    script = ""
    territory = ""
    for subtag in subtags[1:]:
        if len(subtag) == 1:  # a singleton (-u-, -t-, -x-) starts extensions / private use
            break
        if len(subtag) == 4 and subtag.isalpha() and not script:
            script = subtag.casefold()
        elif len(subtag) == 2 and subtag.isalpha() and not territory:
            territory = subtag.upper()
    if not script:  # glibc / KDE modifiers: @latin, @ijekavianlatin, @cyrillic
        if "latin" in modifier or modifier == "latn":
            script = "latn"
        elif "cyrillic" in modifier or modifier == "cyrl":
            script = "cyrl"
    if script == "latn":
        return LANG_SR_LATN
    if script == "cyrl":
        return LANG_SR_CYRL
    return LANG_SR_LATN if territory in _LATIN_TERRITORIES else LANG_SR_CYRL


def resolve_language(setting: str | None, system_locale: str | None) -> str:
    """Return the interface language for a setting and a locale; never ``'auto'``.

    ``setting`` is the stored HamQ choice: ``'en'``, ``'sr_Latn'``, ``'sr_Cyrl'``
    (case and ``-`` / ``_`` do not matter) or ``'auto'``; ``None`` or an empty
    text means the default, ``'auto'``. Unknown values give ``'en'``. For
    ``'auto'`` the language comes from ``system_locale`` (the QGIS or system
    locale name): ``resolve_language("auto", "sr_RS")`` -> ``'sr_Cyrl'``,
    ``"sr@latin"`` / ``"sr_Latn"`` / ``"sr_Latn_RS"`` -> ``'sr_Latn'``, a
    non-Serbian or missing locale -> ``'en'``.
    """
    if setting is None or (isinstance(setting, str) and not setting.strip()):
        code = LANG_AUTO
    else:
        code = _language_code(setting) or LANG_EN
    if code != LANG_AUTO:
        return code
    return _serbian_from_locale(system_locale) or LANG_EN


def is_serbian(language: str) -> bool:
    """True for ``'sr_Latn'`` and ``'sr_Cyrl'`` (and Serbian locale names such as ``'sr_RS'``)."""
    code = _language_code(language)
    if code is not None:
        return code in _SERBIAN_CODES
    return _serbian_from_locale(language) is not None


def language_name(language: str) -> str:
    """Name to show for a language choice in menus and settings.

    The native name for ``'en'``, ``'sr_Latn'`` and ``'sr_Cyrl'`` (never
    translated), the translated "Auto (QGIS language)" for ``'auto'`` and
    ``language`` itself for an unknown code.
    """
    code = _language_code(language)
    if code == LANG_AUTO:
        return tr(_AUTO_NAME)
    for known, name in LANGUAGES:
        if known == code:
            return name
    return language


def _coerce_language(language: object) -> str:
    code = _language_code(language)
    return code if code in (LANG_EN, LANG_SR_LATN, LANG_SR_CYRL) else LANG_EN


# --------------------------------------------------------------------------- transliteration

#: Words kept in Latin script although nothing else marks them as technical: names
#: of programs and people written like ordinary words, unit symbols (SI symbols stay
#: Latin in Cyrillic text; ``m``, ``s``, ``h`` and ``min`` depend on the context, see
#: the module documentation), keyboard keys and network terms. Compared ignoring case.
PROTECTED_WORDS: frozenset[str] = frozenset(
    {
        # programs, libraries and frameworks
        "Maidenhead",
        "Hamlib",
        "Processing",
        "Python",
        "rigctld",
        "rotctld",
        "Linux",
        "Windows",
        "macOS",
        "Big",  # "Big CTY", the country-files.com edition
        # people and organisations credited in the About box
        "Jim",
        "Reisert",
        "Joe",
        "Taylor",
        "Development",
        "Group",
        # unit symbols
        "Hz",
        "kHz",
        "MHz",
        "GHz",
        "mm",
        "cm",
        "km",
        "kg",
        "dB",
        "dBm",
        "dBi",
        "dBd",
        "ms",
        "mW",
        "kW",
        # keyboard keys
        "Ctrl",
        "Shift",
        "Alt",
        "Enter",
        "Return",
        "Esc",
        "Tab",
        "Space",
        "Backspace",
        "Delete",
        "Del",
        "Insert",
        "Home",
        "End",
        # network terms
        "localhost",
        "multicast",
        "unicast",
        "broadcast",
    }
)
_PROTECTED_FOLDED = frozenset(word.casefold() for word in PROTECTED_WORDS)

_UPPER_LETTERS = {
    "A": "А",
    "B": "Б",
    "C": "Ц",
    "Č": "Ч",
    "Ć": "Ћ",
    "D": "Д",
    "Đ": "Ђ",
    "E": "Е",
    "F": "Ф",
    "G": "Г",
    "H": "Х",
    "I": "И",
    "J": "Ј",
    "K": "К",
    "L": "Л",
    "M": "М",
    "N": "Н",
    "O": "О",
    "P": "П",
    "R": "Р",
    "S": "С",
    "Š": "Ш",
    "T": "Т",
    "U": "У",
    "V": "В",
    "Z": "З",
    "Ž": "Ж",
}
_LETTERS = dict(_UPPER_LETTERS)
_LETTERS.update({latin.lower(): cyrillic.lower() for latin, cyrillic in _UPPER_LETTERS.items()})
_LETTERS.update(
    {
        # single code point digraphs (U+01C4..U+01CC) and U+00D0, often typed for Đ
        "\u01c4": "Џ",
        "\u01c5": "Џ",
        "\u01c6": "џ",
        "\u01c7": "Љ",
        "\u01c8": "Љ",
        "\u01c9": "љ",
        "\u01ca": "Њ",
        "\u01cb": "Њ",
        "\u01cc": "њ",
        "\u00d0": "Ђ",
    }
)
_LETTER_TABLE = str.maketrans(_LETTERS)

_DIGRAPHS: dict[str, str] = {}
for _first, _second, _cyrillic in (("l", "j", "Љ"), ("n", "j", "Њ"), ("d", "ž", "Џ")):
    for _a in (_first.upper(), _first):
        for _b in (_second.upper(), _second):
            _DIGRAPHS[_a + _b] = _cyrillic if _a.isupper() else _cyrillic.lower()
del _first, _second, _cyrillic, _a, _b
_DIGRAPH_RE = re.compile("|".join(_DIGRAPHS))

# Letters that make an all-capitals word Serbian rather than an acronym (NJEGOŠ).
_SERBIAN_SPECIAL = frozenset(
    "ČĆĐŠŽčćđšž\u01c4\u01c5\u01c6\u01c7\u01c8\u01c9\u01ca\u01cb\u01cc\u00d0"
)
_HAS_LATIN_RE = re.compile("[A-Za-z" + "".join(sorted(_SERBIAN_SPECIAL)) + "]")

# Spans copied unchanged, even when glued to words. An HTML comment is matched by its
# opening "<!--" only and its end is found with str.find: a pattern for the whole comment
# would rescan the rest of the text for every unclosed "<!--" (quadratic time).
_SPAN_RE = re.compile(
    r"""
      (?P<url>(?:[A-Za-z][A-Za-z0-9+.\-]{0,20}://|www\.)[^\s<>"'`]*)  # URL (short scheme)
    | (?P<comment><!--)                                     # HTML comment
    | (?P<tag></?[A-Za-z][^<>]*>)                           # HTML / XML tag
    | (?P<entity>&(?:[A-Za-z][A-Za-z0-9]*|\#[0-9]+|\#[xX][0-9A-Fa-f]+);)
    | (?P<text>\{\{[^{}\s]*\}\} | \{\{ | \}\})              # {{call}} shown as text, {{ and }}
    | (?P<field>\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})            # str.format field
    | (?P<printf>%(?:\([^()]*\))?[-+\#0]*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?[sdifFeEgGxXocru%])
    | (?P<qt>%L?[0-9]+ | %n)                                # Qt arguments
    """,
    re.VERBOSE,
)
# Spans that stand for a value, such as a number: a unit symbol may follow them.
_VALUE_SPANS = frozenset({"field", "printf", "qt"})
_SPACE_SPLIT_RE = re.compile(r"(\s+)")
# A chunk that is a path or a command-line argument: /usr, ~/x, ./x, C:\x, -m, --port, +f
_PATH_START_RE = re.compile(r"[~.]*[/\\]|[A-Za-z]:[/\\]")
_OPTION_START_RE = re.compile(r"(?:\+|--?)[^\W_]")
_OPENING_PUNCTUATION = "([{\"'„“”«»‘’‚"
# Characters that never occur inside an ordinary word (identifiers, formulas, markup).
# '+' is judged separately: Ctrl+klik has a Serbian word, a+b is a formula.
_TECHNICAL_CHARS = frozenset("_=#|*^$~<>@\\")
# A dot or colon between letters or digits: file names, domains, versions, EPSG:4326.
_JOINED_RE = re.compile(r"[^\W_][.:][^\W_]")
_WORD_RE = re.compile(r"[^\W_]+")
# A compound: words joined by hyphens, dashes, '&' (Qt accelerator) or apostrophes.
_JOINERS = re.escape("-\u2010\u2011\u2013\u2014&'\u2019")  # hyphens, dashes, &, apostrophes
_COMPOUND_RE = re.compile(r"[^\W_]+(?:[" + _JOINERS + r"]+[^\W_]+)*")
_JOINER_SPLIT_RE = re.compile("([" + _JOINERS + "]+)")
# Words joined by '/' or '+': km/h, YU1AB/P, ulaza/izlaza, Ctrl+klik.
_SLASH_PLUS_SPLIT_RE = re.compile("([/+])")
# A Serbian case ending after a hyphen: lowercase Serbian letters that start with a vowel
# or with j and a vowel (-a, -u, -om, -ima, -ova, -ja, -jem). Technical tails such as
# -shm, -slim, -dev or -wal do not match. Cyrillic endings match too, so that converting
# a converted text changes nothing.
_HYPHENS = "-\u2010\u2011"
_SERBIAN_LOWER = "".join(
    sorted(
        {letter for letter in _LETTERS if letter.islower()}
        | {letter for letter in _LETTERS.values() if letter.islower()}
        | {letter for letter in _DIGRAPHS.values() if letter.islower()}
    )
)
_ENDING_RE = re.compile(
    "(?:[aeiou\u0430\u0435\u0438\u043e\u0443]|[j\u0458][aeiou\u0430\u0435\u0438\u043e\u0443])"
    "[" + _SERBIAN_LOWER + "]*"
)
# Unit symbols that are also Serbian words or letters (s is a preposition). They stay in
# Latin only where they are units: after a number or a value placeholder, alone in
# brackets or in a unit such as m/s. Other unit symbols are in PROTECTED_WORDS.
_CONTEXT_UNITS = frozenset({"m", "s", "h", "min"})
_NUMBER_RE = re.compile("[-+\u2212\u00b1]?[0-9]+(?:[.,][0-9]+)*")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _is_foreign_letter(char: str) -> bool:
    """A Latin letter outside the Serbian alphabet: q w x y, é, ü, ß, ..."""
    if char in _LETTERS:
        return False
    if char.isascii():
        return char.isalpha()
    return unicodedata.name(char, "").startswith("LATIN ")


def _is_protected_piece(piece: str) -> bool:
    """True when a run of letters and digits must stay in Latin script."""
    if piece.casefold() in _PROTECTED_FOLDED:
        return True
    if any(char.isdigit() or _is_foreign_letter(char) for char in piece):
        return True
    capitals = sum(1 for char in piece if char.isupper())
    return capitals >= 2 and not any(char in _SERBIAN_SPECIAL for char in piece)


def _transliterate(word: str) -> str:
    return _DIGRAPH_RE.sub(lambda match: _DIGRAPHS[match.group()], word).translate(_LETTER_TABLE)


def _convert_compound(match: re.Match[str]) -> str:
    """Transliterate the Serbian parts of a compound such as ``WSJT-X-a`` or ``radio-amater``."""
    parts = _JOINER_SPLIT_RE.split(match.group())
    technical = any(_is_protected_piece(word) for word in parts[0::2])
    for index in range(0, len(parts), 2):
        word = parts[index]
        # Next to a technical part, capitalized parts belong to the name (Latin-1, Wi-Fi,
        # AT&T) and lowercase parts are Serbian: case endings (QGIS-u) or words (QSO-veza).
        if not (_is_protected_piece(word) or (technical and word != word.lower())):
            parts[index] = _transliterate(word)
    return "".join(parts)


def _is_technical(text: str) -> bool:
    """True for a token kept whole: identifiers, markup, file names, domains, versions."""
    return any(char in _TECHNICAL_CHARS for char in text) or _JOINED_RE.search(text) is not None


def _split_ending(core: str) -> tuple[str, str, str] | None:
    """Split a Serbian case ending off a technical token: 'cty.dat-a' -> ('cty.dat', '-', 'a').

    ``None`` when ``core`` does not end in a hyphen and a case ending, or when the part
    before it is an ordinary word or compound (the compound rule handles ``QGIS-u``).
    """
    index = max(core.rfind(hyphen) for hyphen in _HYPHENS)
    if index <= 0 or not _is_word_char(core[index - 1]):
        return None
    ending = unicodedata.normalize("NFC", core[index + 1 :])
    if not _ENDING_RE.fullmatch(ending):
        return None
    stem = unicodedata.normalize("NFC", core[:index])
    if _is_technical(stem) or "/" in stem or "+" in stem:
        return core[:index], core[index], ending
    return None


def _is_unit_or_technical(word: str) -> bool:
    return word in _CONTEXT_UNITS or (
        _WORD_RE.fullmatch(word) is not None and _is_protected_piece(word)
    )


def _convert_slash_plus(text: str) -> str:
    """Convert words joined by '/' or '+', one by one.

    Technical parts, one-letter parts next to them and units after '/' stay (YU1AB/P,
    km/h, m/s, TX/RX, Ctrl+S, veza/h); Serbian words are transliterated (stanica/QTH,
    Ctrl+klik, ulaza/izlaza). Single letters joined by '+' are a formula (a+b) and a
    lowercase token with a technical part and two or more '/' is a relative path
    (python/plugins/hamq): both stay whole.
    """
    parts = _SLASH_PLUS_SPLIT_RE.split(text)
    words = parts[0::2]
    if all(_is_unit_or_technical(word) for word in words):
        return text
    technical = any(
        _is_protected_piece(piece) for word in words for piece in _WORD_RE.findall(word)
    )
    if "+" in text:
        if not technical and all(len(word) <= 1 for word in words):
            return text
    elif technical and text.count("/") >= 2 and text == text.lower():
        return text
    for index in range(0, len(parts), 2):
        word = parts[index]
        if technical and len(word) == 1:
            continue
        if index > 0 and parts[index - 1] == "/" and word in _CONTEXT_UNITS:
            continue
        parts[index] = _COMPOUND_RE.sub(_convert_compound, word)
    return "".join(parts)


def _convert_core(core: str) -> str:
    """Convert a chunk without its surrounding punctuation; return it unchanged if technical."""
    text = unicodedata.normalize("NFC", core)
    if text.casefold() in _PROTECTED_FOLDED:
        return core
    ending = _split_ending(core)
    if ending is not None:  # cty.dat-a, my_call-u: the token stays, the ending is Serbian
        stem, hyphen, suffix = ending
        return _convert_core(stem) + hyphen + _transliterate(suffix)
    if _is_technical(text):
        return core
    if "/" in text or "+" in text:
        converted = _convert_slash_plus(text)
        return core if converted == text else converted
    return _COMPOUND_RE.sub(_convert_compound, text)


def _convert_chunk(chunk: str, glued: bool, after_value: bool) -> str:
    """Convert a run of non-space text.

    ``glued`` when it directly follows a protected span, ``after_value`` when a number or a
    value placeholder (``{count}``, ``%d``) comes right before it.
    """
    if not _HAS_LATIN_RE.search(chunk):
        return chunk
    if "@" in chunk or "\\" in chunk or "://" in chunk:
        return chunk
    if not glued:
        bare = chunk.lstrip(_OPENING_PUNCTUATION)
        if _PATH_START_RE.match(bare) or _OPTION_START_RE.match(bare):
            return chunk
    start, end = 0, len(chunk)
    while start < end and not _is_word_char(chunk[start]):
        start += 1
    while end > start and not _is_word_char(chunk[end - 1]):
        end -= 1
    if start == end:
        return chunk
    core = chunk[start:end]
    if core in _CONTEXT_UNITS and (
        after_value or (chunk[:start].endswith(("(", "[")) and chunk[end:].startswith((")", "]")))
    ):
        return chunk  # 5 s, {seconds} s, (m): a unit symbol
    return chunk[:start] + _convert_core(core) + chunk[end:]


def _convert_plain(segment: str, after_span: bool, after_value: bool) -> str:
    pieces = _SPACE_SPLIT_RE.split(segment)
    out = []
    for index, piece in enumerate(pieces):
        if not piece or piece.isspace():
            out.append(piece)
            continue
        out.append(_convert_chunk(piece, after_span and index == 0, after_value))
        after_value = _NUMBER_RE.fullmatch(piece.lstrip(_OPENING_PUNCTUATION)) is not None
    return "".join(out)


def _protected_spans(text: str) -> Iterator[tuple[int, int, bool]]:
    """``(start, end, is_value)`` of the spans of ``text`` that are copied unchanged."""
    position = 0
    comments = True  # False after an unclosed "<!--": no later comment can close either
    while True:
        match = _SPAN_RE.search(text, position)
        if match is None:
            return
        start, end = match.span()
        kind = match.lastgroup
        if kind == "comment":
            close = text.find("-->", end) if comments else -1
            if close < 0:  # not a comment: "<!--" stays in the text
                comments = False
                position = end
                continue
            end = close + 3
        yield start, end, kind in _VALUE_SPANS and match.group() != "%%"
        position = end


def latin_to_cyrillic(text: str) -> str:
    """Transliterate Serbian Latin ``text`` to Serbian Cyrillic.

    Placeholders, markup, URLs, e-mail addresses, paths, file names and technical
    tokens stay unchanged (see the module documentation for the exact rules and
    the known limitations). ``text`` that is not a ``str`` is returned as it is.
    Runs in time linear in the length of ``text``.
    """
    if not isinstance(text, str) or not _HAS_LATIN_RE.search(text):
        return text
    out: list[str] = []
    position = 0
    after_value = False
    for start, end, value in _protected_spans(text):
        if start > position:
            out.append(_convert_plain(text[position:start], position > 0, after_value))
        out.append(text[start:end])
        position = end
        after_value = value
    if position < len(text):
        out.append(_convert_plain(text[position:], position > 0, after_value))
    return "".join(out)


# --------------------------------------------------------------------------- catalogs

# Load problems as (English template, parameters), translated only when read. They are
# kept per source: the latest load_catalog() call (None: no call yet) and the module
# translator's own lazy load of the plugin catalogs (None: not loaded yet), so that the
# lazy load, which translating the messages may trigger, never replaces a call's problems.
_problems: list[tuple[str, dict[str, str]]] | None = None
_translator_problems: list[tuple[str, dict[str, str]]] | None = None
_problems_lock = threading.Lock()


def _format(template: str, params: Mapping[str, str]) -> str:
    try:
        return tr(template).format(**params)
    except (KeyError, IndexError, ValueError):  # a broken translation: use the source text
        return template.format(**params)


def load_problems() -> list[str]:
    """Problems found while loading translation catalogs, translated now.

    These are the problems of the latest :func:`load_catalog` call. While no such call
    was made, they are those of the module translator's own load of
    ``hamq/i18n/sr_Latn/``, which happens lazily on its first translation into
    Serbian: the plugin logs them after applying the language. That lazy load never
    replaces the problems of a :func:`load_catalog` call, so reading the problems (which
    may trigger it) returns the same list every time. Each skipped file, skipped entry or
    conflicting translation gives one message; the list is empty after a clean load.
    """
    with _problems_lock:
        recorded = list((_problems if _problems is not None else _translator_problems) or ())
    return [_format(template, params) for template, params in recorded]


def _shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _read_catalog_file(
    path: str, name: str, problems: list[tuple[str, dict[str, str]]]
) -> dict[str, str] | None:
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8-sig"))
    except Exception as exc:  # OSError, UnicodeDecodeError, JSONDecodeError, RecursionError
        problems.append((_MSG_UNREADABLE, {"file": name, "error": str(exc)}))
        return None
    if not isinstance(data, dict):
        problems.append((_MSG_NOT_OBJECT, {"file": name}))
        return None
    entries: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            problems.append((_MSG_BAD_ENTRY, {"file": name, "text": _shorten(key)}))
        elif key and value.strip():  # an empty value means "not translated yet"
            entries[key] = value
    return entries


def _catalog_file_names(directory: str | os.PathLike) -> list[str]:
    """Names of the catalog files of ``directory`` in load order.

    Regular files ending in ``.json`` in any case, hidden files excluded, sorted by
    name. The catalog guard test selects its files with this function too. Raises
    ``OSError``, ``TypeError`` or ``ValueError`` for a missing or invalid directory.
    """
    folder = os.fspath(directory)
    return sorted(
        name
        for name in os.listdir(folder)
        if name.lower().endswith(".json")
        and not name.startswith(".")
        and os.path.isfile(os.path.join(folder, name))
    )


def _read_catalogs(
    directory: str | os.PathLike,
) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """The merged catalog of ``directory`` and the problems found; never raises."""
    problems: list[tuple[str, dict[str, str]]] = []
    catalog: dict[str, str] = {}
    origin: dict[str, str] = {}
    try:
        folder = os.fspath(directory)
        names = _catalog_file_names(folder)
    except (OSError, TypeError, ValueError) as exc:
        problems.append((_MSG_FOLDER, {"path": str(directory), "error": str(exc)}))
        return catalog, problems
    for name in names:
        entries = _read_catalog_file(os.path.join(folder, name), name, problems)
        for key, value in (entries or {}).items():
            previous = catalog.get(key)
            if previous is not None and previous != value:
                problems.append(
                    (_MSG_CONFLICT, {"text": _shorten(key), "first": origin[key], "second": name})
                )
            catalog[key] = value
            origin[key] = name
    return catalog, problems


def load_catalog(directory: str | os.PathLike) -> dict[str, str]:
    """Merge every ``*.json`` catalog of ``directory`` into one ``{source: translation}``.

    Files are read in name order; hidden files and anything that is not a
    ``.json`` file (in any case) are ignored. A file that cannot be read, is not
    valid UTF-8 JSON or is not a JSON object is skipped; entries whose value is not
    text are skipped; empty translations are left out (the English source is shown).
    When two files translate the same text differently, the later file wins. Nothing
    raises: problems are kept for :func:`load_problems`.
    """
    global _problems
    catalog, problems = _read_catalogs(directory)
    with _problems_lock:
        _problems = problems
    return catalog


# --------------------------------------------------------------------------- translator


class Translator:
    """Translate English source texts into the current language.

    ``catalog`` maps English sources to Serbian Latin. Instead of a catalog, a
    ``loader`` (keyword only) may supply it: it is called once, lazily, on the
    first translation into Serbian. English returns the text itself; Serbian
    Latin returns the catalog value or the source when missing; Serbian Cyrillic
    transliterates the catalog value (cached) or returns the English source
    unchanged when missing. Safe for concurrent use from several threads.
    """

    def __init__(
        self,
        catalog: dict[str, str] | None = None,
        language: str = LANG_EN,
        *,
        loader: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._loader = loader
        self._catalog: dict[str, str] | None
        if catalog is not None:
            self._catalog = dict(catalog)
        else:
            self._catalog = None if loader is not None else {}
        self._cyrillic: dict[str, str] = {}
        self._language = _coerce_language(language)

    @property
    def language(self) -> str:
        """Current language: ``'en'``, ``'sr_Latn'`` or ``'sr_Cyrl'``."""
        return self._language

    def set_language(self, language: str) -> None:
        """Switch to ``'en'``, ``'sr_Latn'`` or ``'sr_Cyrl'``; anything else selects English.

        Resolve ``'auto'`` with :func:`resolve_language` first.
        """
        self._language = _coerce_language(language)

    def translate(self, text: str) -> str:
        """Return ``text`` in the current language (the English source when not translated)."""
        language = self._language
        if language == LANG_EN or not isinstance(text, str):
            return text
        if language == LANG_SR_CYRL:
            cached = self._cyrillic.get(text)
            if cached is not None:
                return cached
        latin = self._loaded_catalog().get(text)
        if not isinstance(latin, str) or not latin:
            return text
        if language == LANG_SR_LATN:
            return latin
        cyrillic = latin_to_cyrillic(latin)
        self._cyrillic[text] = cyrillic
        return cyrillic

    def _loaded_catalog(self) -> dict[str, str]:
        catalog = self._catalog
        if catalog is None:
            with self._lock:
                catalog = self._catalog
                if catalog is None:
                    catalog = self._load()
                    self._catalog = catalog
        return catalog

    def _load(self) -> dict[str, str]:
        try:
            loaded = self._loader() if self._loader is not None else {}
            return {key: value for key, value in dict(loaded).items() if isinstance(key, str)}
        except Exception:  # a broken loader must not break every tr() call
            return {}


_CATALOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n", LANG_SR_LATN
)
_translator: Translator | None = None
_translator_lock = threading.Lock()


def _load_plugin_catalog() -> dict[str, str]:
    """Loader of the module translator; its problems never replace a load_catalog() call's."""
    global _translator_problems
    catalog, problems = _read_catalogs(_CATALOG_DIR)
    with _problems_lock:
        _translator_problems = problems
    return catalog


def get_translator() -> Translator:
    """The module-wide :class:`Translator`; it reads ``hamq/i18n/sr_Latn/`` lazily."""
    global _translator
    translator = _translator
    if translator is None:
        with _translator_lock:
            translator = _translator
            if translator is None:
                translator = Translator(loader=_load_plugin_catalog)
                _translator = translator
    return translator


def set_language(language: str) -> None:
    """Set the plugin language (``'en'``, ``'sr_Latn'`` or ``'sr_Cyrl'``)."""
    get_translator().set_language(language)


def current_language() -> str:
    """The plugin language: ``'en'``, ``'sr_Latn'`` or ``'sr_Cyrl'``."""
    return get_translator().language


def tr(text: str) -> str:
    """Translate ``text`` (an English source literal) into the plugin language."""
    return get_translator().translate(text)
