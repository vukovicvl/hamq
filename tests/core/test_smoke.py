"""Smoke tests: the package and the finished core helpers import without QGIS."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import hamq

REPO_ROOT = Path(__file__).resolve().parents[2]

# Blocks every QGIS / Qt module, then imports what must work without them.
_IMPORT_WITHOUT_QGIS = """
import sys

BLOCKED = {"qgis", "PyQt5", "PyQt6", "sip", "PySide2", "PySide6"}

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is blocked in this test")
        return None

sys.meta_path.insert(0, Blocker())

import hamq
import hamq.core
import hamq.core.bands
import hamq.core.i18n
import hamq.core.modes

assert callable(hamq.classFactory)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
assert not loaded, loaded
print("ok")
"""


def test_class_factory_is_defined():
    assert callable(hamq.classFactory)
    assert list(inspect.signature(hamq.classFactory).parameters) == ["iface"]


def test_package_and_core_helpers_import_without_qgis():
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_WITHOUT_QGIS],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_core_helpers_work():
    from hamq.core import bands, i18n, modes

    assert bands.band_from_freq(14.074) == "20m"
    assert bands.band_sort_key("160m") < bands.band_sort_key("2m")
    assert modes.display_mode("MFSK", "FT4") == "FT4"
    assert modes.display_mode("ft8", None) == "FT8"
    assert i18n.tr_noop("Total QSOs") == "Total QSOs"
    assert isinstance(i18n.tr("QSO"), str)


def test_plugin_files_exist():
    plugin_dir = REPO_ROOT / "hamq"
    for name in ("__init__.py", "plugin.py", "metadata.txt", "settings.py", "events.py"):
        assert (plugin_dir / name).is_file(), name
    assert (plugin_dir / "resources" / "icons" / "hamq.svg").is_file()
    assert (REPO_ROOT / "LICENSE").is_file()
