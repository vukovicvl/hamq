#!/usr/bin/env python3
"""Run the automatic scan of plugins.qgis.org on a HamQ plugin zip.

Usage::

    python3 scripts/qgis_repo_scan.py               # builds the zip with scripts/package.py
    python3 scripts/qgis_repo_scan.py --zip dist/hamq-0.1.1.zip

plugins.qgis.org scans every uploaded zip (``qgis-app/plugins/security_scanner.py`` of
``qgis/QGIS-Plugins-Website``) and blocks a version on any result of a critical check.
This script runs the same commands with the same options and rules on the extracted zip
and fails on any result, like the site:

- Bandit: ``bandit -r <dir> -f json --quiet -t <the enabled rules>``. Any result fails,
  whatever its own severity; a ``# nosec`` comment is honoured, as on the site.
- detect-secrets: ``detect-secrets scan --all-files`` in the extracted folder, with
  ``metadata.txt`` and ``.secrets.baseline`` excluded and the disabled detectors turned
  off with ``--disable-plugin``.
- Flake8 (with flake8-json): ``--max-line-length=120 --select <the enabled codes>`` on
  every ``.py`` file. HamQ also selects E203 and E501, which the site disables today: a
  stricter margin, reported separately, that fails too.
- File checks of ``_check_file_permissions`` and ``_check_suspicious_files``: an
  executable ``.py`` file and a file with a suspicious extension (``.exe``, ``.so``,
  ``.sh``, ...). HamQ also fails on any executable file, any hidden file or folder
  (the site's rule is disabled today and allows ``.bandit``, ``.flake8`` and
  ``.secrets.baseline``, which would tune the scan) and a binary file (an executable
  format or Python bytecode; the site lists the rule FILE_BINARY but has no check for it).

The Qt6 check of the site (``pyqt5_to_pyqt6.py --dry_run``) needs its own Docker image;
its command is in ``docs/RELEASING.md``.

Exit status: 0 when every check is clean, 1 on any finding (or when a tool fails), 2 when
a tool is missing (the message names the pip command with the site's versions).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import NamedTuple

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = Path(__file__).resolve().with_name("package.py")

# The site's tools and rules, as of 2026-10-02, from qgis/QGIS-Plugins-Website:
# dockerize/docker/REQUIREMENTS.txt (versions), qgis-app/plugins/security_scanner.py
# (commands) and qgis-app/plugins/management/commands/data/bandit_rules.json,
# flake8_rules.json, secrets_rules.json and file_analysis_rules.json (rules with
# "enabled": true). Update them together when the site changes its rules.

#: pip requirements of the scan tools, pinned like the site.
TOOL_REQUIREMENTS = ("bandit~=1.9", "detect-secrets~=1.5", "flake8~=7.3", "flake8-json~=24.4")
#: Bandit tests the site runs (74 of 78; B109, B404, B410 and B411 are disabled).
BANDIT_ENABLED = (
    "B101,B102,B103,B104,B105,B106,B107,B108,B110,B111,B112,B113,B201,B202,B301,B302,"
    "B303,B304,B305,B306,B307,B308,B310,B311,B312,B313,B314,B315,B316,B317,B318,B319,"
    "B320,B321,B323,B324,B401,B402,B403,B405,B406,B407,B408,B409,B412,B413,B501,B502,"
    "B503,B504,B505,B506,B507,B508,B509,B601,B602,B603,B604,B605,B606,B607,B608,B609,"
    "B610,B611,B612,B613,B614,B615,B701,B702,B703,B704"
).split(",")
#: detect-secrets detectors the site turns off (26 of its 27 detectors stay on).
SECRETS_DISABLED = ("IPPublicDetector",)
#: Flake8 codes the site selects (26 of 96; C901 is never reported, as the site runs
#: Flake8 without --max-complexity).
FLAKE8_ENABLED = (
    "C901,E101,E711,E712,E713,E714,E721,E722,E731,E741,E742,E743,E901,E902,E999,"
    "F402,F403,F404,F405,F811,F821,F822,F823,F831,F901,W605"
).split(",")
#: HamQ's stricter margin: disabled on the site today, selected here too.
FLAKE8_MARGIN = ("E203", "E501")
FLAKE8_MAX_LINE_LENGTH = 120
#: Extensions ``_check_suspicious_files`` reports (FILE_SUSPICIOUS).
SUSPICIOUS_EXTENSIONS = (".exe", ".dll", ".so", ".dylib", ".bat", ".sh", ".ps1", ".cmd")
#: Python bytecode and native modules: binary files (HamQ's margin, FILE_BINARY).
BINARY_EXTENSIONS = (".pyc", ".pyo", ".pyd")
#: Start of executable formats (HamQ's margin, FILE_BINARY): ELF, Windows PE and Mach-O
#: (32 and 64 bit, both byte orders, universal).
BINARY_MAGIC = (
    b"\x7fELF",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)
TOOL_TIMEOUT = 600


class Finding(NamedTuple):
    """One result of a check: the rule, the file (relative to the zip) and line, a text."""

    rule: str
    path: str
    line: int
    text: str


class ToolError(Exception):
    """A scan tool ran but its output could not be used."""


def pip_command() -> str:
    """The pip command that installs the scan tools with the site's versions."""
    return "python3 -m pip install " + " ".join(f'"{req}"' for req in TOOL_REQUIREMENTS)


def find_tool(name: str) -> str | None:
    """Path of a console script: on PATH, else next to this Python (a venv not activated)."""
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def tool_version(executable: str) -> str:
    """The version line of ``<tool> --version``; Flake8 lists its plugins there, in
    parentheses that may wrap onto the next lines."""
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    lines = [line.strip() for line in (result.stdout or result.stderr).strip().splitlines()]
    if not lines:
        return "?"
    text = " ".join(lines)
    if "(" in lines[0] and ")" in text:
        return text[: text.index(")") + 1]
    return lines[0]


def missing_tools(tools: dict[str, str | None], flake8_version: str) -> list[str]:
    """Names of the tools (and the flake8-json plugin) that are not installed."""
    missing = [name for name, path in tools.items() if path is None]
    if tools.get("flake8") and "flake8-json" not in flake8_version:
        missing.append("flake8-json")
    return missing


def _relative(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a scan tool and return its standard output (its exit status is 1 on findings)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"{Path(cmd[0]).name} could not run: {exc}") from exc
    if not result.stdout.strip() and result.stderr.strip():
        raise ToolError(f"{Path(cmd[0]).name} failed: {result.stderr.strip()[:500]}")
    return result.stdout


def bandit_command(bandit: str, directory: Path) -> list[str]:
    """The site's Bandit command (``_check_with_bandit``)."""
    return [
        bandit,
        "-r",
        str(directory),
        "-f",
        "json",
        "--quiet",
        "-t",
        ",".join(sorted(BANDIT_ENABLED)),
    ]


def parse_bandit(stdout: str, base: Path) -> list[Finding]:
    """Bandit results, and files Bandit could not scan (the site ignores those; here they
    fail, since such a file is not checked)."""
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Bandit wrote no JSON report: {stdout[:200]!r}") from exc
    findings = [
        Finding(
            result.get("test_id", "?"),
            _relative(result.get("filename", ""), base),
            int(result.get("line_number", 0) or 0),
            f"[{result.get('issue_severity', '?')}/{result.get('issue_confidence', '?')}] "
            f"{result.get('issue_text', '')}",
        )
        for result in report.get("results", [])
    ]
    findings.extend(
        Finding("ERROR", _relative(error.get("filename", ""), base), 0, error.get("reason", ""))
        for error in report.get("errors", [])
    )
    return findings


def secrets_command(detect_secrets: str, directory: Path) -> list[str]:
    """The site's detect-secrets command (``_check_secrets``); it runs in ``directory``."""
    cmd = [
        detect_secrets,
        "scan",
        "--all-files",
        "--exclude-files",
        r"metadata\.txt",
        "--exclude-files",
        r"\.secrets\.baseline",
    ]
    baselines = sorted(directory.rglob(".secrets.baseline"))
    if baselines:  # the site passes the first one it finds; package.py never packs one
        cmd += ["--baseline", baselines[0].relative_to(directory).as_posix()]
    for detector in SECRETS_DISABLED:
        cmd += ["--disable-plugin", detector]
    return [*cmd, "."]


def parse_secrets(stdout: str) -> list[Finding]:
    """detect-secrets results; like the site, results in compiled files are left out."""
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"detect-secrets wrote no JSON report: {stdout[:200]!r}") from exc
    findings: list[Finding] = []
    for path, secrets in sorted(report.get("results", {}).items()):
        if path.endswith((".pyc", ".pyo", ".so", ".dll", ".exe")):
            continue
        findings.extend(
            Finding(secret.get("type", "secret"), path, int(secret.get("line_number", 0) or 0), "")
            for secret in secrets
        )
    return findings


def python_files(directory: Path) -> list[Path]:
    """Every ``.py`` file under ``directory``, sorted."""
    return sorted(path for path in directory.rglob("*.py") if path.is_file())


def flake8_command(flake8: str, files: list[Path]) -> list[str]:
    """The site's Flake8 command (``_check_code_quality``) with HamQ's margin selected too;
    ``--select`` only filters, so the site's results are those with its codes."""
    codes = sorted({*FLAKE8_ENABLED, *FLAKE8_MARGIN})
    return [
        flake8,
        "--format=json",
        f"--max-line-length={FLAKE8_MAX_LINE_LENGTH}",
        "--select",
        ",".join(codes),
        *(str(path) for path in files),
    ]


def parse_flake8(stdout: str, base: Path) -> list[Finding]:
    """Flake8 results of the flake8-json report."""
    if not stdout.strip():
        return []
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Flake8 wrote no JSON report: {stdout[:200]!r}") from exc
    return [
        Finding(
            issue.get("code", "?"),
            _relative(path, base),
            int(issue.get("line_number", 0) or 0),
            issue.get("text", ""),
        )
        for path, issues in sorted(report.items())
        for issue in issues
    ]


def check_files(zip_path: Path) -> tuple[list[Finding], list[Finding]]:
    """The file checks on the zip entries: (the site's findings, HamQ's margin)."""
    site: list[Finding] = []
    margin: list[Finding] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if mode & 0o111:
                if name.endswith(".py"):
                    site.append(Finding("FILE_EXECUTABLE", name, 0, "executable Python file"))
                else:
                    margin.append(Finding("FILE_EXECUTABLE", name, 0, "executable file"))
            lower = name.lower()
            suspicious = next((ext for ext in SUSPICIOUS_EXTENSIONS if lower.endswith(ext)), None)
            if suspicious:
                site.append(Finding("FILE_SUSPICIOUS", name, 0, f"suspicious type ({suspicious})"))
            if any(part.startswith(".") for part in name.split("/") if part):
                margin.append(Finding("FILE_HIDDEN", name, 0, "hidden file or folder"))
            if lower.endswith(BINARY_EXTENSIONS):
                margin.append(Finding("FILE_BINARY", name, 0, "compiled Python"))
            else:
                with archive.open(info) as handle:
                    head = handle.read(4)
                if head.startswith(BINARY_MAGIC):
                    margin.append(Finding("FILE_BINARY", name, 0, "executable binary format"))
    return site, margin


def by_rule(findings: list[Finding]) -> str:
    """``14 B608, 2 B104, 1 B110``: counts per rule, most frequent first."""
    counts = Counter(finding.rule for finding in findings)
    return ", ".join(f"{count} {rule}" for rule, count in counts.most_common())


def report(title: str, findings: list[Finding], note: str = "") -> None:
    """Print one check: a status line, then each finding."""
    status = "OK, no findings" if not findings else f"FAIL, {len(findings)}: {by_rule(findings)}"
    print(f"{title}: {status}{f' ({note})' if note else ''}")
    for finding in findings:
        where = f"{finding.path}:{finding.line}" if finding.line else finding.path
        print(f"  {finding.rule} {where} {finding.text}".rstrip())


def build_zip(root: Path, output_dir: Path) -> Path:
    """Build the plugin zip with scripts/package.py into ``output_dir``; return its path."""
    result = subprocess.run(
        [sys.executable, str(PACKAGE_SCRIPT), "--root", str(root), "--output-dir", str(output_dir)],
        capture_output=True,
        text=True,
        timeout=TOOL_TIMEOUT,
        check=False,
    )
    zips = sorted(output_dir.glob("*.zip"))
    if result.returncode != 0 or len(zips) != 1:
        raise ToolError(f"scripts/package.py failed:\n{result.stderr.strip()}")
    return zips[0]


def scan(zip_path: Path, tools: dict[str, str], work_dir: Path) -> int:
    """Run every check on ``zip_path`` and print the report; return the exit status."""
    extracted = work_dir / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extracted)
    files = python_files(extracted)
    print(f"Scanning {zip_path} ({len(files)} Python files) like plugins.qgis.org")
    failed = False

    try:
        bandit = parse_bandit(_run(bandit_command(tools["bandit"], extracted)), extracted)
    except ToolError as exc:
        print(f"Bandit: FAIL, {exc}")
        failed = True
    else:
        report(f"Bandit ({len(BANDIT_ENABLED)} rules of the site)", bandit)
        failed = failed or bool(bandit)

    try:
        stdout = _run(secrets_command(tools["detect-secrets"], extracted), cwd=extracted)
        secrets = parse_secrets(stdout)
    except ToolError as exc:
        print(f"detect-secrets: FAIL, {exc}")
        failed = True
    else:
        disabled = ", ".join(SECRETS_DISABLED)
        report(f"detect-secrets (every detector but {disabled})", secrets)
        failed = failed or bool(secrets)

    try:
        flake8 = (
            parse_flake8(_run(flake8_command(tools["flake8"], files)), extracted) if files else []
        )
    except ToolError as exc:
        print(f"Flake8: FAIL, {exc}")
        failed = True
    else:
        site_codes = [finding for finding in flake8 if finding.rule not in FLAKE8_MARGIN]
        margin = [finding for finding in flake8 if finding.rule in FLAKE8_MARGIN]
        report(
            f"Flake8 ({len(FLAKE8_ENABLED)} codes of the site, "
            f"--max-line-length={FLAKE8_MAX_LINE_LENGTH})",
            site_codes,
        )
        report(
            "Flake8 margin " + " and ".join(FLAKE8_MARGIN),
            margin,
            "both disabled on the site today, required by HamQ",
        )
        failed = failed or bool(flake8)

    site_files, margin_files = check_files(zip_path)
    report("File checks of the site (executable .py, suspicious types)", site_files)
    report(
        "File checks, margin (any executable, hidden or binary file)",
        margin_files,
        "FILE_HIDDEN is disabled on the site today, FILE_BINARY has no check there",
    )
    failed = failed or bool(site_files or margin_files)

    print(
        "Result: FAIL, plugins.qgis.org would report findings"
        if failed
        else "Result: OK, no findings (the Qt6 check runs separately, see docs/RELEASING.md)"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the plugins.qgis.org scan (Bandit, detect-secrets, Flake8, file "
        "checks) on a HamQ plugin zip. Exit status: 0 clean, 1 findings, 2 a tool is missing.",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=None,
        help="plugin zip to scan (default: build one with scripts/package.py in a "
        "temporary folder)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository root to build the zip from (default: this repository)",
    )
    args = parser.parse_args(argv)

    tools = {name: find_tool(name) for name in ("bandit", "detect-secrets", "flake8")}
    flake8_version = tool_version(tools["flake8"]) if tools["flake8"] else ""
    missing = missing_tools(tools, flake8_version)
    if missing:
        print(
            f"qgis_repo_scan.py: not installed: {', '.join(missing)}. Install the site's "
            f"versions with:\n  {pip_command()}",
            file=sys.stderr,
        )
        return 2
    versions = {name: tool_version(path) for name, path in tools.items() if path}
    print(
        "Tools: "
        + "; ".join(
            text if text.startswith(name) else f"{name} {text}" for name, text in versions.items()
        )
    )

    with tempfile.TemporaryDirectory(prefix="hamq-scan-") as tmp:
        work_dir = Path(tmp)
        if args.zip is not None:
            zip_path = args.zip.resolve()
            if not zipfile.is_zipfile(zip_path):
                print(f"qgis_repo_scan.py: {zip_path} is not a zip file", file=sys.stderr)
                return 1
        else:
            try:
                zip_path = build_zip(args.root.resolve(), work_dir / "dist")
            except ToolError as exc:
                print(f"qgis_repo_scan.py: {exc}", file=sys.stderr)
                return 1
        return scan(zip_path, {name: str(path) for name, path in tools.items()}, work_dir)


if __name__ == "__main__":
    sys.exit(main())
