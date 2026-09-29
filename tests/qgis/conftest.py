"""Shared setup for the QGIS integration tests in ``tests/qgis``.

The tests run inside a QGIS Python environment: the local QGIS 4.x (Qt6) or the
Docker images used by ``scripts/test_qgis.sh`` (QGIS 3.44, 4.0 and 3.34).

* One offscreen ``QgsApplication`` is started for the whole session (in
  ``pytest_configure``, before any test module here is imported) with a
  throw-away profile (``QGIS_CUSTOM_CONFIG_PATH``), so the tests never touch
  the user's QGIS settings.
* Processing is initialized when the ``processing`` plugin is available (the
  QGIS 3.34 server image ships it outside ``sys.path``; it is added here).
* The repository root is importable (``import hamq``).
* QGIS can crash while the interpreter shuts down. The session therefore ends
  with ``os._exit(<pytest exit status>)`` after all output is flushed; set
  ``HAMQ_TEST_NO_OS_EXIT=1`` to disable that (e.g. when calling ``pytest.main``
  from another program).

Fixtures: ``qgis_app``, ``qgis_processing``, ``iface``, ``tmp_gpkg``,
``clean_settings``, ``clean_project``, ``process_events``, ``log_messages``.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from qgis.core import Qgis, QgsApplication
    from qgis.PyQt.QtCore import QT_VERSION_STR, QCoreApplication, QEvent

    QGIS_IMPORT_ERROR = ""
except ImportError as exc:  # plain Python without QGIS: nothing here can run
    QGIS_IMPORT_ERROR = str(exc)
    collect_ignore_glob = ["test_*.py"]

# Session state shared by the hooks below.
_STATE: dict[str, Any] = {
    "app": None,
    "config_dir": None,
    "processing": False,
    "processing_error": "not initialized",
    "session": None,
}


# ---------------------------------------------------------------------------
# QGIS application
# ---------------------------------------------------------------------------


def _processing_plugin_dirs() -> list[str]:
    """Directories that may contain the ``processing`` plugin package."""
    import qgis

    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(qgis.__file__)), "plugins"),
        os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"),
    ]
    return [path for path in candidates if os.path.isdir(os.path.join(path, "processing"))]


def _init_processing() -> tuple[bool, str]:
    for path in _processing_plugin_dirs():
        if path not in sys.path:
            sys.path.append(path)
    try:
        from processing.core.Processing import Processing
    except ImportError as exc:
        return False, f"the QGIS processing plugin is not available here ({exc})"
    try:
        Processing.initialize()
    except Exception as exc:  # a broken provider must not stop the other tests
        return False, f"Processing.initialize() failed: {exc!r}"
    return True, ""


def _start_qgis() -> None:
    from qgis.testing import start_app

    # start_app() prints the application state; keep the pytest output clean.
    with contextlib.redirect_stdout(io.StringIO()):
        app = start_app(cleanup=False)
    config_dir = os.environ.get("QGIS_CUSTOM_CONFIG_PATH", "")
    settings_dir = os.path.realpath(QgsApplication.qgisSettingsDirPath())
    if not config_dir or not settings_dir.startswith(os.path.realpath(config_dir)):
        pytest.exit(
            f"refusing to run: QGIS profile {settings_dir!r} is not a temporary test profile",
            returncode=3,
        )
    _STATE["app"] = app
    _STATE["config_dir"] = config_dir
    # start_app() echoes log messages to stdout; keep provider warnings
    # ("GRASS was not found", ...) out of the pytest header.
    with contextlib.redirect_stdout(io.StringIO()):
        _STATE["processing"], _STATE["processing_error"] = _init_processing()


def pytest_configure(config: pytest.Config) -> None:
    # pytest_configure is historic: it also runs when this conftest is loaded
    # late (``pytest tests``), always before the test modules here are imported.
    if QGIS_IMPORT_ERROR or _STATE["app"] is not None:
        return
    if getattr(config.option, "help", False) or getattr(config.option, "version", False):
        return
    _start_qgis()


def pytest_report_header(config: pytest.Config) -> list[str]:
    if QGIS_IMPORT_ERROR:
        return [f"QGIS: not available ({QGIS_IMPORT_ERROR}); tests/qgis are not collected"]
    processing = "yes" if _STATE["processing"] else f"no - {_STATE['processing_error']}"
    return [
        f"QGIS: {Qgis.version()} (Qt {QT_VERSION_STR}, Python {sys.version.split()[0]})",
        f"QGIS test profile: {_STATE['config_dir']}",
        f"Processing: {processing}",
    ]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _STATE["session"] = session


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """End the process before QGIS gets a chance to crash in its destructors."""
    if _STATE["app"] is None or os.environ.get("HAMQ_TEST_NO_OS_EXIT"):
        return
    session = _STATE["session"]
    if session is None:
        return
    status = int(session.exitstatus)
    capture = config.pluginmanager.getplugin("capturemanager")
    if capture is not None:
        with contextlib.suppress(Exception):
            capture.stop_global_capturing()
    if _STATE["config_dir"]:
        shutil.rmtree(_STATE["config_dir"], ignore_errors=True)
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(status)


# ---------------------------------------------------------------------------
# Fake QGIS interface
# ---------------------------------------------------------------------------


class _FakeMessageBar:
    """Message bar stand-in when ``qgis.gui`` is not available."""

    def __init__(self) -> None:
        self.messages: list[tuple[Any, ...]] = []

    def pushMessage(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append(args)

    def clearWidgets(self) -> None:
        self.messages.clear()


class FakeIface:
    """Minimal ``QgisInterface`` backed by a real ``QMainWindow``.

    Menus, toolbars and docks are real Qt objects, so tests can check that a
    plugin adds them and that ``unload()`` removes them again.
    """

    def __init__(self) -> None:
        from qgis.PyQt.QtWidgets import QMainWindow

        self._main = QMainWindow()
        self._main.setObjectName("QgisApp")
        self._plugin_menu = self._main.menuBar().addMenu("&Plugins")
        self._plugin_menu.setObjectName("mPluginMenu")
        self._plugin_toolbar = self._main.addToolBar("Plugins")
        self._plugin_toolbar.setObjectName("mPluginToolBar")
        self._submenus: dict[str, Any] = {}
        self._canvas: Any = None
        self._message_bar: Any = None

    # QgisInterface API ---------------------------------------------------
    def mainWindow(self) -> Any:
        return self._main

    def pluginMenu(self) -> Any:
        return self._plugin_menu

    def pluginToolBar(self) -> Any:
        return self._plugin_toolbar

    def addPluginToMenu(self, name: str, action: Any) -> None:
        menu = self._submenus.get(name)
        if menu is None:
            menu = self._plugin_menu.addMenu(name)
            self._submenus[name] = menu
        menu.addAction(action)

    def removePluginMenu(self, name: str, action: Any) -> None:
        menu = self._submenus.get(name)
        if menu is None:
            return
        menu.removeAction(action)
        if not menu.actions():
            self._plugin_menu.removeAction(menu.menuAction())
            del self._submenus[name]
            menu.deleteLater()

    def addToolBar(self, name_or_toolbar: Any, area: Any = None) -> Any:
        if isinstance(name_or_toolbar, str):
            return self._main.addToolBar(name_or_toolbar)
        if area is None:
            self._main.addToolBar(name_or_toolbar)
        else:
            self._main.addToolBar(area, name_or_toolbar)
        return name_or_toolbar

    def addToolBarIcon(self, action: Any) -> int:
        self._plugin_toolbar.addAction(action)
        return 0

    def removeToolBarIcon(self, action: Any) -> None:
        self._plugin_toolbar.removeAction(action)

    def addDockWidget(self, area: Any, dock: Any) -> None:
        self._main.addDockWidget(area, dock)

    def removeDockWidget(self, dock: Any) -> None:
        self._main.removeDockWidget(dock)

    def mapCanvas(self) -> Any:
        if self._canvas is None:
            from qgis.gui import QgsMapCanvas

            self._canvas = QgsMapCanvas(self._main)
        return self._canvas

    def messageBar(self) -> Any:
        if self._message_bar is None:
            try:
                from qgis.gui import QgsMessageBar

                self._message_bar = QgsMessageBar(self._main)
            except ImportError:
                self._message_bar = _FakeMessageBar()
        return self._message_bar

    # test helpers ----------------------------------------------------------
    def close(self) -> None:
        self._main.close()
        self._main.deleteLater()


#: QgisInterface methods delegated from the ``mocked.get_iface()`` mock to FakeIface.
_IFACE_METHODS = (
    "mainWindow",
    "pluginMenu",
    "pluginToolBar",
    "addPluginToMenu",
    "removePluginMenu",
    "addToolBar",
    "addToolBarIcon",
    "removeToolBarIcon",
    "addDockWidget",
    "removeDockWidget",
    "mapCanvas",
    "messageBar",
)


def _mocked_iface(backend: FakeIface) -> Any:
    """``qgis.testing.mocked.get_iface()`` wired to ``backend``, or ``None``.

    The mock has ``spec=QgisInterface``: calling a method that QGIS does not
    have fails, which catches typos in plugin code.
    """
    try:
        from qgis.testing.mocked import get_iface

        mock_iface = get_iface()
    except Exception:  # qgis.gui missing or incompatible in this build
        return None
    for name in _IFACE_METHODS:
        getattr(mock_iface, name).side_effect = getattr(backend, name)
    return mock_iface


def _process_events() -> None:
    """Run pending events and deferred deletes (``deleteLater``)."""
    app = QgsApplication.instance()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def qgis_app() -> Any:
    """The running ``QgsApplication``."""
    if _STATE["app"] is None:
        pytest.skip("QGIS application is not running")
    return _STATE["app"]


@pytest.fixture(scope="session")
def qgis_processing(qgis_app: Any) -> Any:
    """The initialized ``processing`` module; skips when this QGIS build has none."""
    if not _STATE["processing"]:
        pytest.skip(_STATE["processing_error"])
    import processing

    return processing


@pytest.fixture
def process_events() -> Callable[[], None]:
    """Callable that runs pending Qt events, including ``deleteLater`` deletions."""
    return _process_events


@pytest.fixture
def iface(qgis_app: Any) -> Iterator[Any]:
    """A QGIS interface: ``mocked.get_iface()`` wired to :class:`FakeIface`, or the fake."""
    backend = FakeIface()
    mock_iface = _mocked_iface(backend)
    yield mock_iface if mock_iface is not None else backend
    backend.close()
    _process_events()


@pytest.fixture
def tmp_gpkg(tmp_path: Path) -> str:
    """Path of a GeoPackage that does not exist yet, in a per-test directory."""
    return str(tmp_path / "hamq_test.gpkg")


@pytest.fixture
def clean_settings(qgis_app: Any) -> Iterator[None]:
    """Remove every ``hamq/`` setting before and after the test."""
    from qgis.core import QgsSettings

    QgsSettings().remove("hamq")
    yield
    QgsSettings().remove("hamq")


@pytest.fixture
def clean_project(qgis_app: Any) -> Iterator[Any]:
    """``QgsProject.instance()``, cleared after the test."""
    from qgis.core import QgsProject

    yield QgsProject.instance()
    QgsProject.instance().clear()


@pytest.fixture
def log_messages(qgis_app: Any) -> Iterator[list[tuple[str, str, Any]]]:
    """``(message, tag, level)`` of every QgsMessageLog message logged during the test."""
    from hamq.qgis_io.compat import connect_message_log

    messages: list[tuple[str, str, Any]] = []
    disconnect = connect_message_log(
        lambda message, tag, level: messages.append((message, tag, level))
    )
    yield messages
    disconnect()
