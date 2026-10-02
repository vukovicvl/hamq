"""scripts/qgis_repo_scan.py runs the plugins.qgis.org scan with the site's commands and rules.

The scan tools themselves are not needed here: their output is replaced by canned reports.
The real run (Bandit, detect-secrets, Flake8 in Docker) is in docs/RELEASING.md and CI.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "qgis_repo_scan.py"


def _load_scan_module():
    spec = importlib.util.spec_from_file_location("hamq_qgis_repo_scan_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


repo_scan = _load_scan_module()


def make_zip(path: Path, entries: dict[str, tuple[bytes, int]]) -> Path:
    """A zip with ``name -> (content, unix mode)`` entries."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, (content, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, content)
    return path


@pytest.fixture
def clean_zip(tmp_path) -> Path:
    return make_zip(
        tmp_path / "clean.zip",
        {
            "hamq/__init__.py": (b"def classFactory(iface):\n    return None\n", 0o644),
            "hamq/metadata.txt": (b"[general]\nname=HamQ\n", 0o644),
            "hamq/resources/icons/hamq.png": (b"\x89PNG\r\n\x1a\n", 0o644),
        },
    )


def test_rule_lists_match_the_site():
    assert len(repo_scan.BANDIT_ENABLED) == 74
    assert len(set(repo_scan.BANDIT_ENABLED)) == 74
    assert not {"B109", "B404", "B410", "B411"} & set(repo_scan.BANDIT_ENABLED)
    assert len(repo_scan.FLAKE8_ENABLED) == 26
    assert len(set(repo_scan.FLAKE8_ENABLED)) == 26
    assert not set(repo_scan.FLAKE8_MARGIN) & set(repo_scan.FLAKE8_ENABLED)
    assert repo_scan.SECRETS_DISABLED == ("IPPublicDetector",)


def test_commands_are_those_of_the_site(tmp_path):
    bandit = repo_scan.bandit_command("bandit", tmp_path)
    assert bandit[:6] == ["bandit", "-r", str(tmp_path), "-f", "json", "--quiet"]
    assert bandit[6] == "-t"
    assert bandit[7] == ",".join(sorted(repo_scan.BANDIT_ENABLED))
    assert "-ll" not in bandit  # every severity of the enabled tests counts

    secrets = repo_scan.secrets_command("detect-secrets", tmp_path)
    assert secrets[:3] == ["detect-secrets", "scan", "--all-files"]
    assert secrets[3:7] == [
        "--exclude-files",
        r"metadata\.txt",
        "--exclude-files",
        r"\.secrets\.baseline",
    ]
    assert secrets[-3:] == ["--disable-plugin", "IPPublicDetector", "."]
    assert "--baseline" not in secrets

    flake8 = repo_scan.flake8_command("flake8", [tmp_path / "a.py"])
    assert flake8[1:3] == ["--format=json", "--max-line-length=120"]
    selected = flake8[flake8.index("--select") + 1].split(",")
    assert set(selected) == set(repo_scan.FLAKE8_ENABLED) | {"E203", "E501"}
    assert flake8[-1] == str(tmp_path / "a.py")


def test_secrets_command_passes_a_shipped_baseline(tmp_path):
    (tmp_path / "hamq").mkdir()
    (tmp_path / "hamq" / ".secrets.baseline").write_text("{}", encoding="utf-8")
    secrets = repo_scan.secrets_command("detect-secrets", tmp_path)
    assert secrets[secrets.index("--baseline") + 1] == "hamq/.secrets.baseline"


def test_parse_bandit(tmp_path):
    report = {
        "results": [
            {
                "test_id": "B608",
                "filename": str(tmp_path / "hamq" / "qgis_io" / "gpkg.py"),
                "line_number": 470,
                "issue_severity": "MEDIUM",
                "issue_confidence": "LOW",
                "issue_text": "Possible SQL injection vector.",
            }
        ],
        "errors": [{"filename": str(tmp_path / "hamq" / "bad.py"), "reason": "syntax error"}],
    }
    findings = repo_scan.parse_bandit(json.dumps(report), tmp_path)
    assert findings[0] == repo_scan.Finding(
        "B608", "hamq/qgis_io/gpkg.py", 470, "[MEDIUM/LOW] Possible SQL injection vector."
    )
    assert findings[1].rule == "ERROR"  # a file Bandit could not check fails the scan
    assert repo_scan.parse_bandit('{"results": [], "errors": []}', tmp_path) == []
    with pytest.raises(repo_scan.ToolError):
        repo_scan.parse_bandit("Traceback ...", tmp_path)


def test_parse_secrets_skips_compiled_files():
    report = {
        "results": {
            "hamq/a.py": [{"type": "Secret Keyword", "line_number": 3}],
            "hamq/b.pyc": [{"type": "Hex High Entropy String", "line_number": 1}],
        }
    }
    findings = repo_scan.parse_secrets(json.dumps(report))
    assert findings == [repo_scan.Finding("Secret Keyword", "hamq/a.py", 3, "")]
    assert repo_scan.parse_secrets('{"results": {}}') == []


def test_parse_flake8(tmp_path):
    report = {
        str(tmp_path / "hamq" / "core" / "qso.py"): [
            {"code": "E501", "line_number": 420, "text": "line too long (129 > 120 characters)"}
        ],
        str(tmp_path / "hamq" / "plugin.py"): [],
    }
    findings = repo_scan.parse_flake8(json.dumps(report), tmp_path)
    assert findings == [
        repo_scan.Finding("E501", "hamq/core/qso.py", 420, "line too long (129 > 120 characters)")
    ]
    assert repo_scan.parse_flake8("{}", tmp_path) == []
    assert repo_scan.by_rule(findings * 3) == "3 E501"


def test_file_checks_clean(clean_zip):
    assert repo_scan.check_files(clean_zip) == ([], [])


def test_file_checks_find_what_the_site_and_the_margin_report(tmp_path):
    bad = make_zip(
        tmp_path / "bad.zip",
        {
            "hamq/run.py": (b"print(1)\n", 0o755),
            "hamq/tool.sh": (b"#!/bin/sh\n", 0o644),
            "hamq/lib.so": (b"\x7fELF\x02", 0o644),
            "hamq/notes.txt": (b"text\n", 0o755),
            "hamq/.bandit": (b"[bandit]\n", 0o644),
            "hamq/.cache/x.json": (b"{}", 0o644),
            "hamq/core/x.pyc": (b"\x00\x00", 0o644),
            "hamq/blob.png": (b"MZ\x90\x00", 0o644),
        },
    )
    site, margin = repo_scan.check_files(bad)
    assert sorted((f.rule, f.path) for f in site) == [
        ("FILE_EXECUTABLE", "hamq/run.py"),
        ("FILE_SUSPICIOUS", "hamq/lib.so"),
        ("FILE_SUSPICIOUS", "hamq/tool.sh"),
    ]
    assert sorted((f.rule, f.path) for f in margin) == [
        ("FILE_BINARY", "hamq/blob.png"),
        ("FILE_BINARY", "hamq/core/x.pyc"),
        ("FILE_BINARY", "hamq/lib.so"),
        ("FILE_EXECUTABLE", "hamq/notes.txt"),
        ("FILE_HIDDEN", "hamq/.bandit"),
        ("FILE_HIDDEN", "hamq/.cache/x.json"),
    ]


def test_the_package_zip_passes_the_file_checks(tmp_path):
    zip_path = repo_scan.build_zip(REPO_ROOT, tmp_path / "dist")
    assert zip_path.name.startswith("hamq-")
    assert repo_scan.check_files(zip_path) == ([], [])


def test_missing_tool_exits_2_with_the_pip_command(monkeypatch, capsys):
    monkeypatch.setattr(repo_scan, "find_tool", lambda name: None if name == "bandit" else name)
    monkeypatch.setattr(repo_scan, "tool_version", lambda path: "7.3.0 (flake8-json: 24.4.0)")
    assert repo_scan.main([]) == 2
    err = capsys.readouterr().err
    assert "not installed: bandit" in err
    assert '"bandit~=1.9" "detect-secrets~=1.5" "flake8~=7.3" "flake8-json~=24.4"' in err


def test_flake8_without_flake8_json_is_a_missing_tool():
    tools = {"bandit": "bandit", "detect-secrets": "detect-secrets", "flake8": "flake8"}
    assert repo_scan.missing_tools(tools, "7.3.0 (mccabe: 0.7.0, pyflakes: 3.4.0)") == [
        "flake8-json"
    ]
    assert repo_scan.missing_tools(tools, "7.3.0 (flake8-json: 24.4.0)") == []


def _fake_tools(monkeypatch, outputs: dict[str, str]) -> None:
    monkeypatch.setattr(repo_scan, "find_tool", lambda name: name)
    monkeypatch.setattr(repo_scan, "tool_version", lambda path: f"{path} 1 (flake8-json: 24.4.0)")
    monkeypatch.setattr(repo_scan, "_run", lambda cmd, cwd=None: outputs[cmd[0]])


CLEAN_OUTPUTS = {
    "bandit": '{"results": [], "errors": []}',
    "detect-secrets": '{"results": {}}',
    "flake8": "{}",
}


def test_clean_scan_exits_0(monkeypatch, capsys, clean_zip):
    _fake_tools(monkeypatch, CLEAN_OUTPUTS)
    assert repo_scan.main(["--zip", str(clean_zip)]) == 0
    out = capsys.readouterr().out
    assert "Bandit (74 rules of the site): OK, no findings" in out
    assert "Result: OK" in out


def test_margin_finding_fails_and_says_the_site_disables_it(monkeypatch, capsys, clean_zip):
    flake8 = {"/x/hamq/a.py": [{"code": "E203", "line_number": 3, "text": "whitespace"}]}
    _fake_tools(monkeypatch, {**CLEAN_OUTPUTS, "flake8": json.dumps(flake8)})
    assert repo_scan.main(["--zip", str(clean_zip)]) == 1
    out = capsys.readouterr().out
    assert "codes of the site, --max-line-length=120): OK, no findings" in out
    assert "Flake8 margin E203 and E501: FAIL, 1: 1 E203" in out
    assert "disabled on the site today" in out
    assert "Result: FAIL" in out


def test_tool_failure_fails_the_scan(monkeypatch, capsys, clean_zip):
    _fake_tools(monkeypatch, {**CLEAN_OUTPUTS, "bandit": "not json"})
    assert repo_scan.main(["--zip", str(clean_zip)]) == 1
    assert "Bandit: FAIL, Bandit wrote no JSON report" in capsys.readouterr().out


def test_not_a_zip_fails(monkeypatch, tmp_path, capsys):
    _fake_tools(monkeypatch, CLEAN_OUTPUTS)
    path = tmp_path / "x.zip"
    path.write_text("not a zip", encoding="utf-8")
    assert repo_scan.main(["--zip", str(path)]) == 1
    assert "is not a zip file" in capsys.readouterr().err


def test_help_runs():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0
    assert "--zip" in result.stdout
