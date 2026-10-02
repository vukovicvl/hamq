#!/usr/bin/env python3
"""Build the HamQ plugin zip for plugins.qgis.org: ``dist/hamq-<version>.zip``.

Usage::

    python3 scripts/package.py [--output-dir DIR] [--root DIR] [--release]

The zip has a single top folder ``hamq/`` with the plugin package, without
``__pycache__``, ``*.pyc``, ``tests``, ``dist`` and hidden files (``.git*``),
plus the repository ``LICENSE`` as ``hamq/LICENSE``. ``metadata.txt`` is
validated first (required keys, icon, version equal to ``pyproject.toml``);
on problems they are printed and the script exits with status 1.

Only the file types the plugin is made of are packed (:data:`ALLOWED_SUFFIXES`).
Data files are never packed, wherever they are in ``hamq/``: the AD1C country files
``cty.dat`` / ``cty.csv`` (the user downloads them; they must not be bundled), QSO logs
and databases (``*.gpkg*``, ``*.adi``, ``*.sqlite``, ...). They are listed as skipped.
Any other unexpected file stops the build, so a stray file never ships unnoticed.

``--release`` checks what a published zip needs on top of that: a
``## [<version>]`` section in ``CHANGELOG.md``, a plain-text ``changelog`` in
``metadata.txt`` that names the version, and, in a git checkout, no uncommitted or
untracked files under ``hamq/`` (the zip must be the committed sources).
"""

from __future__ import annotations

import argparse
import ast
import configparser
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

PLUGIN_DIR_NAME = "hamq"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

#: Keys plugins.qgis.org requires, plus the ones HamQ always sets. Not ``category``: the
#: QGIS documentation allows only Raster, Vector, Database, Mesh and Web there, and HamQ
#: stays in the Plugins menu, the default without the key.
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
    "tags",
    "icon",
    "license",
    "supportsQt6",
    "experimental",
)
EXCLUDED_DIRS = frozenset({"__pycache__", "tests", "dist", "build"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", "~")
EXCLUDED_NAMES = frozenset({"Thumbs.db", "desktop.ini"})
#: File types the plugin is made of; anything else that is not excluded stops the build.
ALLOWED_SUFFIXES = frozenset({".py", ".json", ".txt", ".qml", ".svg", ".png", ".md"})
ALLOWED_NAMES = frozenset({"LICENSE"})
#: Data that must never be in the zip: the AD1C country files (downloaded by the user,
#: never bundled), QSO logs and databases. Skipped wherever they are in ``hamq/``.
DATA_FILE_NAMES = frozenset({"cty.dat", "cty.csv"})
DATA_FILE_SUFFIXES = (
    ".adi",
    ".adif",
    ".adx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".part",
)
_GEOPACKAGE_RE = re.compile(r"\.gpkg(?:-[a-z]+)?$", re.IGNORECASE)  # hamq.gpkg, -wal, -shm
_CHANGELOG_SECTION_RE = re.compile(r"^## \[(?P<version>[^\]]+)\]", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
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

    for relative in scan(plugin_dir).unexpected:
        problems.append(
            f"{PLUGIN_DIR_NAME}/{relative.as_posix()}: unexpected file type; remove it, or add "
            "its type to ALLOWED_SUFFIXES in scripts/package.py if the plugin needs it"
        )
    return metadata, problems


def changelog_versions(path: Path) -> list[str]:
    """Versions of the ``## [x.y.z]`` sections of a Keep a Changelog file, in file order."""
    text = path.read_text(encoding="utf-8")
    return [match.group("version").strip() for match in _CHANGELOG_SECTION_RE.finditer(text)]


def uncommitted_files(root: Path) -> list[str] | None:
    """Files under ``hamq/`` (and ``LICENSE``) whose working copy differs from the last
    commit: modified, added, deleted, renamed or untracked, as ``git status`` reports them.
    Files that are never packed (caches, hidden files, data files) and ignored files do
    not count. ``None`` when ``root`` is not the top of a git checkout or git is not
    available."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all"]
            + ["--", PLUGIN_DIR_NAME, "LICENSE"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    entries = status.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":  # a rename or copy: the source path follows as its own entry
            index += 1
        path = entry[3:]
        parts = Path(path).parts
        if parts and parts[0] == PLUGIN_DIR_NAME:
            relative = Path(*parts[1:])
            if is_excluded(relative) or is_data_file(relative):
                continue
        paths.append(path)
    return paths


def release_problems(root: Path, metadata: dict[str, str]) -> list[str]:
    """What a published zip needs besides :func:`validate`: the CHANGELOG section and the
    metadata ``changelog`` of the version, and committed sources (``--release``)."""
    problems: list[str] = []
    version = metadata.get("version", "").strip()
    changelog_path = root / "CHANGELOG.md"
    if not changelog_path.is_file():
        problems.append(f"{changelog_path} is missing")
    else:
        try:
            versions = changelog_versions(changelog_path)
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{changelog_path}: cannot be read ({exc})")
        else:
            if version not in versions:
                problems.append(
                    f"CHANGELOG.md has no '## [{version}]' section (found: "
                    f"{', '.join(versions) or 'none'}); rename '## [Unreleased]' when releasing"
                )
    changelog = metadata.get("changelog", "").strip()
    if not changelog:
        problems.append(
            "metadata.txt: 'changelog' is missing; plugins.qgis.org and the QGIS plugin "
            "manager show it (plain text, e.g. 'changelog=0.1.0: first release ...')"
        )
    else:
        if version and version not in changelog:
            problems.append(f"metadata.txt: 'changelog' does not mention version {version}")
        if _HTML_TAG_RE.search(changelog):
            problems.append("metadata.txt: 'changelog' must be plain text, without HTML tags")
    pending = uncommitted_files(root)
    if pending is None:
        problems.append(
            f"{root} is not a git checkout (or git is not available): build a release from "
            "a clean checkout so that the zip holds the committed sources"
        )
    elif pending:
        shown = ", ".join(pending[:10])
        more = f" and {len(pending) - 10} more" if len(pending) > 10 else ""
        problems.append(
            f"uncommitted or untracked files under {PLUGIN_DIR_NAME}/ or LICENSE: {shown}{more}; "
            "commit them (or remove them) before building a release"
        )
    return problems


def is_excluded(relative: Path) -> bool:
    """True for files that must not be shipped (caches, tests, hidden files)."""
    for part in relative.parts:
        if part in EXCLUDED_DIRS or part.startswith("."):
            return True
    return relative.name in EXCLUDED_NAMES or relative.name.endswith(EXCLUDED_SUFFIXES)


def is_data_file(relative: Path) -> bool:
    """True for data that must never ship: cty.dat / cty.csv, logs and databases."""
    name = relative.name.lower()
    return (
        name in DATA_FILE_NAMES
        or name.endswith(DATA_FILE_SUFFIXES)
        or _GEOPACKAGE_RE.search(name) is not None
    )


def is_allowed_type(relative: Path) -> bool:
    """True for the file types the plugin is made of (:data:`ALLOWED_SUFFIXES`)."""
    return relative.name in ALLOWED_NAMES or relative.suffix.lower() in ALLOWED_SUFFIXES


class Scan(NamedTuple):
    """The files under ``hamq/``, relative to it and sorted: what is packed, the data files
    that are skipped and the files of an unexpected type (which stop the build)."""

    files: list[Path]
    data: list[Path]
    unexpected: list[Path]


def scan(plugin_dir: Path) -> Scan:
    """Sort the files of the plugin package (see :class:`Scan`); excluded ones are left out."""
    result = Scan([], [], [])
    if not plugin_dir.is_dir():
        return result
    for path in sorted(plugin_dir.rglob("*")):
        relative = path.relative_to(plugin_dir)
        if not path.is_file() or is_excluded(relative):
            continue
        if is_data_file(relative):
            result.data.append(relative)
        elif is_allowed_type(relative):
            result.files.append(relative)
        else:
            result.unexpected.append(relative)
    return result


def plugin_files(plugin_dir: Path) -> list[Path]:
    """Files of the plugin package to ship, relative to ``plugin_dir``, sorted."""
    return scan(plugin_dir).files


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
    parser.add_argument(
        "--release",
        action="store_true",
        help="also check what a published zip needs: the CHANGELOG.md section and the "
        "metadata changelog of the version, and committed sources",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = (args.output_dir or root / "dist").resolve()

    metadata, problems = validate(root)
    if args.release and metadata:
        problems.extend(release_problems(root, metadata))
    if problems:
        print("package.py: the plugin cannot be packaged:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    for relative in scan(root / PLUGIN_DIR_NAME).data:
        print(
            f"package.py: not packed (data file, never shipped): "
            f"{PLUGIN_DIR_NAME}/{relative.as_posix()}",
            file=sys.stderr,
        )
    zip_path = build(root, output_dir, metadata["version"].strip())
    with zipfile.ZipFile(zip_path) as archive:
        count = len(archive.namelist())
    print(f"built {zip_path} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
