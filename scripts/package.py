#!/usr/bin/env python3
"""Build the HamQ plugin zip for plugins.qgis.org: ``dist/hamq-<version>.zip``.

Usage::

    python3 scripts/package.py [--output-dir DIR] [--root DIR]

The zip has a single top folder ``hamq/`` with the plugin package, without
``__pycache__``, ``*.pyc``, ``tests``, ``dist`` and hidden files (``.git*``),
plus the repository ``LICENSE`` as ``hamq/LICENSE``. ``metadata.txt`` is
validated first (required keys, icon, version equal to ``pyproject.toml``);
on problems they are printed and the script exits with status 1.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import re
import sys
import zipfile
from pathlib import Path

PLUGIN_DIR_NAME = "hamq"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

#: Keys plugins.qgis.org requires, plus the ones HamQ always sets.
REQUIRED_KEYS = (
    "name",
    "qgisMinimumVersion",
    "qgisMaximumVersion",
    "description",
    "about",
    "version",
    "author",
    "email",
    "repository",
    "tracker",
    "homepage",
    "category",
    "tags",
    "icon",
    "license",
    "supportsQt6",
    "experimental",
)
EXCLUDED_DIRS = frozenset({"__pycache__", "tests", "dist", "build"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", "~")
EXCLUDED_NAMES = frozenset({"Thumbs.db", "desktop.ini"})
_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?([-.]?[0-9A-Za-z]+)*$")
_QGIS_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def read_metadata(path: Path) -> dict[str, str]:
    """Read the ``[general]`` section of a metadata.txt (keys keep their case)."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep "qgisMinimumVersion" as written
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    if not parser.has_section("general"):
        raise configparser.Error("missing [general] section")
    return dict(parser.items("general"))


def pyproject_version(path: Path) -> str | None:
    """``version`` of the ``[project]`` table of pyproject.toml, or None."""
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped
        elif section == "[project]":
            match = re.match(r"""version\s*=\s*["']([^"']+)["']""", stripped)
            if match:
                return match.group(1)
    return None


def _defines_class_factory(init_path: Path) -> bool:
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "classFactory" for node in tree.body
    )


def validate(root: Path) -> tuple[dict[str, str], list[str]]:
    """Check the plugin sources under ``root``; return (metadata, problems)."""
    problems: list[str] = []
    plugin_dir = root / PLUGIN_DIR_NAME
    init_path = plugin_dir / "__init__.py"
    if not init_path.is_file():
        problems.append(f"{init_path} is missing")
    elif not _defines_class_factory(init_path):
        problems.append(f"{init_path} does not define classFactory(iface)")
    if not (root / "LICENSE").is_file():
        problems.append(f"{root / 'LICENSE'} is missing")

    metadata_path = plugin_dir / "metadata.txt"
    try:
        metadata = read_metadata(metadata_path)
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        problems.append(f"{metadata_path}: cannot read metadata ({exc})")
        return {}, problems

    for key in REQUIRED_KEYS:
        if not metadata.get(key, "").strip():
            problems.append(f"metadata.txt: required key '{key}' is missing or empty")

    version = metadata.get("version", "").strip()
    if version and not _VERSION_RE.match(version):
        problems.append(f"metadata.txt: version '{version}' is not like 1.2.3")
    for key in ("qgisMinimumVersion", "qgisMaximumVersion"):
        value = metadata.get(key, "").strip()
        if value and not _QGIS_VERSION_RE.match(value):
            problems.append(f"metadata.txt: {key} '{value}' is not like 3.34")
    if metadata.get("supportsQt6", "").strip().lower() not in ("true", "yes", "1", ""):
        problems.append("metadata.txt: supportsQt6 must be True (HamQ runs on QGIS 4 / Qt6)")

    icon = metadata.get("icon", "").strip()
    if icon:
        icon_path = (plugin_dir / icon).resolve()
        if plugin_dir.resolve() not in icon_path.parents or not icon_path.is_file():
            problems.append(f"metadata.txt: icon '{icon}' does not exist inside {plugin_dir}")

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        problems.append(f"{pyproject} is missing")
    else:
        project_version = pyproject_version(pyproject)
        if project_version is None:
            problems.append("pyproject.toml: [project] version is missing")
        elif version and project_version != version:
            problems.append(
                f"version mismatch: metadata.txt has {version}, "
                f"pyproject.toml has {project_version}"
            )
    return metadata, problems


def is_excluded(relative: Path) -> bool:
    """True for files that must not be shipped (caches, tests, hidden files)."""
    for part in relative.parts:
        if part in EXCLUDED_DIRS or part.startswith("."):
            return True
    return relative.name in EXCLUDED_NAMES or relative.name.endswith(EXCLUDED_SUFFIXES)


def plugin_files(plugin_dir: Path) -> list[Path]:
    """Files of the plugin package to ship, relative to ``plugin_dir``, sorted."""
    files = []
    for path in sorted(plugin_dir.rglob("*")):
        relative = path.relative_to(plugin_dir)
        if path.is_file() and not is_excluded(relative):
            files.append(relative)
    return files


def build(root: Path, output_dir: Path, version: str) -> Path:
    """Write ``output_dir/hamq-<version>.zip`` and return its path."""
    plugin_dir = root / PLUGIN_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{PLUGIN_DIR_NAME}-{version}.zip"
    files = plugin_files(plugin_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(plugin_dir / relative, f"{PLUGIN_DIR_NAME}/{relative.as_posix()}")
        if Path("LICENSE") not in files:
            archive.write(root / "LICENSE", f"{PLUGIN_DIR_NAME}/LICENSE")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the HamQ plugin zip.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="directory for the zip (default: <root>/dist)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository root with hamq/, LICENSE and pyproject.toml (default: this repository)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = (args.output_dir or root / "dist").resolve()

    metadata, problems = validate(root)
    if problems:
        print("package.py: the plugin cannot be packaged:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    zip_path = build(root, output_dir, metadata["version"].strip())
    with zipfile.ZipFile(zip_path) as archive:
        count = len(archive.namelist())
    print(f"built {zip_path} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
