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
is kept for :func:`load_problems`. Serbian Cyrillic is derived from the Latin
text by :func:`latin_to_cyrillic` and cached per text.

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
  ``%(name)s`` / ``%1`` / ``%n`` arguments, ``<html tags>``, ``&entities;``;
* URLs, e-mail addresses, paths (starting with ``/``, ``~/``, ``./`` or a drive,
  or containing ``\\``), file names and domains (a dot between letters:
  ``cty.dat``, ``www.qrz.com``), identifiers (``my_call``, ``hamq:import_adif``)
  and command-line arguments (``-m``, ``+f``);
* words with a digit (``FT8``, ``KN04ft``, ``20m``, ``EPSG:4326``), with two or
  more capital letters (``ADIF``, ``QSO``, ``QGIS``, ``WSJT-X``, ``GeoPackage``,
  ``HamQ``; Roman numerals like ``XX`` too), with ``q``, ``w``, ``x``, ``y`` or any
  other letter outside the Serbian alphabet (``Linux``, ``Müller``), and the words
  in :data:`PROTECTED_WORDS` (names such as ``Maidenhead`` or ``Hamlib``, unit
  symbols such as ``km`` or ``MHz``);
* words joined by ``/`` when one of them is technical (``YU1AB/P``, ``km/h``);
  plain words stay words (``ulaza/izlaza`` -> ``улаза/излаза``).

Everything else is transliterated, including capitalized words (``Veza`` ->
``Веза``). In a compound joined by hyphens or ``&``, capitalized parts next to a
technical part belong to the name (``Latin-1``, ``Wi-Fi``) and lowercase parts are
Serbian case endings or words (``QGIS-u`` -> ``QGIS-у``, ``WSJT-X-a`` ->
``WSJT-X-а``, ``QSO-veza`` -> ``QSO-веза``). Write case endings of protected
names after a hyphen (``Hamlib-a``); glued to the name (``Hamliba``) the whole
word is transliterated.

Known limitations (out of scope, they need a dictionary):

* a digraph pair that belongs to two morphemes is taken as a digraph:
  ``nadživeti`` -> ``наџивети`` (correct: ``надживети``), likewise ``podžupan``,
  ``injekcija`` and ``konjunkcija`` (correct: ``инјекција``, ``конјункција``);
* an all-capitals word without ``č ć đ š ž`` counts as an acronym and stays in
  Latin (``UPOZORENJE``); ``GREŠKA`` is transliterated. Write words in normal case.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from collections.abc import Callable, Mapping

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
    (``sr_Latn_RS``) and Windows (``Serbian (Latin)_Serbia.1250``) spellings.
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
#: Latin in Cyrillic text), keyboard keys and network terms. Compared ignoring case.
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
        "km",
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
        "Esc",
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

# Spans copied unchanged, even when glued to words.
_SPAN_RE = re.compile(
    r"""
      (?:[A-Za-z][A-Za-z0-9+.\-]{0,20}://|www\.)[^\s<>"'`]*  # URL (short scheme: no backtracking)
    | <!--.*?-->                                            # HTML comment
    | </?[A-Za-z][^<>]*>                                    # HTML / XML tag
    | &(?:[A-Za-z][A-Za-z0-9]*|\#[0-9]+|\#[xX][0-9A-Fa-f]+);  # entity
    | \{\{[^{}\s]*\}\}                                      # {{call}}: a field shown as text
    | \{\{ | \}\}                                           # escaped brace (str.format)
    | \{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}                       # str.format field
    | %(?:\([^()]*\))?[-+\#0]*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?[sdifFeEgGxXocru%]  # printf
    | %L?[0-9]+ | %n                                        # Qt arguments
    """,
    re.VERBOSE | re.DOTALL,
)
_SPACE_SPLIT_RE = re.compile(r"(\s+)")
# A chunk that is a path or a command-line argument: /usr, ~/x, ./x, C:\x, -m, --port, +f
_PATH_START_RE = re.compile(r"[~.]*[/\\]|[A-Za-z]:[/\\]")
_OPTION_START_RE = re.compile(r"(?:\+|--?)[^\W_]")
_OPENING_PUNCTUATION = "([{\"'„“”«»‘’‚"
# Characters that never occur inside an ordinary word (identifiers, formulas, markup).
_TECHNICAL_CHARS = frozenset("_=#|*^$~+<>@\\")
# A dot or colon between letters or digits: file names, domains, versions, EPSG:4326.
_JOINED_RE = re.compile(r"[^\W_][.:][^\W_]")
_WORD_RE = re.compile(r"[^\W_]+")
# A compound: words joined by hyphens, dashes, '&' (Qt accelerator) or apostrophes.
_JOINERS = re.escape("-\u2010\u2011\u2013\u2014&'\u2019")  # hyphens, dashes, &, apostrophes
_COMPOUND_RE = re.compile(r"[^\W_]+(?:[" + _JOINERS + r"]+[^\W_]+)*")
_JOINER_SPLIT_RE = re.compile("([" + _JOINERS + "]+)")


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


def _convert_core(core: str) -> str:
    """Convert a chunk without its surrounding punctuation; return it unchanged if technical."""
    text = unicodedata.normalize("NFC", core)
    if text.casefold() in _PROTECTED_FOLDED:
        return core
    if any(char in _TECHNICAL_CHARS for char in text) or _JOINED_RE.search(text):
        return core
    # YU1AB/P, km/h and TX/RX stay as they are; ulaza/izlaza and da/ne are words.
    if "/" in text and any(_is_protected_piece(word) for word in _WORD_RE.findall(text)):
        return core
    return _COMPOUND_RE.sub(_convert_compound, text)


def _convert_chunk(chunk: str, glued: bool) -> str:
    """Convert a run of non-space text; ``glued`` when it directly follows a protected span."""
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
    return chunk[:start] + _convert_core(chunk[start:end]) + chunk[end:]


def _convert_plain(segment: str, after_span: bool) -> str:
    pieces = _SPACE_SPLIT_RE.split(segment)
    out = []
    for index, piece in enumerate(pieces):
        if not piece or piece.isspace():
            out.append(piece)
        else:
            out.append(_convert_chunk(piece, glued=after_span and index == 0))
    return "".join(out)


def latin_to_cyrillic(text: str) -> str:
    """Transliterate Serbian Latin ``text`` to Serbian Cyrillic.

    Placeholders, markup, URLs, e-mail addresses, paths, file names and technical
    tokens stay unchanged (see the module documentation for the exact rules and
    the known limitations). ``text`` that is not a ``str`` is returned as it is.
    """
    if not isinstance(text, str) or not _HAS_LATIN_RE.search(text):
        return text
    out: list[str] = []
    position = 0
    for match in _SPAN_RE.finditer(text):
        if match.start() > position:
            out.append(_convert_plain(text[position : match.start()], after_span=position > 0))
        out.append(match.group())
        position = match.end()
    if position < len(text):
        out.append(_convert_plain(text[position:], after_span=position > 0))
    return "".join(out)


# --------------------------------------------------------------------------- catalogs

_problems: list[tuple[str, dict[str, str]]] = []
_problems_lock = threading.Lock()


def _set_problems(problems: list[tuple[str, dict[str, str]]]) -> None:
    global _problems
    with _problems_lock:
        _problems = problems


def _format(template: str, params: Mapping[str, str]) -> str:
    try:
        return tr(template).format(**params)
    except (KeyError, IndexError, ValueError):  # a broken translation: use the source text
        return template.format(**params)


def load_problems() -> list[str]:
    """Problems found by the latest :func:`load_catalog` call, translated now.

    Each skipped file, skipped entry or conflicting translation gives one message;
    the list is empty after a clean load. Callers on the QGIS side log them.
    """
    with _problems_lock:
        recorded = list(_problems)
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


def load_catalog(directory: str | os.PathLike) -> dict[str, str]:
    """Merge every ``*.json`` catalog of ``directory`` into one ``{source: translation}``.

    Files are read in name order; hidden files and anything that is not a
    ``.json`` file are ignored. A file that cannot be read, is not valid UTF-8
    JSON or is not a JSON object is skipped; entries whose value is not text are
    skipped; empty translations are left out (the English source is shown). When
    two files translate the same text differently, the later file wins. Nothing
    raises: problems are recorded for :func:`load_problems`.
    """
    problems: list[tuple[str, dict[str, str]]] = []
    catalog: dict[str, str] = {}
    origin: dict[str, str] = {}
    try:
        folder = os.fspath(directory)
        names = sorted(
            name
            for name in os.listdir(folder)
            if name.lower().endswith(".json")
            and not name.startswith(".")
            and os.path.isfile(os.path.join(folder, name))
        )
    except (OSError, TypeError, ValueError) as exc:
        problems.append((_MSG_FOLDER, {"path": str(directory), "error": str(exc)}))
        names = []
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
    _set_problems(problems)
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
    return load_catalog(_CATALOG_DIR)


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
