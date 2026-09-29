"""Architecture rule: hamq/core is pure Python (no QGIS or Qt imports, AGENTS.md).

Every module under hamq/core is parsed with ``ast`` (never imported) and checked
for imports of QGIS, Qt bindings or sip, including dynamic imports and imports
of HamQ modules outside ``hamq.core`` (those pull QGIS in indirectly).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "hamq" / "core"
FORBIDDEN_TOP_LEVEL = frozenset(
    {"qgis", "PyQt4", "PyQt5", "PyQt6", "PySide2", "PySide6", "sip", "shiboken2", "shiboken6"}
)


def _module_package(path: Path) -> list[str]:
    """Package a module belongs to: hamq/core/bands.py and hamq/core/__init__.py -> hamq.core."""
    return list(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1])


def _problem(module: str) -> str | None:
    """Why importing ``module`` from core is forbidden, or None when it is fine."""
    top = module.split(".")[0]
    if top in FORBIDDEN_TOP_LEVEL:
        return f"imports {module}"
    if (
        top == "hamq"
        and module not in ("hamq", "hamq.core")
        and not module.startswith("hamq.core.")
    ):
        return f"imports {module} (outside hamq.core)"
    return None


def forbidden_imports(source: str, package: list[str], filename: str = "<core>") -> list[str]:
    """Return ``"line: reason"`` for every forbidden import in ``source``."""
    tree = ast.parse(source, filename=filename)
    problems: list[str] = []

    def report(node: ast.AST, module: str) -> None:
        reason = _problem(module)
        if reason:
            problems.append(f"{filename}:{node.lineno}: {reason}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                report(node, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(package):
                    problems.append(f"{filename}:{node.lineno}: relative import beyond hamq")
                    continue
                base = package[: len(package) - node.level + 1]
            else:
                base = []
            module = ".".join(base + ([node.module] if node.module else []))
            report(node, module)
            if module in ("hamq", "hamq.core") or not node.module:
                for alias in node.names:  # from hamq import plugin / from .. import settings
                    report(node, f"{module}.{alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                name = ""
            if (
                name in ("import_module", "__import__")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                report(node, node.args[0].value)
    return problems


CORE_FILES = sorted(CORE_DIR.rglob("*.py"))


def test_core_directory_has_modules():
    names = {path.name for path in CORE_FILES}
    assert {"__init__.py", "bands.py", "modes.py", "i18n.py"} <= names


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: str(p.relative_to(CORE_DIR)))
def test_core_module_has_no_qgis_or_qt_imports(path):
    source = path.read_text(encoding="utf-8")
    rel = str(path.relative_to(REPO_ROOT))
    assert forbidden_imports(source, _module_package(path), rel) == []


CORE_PACKAGE = ["hamq", "core"]


@pytest.mark.parametrize(
    "source",
    [
        "import qgis",
        "import qgis.core",
        "from qgis.core import QgsPointXY",
        "from qgis.PyQt.QtCore import QObject",
        "from qgis import PyQt",
        "import PyQt5.QtCore",
        "from PyQt6 import QtCore",
        "import sip",
        "from PySide6.QtCore import QObject",
        "def lazy():\n    from qgis.core import QgsGeometry\n",
        "try:\n    import PyQt5\nexcept ImportError:\n    pass\n",
        "import importlib\nimportlib.import_module('qgis.core')",
        "__import__('PyQt6.QtCore')",
        "from ..qgis_io import compat",
        "from .. import settings",
        "from hamq import plugin",
        "from hamq.gui.dock import HamQDock",
        "import hamq.qgis_io.gpkg",
        "from ... import something",
    ],
)
def test_scanner_detects_forbidden_imports(source):
    assert forbidden_imports(source, CORE_PACKAGE) != []


@pytest.mark.parametrize(
    "source",
    [
        "import json\nimport os.path",
        "from datetime import datetime, timezone",
        "from . import bands",
        "from .bands import band_from_freq",
        "from .modes import display_mode",
        "from hamq.core.bands import BANDS",
        "import hamq.core.maidenhead",
        "from .. import core",
        "import importlib\nimportlib.import_module('json')",
        "text = 'from qgis.core import QgsGeometry'",
        "import qgisx",
    ],
)
def test_scanner_allows_pure_python_imports(source):
    assert forbidden_imports(source, CORE_PACKAGE) == []


def test_scanner_resolves_subpackage_relative_imports():
    package = ["hamq", "core", "sub"]
    assert forbidden_imports("from .. import bands", package) == []
    assert forbidden_imports("from ... import plugin", package) != []
