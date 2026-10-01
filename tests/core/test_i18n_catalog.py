"""Project-wide translation guard (docs/ARCHITECTURE.md, "Translation (i18n) rules").

Every ``hamq/**/*.py`` file is parsed with ``ast`` (never imported, so QGIS is not
needed) and every catalog in ``hamq/i18n/sr_Latn/`` is read strictly. The tests are
parametrized per file with the file path as the test id, so a failure names the module
or catalog, and so the owner, that has to change:

a. ``tr()``, ``tr_noop()`` and ``<anything>.tr()`` get one plain string literal. Allowed
   exceptions: calls inside a function named ``tr`` (delegating helpers) and ``tr()`` of
   a bare name, an attribute or a subscript of a name or attribute (a value or table
   marked with ``tr_noop`` elsewhere; an indexed literal such as ``("Yes", "No")[i]``
   is an error). f-strings, ``+``, ``%`` and ``.format()`` inside the argument are
   errors, and so is ``tr()`` at import time (the text would never follow a language
   switch).
b. Every such literal is in the merged Serbian (Latin) catalog with a non-empty value.
c. The ``{placeholders}`` of a key and of its value are the same set, also after
   transliteration to Cyrillic.
d. Each catalog is a UTF-8 JSON object with sorted keys, no duplicate keys, non-empty
   string values in Latin script, written canonically; a text translated in two files
   has the same translation in both. The catalog files are selected exactly as the
   runtime loader selects them (``*.json`` in any case, no hidden files).
e. No Qt translation API: ``translate()`` / ``tr()`` of ``QCoreApplication``,
   ``QGuiApplication``, ``QApplication``, ``QgsApplication`` (also imported under
   another name, their ``instance()`` or ``qApp``), ``QObject.tr``, ``super().tr()``,
   ``QT_TR_NOOP`` and friends (also as ``QtCore.QT_TR_NOOP``), or ``self.tr()`` in a
   class without its own HamQ ``tr`` method (that is Qt's ``QObject.tr``, which never
   sees the HamQ catalogs).
f. Unused catalog keys are reported as a warning; with ``HAMQ_STRICT_I18N=1`` they fail.

While modules are being written in parallel, tests for other agents' files may fail;
each failure message says what to change and where.
"""

from __future__ import annotations

import ast
import json
import os
import re
import string
import warnings
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

from hamq.core import i18n
from hamq.core.i18n import latin_to_cyrillic, load_catalog, load_problems

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "hamq"
CATALOG_DIR = PACKAGE_DIR / "i18n" / "sr_Latn"


def catalog_files(directory: Path) -> list[Path]:
    """Catalog files in load order, selected exactly as the runtime loader selects them."""
    try:
        return [directory / name for name in i18n._catalog_file_names(directory)]
    except OSError:
        return []


SOURCE_FILES = sorted(PACKAGE_DIR.rglob("*.py"))
CATALOG_FILES = catalog_files(CATALOG_DIR)
STRICT = os.environ.get("HAMQ_STRICT_I18N", "").strip() == "1"

TR_FUNCTIONS = ("tr", "tr_noop")
# QgsApplication derives from QApplication: its translate() is QCoreApplication.translate().
QT_APPLICATION_CLASSES = frozenset(
    {"QCoreApplication", "QGuiApplication", "QApplication", "QgsApplication"}
)
QT_APPLICATION_OBJECTS = frozenset({"qApp"})  # the Qt5 global application object
QT_TRANSLATION_MARKERS = frozenset(
    {
        "QT_TR_NOOP",
        "QT_TR_NOOP_UTF8",
        "QT_TRANSLATE_NOOP",
        "QT_TRANSLATE_NOOP3",
        "QT_TRANSLATE_NOOP_UTF8",
        "QT_TRID_NOOP",
        "qsTr",
        "qsTranslate",
        "qtTrId",
    }
)
CYRILLIC_RE = re.compile("[\u0400-\u04ff]")
CANONICAL = "json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\\n'"


def rel(path: Path) -> str:
    """Repository-relative path, used as the test id."""
    return path.relative_to(REPO_ROOT).as_posix()


def catalog_name_for(path: Path) -> str:
    """Catalog of a module: hamq/core/adif.py -> core_adif.json, hamq/plugin.py -> plugin.json."""
    parts = list(path.relative_to(PACKAGE_DIR).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ("_".join(parts) or "hamq") + ".json"


# --------------------------------------------------------------------------- source scanner


@dataclass
class ClassInfo:
    bases: list[str]
    defines_tr: bool


@dataclass
class Scan:
    """What one source file does with translations."""

    literals: list[tuple[str, int]] = field(default_factory=list)  # (text, line)
    errors: list[str] = field(default_factory=list)  # rule a
    qt_errors: list[str] = field(default_factory=list)  # rule e
    classes: dict[str, list[ClassInfo]] = field(default_factory=dict)
    self_tr_calls: list[tuple[str, int]] = field(default_factory=list)  # (class, line)


def _tail_name(node: ast.AST) -> str:
    """Last name of ``a.b.C``, ``C(...)`` or ``C[...]``; '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, (ast.Call, ast.Subscript)):
        return _tail_name(node.func if isinstance(node, ast.Call) else node.value)
    return ""


def _string_building(node: ast.AST) -> str | None:
    """First f-string, ``+``, ``%`` or ``.format()`` in ``node``; subscript indexes are skipped
    (``LABELS[key + 1]`` only selects a marked text)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.JoinedStr):
            return "an f-string"
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Add):
            return "concatenation (+)"
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Mod):
            return "% formatting"
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Attribute)
            and current.func.attr in ("format", "format_map")
        ):
            return ".format()"
        if isinstance(current, ast.Subscript):
            stack.append(current.value)
            continue
        stack.extend(ast.iter_child_nodes(current))
    return None


def _defines_tr(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return statement.name == "tr"
    if isinstance(statement, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "tr" for target in statement.targets
        )
    if isinstance(statement, ast.AnnAssign):
        return isinstance(statement.target, ast.Name) and statement.target.id == "tr"
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scan = Scan()
        self._scopes: list[str] = []  # function names, "<lambda>", "<class>"
        self._classes: list[str] = []
        self._aliases = {name: name for name in TR_FUNCTIONS}
        # other names of Qt application classes / objects and QObject in this file:
        # 'QCA' -> 'QCoreApplication', 'app' -> 'QgsApplication.instance()'
        self._qt_aliases: dict[str, str] = {}

    # --- helpers
    def _error(self, node: ast.AST, message: str) -> None:
        self.scan.errors.append(f"line {node.lineno}: {message}")

    def _qt(self, node: ast.AST, message: str) -> None:
        self.scan.qt_errors.append(f"line {node.lineno}: {message}")

    def _at_import_time(self) -> bool:
        return all(scope == "<class>" for scope in self._scopes)

    def _qt_name(self, node: ast.AST) -> str:
        """Qt class or object ``node`` stands for, aliases resolved: ``QtCore.QCoreApplication``
        -> 'QCoreApplication', ``QgsApplication.instance()`` -> 'QgsApplication.instance()'."""
        if isinstance(node, ast.Name):
            return self._qt_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return node.attr
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "instance"
        ):
            owner = self._qt_name(node.func.value)
            if owner in QT_APPLICATION_CLASSES:
                return f"{owner}.instance()"
        return ""

    @staticmethod
    def _is_application(name: str) -> bool:
        return (
            name in QT_APPLICATION_CLASSES
            or name in QT_APPLICATION_OBJECTS
            or name.endswith(".instance()")
        )

    def _record_qt_alias(self, targets: list[ast.expr], value: ast.AST) -> None:
        owner = self._qt_name(value)
        if owner == "QObject" or self._is_application(owner):
            for target in targets:
                if isinstance(target, ast.Name):
                    self._qt_aliases[target.id] = owner

    # --- scopes
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name in QT_TRANSLATION_MARKERS:
                self._qt(node, f"imports {alias.name}, a Qt translation marker; use tr_noop()")
            if module.rsplit(".", 1)[-1] == "i18n" and alias.name in TR_FUNCTIONS and alias.asname:
                self._aliases[alias.asname] = alias.name
            if alias.asname and (alias.name == "QObject" or self._is_application(alias.name)):
                self._qt_aliases[alias.asname] = alias.name
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_qt_alias(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._record_qt_alias([node.target], node.value)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(child)
        info = ClassInfo(
            [_tail_name(base) for base in node.bases], any(map(_defines_tr, node.body))
        )
        self.scan.classes.setdefault(node.name, []).append(info)
        self._classes.append(node.name)
        self._scopes.append("<class>")
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()
        self._classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:  # decorators and defaults run at definition
            self.visit(decorator)
        self.visit(node.args)
        self._scopes.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self._scopes.append("<lambda>")
        self.visit(node.body)
        self._scopes.pop()

    # --- rule a
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        kind = None
        if isinstance(func, ast.Name):
            kind = self._aliases.get(func.id)
        elif isinstance(func, ast.Attribute) and func.attr in TR_FUNCTIONS:
            kind = func.attr
            if (
                func.attr == "tr"
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and self._classes
            ):
                self.scan.self_tr_calls.append((self._classes[-1], node.lineno))
        if kind is not None and not (self._scopes and self._scopes[-1] == "tr"):
            self._check_call(node, kind)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call, kind: str) -> None:
        name = f"{kind}()"
        if kind == "tr" and self._at_import_time():
            self._error(
                node,
                "tr() runs at import time, so the text never follows a language switch; "
                "mark it with tr_noop() and call tr() when the text is shown",
            )
        if len(node.args) != 1 or node.keywords or isinstance(node.args[0], ast.Starred):
            self._error(node, f"{name} takes exactly one positional argument, a string literal")
            return
        arg = node.args[0]
        if isinstance(arg, ast.Constant):
            if isinstance(arg.value, str):
                self.scan.literals.append((arg.value, node.lineno))
            else:
                self._error(node, f"{name} argument {arg.value!r} is not a string literal")
            return
        building = _string_building(arg)
        if building is not None:
            self._error(
                node,
                f"{name} argument uses {building}; translate a plain literal and format the "
                'result: tr("QSO: {count}").format(count=n)',
            )
        elif not isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
            self._error(
                node, f"{name} argument must be a plain string literal, not {type(arg).__name__}"
            )
        elif kind == "tr_noop":
            self._error(node, "tr_noop() marks a text for translation; pass a string literal")
        elif isinstance(arg, ast.Subscript):
            base = arg.value
            while isinstance(base, ast.Subscript):
                base = base.value
            if not isinstance(base, (ast.Name, ast.Attribute)):  # ("Yes", "No")[flag]
                self._error(
                    node,
                    f"{name} argument indexes a {type(base).__name__}, whose texts are never "
                    "checked against the catalog; mark them with tr_noop() in a named table "
                    "and pass TABLE[key]",
                )

    # --- rule e
    def visit_Attribute(self, node: ast.Attribute) -> None:
        owner = self._qt_name(node.value)
        if node.attr == "translate" and self._is_application(owner):
            self._qt(node, f"uses {owner}.translate(); use tr() from hamq.core.i18n")
        elif node.attr == "tr" and (owner == "QObject" or self._is_application(owner)):
            self._qt(node, f"uses {owner}.tr(); use tr() from hamq.core.i18n")
        elif (
            node.attr == "tr"
            and isinstance(node.value, ast.Call)
            and _tail_name(node.value) == "super"
        ):
            self._qt(node, "calls super().tr(), Qt's QObject.tr(); use tr() from hamq.core.i18n")
        elif node.attr in QT_TRANSLATION_MARKERS:
            self._qt(node, f"uses {node.attr}, a Qt translation marker; use tr_noop()")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in QT_TRANSLATION_MARKERS:
            self._qt(node, f"uses {node.id}, a Qt translation marker; use tr_noop()")


def scan_source(source: str, filename: str = "<source>") -> Scan:
    visitor = _Visitor()
    visitor.visit(ast.parse(source, filename=filename))
    return visitor.scan


@cache
def scan_file(path: Path) -> Scan:
    return scan_source(path.read_text(encoding="utf-8"), rel(path))


@cache
def all_scans() -> tuple[Scan, ...]:
    """Scans of every source file that parses (a broken file fails its own tests)."""
    scans = []
    for path in SOURCE_FILES:
        try:
            scans.append(scan_file(path))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
    return tuple(scans)


def class_registry(scans: tuple[Scan, ...]) -> dict[str, list[ClassInfo]]:
    registry: dict[str, list[ClassInfo]] = {}
    for scan in scans:
        for name, infos in scan.classes.items():
            registry.setdefault(name, []).extend(infos)
    return registry


def has_hamq_tr(
    name: str, registry: dict[str, list[ClassInfo]], seen: set[str] | None = None
) -> bool:
    """True when class ``name`` or a HamQ base class of it defines ``tr``."""
    seen = set() if seen is None else seen
    if name in seen:
        return False
    seen.add(name)
    for info in registry.get(name, []):
        if info.defines_tr or any(has_hamq_tr(base, registry, seen) for base in info.bases):
            return True
    return False


def qt_problems(scan: Scan, registry: dict[str, list[ClassInfo]]) -> list[str]:
    problems = list(scan.qt_errors)
    for class_name, line in scan.self_tr_calls:
        if not has_hamq_tr(class_name, registry):
            problems.append(
                f"line {line}: self.tr() in class {class_name}, which has no tr() of its own: "
                "that is Qt's QObject.tr() (or missing); add "
                "'def tr(self, text): return tr(text)' with tr from hamq.core.i18n"
            )
    return problems


@cache
def used_texts() -> frozenset[str]:
    return frozenset(text for scan in all_scans() for text, _ in scan.literals)


# --------------------------------------------------------------------------- catalog reader


@dataclass
class CatalogFile:
    data: dict[str, str]  # valid entries (empty when the file is unreadable)
    problems: list[str]
    readable: bool


def format_fields(text: str) -> set[str]:
    """Field names of a ``str.format`` template ('{count:,}' -> {'count'}); ValueError if invalid."""
    names = set()
    for _, name, spec, _ in string.Formatter().parse(text):
        if name is not None:
            names.add(name)
            if spec:
                names |= format_fields(spec)
    return names


@cache
def read_catalog(path: Path) -> CatalogFile:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return CatalogFile({}, [f"cannot be read as UTF-8: {exc}"], False)
    duplicates: list[str] = []

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=no_duplicates)
    except ValueError as exc:
        return CatalogFile({}, [f"is not valid JSON (a BOM is not allowed either): {exc}"], False)
    if not isinstance(data, dict):
        return CatalogFile({}, [f"must be a JSON object, not {type(data).__name__}"], False)
    problems = [f"duplicate key {key!r}" for key in duplicates]
    valid: dict[str, str] = {}
    for key, value in data.items():
        if not key.strip():
            problems.append(f"empty key {key!r}")
        if not isinstance(value, str):
            problems.append(f"{key!r}: the value must be a string, not {type(value).__name__}")
        elif not value.strip():
            problems.append(f"{key!r}: empty translation")
        else:
            if CYRILLIC_RE.search(value):
                problems.append(
                    f"{key!r}: Cyrillic letters in {value!r}; catalogs are Serbian Latin "
                    "(Cyrillic is derived at run time)"
                )
            valid[key] = value
    if list(data) != sorted(data):
        problems.append("keys are not sorted")
    if text != json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n":
        problems.append(f"is not written canonically; write it with {CANONICAL}")
    return CatalogFile(valid, problems, True)


@cache
def merged_catalog() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in CATALOG_FILES:
        merged.update(read_catalog(path).data)
    return merged


def report(path: Path, problems: list[str], hint: str = "") -> str:
    lines = [f"{rel(path)}:"] + [f"  {problem}" for problem in problems]
    if hint:
        lines.append(hint)
    return "\n".join(lines)


# --------------------------------------------------------------------------- the guard


def test_sources_and_catalogs_are_found():
    assert PACKAGE_DIR / "core" / "i18n.py" in SOURCE_FILES
    assert CATALOG_DIR / "core_i18n.json" in CATALOG_FILES
    assert catalog_name_for(PACKAGE_DIR / "core" / "i18n.py") == "core_i18n.json"
    assert catalog_name_for(PACKAGE_DIR / "plugin.py") == "plugin.json"
    assert catalog_name_for(PACKAGE_DIR / "gui" / "__init__.py") == "gui.json"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=rel)
def test_tr_arguments_are_plain_literals(path):  # rule a
    errors = scan_file(path).errors
    assert not errors, report(path, errors)


@pytest.mark.parametrize("path", SOURCE_FILES, ids=rel)
def test_translatable_texts_are_in_the_catalog(path):  # rule b
    catalog = merged_catalog()
    missing = sorted(
        {
            (line, text)
            for text, line in scan_file(path).literals
            if not catalog.get(text, "").strip()
        }
    )
    assert not missing, report(
        path,
        [f"line {line}: {text!r}" for line, text in missing],
        f"Add them with their Serbian (Latin) translation to "
        f"hamq/i18n/sr_Latn/{catalog_name_for(path)}",
    )


@pytest.mark.parametrize("path", SOURCE_FILES, ids=rel)
def test_no_qt_translation_api(path):  # rule e
    problems = qt_problems(scan_file(path), class_registry(all_scans()))
    assert not problems, report(path, problems)


@pytest.mark.parametrize("path", CATALOG_FILES, ids=rel)
def test_catalog_file_is_well_formed(path):  # rule d
    problems = read_catalog(path).problems
    assert not problems, report(path, problems)


@pytest.mark.parametrize("path", CATALOG_FILES, ids=rel)
def test_catalog_placeholders_match(path):  # rule c
    problems = []
    for key, value in read_catalog(path).data.items():
        try:
            expected, found = format_fields(key), format_fields(value)
        except ValueError as exc:
            problems.append(f"{key!r}: not a valid str.format template: {exc}")
            continue
        if expected != found:
            problems.append(
                f"{key!r}: placeholders {sorted(expected)} in the source, {sorted(found)} in "
                f"{value!r}"
            )
            continue
        cyrillic = latin_to_cyrillic(value)
        try:
            kept = format_fields(cyrillic) == found
        except ValueError:
            kept = False
        if not kept:
            problems.append(f"{key!r}: transliteration broke the placeholders: {cyrillic!r}")
    assert not problems, report(path, problems)


@pytest.mark.parametrize("path", CATALOG_FILES, ids=rel)
def test_catalog_agrees_with_other_catalogs(path):  # rule d
    data = read_catalog(path).data
    problems = []
    for other in CATALOG_FILES:
        if other == path:
            continue
        other_data = read_catalog(other).data
        for key in sorted(data.keys() & other_data.keys()):
            if data[key] != other_data[key]:
                problems.append(f"{key!r}: {data[key]!r} here, {other_data[key]!r} in {rel(other)}")
    assert not problems, report(path, problems, "Use one translation for the same English text.")


@pytest.mark.parametrize("path", CATALOG_FILES, ids=rel)
def test_catalog_keys_are_used(path):  # rule f
    unused = sorted(read_catalog(path).data.keys() - used_texts())
    if not unused:
        return
    message = report(
        path,
        [repr(key) for key in unused],
        "Unused catalog keys: no tr()/tr_noop() literal in hamq/ uses them.",
    )
    if STRICT:
        pytest.fail(message)
    warnings.warn(message, UserWarning, stacklevel=1)


def test_runtime_loader_agrees_with_the_guard(monkeypatch):
    monkeypatch.setattr(i18n, "_problems", None)
    assert load_catalog(CATALOG_DIR) == merged_catalog()
    if all(read_catalog(path).readable for path in CATALOG_FILES):
        conflicts = [problem for problem in load_problems() if "differs" in problem]
        assert load_problems() == conflicts  # only cross-file conflicts can remain


# --------------------------------------------------------------------------- scanner self-test

FLAGGED = [
    pytest.param('def f(n):\n    return tr(f"QSO {n}")\n', "an f-string", id="f-string"),
    pytest.param('def f(n):\n    return tr("QSO " + n)\n', "concatenation", id="concat"),
    pytest.param('def f(n):\n    return tr("QSO %d" % n)\n', "% formatting", id="percent"),
    pytest.param('def f(n):\n    return tr("QSO {n}".format(n=n))\n', ".format()", id="format"),
    pytest.param(
        'def f(self, n):\n    return self.tr("QSO " + n)\n', "concatenation", id="self-tr"
    ),
    pytest.param('def f(n):\n    return i18n.tr(f"{n}")\n', "an f-string", id="module-attr"),
    pytest.param(
        'def f(n):\n    return tr(("QSO " + n)[0])\n', "concatenation", id="sliced-concat"
    ),
    pytest.param("def f(name):\n    return tr_noop(name)\n", "string literal", id="noop-variable"),
    pytest.param("def f():\n    return tr()\n", "one positional", id="no-argument"),
    pytest.param('def f():\n    return tr("a", "b")\n', "one positional", id="two-arguments"),
    pytest.param('def f():\n    return tr(text="a")\n', "one positional", id="keyword"),
    pytest.param("def f(args):\n    return tr(*args)\n", "one positional", id="starred"),
    pytest.param("def f():\n    return tr(5)\n", "is not a string literal", id="number"),
    pytest.param("def f():\n    return tr(label())\n", "not Call", id="call"),
    pytest.param('def f(x):\n    return tr(x or "QSO")\n', "not BoolOp", id="bool-op"),
    pytest.param('def f(x):\n    return tr("A" if x else "B")\n', "not IfExp", id="if-exp"),
    pytest.param(
        'from hamq.core.i18n import tr as _t\n\n\ndef f(n):\n    return _t(f"{n}")\n',
        "an f-string",
        id="alias",
    ),
    pytest.param('TITLE = tr("Statistics")\n', "import time", id="module-level"),
    pytest.param('class Dock:\n    TITLE = tr("Statistics")\n', "import time", id="class-level"),
    pytest.param(
        'def f(title=tr("Statistics")):\n    return title\n', "import time", id="default-arg"
    ),
    pytest.param('LABELS = [tr(x) for x in ("a", "b")]\n', "import time", id="comprehension"),
    # a literal container indexed at the call: its texts are never checked against the catalog
    pytest.param(
        'def f(flag):\n    return tr(("Yes", "No")[flag])\n', "indexes a Tuple", id="tuple-index"
    ),
    pytest.param(
        'def f(key):\n    return tr({"a": "Yes"}[key])\n', "indexes a Dict", id="dict-index"
    ),
    pytest.param('def f():\n    return tr("Yes"[:2])\n', "indexes a Constant", id="sliced-literal"),
    pytest.param("def f(key):\n    return tr(labels()[key])\n", "indexes a Call", id="call-index"),
]


@pytest.mark.parametrize(("source", "reason"), FLAGGED)
def test_scanner_flags_bad_tr_calls(source, reason):
    errors = scan_source(source).errors
    assert any(reason in error for error in errors), errors


ALLOWED = [
    pytest.param('def f():\n    return tr("Veza")\n', id="literal"),
    pytest.param('def f(c):\n    return tr("Veza {call}").format(call=c)\n', id="format-after"),
    pytest.param('LABELS = {"total": tr_noop("Total QSOs")}\n', id="noop-module-level"),
    pytest.param("def f(key):\n    return tr(LABELS[key])\n", id="subscript"),
    pytest.param("def f(i):\n    return tr(LABELS[i + 1])\n", id="computed-index"),
    pytest.param("def f(key, i):\n    return tr(LABELS[key][i])\n", id="nested-subscript"),
    pytest.param("def f(self, key):\n    return tr(self.LABELS[key])\n", id="attribute-table"),
    pytest.param("def f(entry):\n    return tr(entry.text)\n", id="attribute"),
    pytest.param("def f(text):\n    return tr(text)\n", id="name"),
    pytest.param("def tr(text):\n    return _translator.translate(text.strip())\n", id="def-tr"),
    pytest.param(
        "class A:\n    def tr(self, text):\n        return tr(text.strip() + '')\n",
        id="method-tr",
    ),
    pytest.param('def f():\n    return tr("Duga " "poruka")\n', id="implicit-concat"),
    pytest.param(
        'def f(text, table):\n    return text.translate(table) + "x" % 1\n', id="str-translate"
    ),
    pytest.param('LAZY = lambda: tr("Statistics")\n', id="lambda"),
    pytest.param(
        'class Dock:\n    def title(self):\n        return self.tr("Statistics")\n\n'
        "    def tr(self, text):\n        return tr(text)\n",
        id="own-tr",
    ),
]


@pytest.mark.parametrize("source", ALLOWED)
def test_scanner_allows_good_tr_calls(source):
    assert scan_source(source).errors == []


def test_scanner_collects_literals_with_lines():
    scan = scan_source(
        'from hamq.core.i18n import tr_noop as mark\n\nA = mark("One")\n\n\n'
        'def f(self):\n    return tr("Two"), self.tr("Three"), x.tr_noop("Four")\n'
    )
    assert scan.literals == [("One", 3), ("Two", 7), ("Three", 7), ("Four", 7)]


def _qt(source: str) -> list[str]:
    scan = scan_source(source)
    return qt_problems(scan, class_registry((scan,)))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'def f():\n    return QCoreApplication.translate("HamQ", "Veza")\n', id="translate"
        ),
        pytest.param(
            'def f():\n    return QtCore.QCoreApplication.translate("HamQ", "Veza")\n',
            id="qualified-translate",
        ),
        pytest.param("translate = QApplication.translate\n", id="translate-reference"),
        pytest.param('def f():\n    return QObject.tr("Veza")\n', id="qobject-tr"),
        pytest.param("from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP\n", id="import-marker"),
        pytest.param('X = QT_TR_NOOP("Veza")\n', id="marker"),
        pytest.param(
            "class A(Base):\n    def tr(self, text):\n        return super().tr(text)\n",
            id="super-tr",
        ),
        pytest.param(
            'class Dock(QDockWidget):\n    def title(self):\n        return self.tr("Statistics")\n',
            id="self-tr-without-own-tr",
        ),
        pytest.param(
            "class Base(QDialog):\n    pass\n\n\nclass Dialog(Base):\n    def title(self):\n"
            '        return self.tr("Settings")\n',
            id="self-tr-base-without-tr",
        ),
        # review findings: QgsApplication is a QApplication, aliases, instance(), qApp
        pytest.param(
            'def f():\n    return QgsApplication.translate("HamQ", "Veza")\n',
            id="qgsapplication-translate",
        ),
        pytest.param('def f():\n    return QgsApplication.tr("Veza")\n', id="qgsapplication-tr"),
        pytest.param(
            "from qgis.PyQt.QtCore import QCoreApplication as QCA\n\n\n"
            'def f():\n    return QCA.translate("HamQ", "Veza")\n',
            id="aliased-application",
        ),
        pytest.param(
            "from qgis.PyQt.QtCore import QObject as Base\n\n\n"
            'def f():\n    return Base.tr("Veza")\n',
            id="aliased-qobject",
        ),
        pytest.param(
            'def f():\n    return QCoreApplication.instance().translate("HamQ", "Veza")\n',
            id="instance-translate",
        ),
        pytest.param(
            'def f():\n    return QgsApplication.instance().tr("Veza")\n', id="instance-tr"
        ),
        pytest.param(
            "def f():\n    app = QgsApplication.instance()\n"
            '    return app.translate("HamQ", "Veza")\n',
            id="instance-variable",
        ),
        pytest.param('def f():\n    return qApp.translate("HamQ", "Veza")\n', id="qapp"),
        pytest.param('X = QtCore.QT_TRANSLATE_NOOP("HamQ", "Veza")\n', id="qualified-marker"),
    ],
)
def test_scanner_flags_qt_translation_api(source):
    assert _qt(source) != []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'class Dock(QDockWidget):\n    def title(self):\n        return self.tr("Statistics")\n\n'
            "    def tr(self, text):\n        return tr(text)\n",
            id="own-tr",
        ),
        pytest.param(
            "class TrMixin:\n    def tr(self, text):\n        return tr(text)\n\n\n"
            'class Dock(TrMixin, QDockWidget):\n    def title(self):\n        return self.tr("Stats")\n',
            id="mixin-tr",
        ),
        pytest.param(
            "class Alg(QgsProcessingAlgorithm):\n    tr = staticmethod(tr)\n\n"
            '    def displayName(self):\n        return self.tr("Import ADIF")\n',
            id="assigned-tr",
        ),
        pytest.param("def f(text, table):\n    return text.translate(table)\n", id="str-translate"),
        pytest.param("class A(A):\n    def f(self):\n        return 1\n", id="self-inheriting"),
        pytest.param(
            "def f():\n    return QgsApplication.instance().processingRegistry()\n",
            id="application-instance",
        ),
        pytest.param(
            "from qgis.core import QgsApplication as App\n\n\ndef f():\n    return App.locale()\n",
            id="aliased-application-locale",
        ),
        pytest.param(
            "def f(app, table):\n    return app.translate(table)\n", id="unrelated-translate"
        ),
    ],
)
def test_scanner_allows_hamq_translation(source):
    assert _qt(source) == []


def test_guard_checks_the_files_the_runtime_loads(tmp_path, monkeypatch):
    # Review finding: the guard globbed '*.json' (case-sensitive, dotfiles included) while
    # the runtime loads '*.json' in any case and skips hidden files.
    monkeypatch.setattr(i18n, "_problems", None)
    (tmp_path / "b.json").write_text('{"B": "Be"}', encoding="utf-8")
    (tmp_path / "A_UPPER.JSON").write_text('{"Upper": "Gornji"}', encoding="utf-8")
    (tmp_path / "c.Json").write_text('{"C": "Ce"}', encoding="utf-8")
    (tmp_path / ".hidden.json").write_text('{"Hidden": "Skriven"}', encoding="utf-8")
    (tmp_path / "notes.txt").write_text('{"Notes": "Beleške"}', encoding="utf-8")
    (tmp_path / "folder.json").mkdir()
    files = catalog_files(tmp_path)
    assert [path.name for path in files] == ["A_UPPER.JSON", "b.json", "c.Json"]
    merged: dict[str, str] = {}
    for path in files:
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    assert load_catalog(tmp_path) == merged
    assert catalog_files(tmp_path / "missing") == []
