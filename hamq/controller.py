"""HamQ controller: the glue between the GUI, the GeoPackage log and the network clients.

:class:`HamQController` owns the long-lived parts of the plugin and wires them together:

* :class:`~hamq.settings.HamQSettings` and the :class:`~hamq.gui.language.LanguageManager`
  (the interface language is applied as soon as the controller exists, so everything
  created afterwards starts in that language);
* :class:`~hamq.net.cty_download.CtyManager` (cty.dat cache and download);
* :class:`~hamq.net.wsjtx_listener.WsjtxListener` (WSJT-X / JTDX over UDP);
* :class:`~hamq.net.hamlib_client.RigClient` and :class:`~hamq.net.hamlib_client.RotatorClient`
  (Hamlib ``rigctld`` / ``rotctld``);
* :class:`~hamq.gui.dock.HamQDock` (the panel), :class:`~hamq.gui.azimuthal.AzimuthalMap`
  and :class:`~hamq.gui.rotator_tool.RotatorMapTool`.

``plugin.py`` adds :attr:`HamQController.dock` to the main window, creates the menu and
toolbar actions that call the public methods here, calls :meth:`HamQController.start` once
and :meth:`HamQController.cleanup` on unload. The flows:

* **Statistics** (:meth:`~HamQController.refresh`): ``gpkg.read_qso_rows`` and
  ``core.stats.compute_stats`` run in a hidden ``QgsTask`` (10 000 QSOs take about 0.1 s,
  a big contest log several times that) and the dock shows the result.
  ``events().dataChanged`` of the log file starts it; requests that arrive while a refresh
  runs are merged into one more refresh.
* **Live WSJT-X QSOs**: ``WsjtxListener.adifLogged`` -> ``core.adif.parse_adi`` ->
  ``core.qso.record_to_qso`` (my station, cty.dat, source ``wsjtx``) ->
  ``gpkg.insert_qsos`` (a QSO already in the log is skipped by its ``dedup_key``) -> the
  QSO layers are added to the project when they are missing -> ``events().dataChanged``
  (the layers and the statistics refresh) -> "New QSO: ..." in the QGIS message bar and
  the last QSO in the dock.
* **Manual QSOs**: :meth:`~HamQController.log_qso` opens the QSO dialog (frequency and
  mode from the radio when it is connected) and saves the QSO the same way (source
  ``manual``).
* **Hamlib**: the clients start and stop with their settings; the Radio tab of the dock
  shows their state and sends its commands through here; the rotator map tool turns the
  rotator toward a clicked point.
* **Settings** (``events().settingsChanged``): a running listener restarts when its
  address or port changed, the Hamlib clients follow their settings and the statistics
  follow the GeoPackage path.
* **First run**: without cty.dat a message bar hint offers the download, without a valid
  QTH locator a hint opens the settings.

Everything runs in the main thread except the statistics task. Slots never let an
exception reach Qt: it is logged in the HamQ tab of the QGIS log instead.
"""

from __future__ import annotations

import contextlib
import functools
import os
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from qgis.core import QgsApplication, QgsMessageLog, QgsProcessingAlgorithm, QgsTask
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtWidgets import QPushButton

from .core import maidenhead
from .core.adif import parse_adi
from .core.hamlib import rotator_target
from .core.i18n import tr, tr_noop
from .core.qso import Qso, record_to_qso
from .core.stats import QsoStats, compute_stats
from .events import events
from .gui.azimuthal import AzimuthalMap
from .gui.dock import HamQDock, format_bearing, format_number
from .gui.language import LanguageManager
from .gui.qso_dialog import QsoDialog
from .gui.rotator_tool import RotatorMapTool
from .gui.settings_dialog import SettingsDialog
from .net.cty_download import CtyManager
from .net.hamlib_client import RigClient, RotatorClient
from .net.wsjtx_listener import WsjtxListener
from .qgis_io import compat, gpkg, layers
from .settings import HamQSettings

__all__ = [
    "ALG_IMPORT_ADIF",
    "ALG_LOCATOR_TO_POINT",
    "ALG_MAIDENHEAD_GRID",
    "ALG_RECALCULATE",
    "MESSAGE_TITLE",
    "SOURCE_MANUAL",
    "SOURCE_WSJTX",
    "HamQController",
]

LOG_TAG = "HamQ"
#: Title of HamQ messages in the QGIS message bar (a product name, not translated).
MESSAGE_TITLE = "HamQ"
#: Seconds a "New QSO" message stays in the message bar.
QSO_MESSAGE_SECONDS = 6
#: Seconds an information stays in the message bar.
INFO_SECONDS = 5
#: Seconds a warning stays in the message bar.
WARNING_SECONDS = 10
#: ``source`` of QSOs received from WSJT-X / JTDX and of QSOs entered by hand.
SOURCE_WSJTX, SOURCE_MANUAL = "wsjtx", "manual"
#: Processing algorithms opened by the menu actions and the dock.
ALG_IMPORT_ADIF = "hamq:import_adif"
ALG_MAIDENHEAD_GRID = "hamq:maidenhead_grid"
ALG_LOCATOR_TO_POINT = "hamq:locator_to_point"
ALG_RECALCULATE = "hamq:recalculate"

# Name shown for a WSJT-X client that sent a null id.
_DEFAULT_CLIENT = "WSJT-X"
# Rig polling interval range of the settings dialog; a value stored by hand outside it
# (settings.py only checks > 0) is clamped, so it can never break the plugin start.
_MIN_POLL_MS, _MAX_POLL_MS = 100, 60_000
# First-run hints: key -> (text, button text), translated when shown.
_HINT_CTY, _HINT_GRID = "cty", "grid"
_HINTS = {
    _HINT_CTY: (
        tr_noop(
            "DXCC data (cty.dat) has not been downloaded yet. HamQ uses it to find the "
            "country, continent and zones of each callsign."
        ),
        tr_noop("Download"),
    ),
    _HINT_GRID: (
        tr_noop("Set your QTH locator in the HamQ settings to see distances, bearings and paths."),
        tr_noop("Settings..."),
    ),
}
# Statistics tasks still running; QGIS's task manager owns them, the reference keeps the
# Python side alive until they end (also after the plugin was unloaded).
_RUNNING_TASKS: set[_StatsTask] = set()


def _log(message: str, level: Any) -> None:
    QgsMessageLog.logMessage(message, LOG_TAG, level)


def _log_error(where: str, exc: BaseException) -> None:
    message = tr("Unexpected error in {slot}: {error}").format(slot=where, error=exc)
    _log(f"{message}\n{traceback.format_exc()}", compat.MSG_CRITICAL)


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Qt slot: an exception is logged instead of escaping into Qt."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except Exception as exc:  # a slot must never raise into Qt
            _log_error(method.__qualname__, exc)
            return None

    return wrapper


def _task_flags() -> Any:
    """Flags of the statistics task: cancelable without asking, hidden and silent."""
    flags = compat.TASK_CAN_CANCEL
    for extra in (compat.TASK_CANCEL_WITHOUT_PROMPT, compat.TASK_HIDDEN, compat.TASK_SILENT):
        if extra is not None:
            flags = flags | extra
    return flags


def _poll_ms(value: int) -> int:
    return min(max(int(value), _MIN_POLL_MS), _MAX_POLL_MS)


def _file_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path))) if path else ""


def _alive(obj: Any) -> bool:
    """True while the C++ object of a Qt wrapper exists."""
    try:
        return obj is not None and not sip.isdeleted(obj)
    except TypeError:  # not a sip wrapper (a test double): treat as alive
        return obj is not None


class _StatsTask(QgsTask):
    """Reads the QSO rows of a GeoPackage and computes their statistics in a worker thread.

    ``on_done(task)`` runs in the main thread when the task ended (``task.stats`` is set on
    success, ``task.error`` on failure); clear it to ignore the result.
    """

    def __init__(self, path: str, on_done: Callable[[_StatsTask], None] | None) -> None:
        super().__init__(tr("Computing the HamQ statistics"), _task_flags())
        self.path = path
        self.stats: QsoStats | None = None
        self.error = ""
        self.on_done = on_done

    def run(self) -> bool:
        """Worker thread: plain SQLite reads and pure Python, no Qt object is touched."""
        try:
            rows = gpkg.read_qso_rows(self.path)
            if self.isCanceled():
                return False
            self.stats = compute_stats(rows)
        except Exception as exc:  # reported by finished() in the main thread
            self.error = str(exc) or type(exc).__name__
            return False
        return True

    def finished(self, result: bool) -> None:
        """Main thread: hand the result to ``on_done``."""
        _RUNNING_TASKS.discard(self)
        callback, self.on_done = self.on_done, None
        if callback is None:
            return
        try:
            callback(self)
        except Exception as exc:  # never raise into the task manager
            _log_error("HamQController.refresh", exc)


class _Hint:
    """A message bar item with a button; its texts follow language changes."""

    def __init__(self, bar: Any, text: str, button_text: str, callback: Callable[[], None]) -> None:
        self._bar = bar
        self._text = text  # untranslated sources, marked with tr_noop()
        self._button_text = button_text
        self.item = bar.createMessage(MESSAGE_TITLE, tr(text))
        self.button = QPushButton(self.item)
        self.button.setObjectName("HamQHintButton")
        self.button.setText(tr(button_text))
        self.button.clicked.connect(lambda _checked=False: callback())
        self.item.layout().addWidget(self.button)
        bar.pushWidget(self.item, compat.MSG_INFO, 0)

    def is_shown(self) -> bool:
        """True while the item is in the message bar (the user did not close it)."""
        if not _alive(self.item) or not _alive(self._bar):
            return False
        return any(item is self.item for item in self._bar.items())

    def retranslate(self) -> None:
        if _alive(self.item) and _alive(self.button):
            self.item.setText(tr(self._text))
            self.button.setText(tr(self._button_text))

    def close(self) -> None:
        if self.is_shown():
            with contextlib.suppress(RuntimeError):
                self._bar.popWidget(self.item)


class HamQController(QObject):
    """Owns the HamQ components and wires them together (see the module documentation).

    ``iface`` is the ``QgisInterface``; ``settings`` the :class:`HamQSettings` to use
    (created when omitted); ``cty_manager`` replaces the :class:`CtyManager` the
    controller would create (tests). Create it in the main thread; the panel is
    :attr:`dock`, which the caller adds to the main window. Call :meth:`start` once, then
    :meth:`cleanup` before the object goes away (both are safe to call twice).
    """

    #: The WSJT-X listener runs (True) or not (False); emitted after every start or stop
    #: request, also when the state did not change (a checkable action follows it).
    listeningChanged = pyqtSignal(bool)
    #: The rotator map tool is the active map tool (True) or not (False); emitted after
    #: every request and when another map tool took over.
    pointOnMapChanged = pyqtSignal(bool)
    #: New statistics of the log (a ``core.stats.QsoStats``) are shown in the dock.
    statsChanged = pyqtSignal(object)

    def __init__(
        self,
        iface: Any,
        settings: HamQSettings | None = None,
        parent: QObject | None = None,
        *,
        cty_manager: CtyManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.iface = iface
        self.settings = settings if settings is not None else HamQSettings()
        self.language_manager: LanguageManager | None = None
        self.cty_manager: CtyManager | None = None
        self.listener: WsjtxListener | None = None
        self.rig: RigClient | None = None
        self.rotator: RotatorClient | None = None
        self.azimuthal: AzimuthalMap | None = None
        self.dock: HamQDock | None = None
        self.rotator_tool: RotatorMapTool | None = None
        #: The statistics shown in the dock (``None`` before the first refresh).
        self.stats: QsoStats | None = None
        self._connections: list[tuple[Any, Callable[..., Any]]] = []
        self._hints: dict[str, _Hint] = {}
        self._stats_task: _StatsTask | None = None
        self._refresh_pending = False
        self._last_stats_error = ""
        self._listen_target: tuple[str, int] | None = None
        self._point_on = False
        self._previous_tool: Any = None
        self._snapshot: dict[str, Any] = {}
        self._started = False
        self._closed = False
        try:
            self._build(cty_manager)
        except Exception:
            self.cleanup()  # nothing half-built stays connected
            if _alive(self.dock):  # nobody else knows the panel yet
                self.dock.deleteLater()
            raise

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator."""
        return tr(text)

    # ------------------------------------------------------------------ building

    def _build(self, cty_manager: CtyManager | None) -> None:
        self.language_manager = LanguageManager(self.settings, self)
        self.language_manager.apply_from_settings()
        self.cty_manager = cty_manager if cty_manager is not None else CtyManager(self)
        self.listener = WsjtxListener(self)
        self.rig = RigClient(_poll_ms(self.settings.rig_poll_ms), self)
        self.rotator = RotatorClient(parent=self)
        self.azimuthal = AzimuthalMap(self.iface, self.settings, self)
        self.dock = HamQDock(self.iface.mainWindow(), self.settings)
        self.rotator_tool = RotatorMapTool(
            self.iface.mapCanvas(),
            self._station_latlon,
            self._rotator_range,
            self._on_rotator_target,
            notify=self._warn,
            get_current_az=self._rotator_azimuth,
            get_long_path=self.dock.is_long_path,
            settings=self.settings,
        )
        hub = events()
        dock = self.dock
        for signal, slot in (
            (hub.dataChanged, self._on_data_changed),
            (hub.settingsChanged, self._on_settings_changed),
            (hub.languageChanged, self._on_language_changed),
            (self.listener.heartbeatReceived, self._on_heartbeat),
            (self.listener.statusReceived, self._on_status),
            (self.listener.adifLogged, self._on_adif_logged),
            (self.listener.clientClosed, self._on_client_closed),
            (self.listener.connectionChanged, self._on_wsjtx_connection),
            (self.listener.errorOccurred, self._on_listener_error),
            (self.rig.connectedChanged, self._on_rig_connected),
            (self.rig.stateChanged, self._on_rig_state),
            (self.rig.errorOccurred, self._on_rig_error),
            (self.rotator.connectedChanged, self._on_rotator_connected),
            (self.rotator.positionChanged, self._on_rotator_position),
            (self.rotator.errorOccurred, self._on_rotator_error),
            (self.cty_manager.downloadFinished, self._on_cty_downloaded),
            (dock.refreshRequested, self._on_refresh_requested),
            (dock.importRequested, self._on_import_requested),
            (dock.settingsRequested, self._on_settings_requested),
            (dock.listenToggled, self._on_listen_toggled),
            (dock.rigSetRequested, self._on_rig_set_requested),
            (dock.rotatorTurnRequested, self._on_turn_requested),
            (dock.rotatorStopRequested, self._on_stop_requested),
            (dock.pointOnMapToggled, self._on_point_on_map_toggled),
            (dock.logQsoRequested, self._on_log_qso_requested),
            (self.rotator_tool.deactivated, self._on_tool_deactivated),
        ):
            signal.connect(slot)
            self._connections.append((signal, slot))

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start what the settings ask for; call once after the dock was added.

        Parses the cached cty.dat in the background, connects the Hamlib clients that are
        enabled, starts the WSJT-X listener when ``wsjtx_autostart`` is set, shows the
        first-run hints (no cty.dat, no QTH locator) and refreshes the statistics.
        """
        if self._started or self._closed:
            return
        self._started = True
        self._snapshot = self._settings_snapshot()
        for step in (
            self.cty_manager.preload,
            self._apply_rig_settings,
            self._apply_rotator_settings,
            self._autostart_listener,
            self._show_first_run_hints,
            self.refresh,
        ):
            try:
                step()
            except Exception as exc:  # one broken part must not stop the others
                _log_error(f"HamQController.start: {step.__name__}", exc)

    def _autostart_listener(self) -> None:
        if self.settings.wsjtx_autostart:
            self.set_listening(True)

    def is_started(self) -> bool:
        """True between :meth:`start` and :meth:`cleanup`."""
        return self._started and not self._closed

    def cleanup(self) -> None:
        """Stop and release everything this controller started or created. Safe twice.

        Disconnects every signal, stops the statistics task, the WSJT-X listener, the
        Hamlib clients and a cty.dat download, unsets and deletes the rotator map tool,
        switches the azimuthal map off (the project CRS is restored), removes the hints
        from the message bar and disconnects the dock and the language manager from
        ``events()``. The dock itself belongs to whoever added it to the main window.
        """
        if self._closed:
            return
        self._closed = True
        while self._connections:
            signal, slot = self._connections.pop()
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        for step in (
            self._stop_refresh,
            self._stop_listener,
            self._stop_clients,
            self._stop_cty,
            self._release_tool,
            self._stop_azimuthal,
            self._close_hints,
            self._release_dock,
            self._release_language,
        ):
            try:
                step()
            except Exception as exc:  # cleanup must finish whatever one step does
                _log_error(f"HamQController.cleanup: {step.__name__}", exc)

    def _stop_refresh(self) -> None:
        task, self._stats_task = self._stats_task, None
        self._refresh_pending = False
        if task is not None:
            task.on_done = None
            with contextlib.suppress(RuntimeError):
                task.cancel()

    def _stop_listener(self) -> None:
        if self.listener is not None:
            self.listener.stop()
        self._listen_target = None

    def _stop_clients(self) -> None:
        for client in (self.rig, self.rotator):
            if client is not None:
                client.stop()

    def _stop_cty(self) -> None:
        if self.cty_manager is not None:
            self.cty_manager.cleanup()

    def _release_tool(self) -> None:
        tool, self.rotator_tool = self.rotator_tool, None
        previous, self._previous_tool = self._previous_tool, None
        self._point_on = False
        if not _alive(tool):
            return
        canvas = self._canvas()
        was_active = _alive(canvas) and canvas.mapTool() is tool
        tool.cleanup()  # unsets the tool and removes the beam line
        if was_active and _alive(previous):
            with contextlib.suppress(RuntimeError):
                canvas.setMapTool(previous)
        with contextlib.suppress(RuntimeError):
            tool.deleteLater()

    def _stop_azimuthal(self) -> None:
        if self.azimuthal is not None:
            self.azimuthal.cleanup()

    def _close_hints(self) -> None:
        while self._hints:
            _key, hint = self._hints.popitem()
            hint.close()

    def _release_dock(self) -> None:
        if _alive(self.dock):
            self.dock.cleanup()

    def _release_language(self) -> None:
        if self.language_manager is not None:
            self.language_manager.cleanup()

    # ------------------------------------------------------------------ storage and statistics

    def gpkg_path(self) -> str:
        """The GeoPackage with the QSO log (``settings.gpkg_path``)."""
        return self.settings.gpkg_path

    def ensure_storage(self) -> bool:
        """Create the GeoPackage of the log when needed; False (and a warning) on failure."""
        try:
            gpkg.ensure_gpkg(self.gpkg_path())
        except gpkg.GpkgError as exc:
            self._warn(
                tr("The GeoPackage with the QSO log cannot be used: {error}").format(error=exc)
            )
            return False
        return True

    def load_layers(self) -> tuple[Any, Any] | None:
        """Add the QSO and path layers of the log to the project (nothing when they are in
        it already); returns ``(qso, qso_path)`` or ``None`` (and a warning) on failure."""
        try:
            return layers.load_layers(self.gpkg_path())
        except gpkg.GpkgError as exc:
            self._warn(
                tr("The QSO layers could not be added to the project: {error}").format(error=exc)
            )
            return None

    def refresh(self) -> None:
        """Recompute the statistics of the log in the background; the dock shows them.

        While a refresh runs, further requests are merged into one more refresh after it.
        """
        if self._closed:
            return
        if self._stats_task is not None:
            self._refresh_pending = True
            return
        task = _StatsTask(self.gpkg_path(), self._on_stats_done)
        self._stats_task = task
        _RUNNING_TASKS.add(task)
        if not QgsApplication.taskManager().addTask(task):  # 0: not queued
            _RUNNING_TASKS.discard(task)
            task.on_done = None
            self._stats_task = None
            self.refresh_now()

    def refresh_now(self) -> QsoStats | None:
        """Recompute the statistics in this thread (blocks) and show them; ``None`` on error."""
        path = self.gpkg_path()
        try:
            stats = compute_stats(gpkg.read_qso_rows(path))
        except Exception as exc:
            self._stats_failed(str(exc) or type(exc).__name__)
            return None
        self._show_stats(stats)
        return stats

    def is_refreshing(self) -> bool:
        """True while a background refresh runs or waits to run."""
        return self._stats_task is not None or self._refresh_pending

    def _on_stats_done(self, task: _StatsTask) -> None:
        # deleted without cleanup(): QGIS closing while the task still ran
        if task is not self._stats_task or self._closed or not _alive(self):
            return
        self._stats_task = None
        if _file_key(task.path) == _file_key(self.gpkg_path()):
            if task.stats is not None:
                self._show_stats(task.stats)
            elif task.error:
                self._stats_failed(task.error)
        else:  # the log moved meanwhile: its statistics are computed next
            self._refresh_pending = True
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()

    def _show_stats(self, stats: QsoStats) -> None:
        self.stats = stats
        self._last_stats_error = ""
        if _alive(self.dock):
            self.dock.set_stats(stats)
        self.statsChanged.emit(stats)

    def _stats_failed(self, error: str) -> None:
        text = tr("The statistics could not be computed: {error}").format(error=error)
        if text != self._last_stats_error:  # once per problem, not on every refresh
            self._last_stats_error = text
            self._warn(text)

    # ------------------------------------------------------------------ WSJT-X

    def set_listening(self, listening: bool) -> bool:
        """Start (or restart) the WSJT-X listener with the address and port of the settings,
        or stop it. Returns whether it runs; a failure is shown in the dock and the message
        bar (``WsjtxListener.errorOccurred``). Emits :attr:`listeningChanged`."""
        listener = self.listener
        if listening:
            address, port = self.settings.wsjtx_addr, self.settings.wsjtx_port
            started = listener.start(address, port)
            self._listen_target = (address, port) if started else None
            if started:
                self.dock.set_listening(True, address, port)
            else:
                self.dock.set_listening(False)
        else:
            listener.stop()
            self._listen_target = None
            self.dock.set_listening(False)
        running = listener.is_running()
        self.listeningChanged.emit(running)
        return running

    def is_listening(self) -> bool:
        """True while the WSJT-X listener runs."""
        return self.listener is not None and self.listener.is_running()

    def handle_logged_adif(self, client: str, adif: str) -> int:
        """Save the QSO of a WSJT-X "Logged ADIF" message; returns the number of new QSOs.

        The record goes through ``record_to_qso`` (my station from the settings, cty.dat,
        source ``wsjtx``) into the log; a QSO already in the log is not saved again. The
        dock shows it as the last logged QSO and the message bar says what happened.
        """
        name = client or _DEFAULT_CLIENT
        records, warnings = parse_adi(adif)
        for message in warnings:
            _log(message, compat.MSG_WARNING)
        if not records:
            self._warn(tr("{client} sent a logged QSO without an ADIF record").format(client=name))
            return 0
        qsos = self._to_qsos(records, SOURCE_WSJTX, name)
        if not qsos:
            return 0
        if _alive(self.dock):
            self.dock.set_last_qso(qsos[-1].attributes())
        return self._save_qsos(qsos)

    # ------------------------------------------------------------------ QSOs

    def log_qso(self) -> bool:
        """Open the QSO dialog (prefilled from the radio when it is connected) and save the
        QSO; True when a new QSO was saved."""
        rig_state = self.rig.state() if self.rig.is_connected() else None
        dialog = QsoDialog(self.settings, rig_state, self.iface.mainWindow())
        try:
            if dialog.exec() != compat.DIALOG_ACCEPTED:
                return False
            record = dialog.record()
        finally:
            dialog.cleanup()
            dialog.deleteLater()
        qsos = self._to_qsos([record], SOURCE_MANUAL, MESSAGE_TITLE)
        return bool(qsos) and self._save_qsos(qsos) > 0

    def save_records(self, records: Sequence[Mapping[str, str]], source: str) -> int:
        """Save ADIF-like records (``{"CALL": ..., "QSO_DATE": ..., ...}``) to the log the
        way live QSOs are saved; returns the number of new QSOs."""
        qsos = self._to_qsos(records, source, MESSAGE_TITLE)
        return self._save_qsos(qsos) if qsos else 0

    def _to_qsos(self, records: Sequence[Mapping[str, str]], source: str, sender: str) -> list[Qso]:
        station = self.settings.station()
        cty = self.cty_manager.database()
        qsos = []
        for record in records:
            qso, warnings = record_to_qso(record, station=station, cty=cty, source=source)
            for message in warnings:
                _log(message, compat.MSG_INFO if qso is not None else compat.MSG_WARNING)
            if qso is None:
                self._warn(
                    tr("{client} sent a QSO that could not be read: {problem}").format(
                        client=sender, problem=warnings[0] if warnings else "?"
                    )
                )
                continue
            qsos.append(qso)
        return qsos

    def _save_qsos(self, qsos: list[Qso]) -> int:
        if not self.ensure_storage():
            return 0
        path = self.gpkg_path()
        try:
            result = gpkg.insert_qsos(path, qsos)
        except gpkg.GpkgError as exc:
            self._warn(
                tr("The QSO with {call} could not be saved: {error}").format(
                    call=qsos[-1].call, error=exc
                )
            )
            return 0
        for message in result.warnings:
            _log(message, compat.MSG_WARNING)
        if result.inserted:
            qso_layer, path_layer = layers.find_layers(path)
            if qso_layer is None or path_layer is None:
                self.load_layers()
            events().dataChanged.emit(path)
        self._report_saved(qsos, result)
        return result.inserted

    def _report_saved(self, qsos: list[Qso], result: gpkg.InsertResult) -> None:
        if len(qsos) == 1:
            qso = qsos[0]
            if result.inserted:
                text = self._new_qso_text(qso)
                _log(text, compat.MSG_INFO)
                self._message(text, compat.MSG_SUCCESS, QSO_MESSAGE_SECONDS)
            elif result.duplicates:
                self._message(
                    tr("QSO already in the log: {call} {band} {mode}").format(
                        **self._qso_parts(qso)
                    ),
                    compat.MSG_INFO,
                    INFO_SECONDS,
                )
            else:
                self._warn(
                    tr("The QSO with {call} could not be saved: {error}").format(
                        call=qso.call, error=result.warnings[0] if result.warnings else "?"
                    ),
                    log=False,  # insert_qsos's warnings are logged already
                )
                return
        else:
            text = tr("QSOs saved: {inserted}, duplicates: {duplicates}").format(
                inserted=result.inserted, duplicates=result.duplicates
            )
            _log(text, compat.MSG_INFO)
            self._message(
                text, compat.MSG_SUCCESS if result.inserted else compat.MSG_INFO, INFO_SECONDS
            )
        if result.inserted and result.warnings:  # e.g. the layers are in edit mode
            self._message(result.warnings[0], compat.MSG_WARNING, WARNING_SECONDS)

    @staticmethod
    def _qso_parts(qso: Qso) -> dict[str, str]:
        return {"call": qso.call, "band": qso.band or "?", "mode": qso.display_mode or "?"}

    def _new_qso_text(self, qso: Qso) -> str:
        parts = self._qso_parts(qso)
        if qso.distance_km is None:
            return tr("New QSO: {call} {band} {mode}").format(**parts)
        return tr("New QSO: {call} {band} {mode} ({distance} km)").format(
            distance=format_number(qso.distance_km, 0), **parts
        )

    # ------------------------------------------------------------------ radio and rotator

    def set_rig(self, hz: int, mode: str) -> None:
        """Tune the radio to ``hz`` and set ``mode`` (a Hamlib mode name) when it differs
        from the radio's mode; the mode gets the radio's default passband."""
        try:
            if hz > 0:
                self.rig.set_frequency(int(hz))
            if mode and mode != self.rig.state().get("mode"):
                self.rig.set_mode(mode, 0)
        except (TypeError, ValueError) as exc:  # the dock sends checked values; be safe
            self.dock.set_rig_error(tr("The radio could not be set: {error}").format(error=exc))

    def turn_rotator(self, bearing: float) -> float | None:
        """Turn the rotator toward a compass ``bearing``; returns the azimuth sent.

        The bearing is mapped into the rotator range of the settings
        (``core.hamlib.rotator_target``, the equivalent closest to the current position);
        ``None`` and a warning when the range does not reach it.
        """
        low, high = self._rotator_range()
        try:
            azimuth = rotator_target(bearing, low, high, self._rotator_azimuth())
        except (TypeError, ValueError):
            azimuth = None
        if azimuth is None:
            self._warn(
                tr(
                    "The rotator cannot turn to azimuth {azimuth}: its range is "
                    "{minimum}° to {maximum}°."
                ).format(
                    azimuth=format_bearing(bearing),
                    minimum=format_number(low, 0),
                    maximum=format_number(high, 0),
                )
            )
            return None
        self.rotator.set_position(azimuth)
        self.dock.set_rotator_target(azimuth)
        return azimuth

    def stop_rotator(self) -> None:
        """Stop the rotator."""
        self.rotator.stop_rotation()

    def set_point_on_map(self, enabled: bool) -> bool:
        """Make the rotator map tool the active map tool (a click turns the antenna) or end
        it (the previous map tool comes back). Needs a connected rotator. Returns whether
        the tool is active; emits :attr:`pointOnMapChanged`."""
        canvas = self._canvas()
        tool = self.rotator_tool
        if enabled and _alive(canvas) and _alive(tool):
            if not self.rotator.is_connected():
                self._warn(
                    tr(
                        "The rotator is not connected. Turn on rotator control in the HamQ "
                        "settings and start rotctld."
                    )
                )
            elif canvas.mapTool() is not tool:
                self._previous_tool = canvas.mapTool()
                canvas.setMapTool(tool)
        elif _alive(canvas) and _alive(tool) and canvas.mapTool() is tool:
            previous, self._previous_tool = self._previous_tool, None
            canvas.unsetMapTool(tool)
            if _alive(previous):
                canvas.setMapTool(previous)
        active = _alive(canvas) and _alive(tool) and canvas.mapTool() is tool
        self._set_point_state(active)
        return active

    def is_pointing_on_map(self) -> bool:
        """True while the rotator map tool is the active map tool."""
        return self._point_on

    def _set_point_state(self, active: bool) -> None:
        self._point_on = active
        if _alive(self.dock):
            self.dock.set_point_on_map_checked(active)
        self.pointOnMapChanged.emit(active)

    def _station_latlon(self) -> tuple[float, float] | None:
        return self.settings.station().latlon()

    def _rotator_range(self) -> tuple[float, float]:
        return self.settings.rot_min_az, self.settings.rot_max_az

    def _rotator_azimuth(self) -> float | None:
        position = self.rotator.position() if self.rotator is not None else None
        return position[0] if position else None

    def _on_rotator_target(
        self, azimuth: float, _bearing: float, _distance_km: float, _point: Any
    ) -> None:
        self.rotator.set_position(azimuth)
        self.dock.set_rotator_target(azimuth)

    def _apply_rig_settings(self) -> None:
        settings = self.settings
        enabled = settings.rig_enabled
        self.dock.set_rig_enabled(enabled)
        if enabled:
            self.rig.set_poll_interval(_poll_ms(settings.rig_poll_ms))
            self.rig.start(settings.rig_host, settings.rig_port)
        else:
            self.rig.stop()

    def _apply_rotator_settings(self) -> None:
        settings = self.settings
        enabled = settings.rot_enabled
        self.dock.set_rotator_enabled(enabled)
        if enabled:
            self.rotator.start(settings.rot_host, settings.rot_port)
        else:
            self.rotator.stop()
            if self._point_on:
                self.set_point_on_map(False)

    # ------------------------------------------------------------------ dialogs and actions

    def show_settings(self) -> bool:
        """Open the settings dialog (modal); True when the settings were saved."""
        dialog = SettingsDialog(
            self.settings, self.language_manager, self.cty_manager, self.iface.mainWindow()
        )
        try:
            return dialog.exec() == compat.DIALOG_ACCEPTED
        finally:
            dialog.cleanup()
            dialog.deleteLater()

    def download_cty(self) -> None:
        """Download cty.dat (and cty.csv) in the background; the message bar tells the end."""
        self._close_hint(_HINT_CTY)
        if self.cty_manager.is_downloading():
            self._message(
                tr("A cty.dat download is already running"), compat.MSG_INFO, INFO_SECONDS
            )
            return
        self._message(tr("Downloading cty.dat…"), compat.MSG_INFO, INFO_SECONDS)
        self.cty_manager.download()

    def open_algorithm_dialog(
        self, algorithm_id: str, parameters: Mapping[str, Any] | None = None
    ) -> None:
        """Open the Processing dialog of ``algorithm_id`` (e.g. ``hamq:import_adif``)."""
        registry = QgsApplication.processingRegistry()
        algorithm = registry.algorithmById(algorithm_id)
        if not isinstance(algorithm, QgsProcessingAlgorithm):
            self._warn(
                tr("The Processing algorithm {algorithm} is not available.").format(
                    algorithm=algorithm_id
                )
            )
            return
        try:
            import processing  # the QGIS Processing plugin, needed only for its dialogs
        except ImportError as exc:
            self._warn(tr("Processing is not available: {error}").format(error=exc))
            return
        try:
            processing.execAlgorithmDialog(algorithm_id, dict(parameters or {}))
        except Exception as exc:  # a broken dialog must not break the action
            self._warn(
                tr("The Processing algorithm {algorithm} could not be opened: {error}").format(
                    algorithm=algorithm.displayName(), error=exc
                )
            )

    def import_adif(self) -> None:
        """Open the "Import ADIF" dialog (defaults: the log and my locator of the settings)."""
        self.open_algorithm_dialog(ALG_IMPORT_ADIF)

    # ------------------------------------------------------------------ hints and messages

    def _show_first_run_hints(self) -> None:
        if not self.cty_manager.is_available():
            self._show_hint(_HINT_CTY, self.download_cty)
        if not maidenhead.is_valid(self.settings.my_grid):
            self._show_hint(_HINT_GRID, self._open_settings_from_hint)

    def _show_hint(self, key: str, callback: Callable[[], None]) -> None:
        bar = self._message_bar()
        if bar is None or not hasattr(bar, "createMessage"):  # no qgis.gui message bar
            return
        self._close_hint(key)
        text, button_text = _HINTS[key]
        self._hints[key] = _Hint(bar, text, button_text, _guarded(callback))

    def _open_settings_from_hint(self) -> None:
        self._close_hint(_HINT_GRID)
        self.show_settings()

    def _close_hint(self, key: str) -> None:
        hint = self._hints.pop(key, None)
        if hint is not None:
            hint.close()

    def _update_hints(self) -> None:
        if self.cty_manager.is_available():
            self._close_hint(_HINT_CTY)
        if maidenhead.is_valid(self.settings.my_grid):
            self._close_hint(_HINT_GRID)

    def hint_keys(self) -> list[str]:
        """Keys of the first-run hints shown in the message bar (``cty``, ``grid``)."""
        return [key for key, hint in self._hints.items() if hint.is_shown()]

    def _message_bar(self) -> Any:
        try:
            return self.iface.messageBar()
        except (AttributeError, RuntimeError):
            return None

    def _message(self, text: str, level: Any, duration: int) -> None:
        bar = self._message_bar()
        if bar is not None:
            bar.pushMessage(MESSAGE_TITLE, text, level, duration)

    def _warn(self, text: str, *, log: bool = True) -> None:
        """A translated warning in the message bar (and the HamQ log)."""
        if log:
            _log(text, compat.MSG_WARNING)
        self._message(text, compat.MSG_WARNING, WARNING_SECONDS)

    def _canvas(self) -> Any:
        try:
            return self.iface.mapCanvas()
        except (AttributeError, RuntimeError):
            return None

    # ------------------------------------------------------------------ settings

    def _settings_snapshot(self) -> dict[str, Any]:
        settings = self.settings
        return {
            name: getattr(settings, name)
            for name in (
                "gpkg_path",
                "wsjtx_addr",
                "wsjtx_port",
                "rig_enabled",
                "rig_host",
                "rig_port",
                "rig_poll_ms",
                "rot_enabled",
                "rot_host",
                "rot_port",
            )
        }

    @staticmethod
    def _changed(old: Mapping[str, Any], new: Mapping[str, Any], *names: str) -> bool:
        return any(old.get(name) != new.get(name) for name in names)

    # ------------------------------------------------------------------ slots: events()

    @_guarded
    def _on_data_changed(self, path: str) -> None:
        if _file_key(path) == _file_key(self.gpkg_path()):
            self.refresh()

    @_guarded
    def _on_settings_changed(self) -> None:
        if not self._started:
            return
        old, new = self._snapshot, self._settings_snapshot()
        self._snapshot = new
        if self.is_listening() and self._listen_target != (new["wsjtx_addr"], new["wsjtx_port"]):
            self.set_listening(True)  # restart on the new address / port
        if self._changed(old, new, "rig_enabled", "rig_host", "rig_port", "rig_poll_ms"):
            self._apply_rig_settings()
        if self._changed(old, new, "rot_enabled", "rot_host", "rot_port"):
            self._apply_rotator_settings()
        if _file_key(old.get("gpkg_path", "")) != _file_key(new["gpkg_path"]):
            self.refresh()
        self._update_hints()

    @_guarded
    def _on_language_changed(self, _language: str) -> None:
        for hint in self._hints.values():
            hint.retranslate()

    # ------------------------------------------------------------------ slots: WSJT-X

    @_guarded
    def _on_heartbeat(self, message: dict) -> None:
        self.dock.set_wsjtx_connected(True, message.get("client"), message.get("version"))

    @_guarded
    def _on_status(self, message: dict) -> None:
        self.dock.set_wsjtx_status(message)

    @_guarded
    def _on_adif_logged(self, client: str, adif: str) -> None:
        self.handle_logged_adif(client, adif)

    @_guarded
    def _on_client_closed(self, _client: str) -> None:
        self.dock.set_wsjtx_connected(self.listener.is_connected())

    @_guarded
    def _on_wsjtx_connection(self, connected: bool) -> None:
        self.dock.set_wsjtx_connected(bool(connected))

    @_guarded
    def _on_listener_error(self, message: str) -> None:
        # already logged by the listener
        self.dock.set_wsjtx_error(message)
        self._warn(message, log=False)
        if not self.listener.is_running():
            self._listen_target = None
            self.dock.set_listening(False)
            self.listeningChanged.emit(False)

    # ------------------------------------------------------------------ slots: Hamlib

    @_guarded
    def _on_rig_connected(self, connected: bool) -> None:
        self.dock.set_rig_connected(bool(connected))

    @_guarded
    def _on_rig_state(self, state: dict) -> None:
        self.dock.set_rig_state(state)

    @_guarded
    def _on_rig_error(self, message: str) -> None:
        self.dock.set_rig_error(message)  # already logged by the client

    @_guarded
    def _on_rotator_connected(self, connected: bool) -> None:
        self.dock.set_rotator_connected(bool(connected))
        if not connected and self._point_on:
            self.set_point_on_map(False)

    @_guarded
    def _on_rotator_position(self, azimuth: float, elevation: float) -> None:
        self.dock.set_rotator_position(azimuth, elevation)

    @_guarded
    def _on_rotator_error(self, message: str) -> None:
        self.dock.set_rotator_error(message)  # already logged by the client
        if self._point_on:  # the user is clicking on the map, not looking at the panel
            self._warn(message, log=False)

    @_guarded
    def _on_cty_downloaded(self, ok: bool, message: str) -> None:
        # already logged by the manager
        self._message(message, compat.MSG_SUCCESS if ok else compat.MSG_WARNING, WARNING_SECONDS)
        if ok:
            self._close_hint(_HINT_CTY)

    # ------------------------------------------------------------------ slots: dock and map tool

    @_guarded
    def _on_refresh_requested(self) -> None:
        self.refresh()

    @_guarded
    def _on_import_requested(self) -> None:
        self.import_adif()

    @_guarded
    def _on_settings_requested(self) -> None:
        self.show_settings()

    @_guarded
    def _on_listen_toggled(self, listening: bool) -> None:
        self.set_listening(bool(listening))

    @_guarded
    def _on_rig_set_requested(self, hz: int, mode: str) -> None:
        self.set_rig(int(hz), mode)

    @_guarded
    def _on_turn_requested(self, bearing: float) -> None:
        self.turn_rotator(bearing)

    @_guarded
    def _on_stop_requested(self) -> None:
        self.stop_rotator()

    @_guarded
    def _on_point_on_map_toggled(self, enabled: bool) -> None:
        self.set_point_on_map(bool(enabled))

    @_guarded
    def _on_log_qso_requested(self) -> None:
        self.log_qso()

    @_guarded
    def _on_tool_deactivated(self) -> None:
        # another map tool took over (or ours was unset): the previous one stays forgotten
        if self._point_on:
            self._previous_tool = None
            self._set_point_state(False)
