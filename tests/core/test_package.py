"""scripts/package.py builds a valid plugin zip and refuses broken metadata."""

from __future__ import annotations

import configparser
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "package.py"


def _load_package_module():
    spec = importlib.util.spec_from_file_location("hamq_package_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


package = _load_package_module()


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def metadata_version() -> str:
    return package.read_metadata(REPO_ROOT / "hamq" / "metadata.txt")["version"]


@pytest.fixture(scope="module")
def built_zip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    result = run_script("--output-dir", str(out))
    assert result.returncode == 0, result.stderr
    zip_path = out / f"hamq-{metadata_version()}.zip"
    assert zip_path.is_file()
    assert str(zip_path) in result.stdout
    return zip_path


def test_zip_is_valid(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        assert archive.testzip() is None


def test_single_top_folder(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        names = archive.namelist()
    assert names
    assert {name.split("/")[0] for name in names} == {"hamq"}


def test_required_files_are_included(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        names = set(archive.namelist())
    for name in (
        "hamq/__init__.py",
        "hamq/plugin.py",
        "hamq/metadata.txt",
        "hamq/LICENSE",
        "hamq/core/bands.py",
        "hamq/qgis_io/compat.py",
        "hamq/processing/provider.py",
        "hamq/resources/icons/hamq.svg",
        "hamq/i18n/sr_Latn/plugin.json",
    ):
        assert name in names, name


def test_unwanted_files_are_excluded(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        names = archive.namelist()
    for name in names:
        parts = name.split("/")
        assert "__pycache__" not in parts, name
        assert "tests" not in parts, name
        assert "dist" not in parts, name
        assert not any(part.startswith(".") for part in parts), name
        assert not name.endswith((".pyc", ".pyo")), name


def test_license_is_the_gpl(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        text = archive.read("hamq/LICENSE").decode("utf-8")
    assert text == (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text


def test_metadata_in_zip(built_zip):
    with zipfile.ZipFile(built_zip) as archive:
        text = archive.read("hamq/metadata.txt").decode("utf-8")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    general = dict(parser.items("general"))
    assert general["name"] == "HamQ"
    assert general["qgisMinimumVersion"] == "3.34"
    assert general["qgisMaximumVersion"] == "4.99"
    assert general["supportsQt6"] == "True"
    assert general["hasProcessingProvider"] == "yes"
    assert built_zip.name == f"hamq-{general['version']}.zip"


def test_repository_metadata_is_valid():
    metadata, problems = package.validate(REPO_ROOT)
    assert problems == []
    assert metadata["version"] == package.pyproject_version(REPO_ROOT / "pyproject.toml")


@pytest.mark.parametrize(
    ("relative", "excluded"),
    [
        ("plugin.py", False),
        ("resources/icons/hamq.svg", False),
        ("core/__pycache__/bands.cpython-312.pyc", True),
        ("core/bands.pyc", True),
        ("tests/test_x.py", True),
        (".gitignore", True),
        (".github/workflows/ci.yml", True),
        ("dist/hamq-0.1.0.zip", True),
        ("core/bands.py~", True),
    ],
)
def test_is_excluded(relative, excluded):
    assert package.is_excluded(Path(relative)) is excluded


# --- broken sources ------------------------------------------------------------------


@pytest.fixture
def fake_root(tmp_path) -> Path:
    """A copy of the parts of the repository the script reads."""
    root = tmp_path / "repo"
    plugin = root / "hamq"
    (plugin / "resources" / "icons").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "hamq" / "__init__.py", plugin / "__init__.py")
    shutil.copy(REPO_ROOT / "hamq" / "metadata.txt", plugin / "metadata.txt")
    shutil.copy(
        REPO_ROOT / "hamq" / "resources" / "icons" / "hamq.svg",
        plugin / "resources" / "icons" / "hamq.svg",
    )
    shutil.copy(REPO_ROOT / "LICENSE", root / "LICENSE")
    shutil.copy(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "x.cpython-39.pyc").write_bytes(b"\0")
    return root


def _edit_metadata(root: Path, old: str, new: str) -> None:
    path = root / "hamq" / "metadata.txt"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_fake_root_builds(fake_root, tmp_path):
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(tmp_path / "out" / f"hamq-{metadata_version()}.zip") as archive:
        assert sorted(archive.namelist()) == [
            "hamq/LICENSE",
            "hamq/__init__.py",
            "hamq/metadata.txt",
            "hamq/resources/icons/hamq.svg",
        ]


def test_default_output_dir_is_dist(fake_root):
    result = run_script("--root", str(fake_root))
    assert result.returncode == 0, result.stderr
    assert (fake_root / "dist" / f"hamq-{metadata_version()}.zip").is_file()


def test_missing_required_key_fails(fake_root, tmp_path):
    _edit_metadata(fake_root, "email=", "e_mail=")
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 1
    assert "required key 'email'" in result.stderr
    assert not (tmp_path / "out").exists()


def test_missing_icon_fails(fake_root, tmp_path):
    (fake_root / "hamq" / "resources" / "icons" / "hamq.svg").unlink()
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 1
    assert "icon 'resources/icons/hamq.svg' does not exist" in result.stderr


def test_icon_outside_the_plugin_fails(fake_root):
    _edit_metadata(fake_root, "icon=resources/icons/hamq.svg", "icon=../LICENSE")
    _metadata, problems = package.validate(fake_root)
    assert any("icon '../LICENSE'" in problem for problem in problems)


def test_version_mismatch_fails(fake_root, tmp_path):
    version = metadata_version()
    _edit_metadata(fake_root, f"version={version}", "version=9.9.9")
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 1
    assert f"metadata.txt has 9.9.9, pyproject.toml has {version}" in result.stderr


def test_bad_version_and_qt6_flag_fail(fake_root):
    _edit_metadata(fake_root, f"version={metadata_version()}", "version=latest")
    _edit_metadata(fake_root, "supportsQt6=True", "supportsQt6=False")
    _metadata, problems = package.validate(fake_root)
    assert any("version 'latest'" in problem for problem in problems)
    assert any("supportsQt6" in problem for problem in problems)


def test_missing_metadata_fails(fake_root, tmp_path):
    (fake_root / "hamq" / "metadata.txt").unlink()
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 1
    assert "cannot read metadata" in result.stderr


def test_missing_license_and_class_factory_fail(fake_root):
    (fake_root / "LICENSE").unlink()
    (fake_root / "hamq" / "__init__.py").write_text('"""no entry point"""\n', encoding="utf-8")
    _metadata, problems = package.validate(fake_root)
    assert any("LICENSE" in problem for problem in problems)
    assert any("classFactory" in problem for problem in problems)


def test_pyproject_version_parser(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.other]\nversion = "0.0.1"\n\n[project]\nname = "x"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    assert package.pyproject_version(path) == "1.2.3"
    path.write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert package.pyproject_version(path) is None


# --- data files, unexpected files and --release ------------------------------------------


@pytest.mark.parametrize(
    ("relative", "data"),
    [
        ("cty.dat", True),
        ("resources/CTY.CSV", True),
        ("hamq.gpkg", True),
        ("hamq.gpkg-wal", True),
        ("hamq.gpkg-shm", True),
        ("logs/my log.adi", True),
        ("old.sqlite", True),
        ("cty.dat.1234.part", True),
        ("plugin.py", False),
        ("i18n/sr_Latn/plugin.json", False),
        ("resources/styles/qso.qml", False),
        ("gpkg.py", False),
    ],
)
def test_is_data_file(relative, data):
    assert package.is_data_file(Path(relative)) is data


def test_data_files_are_never_packed(fake_root, tmp_path):
    plugin = fake_root / "hamq"
    for name in ("cty.dat", "cty.csv", "hamq.gpkg", "hamq.gpkg-wal", "log.adi"):
        (plugin / name).write_text("data", encoding="utf-8")
    (plugin / "resources" / "cty.csv").write_text("data", encoding="utf-8")
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(tmp_path / "out" / f"hamq-{metadata_version()}.zip") as archive:
        names = archive.namelist()
    assert sorted(names) == [
        "hamq/LICENSE",
        "hamq/__init__.py",
        "hamq/metadata.txt",
        "hamq/resources/icons/hamq.svg",
    ]
    for name in ("cty.dat", "cty.csv", "hamq.gpkg", "hamq.gpkg-wal", "log.adi"):
        assert f"not packed (data file, never shipped): hamq/{name}" in result.stderr
    assert "hamq/resources/cty.csv" in result.stderr


def test_unexpected_file_type_fails(fake_root, tmp_path):
    (fake_root / "hamq" / "notes.docx").write_bytes(b"PK")
    (fake_root / "hamq" / "resources" / "tool.exe").write_bytes(b"MZ")
    result = run_script("--root", str(fake_root), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 1
    assert "hamq/notes.docx: unexpected file type" in result.stderr
    assert "hamq/resources/tool.exe: unexpected file type" in result.stderr
    assert not (tmp_path / "out").exists()


def test_scan_sorts_the_files(fake_root):
    plugin = fake_root / "hamq"
    (plugin / "cty.dat").write_text("data", encoding="utf-8")
    (plugin / "x.bin").write_bytes(b"\0")
    scanned = package.scan(plugin)
    assert scanned.files == [
        Path("__init__.py"),
        Path("metadata.txt"),
        Path("resources/icons/hamq.svg"),
    ]
    assert scanned.data == [Path("cty.dat")]
    assert scanned.unexpected == [Path("x.bin")]
    assert package.plugin_files(plugin) == scanned.files
    assert package.scan(fake_root / "missing") == package.Scan([], [], [])


def test_changelog_versions(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2027-01-01\n\n- x\n\n"
        "## [0.1.0] - 2026-10-01\n\n### Added\n\n- [link](https://example.org) [0.0.9]\n",
        encoding="utf-8",
    )
    assert package.changelog_versions(path) == ["Unreleased", "0.2.0", "0.1.0"]


def _release_ready(root: Path) -> None:
    version = metadata_version()
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-10-01\n\n### Added\n\n- HamQ\n",
        encoding="utf-8",
    )
    path = root / "hamq" / "metadata.txt"
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("changelog=")
    ]
    lines.append(f"changelog={version}: first experimental release")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_release_needs_changelog_and_metadata_changelog(fake_root):
    metadata = package.read_metadata(fake_root / "hamq" / "metadata.txt")
    metadata.pop("changelog", None)
    problems = package.release_problems(fake_root, metadata)
    assert any("CHANGELOG.md" in problem and "is missing" in problem for problem in problems)
    assert any("'changelog' is missing" in problem for problem in problems)
    assert any("not a git checkout" in problem for problem in problems)

    (fake_root / "CHANGELOG.md").write_text("## [Unreleased]\n\n- x\n", encoding="utf-8")
    metadata["changelog"] = "<b>9.9.9</b>: bold"
    problems = package.release_problems(fake_root, metadata)
    version = metadata["version"]
    assert any(f"no '## [{version}]' section (found: Unreleased)" in p for p in problems)
    assert any(f"does not mention version {version}" in p for p in problems)
    assert any("without HTML tags" in p for p in problems)


def test_release_fails_without_the_version_section(fake_root, tmp_path):
    result = run_script(
        "--root", str(fake_root), "--output-dir", str(tmp_path / "out"), "--release"
    )
    assert result.returncode == 1
    assert "CHANGELOG.md" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_release_from_a_clean_git_checkout(fake_root, tmp_path):
    _release_ready(fake_root)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(fake_root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    (fake_root / "hamq" / "extra.py").write_text('"""Extra."""\n', encoding="utf-8")
    git("init", "-q")
    git("add", "hamq/__init__.py", "hamq/extra.py", "hamq/metadata.txt", "hamq/resources")
    git("add", "LICENSE")
    git(
        *("-c", "user.name=HamQ", "-c", "user.email=hamq@example.org"),
        *("-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "x"),
    )
    out = tmp_path / "out"
    result = run_script("--root", str(fake_root), "--output-dir", str(out), "--release")
    assert result.returncode == 0, result.stderr

    (fake_root / "hamq" / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    result = run_script("--root", str(fake_root), "--output-dir", str(out), "--release")
    assert result.returncode == 1
    assert "uncommitted or untracked files" in result.stderr
    assert "hamq/scratch.py" in result.stderr
    (fake_root / "hamq" / "scratch.py").unlink()
    (fake_root / "hamq" / "extra.py").unlink()  # a committed file missing from the zip
    result = run_script("--root", str(fake_root), "--output-dir", str(out), "--release")
    assert result.returncode == 1
    assert "hamq/extra.py" in result.stderr
    git("checkout", "-q", "--", "hamq/extra.py")
    # an ignored data file is skipped, not reported as uncommitted
    (fake_root / ".gitignore").write_text("cty.dat\n", encoding="utf-8")
    (fake_root / "hamq" / "cty.dat").write_text("data", encoding="utf-8")
    result = run_script("--root", str(fake_root), "--output-dir", str(out), "--release")
    assert result.returncode == 0, result.stderr
    assert "not packed (data file, never shipped): hamq/cty.dat" in result.stderr
