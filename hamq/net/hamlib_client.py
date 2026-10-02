"""Asynchronous TCP clients for the Hamlib daemons ``rigctld`` and ``rotctld``.

HamQ never talks to a radio or a rotator directly: the user runs the Hamlib
network daemons and HamQ sends them one-line commands in the extended response
form (``+f``, ``+M USB 0``, ...), built and parsed by :mod:`hamq.core.hamlib`.

* :class:`RigClient` polls ``rigctld`` for the frequency (``+f``) and the mode
  (``+m``), emits ``stateChanged`` when they change and sets them (``+F``, ``+M``).
* :class:`RotatorClient` polls ``rotctld`` for the position (``+p``), emits
  ``positionChanged`` when it changes, turns the rotator (``+P``) and stops it (``+S``).

Both are built on :class:`HamlibClient`, which runs in the main thread and never
blocks it (``QTcpSocket`` signals and ``QTimer`` only):

* **One command in flight.** A command is written only after the reply to the
  previous one arrived. A reply belongs to the command in flight when its header
  is ``expected_command(command)`` (``get_freq`` for ``+f``); anything else, such
  as the stray ``RPRT`` lines a daemon prints after a command it misunderstood,
  is ignored, so the client resynchronizes by itself.
* **Queue.** Set commands wait in a FIFO ahead of the queued polls. A new set
  command replaces a queued (not yet sent) one of the same kind, so only the
  latest frequency, mode or rotator target is sent; the queue is bounded. A poll
  is never queued while the same poll is waiting or in flight (a slow daemon
  cannot pile up polls). ``stop_rotation`` cancels a queued turn and goes first.
* **Timeouts and reconnecting.** Connecting and every command have a 2 s timeout.
  A timeout, a socket error or a remote close (``rigctld`` closes the client socket
  after a hard rig error when it cannot reopen the rig) closes the socket, resets
  the parser, drops the queue, reports the problem and reconnects every 5 s until
  :meth:`HamlibClient.stop`. Polling runs only while the socket is connected.
* **A blocked GUI thread is not a timeout.** QGIS may block its GUI thread for
  seconds while the daemon answers in time. Commands are therefore sent to the
  operating system at once (``flush()``), not when the event loop runs again, and a
  timeout is reported only after one more short pass of the event loop, which
  delivers a reply (or the connection) that arrived while the thread was blocked.
* **Connected** (:meth:`HamlibClient.is_connected`, ``connectedChanged``) means
  that the daemon answered a command on the current connection; a port that
  accepts connections but never answers does not flicker between the states.
* **Errors** are translated texts, logged in the HamQ tab of the QGIS log and
  emitted with ``errorOccurred``; the GUI shows them and must not log them again.
  Connection errors say what to do (start the daemon, check the address and port in
  the HamQ settings) instead of ending in Qt's English error text. Failed set
  commands are always reported. Poll and connection errors are rate limited: a
  problem is reported when it starts, not on every poll or reconnect, and a problem
  that comes back is reported again at most once a minute.
  :meth:`HamlibClient.current_error` translates the problem reported last again, in
  the current language (after a language switch).
* Exceptions never escape into Qt: slots log them instead.

Usage (main thread)::

    rig = RigClient(poll_ms=settings.rig_poll_ms, parent=owner)
    rig.stateChanged.connect(show_state)       # {"freq_hz", "mode", "passband"}
    rig.start(settings.rig_host, settings.rig_port)
    rig.set_frequency(14074000)
    ...
    rig.stop()                                 # on unload; safe to call twice
"""

from __future__ import annotations

import contextlib
import functools
import numbers
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from qgis.core import QgsMessageLog
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkProxy, QTcpSocket

from ..core.hamlib import (
    HamlibResponse,
    ResponseParser,
    cmd_get_freq,
    cmd_get_mode,
    cmd_get_pos,
    cmd_set_freq,
    cmd_set_mode,
    cmd_set_pos,
    cmd_stop,
    error_message,
    expected_command,
    parse_freq,
    parse_mode,
    parse_pos,
)
from ..core.i18n import tr, tr_noop
from ..qgis_io.compat import (
    MSG_CRITICAL,
    MSG_INFO,
    MSG_WARNING,
    NET_PROXY_NONE,
    SOCKET_ERROR_CONNECTION_REFUSED,
    SOCKET_ERROR_HOST_NOT_FOUND,
    SOCKET_ERROR_REMOTE_CLOSED,
    SOCKET_ERROR_TIMEOUT,
)

__all__ = [
    "DEFAULT_ERROR_INTERVAL_MS",
    "DEFAULT_POLL_MS",
    "DEFAULT_RECONNECT_MS",
    "DEFAULT_TIMEOUT_MS",
    "MAX_QUEUE",
    "HamlibClient",
    "RigClient",
    "RotatorClient",
]

#: Polling interval in milliseconds (the rig uses ``settings.rig_poll_ms``).
DEFAULT_POLL_MS = 1000
#: Time allowed for connecting and for the reply to each command.
DEFAULT_TIMEOUT_MS = 2000
#: Delay between reconnection attempts.
DEFAULT_RECONNECT_MS = 5000
#: Minimum time between two reports of the same recurring problem.
DEFAULT_ERROR_INTERVAL_MS = 60000
#: Most commands and polls waiting at the same time (the oldest set command is dropped).
MAX_QUEUE = 16

_LOG_TAG = "HamQ"
_MAX_TIMER_MS = 2**31 - 1  # QTimer intervals are C int milliseconds
_MAX_ERROR_KEYS = 256
# Before a timeout is reported the event loop gets one more pass of this length (see
# HamlibClient._settle), again whenever the watchdog fired this much later than due.
_SETTLE_MS = 100
_LATE_S = 0.25

# Connection states of the socket.
_IDLE, _CONNECTING, _CONNECTED = "idle", "connecting", "connected"

# Messages ({daemon}: rigctld or rotctld, {address}: host:port). Translated when shown.
_MSG_CONNECT_FAILED = tr_noop("Could not connect to {daemon} at {address}: {error}")
_MSG_CONNECT_REFUSED = tr_noop(
    "Could not connect to {daemon} at {address}: the connection was refused. "
    "Start {daemon} or check the address and port in the HamQ settings."
)
_MSG_HOST_NOT_FOUND = tr_noop(
    "Could not connect to {daemon} at {address}: the host name was not found. "
    "Check the address in the HamQ settings."
)
_MSG_CONNECT_TIMEOUT = tr_noop(
    "Could not connect to {daemon} at {address}: no answer within {seconds} s. "
    "Check the address and port in the HamQ settings."
)
_MSG_NO_REPLY = tr_noop("{daemon} at {address} did not reply within {seconds} s; reconnecting")
_MSG_CLOSED = tr_noop("{daemon} at {address} closed the connection; reconnecting")
_MSG_LINK_ERROR = tr_noop("Connection to {daemon} at {address} failed: {error}; reconnecting")
_MSG_CONNECTED = tr_noop("Connected to {daemon} at {address}")
_MSG_NOT_CONNECTED = tr_noop("not connected to {daemon}")
_MSG_CONNECTION_LOST = tr_noop("the connection to {daemon} was lost")
_MSG_QUEUE_FULL = tr_noop("Too many Hamlib commands are waiting; the oldest one was dropped")
_MSG_UNEXPECTED = tr_noop("Unexpected error in the Hamlib client: {error}")
_MSG_COMMAND_FAILED = tr_noop("Hamlib command {command} failed: {error}")
# Reply header of a command -> what failed.
_ACTION_FAILED = {
    "get_freq": tr_noop("Reading the frequency failed: {error}"),
    "get_mode": tr_noop("Reading the mode failed: {error}"),
    "set_freq": tr_noop("Setting the frequency failed: {error}"),
    "set_mode": tr_noop("Setting the mode failed: {error}"),
    "get_pos": tr_noop("Reading the rotator position failed: {error}"),
    "set_pos": tr_noop("Turning the rotator failed: {error}"),
    "stop": tr_noop("Stopping the rotator failed: {error}"),
}

_F = TypeVar("_F", bound=Callable[..., Any])


def _log(message: str, level: Any = MSG_WARNING) -> None:
    QgsMessageLog.logMessage(message, _LOG_TAG, level)


def _guarded(method: _F) -> _F:
    """Run ``method`` so that no exception escapes into Qt: it is logged instead.

    Used for every slot and for the parts of the public methods that run after
    the arguments were checked (the GUI may connect them to signals directly).
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except Exception:
            # When the log itself fails (QGIS shutting down) there is nothing left to do.
            with contextlib.suppress(Exception):
                _log(tr(_MSG_UNEXPECTED).format(error=traceback.format_exc()), MSG_CRITICAL)
            return None

    return wrapper  # type: ignore[return-value]


def _socket_slot(method: _F) -> _F:
    """A guarded slot for a socket signal that also counts the socket slots running.

    Slots emit this client's signals, and a receiver may run a nested event loop
    (a modal dialog) in which the client drops that socket. A socket dropped while
    any socket slot is on the stack is deleted only when the outermost one has
    returned: deleting it earlier frees the sender in the middle of its own signal
    emission (QGIS crashes in QtNetwork).
    """
    guarded = _guarded(method)

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any) -> Any:
        if sip.isdeleted(self):  # a socket's last signals while its parent (we) is destroyed
            return None
        self._socket_depth += 1
        try:
            return guarded(self, *args)
        finally:
            self._socket_depth -= 1
            if self._socket_depth == 0:
                self._delete_dropped_sockets()

    return wrapper  # type: ignore[return-value]


def _milliseconds(value: object, name: str) -> int:
    """A positive timer interval; ``TypeError`` / ``ValueError`` otherwise (programmer error)."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an int (milliseconds), not {type(value).__name__}")
    number = int(value)
    if not 0 < number <= _MAX_TIMER_MS:
        raise ValueError(f"{name} must be between 1 and {_MAX_TIMER_MS} ms, got {value!r}")
    return number


def _seconds_text(milliseconds: int) -> str:
    """``2000`` -> ``'2'``, ``250`` -> ``'0.25'``."""
    return f"{milliseconds / 1000:g}"


@dataclass(frozen=True)
class _Command:
    """A command line and the header its reply must carry."""

    text: str  # "+f\n"
    header: str  # "get_freq" (expected_command(text))
    poll: bool  # periodic read: deduplicated, its errors rate limited


def _command(text: str, *, poll: bool = False) -> _Command:
    return _Command(text, expected_command(text), poll)


class _Text:
    """A message kept untranslated: a source text marked with ``tr_noop`` and its values.

    :meth:`render` translates it into the current language, so a problem reported
    earlier can be shown again after a language switch. A value may be another
    ``_Text`` or a function that returns a translated text (``error_message(code)``).
    """

    __slots__ = ("source", "values")

    def __init__(self, source: str, **values: Any) -> None:
        self.source = source
        self.values = values

    def render(self) -> str:
        values = {name: _rendered(value) for name, value in self.values.items()}
        return tr(self.source).format(**values)


def _rendered(value: Any) -> Any:
    if isinstance(value, _Text):
        return value.render()
    if callable(value):
        return value()
    return value


def _disconnect_all(signal: Any) -> None:
    try:
        signal.disconnect()
    except (TypeError, RuntimeError):  # nothing connected / object already deleted
        pass


class HamlibClient(QObject):
    """Base client: connection, command queue, timeouts, reconnecting, polling.

    ``poll_ms`` is the polling interval; ``timeout_ms`` the time allowed for
    connecting and for each reply (2 s), ``reconnect_ms`` the delay between
    reconnection attempts (5 s) and ``error_interval_ms`` the shortest time between
    two reports of the same recurring problem (1 min). Wrong types or values
    raise ``TypeError`` / ``ValueError``. Use it from the main thread only.
    Subclasses define the polls and handle the replies.
    """

    #: ``True`` when the daemon answered on the current connection, ``False`` when that ends.
    connectedChanged = pyqtSignal(bool)
    #: A translated problem description (already logged in the HamQ log tab).
    errorOccurred = pyqtSignal(str)

    _daemon = "Hamlib"  # program name in messages

    def __init__(
        self,
        poll_ms: int = DEFAULT_POLL_MS,
        parent: QObject | None = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        reconnect_ms: int = DEFAULT_RECONNECT_MS,
        error_interval_ms: int = DEFAULT_ERROR_INTERVAL_MS,
    ) -> None:
        self._poll_ms = _milliseconds(poll_ms, "poll_ms")
        self._timeout_ms = _milliseconds(timeout_ms, "timeout_ms")
        self._reconnect_ms = _milliseconds(reconnect_ms, "reconnect_ms")
        self._error_interval_ms = _milliseconds(error_interval_ms, "error_interval_ms")
        super().__init__(parent)

        self._parser = ResponseParser()
        self._host = ""
        self._port = 0
        self._started = False
        self._socket: QTcpSocket | None = None
        self._phase = _IDLE
        self._confirmed = False  # the daemon answered on the current connection
        self._in_flight: _Command | None = None
        self._commands: deque[_Command] = deque()  # set commands, sent first
        self._polls: deque[_Command] = deque()
        self._replies: deque[HamlibResponse] = deque()  # parsed, not processed yet
        self._sent = 0  # commands written so far (the watchdog looks for progress)
        # Incremented whenever the connection is dropped or replaced; code that
        # emits signals checks it afterwards, because a slot may stop or restart us.
        self._generation = 0
        self._socket_depth = 0  # socket slots running (see _socket_slot)
        self._dropped_sockets: list[QTcpSocket] = []  # aborted, waiting for deleteLater()
        self._clock: Callable[[], float] = time.monotonic
        self._error_last: dict[tuple[Any, ...], float] = {}  # key -> time last reported
        self._error_active: set[tuple[Any, ...]] = set()  # problems that did not end yet
        # The problem reported last and its rate-limit key, until it is over (current_error).
        self._last_error: tuple[_Text, tuple[Any, ...] | None] | None = None
        self._connected_logged: float | None = None
        self._watchdog_due = 0.0  # time.monotonic() when the watchdog is due
        self._settled = False  # the watchdog already gave the event loop one more pass

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._poll_ms)
        self._poll_timer.timeout.connect(self._on_poll_timer)
        self._watchdog = QTimer(self)  # connect / reply timeout
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(self._reconnect_ms)
        self._reconnect_timer.timeout.connect(self._on_reconnect_timer)

    # ------------------------------------------------------------------ public API

    def start(self, host: str, port: int) -> None:
        """Connect to the daemon at ``host``:``port`` and keep reconnecting until :meth:`stop`.

        Returns at once; the connection is made in the background. Calling it
        again with the same address keeps the running connection (while waiting
        to reconnect it retries at once); another address restarts the client.
        ``ValueError`` / ``TypeError`` for an empty host or a port outside 1..65535.
        """
        if not isinstance(host, str):
            raise TypeError(f"host must be str, not {type(host).__name__}")
        host = host.strip()
        if not host:
            raise ValueError("host must not be empty")
        if isinstance(port, bool) or not isinstance(port, numbers.Integral):
            raise TypeError(f"port must be an int, not {type(port).__name__}")
        if not 0 < int(port) < 65536:
            raise ValueError(f"port must be between 1 and 65535, got {port!r}")
        self._start(host, int(port))

    def stop(self) -> None:
        """Disconnect and cancel everything: queue, timers, reconnecting. Safe to call twice.

        Emits ``connectedChanged(False)`` (and the unknown state) when the client
        was connected. Also safe after the Qt object was deleted with its parent
        (its socket and timers went with it): then it does nothing.
        """
        if sip.isdeleted(self):
            self._started = False
            self._socket = None
            self._confirmed = False
            self._last_error = None
            return
        self._stop()

    def is_connected(self) -> bool:
        """``True`` while the daemon answers on the current connection."""
        return self._confirmed

    def is_running(self) -> bool:
        """``True`` between :meth:`start` and :meth:`stop` (connected or reconnecting)."""
        return self._started

    def address(self) -> str:
        """``host:port`` given to :meth:`start` (``[v6]:port`` for IPv6), ``""`` before."""
        if not self._host:
            return ""
        if ":" in self._host:
            return f"[{self._host}]:{self._port}"
        return f"{self._host}:{self._port}"

    def current_error(self) -> str:
        """The problem reported last through ``errorOccurred``, translated again into the
        current language; ``""`` when there is none or it is over.

        For showing that problem again after a language switch (``errorOccurred`` is not
        emitted again while the problem goes on). It is over when the daemon answers on a
        new connection, when the failing poll succeeds again, when a new command is given
        (``set_frequency``, ``set_mode``, ``set_position``, ``stop_rotation``; a refused
        one is the new problem) and after :meth:`stop` or :meth:`start` with another
        address. Never raises.
        """
        last = self._last_error
        if last is None:
            return ""
        try:
            return last[0].render()
        except Exception:  # a broken translation must not break a language switch
            return ""

    def poll_interval(self) -> int:
        """The polling interval in milliseconds."""
        return self._poll_ms

    def set_poll_interval(self, poll_ms: int) -> None:
        """Change the polling interval (e.g. after the settings changed); takes effect at once."""
        self._poll_ms = _milliseconds(poll_ms, "poll_ms")
        if self._poll_timer.isActive():
            self._poll_timer.start(self._poll_ms)
        else:
            self._poll_timer.setInterval(self._poll_ms)

    # ------------------------------------------------------------------ subclass hooks

    def _poll_commands(self) -> Iterable[_Command]:
        """Commands sent on every poll tick."""
        return ()

    def _handle_response(self, command: _Command, reply: HamlibResponse) -> None:
        """Process the reply to ``command`` (``reply.rprt`` may be an error code)."""

    def _forget_values(self) -> bool:
        """Mark the polled values unknown after the connection ended; ``True`` if they changed."""
        return False

    def _values_forgotten(self) -> None:
        """Emit the change made by :meth:`_forget_values` (called after ``connectedChanged``)."""

    # ------------------------------------------------------------------ connection

    @_guarded
    def _start(self, host: str, port: int) -> None:
        if self._started and (host, port) == (self._host, self._port):
            if self._socket is None:  # waiting to reconnect: try now
                self._reconnect_timer.stop()
                self._connect()
            return
        if self._started:
            self._stop()
        self._host, self._port = host, port
        self._started = True
        self._error_last.clear()
        self._error_active.clear()
        self._last_error = None
        self._connected_logged = None
        self._connect()

    @_guarded
    def _stop(self) -> None:
        self._started = False
        self._last_error = None
        self._reconnect_timer.stop()
        if self._drop_link():
            self._announce_disconnected()

    def _connect(self) -> None:
        """Open a new socket to the daemon (the previous one was dropped)."""
        socket = QTcpSocket(self)
        socket.setProxy(QNetworkProxy(NET_PROXY_NONE))
        socket.connected.connect(self._on_connected)
        socket.readyRead.connect(self._on_ready_read)
        socket.errorOccurred.connect(self._on_socket_error)
        socket.disconnected.connect(self._on_disconnected)
        self._generation += 1
        self._socket = socket
        self._phase = _CONNECTING
        self._parser.reset()
        self._arm_watchdog(self._timeout_ms)
        socket.connectToHost(self._host, self._port)  # may report an error synchronously

    def _drop_link(self) -> bool:
        """Close the socket and forget the connection; ``True`` if the daemon had answered.

        Signals of the old socket are disconnected before it is aborted, so
        ``abort()`` cannot call back into this object; it is deleted later (see
        :func:`_socket_slot`).
        """
        self._generation += 1
        self._watchdog.stop()
        self._poll_timer.stop()
        socket, self._socket = self._socket, None
        self._phase = _IDLE
        was_confirmed, self._confirmed = self._confirmed, False
        self._in_flight = None
        self._commands.clear()
        self._polls.clear()
        self._replies.clear()
        self._parser.reset()
        if socket is not None:
            for signal in (
                socket.connected,
                socket.readyRead,
                socket.errorOccurred,
                socket.disconnected,
            ):
                _disconnect_all(signal)
            try:
                socket.abort()
            except RuntimeError:  # already deleted with its parent
                pass
            self._dropped_sockets.append(socket)
            if self._socket_depth == 0:
                self._delete_dropped_sockets()
        return was_confirmed

    def _delete_dropped_sockets(self) -> None:
        """``deleteLater()`` the aborted sockets; only while no socket slot is running."""
        while self._dropped_sockets:
            socket = self._dropped_sockets.pop()
            try:
                socket.deleteLater()
            except RuntimeError:  # already deleted with its parent
                pass

    def _link_lost(self, key: tuple[Any, ...], message: _Text) -> None:
        """The connection failed or ended: drop it, report, reconnect later."""
        if self._socket is None:
            return
        lost = [c for c in (self._in_flight, *self._commands) if c is not None and not c.poll]
        reason = _MSG_CONNECTION_LOST if self._phase == _CONNECTED else _MSG_NOT_CONNECTED
        was_connected = self._drop_link()
        generation = self._generation
        if was_connected:
            self._announce_disconnected()
            if self._generation != generation:
                return
        self._report(message, key)
        for command in lost:
            if self._generation != generation:
                return
            self._report_action(command, _Text(reason, daemon=self._daemon))
        if self._generation == generation and self._started and self._socket is None:
            self._reconnect_timer.start(self._reconnect_ms)

    def _announce_disconnected(self) -> None:
        """Emit ``connectedChanged(False)``, then the unknown values.

        The values are emitted even when a ``connectedChanged`` slot stopped or
        restarted the client: they are unknown either way, and the receivers must
        not keep showing the old ones.
        """
        changed = self._forget_values()
        self.connectedChanged.emit(False)
        if changed:
            self._values_forgotten()

    def _confirm(self) -> None:
        """The daemon answered for the first time on this connection."""
        self._confirmed = True
        self._error_active = {key for key in self._error_active if key[0] != "link"}
        self._last_error = None  # what went wrong before this connection is over
        now = self._clock()
        last = self._connected_logged
        if last is None or now - last >= self._error_interval_ms / 1000:
            self._connected_logged = now
            _log(
                tr(_MSG_CONNECTED).format(daemon=self._daemon, address=self.address()),
                MSG_INFO,
            )
        self.connectedChanged.emit(True)

    # ------------------------------------------------------------------ queue

    def _accepting(self) -> bool:
        """Commands are accepted while connecting or connected, not while waiting to reconnect."""
        return self._started and self._socket is not None

    @_guarded
    def _submit(
        self,
        command: _Command,
        refresh: Iterable[_Command] = (),
        *,
        first: bool = False,
        cancel: Iterable[str] = (),
    ) -> None:
        """Queue a set command, then the polls that show its effect, and send.

        ``first`` puts it ahead of the other waiting commands; ``cancel`` names the
        headers of waiting (unsent) commands it makes pointless.
        """
        self._last_error = None  # a new command: an earlier failure is not the problem now
        if not self._accepting():
            self._report_action(command, _Text(_MSG_NOT_CONNECTED, daemon=self._daemon))
            return
        drop = {command.header, *cancel}
        self._commands = deque(c for c in self._commands if c.header not in drop)
        if first:
            self._commands.appendleft(command)
        else:
            self._commands.append(command)
        for poll in reversed(tuple(refresh)):  # each goes to the front: keep the given order
            self._enqueue_poll(poll, urgent=True)
        while len(self._commands) + len(self._polls) > MAX_QUEUE:
            if len(self._polls) > 1:
                self._polls.pop()
            else:
                self._commands.popleft()
                _log(tr(_MSG_QUEUE_FULL), MSG_WARNING)
        self._send_next()

    def _enqueue_poll(self, command: _Command, *, urgent: bool = False) -> None:
        """Queue a poll unless the same poll is waiting (or, for a timer poll, in flight).

        ``urgent`` polls (after a set command) go ahead of the other polls and
        are queued even when the same poll is in flight: its reply may predate
        the change.
        """
        for queued in self._polls:
            if queued.header == command.header:
                if urgent:
                    self._polls.remove(queued)
                    self._polls.appendleft(queued)
                return
        if urgent:
            self._polls.appendleft(command)
            return
        if self._in_flight is not None and self._in_flight.header == command.header:
            return
        self._polls.append(command)

    def _send_next(self) -> None:
        """Write the next waiting command when the socket is connected and nothing is in flight."""
        socket = self._socket
        if socket is None or self._phase != _CONNECTED or self._in_flight is not None:
            return
        if self._commands:
            command = self._commands.popleft()
        elif self._polls:
            command = self._polls.popleft()
        else:
            return
        self._in_flight = command
        self._sent += 1
        self._arm_watchdog(self._timeout_ms)
        generation = self._generation
        written = socket.write(command.text.encode("utf-8"))
        if self._generation != generation:  # an error was reported inside write()
            return
        if written < 0:
            self._link_lost(("link", "error", socket.errorString()), self._link_error_text(socket))
            return
        # Hand it to the operating system now: write() only buffers it until the event loop
        # runs again. If the GUI thread is blocked before that, the daemon would get the
        # command only after the block, just before the overdue watchdog runs. An error
        # inside flush() is reported through _on_socket_error.
        socket.flush()

    # ------------------------------------------------------------------ slots

    @_socket_slot
    def _on_connected(self) -> None:
        if self._socket is None or self._phase != _CONNECTING:
            return
        self._watchdog.stop()
        self._phase = _CONNECTED
        self._poll_timer.start(self._poll_ms)
        for command in self._poll_commands():
            self._enqueue_poll(command)
        self._send_next()

    @_socket_slot
    def _on_ready_read(self) -> None:
        self._drain()

    def _drain(self) -> None:
        """Read what the socket has buffered and process the replies, in order.

        Qt does not emit ``readyRead`` again while a ``readyRead`` slot runs: data that
        arrives while a receiver of our signals runs a nested event loop (a modal
        dialog) is only buffered. So the poll timer and the watchdog call this too,
        and nested calls share the queue of parsed replies, which keeps the order.
        """
        generation = self._generation
        while self._socket is not None and self._generation == generation:
            if self._replies:
                self._on_reply(self._replies.popleft())
                continue
            # QByteArray -> bytes: ResponseParser.feed() rejects QByteArray.
            data = bytes(self._socket.readAll())
            if not data:
                return
            self._replies.extend(self._parser.feed(data))

    def _on_reply(self, reply: HamlibResponse) -> None:
        command = self._in_flight
        if command is None or reply.command != command.header:
            return  # a stray reply (e.g. RPRT lines after a misunderstood command)
        self._watchdog.stop()
        self._in_flight = None
        generation = self._generation
        if not self._confirmed:
            self._confirm()
            if self._generation != generation:
                return
        if reply.rprt != 0:
            reason = functools.partial(error_message, reply.rprt)
            self._report_action(command, reason, reply.rprt)
            if self._generation != generation:
                return
        elif command.poll:
            self._error_active = {
                key for key in self._error_active if key[:2] != ("rprt", command.header)
            }
            last = self._last_error
            if last is not None and last[1] is not None and last[1][:2] == ("rprt", command.header):
                self._last_error = None  # the poll works again
        self._handle_response(command, reply)
        if self._generation != generation:
            return
        self._send_next()

    @_socket_slot
    def _on_socket_error(self, error: Any) -> None:
        socket = self._socket
        if socket is None:
            return
        if self._phase == _CONNECTING:
            if error == SOCKET_ERROR_TIMEOUT:  # Qt's own connect timeout (30 s), after ours
                self._link_lost(("link", "connect-timeout"), self._connect_timeout_text())
            else:
                text = socket.errorString()
                self._link_lost(("link", "connect", text), self._connect_error_text(error, text))
        elif error == SOCKET_ERROR_REMOTE_CLOSED:
            self._link_lost(("link", "closed"), self._closed_text())
        else:
            self._link_lost(("link", "error", socket.errorString()), self._link_error_text(socket))

    @_socket_slot
    def _on_disconnected(self) -> None:
        # Normally errorOccurred(RemoteHostClosedError) comes first and has dropped
        # the socket already; this covers a close without an error.
        if self._socket is not None:
            self._link_lost(("link", "closed"), self._closed_text())

    def _arm_watchdog(self, milliseconds: int, *, settled: bool = False) -> None:
        self._settled = settled
        self._watchdog_due = time.monotonic() + milliseconds / 1000
        self._watchdog.start(milliseconds)

    def _settle(self) -> bool:
        """Give the event loop one more short pass before a timeout is reported.

        After the GUI thread was blocked, Qt may run the overdue watchdog before it
        delivers the socket notifications that arrived meanwhile: the reply, or the
        connection. ``True`` when the watchdog was armed again for that pass; it is armed
        again too when it fired late once more (the thread was blocked again).
        """
        late = time.monotonic() - self._watchdog_due > _LATE_S
        if self._settled and not late:
            return False
        self._arm_watchdog(min(_SETTLE_MS, self._timeout_ms), settled=True)
        return True

    @_guarded
    def _on_watchdog(self) -> None:
        if self._socket is None:
            return
        if self._phase == _CONNECTING:
            if not self._settle():
                self._link_lost(("link", "connect-timeout"), self._connect_timeout_text())
            return
        if self._in_flight is None:
            return
        # The reply may be waiting unread (see _drain): look before giving up.
        sent, generation = self._sent, self._generation
        self._drain()
        if self._generation != generation or self._sent != sent or self._in_flight is None:
            return
        if self._settle():
            return
        message = _Text(
            _MSG_NO_REPLY,
            daemon=self._daemon,
            address=self.address(),
            seconds=_seconds_text(self._timeout_ms),
        )
        self._link_lost(("link", "no-reply"), message)

    @_guarded
    def _on_poll_timer(self) -> None:
        if self._socket is None or self._phase != _CONNECTED:
            return
        generation = self._generation
        self._drain()  # replies left unread by a nested event loop (see _drain)
        if self._generation != generation:
            return
        for command in self._poll_commands():
            self._enqueue_poll(command)
        self._send_next()

    @_guarded
    def _on_reconnect_timer(self) -> None:
        if self._started and self._socket is None:
            self._connect()

    # ------------------------------------------------------------------ reporting

    def _closed_text(self) -> _Text:
        return _Text(_MSG_CLOSED, daemon=self._daemon, address=self.address())

    def _link_error_text(self, socket: QTcpSocket) -> _Text:
        return _Text(
            _MSG_LINK_ERROR,
            daemon=self._daemon,
            address=self.address(),
            error=socket.errorString(),
        )

    def _connect_timeout_text(self) -> _Text:
        return _Text(
            _MSG_CONNECT_TIMEOUT,
            daemon=self._daemon,
            address=self.address(),
            seconds=_seconds_text(self._timeout_ms),
        )

    def _connect_error_text(self, error: Any, text: str) -> _Text:
        """What went wrong while connecting, with what to do for the common cases."""
        if error == SOCKET_ERROR_CONNECTION_REFUSED:  # nothing listens: the daemon is not running
            return _Text(_MSG_CONNECT_REFUSED, daemon=self._daemon, address=self.address())
        if error == SOCKET_ERROR_HOST_NOT_FOUND:
            return _Text(_MSG_HOST_NOT_FOUND, daemon=self._daemon, address=self.address())
        return _Text(_MSG_CONNECT_FAILED, daemon=self._daemon, address=self.address(), error=text)

    def _report_action(
        self, command: _Command, reason: _Text | Callable[[], str], code: int | None = None
    ) -> None:
        """Report a failed command; polls are rate limited, set commands never are."""
        template = _ACTION_FAILED.get(command.header, _MSG_COMMAND_FAILED)
        key = ("rprt", command.header, code) if command.poll else None
        self._report(_Text(template, error=reason, command=command.header), key)

    def _report(self, text: _Text, key: tuple[Any, ...] | None = None) -> None:
        """Log ``text`` (translated now) and emit ``errorOccurred``.

        With a ``key`` the report is rate limited: once reported, nothing more while
        the same problem is still going on (it ends when the command succeeds or,
        for connection problems, when the daemon answers again), and a problem that
        comes back is reported at most once per ``error_interval_ms`` (when it
        persists, as soon as the interval has passed).
        """
        if key is not None:
            if key in self._error_active:
                return
            now = self._clock()
            last = self._error_last.get(key)
            if last is not None and now - last < self._error_interval_ms / 1000:
                return
            if len(self._error_last) >= _MAX_ERROR_KEYS:
                self._error_last.clear()
            self._error_last[key] = now
            self._error_active.add(key)
        message = text.render()
        self._last_error = (text, key)
        _log(message, MSG_WARNING)
        self.errorOccurred.emit(message)


_GET_FREQ = _command(cmd_get_freq(), poll=True)
_GET_MODE = _command(cmd_get_mode(), poll=True)
_GET_POS = _command(cmd_get_pos(), poll=True)


def _unknown_state() -> dict[str, Any]:
    return {"freq_hz": None, "mode": None, "passband": None}


class RigClient(HamlibClient):
    """Client for ``rigctld``: frequency and mode of the radio.

    Polls ``+f`` and ``+m`` every ``poll_ms`` (``settings.rig_poll_ms``) and emits
    ``stateChanged`` only when the state changed. A value is ``None`` while it is
    unknown: before the first reply, after a failed read and after the connection
    ended.
    """

    #: ``{"freq_hz": int | None, "mode": str | None, "passband": int | None}`` (a new dict).
    stateChanged = pyqtSignal(dict)

    _daemon = "rigctld"

    def __init__(
        self,
        poll_ms: int = DEFAULT_POLL_MS,
        parent: QObject | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(poll_ms, parent, **kwargs)
        self._state = _unknown_state()

    def state(self) -> dict[str, Any]:
        """The last known state as a new dict (``None`` values when unknown)."""
        return dict(self._state)

    def set_frequency(self, hz: int) -> None:
        """Tune the radio to ``hz`` (``+F``), then read the frequency and mode back at once.

        ``ValueError`` when ``hz`` is not above 0, ``TypeError`` when it is not a
        number. Reported through ``errorOccurred`` when the client is not connected.
        """
        self._submit(_command(cmd_set_freq(hz)), (_GET_FREQ, _GET_MODE))

    def set_mode(self, mode: str, passband: int = 0) -> None:
        """Set the mode (``+M``; the passband is always sent), then read the state back.

        ``mode`` is one of ``hamq.core.hamlib.MODES`` (``USB``, ``PKTUSB``, ...);
        ``passband`` in Hz, 0 = the radio's default for the mode, -1 = unchanged.
        ``ValueError`` / ``TypeError`` for other values.
        """
        self._submit(_command(cmd_set_mode(mode, passband)), (_GET_MODE, _GET_FREQ))

    def _poll_commands(self) -> Iterable[_Command]:
        return (_GET_FREQ, _GET_MODE)

    def _handle_response(self, command: _Command, reply: HamlibResponse) -> None:
        if command.header == _GET_FREQ.header:
            self._update_state(freq_hz=parse_freq(reply))
        elif command.header == _GET_MODE.header:
            parsed = parse_mode(reply)
            if parsed is None:
                self._update_state(mode=None, passband=None)
            else:
                self._update_state(mode=parsed[0], passband=parsed[1])

    def _update_state(self, **values: Any) -> None:
        state = dict(self._state)
        state.update(values)
        if state != self._state:
            self._state = state
            self.stateChanged.emit(dict(state))

    def _forget_values(self) -> bool:
        unknown = _unknown_state()
        changed = self._state != unknown
        self._state = unknown
        return changed

    def _values_forgotten(self) -> None:
        self.stateChanged.emit(dict(self._state))


class RotatorClient(HamlibClient):
    """Client for ``rotctld``: position of the rotator, turning and stopping.

    Polls ``+p`` every ``poll_ms`` (1 s) and emits ``positionChanged`` when the
    position changed. The azimuth is sent as given: map a compass bearing into
    the rotator range with ``hamq.core.hamlib.rotator_target`` first (the daemon
    rejects positions outside its range with ``RPRT -21``, reported as an error).
    """

    #: Azimuth and elevation in degrees.
    positionChanged = pyqtSignal(float, float)

    _daemon = "rotctld"

    def __init__(
        self,
        poll_ms: int = DEFAULT_POLL_MS,
        parent: QObject | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(poll_ms, parent, **kwargs)
        self._position: tuple[float, float] | None = None

    def position(self) -> tuple[float, float] | None:
        """The last known ``(azimuth, elevation)``, ``None`` while unknown."""
        return self._position

    def set_position(self, azimuth: float, elevation: float = 0.0) -> None:
        """Turn the rotator (``+P``); a newer target replaces one that was not sent yet.

        ``ValueError`` for non-finite values, ``TypeError`` for non-numbers.
        """
        self._submit(_command(cmd_set_pos(azimuth, elevation)), (_GET_POS,))

    def stop_rotation(self) -> None:
        """Stop the rotator (``+S``): cancels a turn that was not sent yet and goes first."""
        self._submit(_command(cmd_stop()), (_GET_POS,), first=True, cancel=("set_pos",))

    def _poll_commands(self) -> Iterable[_Command]:
        return (_GET_POS,)

    def _handle_response(self, command: _Command, reply: HamlibResponse) -> None:
        if command.header != _GET_POS.header:
            return
        position = parse_pos(reply)
        if position is None:
            self._position = None  # unknown; the next good reply is reported again
        elif position != self._position:
            self._position = position
            self.positionChanged.emit(position[0], position[1])

    def _forget_values(self) -> bool:
        self._position = None
        return False
