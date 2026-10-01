"""WSJT-X / JTDX UDP listener: status, heartbeats and logged QSOs as Qt signals.

WSJT-X sends its UDP messages to the address set under File > Settings > Reporting > UDP
Server, 127.0.0.1:2237 by default. :class:`WsjtxListener` binds a ``QUdpSocket`` to that
port on every IPv4 interface (``AnyIPv4`` with ``ShareAddress | ReuseAddressHint``) and
decodes each datagram in the UI thread with :func:`hamq.core.wsjtx.decode`, which never
raises; datagrams that are not WSJT-X messages are ignored.

Only one program receives the unicast datagrams sent to a port. When another program (a
logger, JTAlert, GridTracker) already listens, WSJT-X and all listeners use a multicast
group instead (224.0.0.0/4, e.g. 224.0.0.1). With a multicast address the listener joins
the group on every interface that is up and can multicast and on the loopback interface,
or on the default interface when none of those works.

Signals:

* ``heartbeatReceived(dict)``, ``statusReceived(dict)``: the decoded message (see
  :func:`hamq.core.wsjtx.decode`; string values may be ``None``);
* ``adifLogged(client, adif)``: a Logged ADIF message (type 12). QSO Logged (type 5)
  carries the same QSO and is ignored. The same record from the same client within
  ``DUPLICATE_WINDOW_S`` is reported once: WSJT-X sends a multicast datagram on every
  outgoing interface selected in its settings;
* ``clientClosed(client)``: a WSJT-X instance closed;
* ``connectionChanged(connected)``: every message marks its client (WSJT-X instance) alive;
  a client is gone after ``CLIENT_TIMEOUT_MS`` without messages (WSJT-X sends a heartbeat
  every 15 s) or when it closes. ``True`` when the first client appears, ``False`` when the
  last one is gone or the listener stops;
* ``errorOccurred(message)``: translated, e.g. the port is used by another program.

A client id that arrives as a null string is ``""`` in ``adifLogged`` and
``clientClosed``; the dicts of the other two signals keep it as ``None``.
"""

from __future__ import annotations

import ipaddress
import time
from contextlib import suppress
from typing import Any

from qgis.core import QgsMessageLog
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtNetwork import QHostAddress, QNetworkDatagram, QNetworkInterface, QUdpSocket

from ..core.i18n import tr
from ..core.wsjtx import decode
from ..qgis_io.compat import (
    BIND_REUSE_ADDRESS_HINT,
    BIND_SHARE_ADDRESS,
    HOST_ANY_IPV4,
    MSG_INFO,
    MSG_WARNING,
    NETIF_CAN_MULTICAST,
    NETIF_IS_LOOPBACK,
    NETIF_IS_UP,
    SOCKET_ERROR_ADDRESS_IN_USE,
)

__all__ = ["WsjtxListener"]

_LOG_TAG = "HamQ"
# WSJT-X's own (English) menu path to its UDP settings: a name, not HamQ text.
_WSJTX_UDP_SETTINGS = "File > Settings > Reporting > UDP Server"
_EXAMPLE_MULTICAST = "224.0.0.1"
_UNICAST, _MULTICAST, _IPV6 = "unicast", "multicast", "ipv6"


def _log(message: str, level: Any) -> None:
    QgsMessageLog.logMessage(message, _LOG_TAG, level)


def _display(client: str) -> str:
    """A client id for log messages: an empty (or null) id is shown as ``WSJT-X``."""
    return client or "WSJT-X"


def _parse_address(text: str) -> tuple[str, str] | None:
    """``(kind, address)`` for the address set in WSJT-X; ``None`` when it is not an address.

    ``""`` means any address and ``localhost`` the loopback address; kind is unicast,
    multicast (224.0.0.0/4) or ipv6.
    """
    if not text:
        return _UNICAST, "0.0.0.0"
    if text.lower() == "localhost":
        return _UNICAST, "127.0.0.1"
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if address.version != 4:
        return _IPV6, str(address)
    return (_MULTICAST if address.is_multicast else _UNICAST), str(address)


def _join_group(socket: QUdpSocket, group: QHostAddress) -> list[QNetworkInterface | None]:
    """Join ``group`` on every interface that is up and can multicast and on the loopback
    interface; on the default interface when none of them works.

    Returns the interfaces joined (``None`` stands for the default interface).
    """
    joined: list[QNetworkInterface | None] = []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if not flags & NETIF_IS_UP:
            continue
        if not (flags & NETIF_CAN_MULTICAST or flags & NETIF_IS_LOOPBACK):
            continue
        if socket.joinMulticastGroup(group, interface):  # fails without an IPv4 address
            joined.append(interface)
    if not joined and socket.joinMulticastGroup(group):
        joined.append(None)
    return joined


class WsjtxListener(QObject):
    """Receives WSJT-X / JTDX UDP messages (see the module documentation).

    Use from the UI thread. :meth:`stop` releases the socket, the multicast membership and
    the timers; call it before the object goes away (plugin unload).
    """

    #: The decoded Heartbeat message.
    heartbeatReceived = pyqtSignal(dict)
    #: The decoded Status message (frequency, mode, DX call, ...).
    statusReceived = pyqtSignal(dict)
    #: ``(client id, ADIF text)`` of a Logged ADIF message.
    adifLogged = pyqtSignal(str, str)
    #: ``client id`` of a WSJT-X instance that closed.
    clientClosed = pyqtSignal(str)
    #: ``True`` when a client sends messages, ``False`` when none is left.
    connectionChanged = pyqtSignal(bool)
    #: A translated error message (the listener is not running afterwards).
    errorOccurred = pyqtSignal(str)

    #: A client counts as gone after this long without a message (milliseconds).
    CLIENT_TIMEOUT_MS = 45_000
    #: Datagrams handled per pass of the event loop; the rest wait for the next pass, so a
    #: flood of datagrams cannot freeze the user interface.
    MAX_DATAGRAMS_PER_PASS = 100
    #: The same Logged ADIF record from the same client within this time is reported once.
    DUPLICATE_WINDOW_S = 2.0

    def __init__(self, parent: QObject | None = None, *, client_timeout_ms: int | None = None):
        super().__init__(parent)
        self._socket: QUdpSocket | None = None
        self._group: QHostAddress | None = None
        self._interfaces: list[QNetworkInterface | None] = []
        self._address = ""
        self._port = 0
        self._clients: dict[str, float] = {}  # client id -> time.monotonic() of its last message
        self._connected = False
        self._client_timeout_ms = client_timeout_ms  # None: CLIENT_TIMEOUT_MS
        self._last_adif: tuple[str, str, float] | None = None
        self._ignored_logged = False
        self._alive_timer = QTimer(self)
        self._alive_timer.setSingleShot(True)
        self._alive_timer.timeout.connect(self._on_alive_timeout)
        self._drain_timer = QTimer(self)
        self._drain_timer.setSingleShot(True)
        self._drain_timer.setInterval(0)
        self._drain_timer.timeout.connect(self._on_ready_read)

    # ------------------------------------------------------------------ public API

    def start(self, address: str, port: int) -> bool:
        """Listen for WSJT-X on UDP ``port``; ``address`` is the address set in WSJT-X.

        A multicast address (224.0.0.0/4) joins that group; any other IPv4 address (or
        ``localhost``) means unicast. The socket always binds all IPv4 interfaces.
        Restarts when already running. Returns ``False`` and emits ``errorOccurred`` when
        the address or port is invalid, the port is in use or the group cannot be joined.
        """
        try:
            self.stop()
            return self._start(address, port)
        except Exception as exc:  # never raise into a button slot
            self._close_socket()
            self._report_error(
                tr("The WSJT-X listener could not be started: {error}").format(error=exc)
            )
            return False

    def stop(self) -> None:
        """Stop listening: leave the multicast group, close the socket, stop the timers.

        Safe to call more than once, before :meth:`start` and after the Qt object was
        deleted; never raises. Emits ``connectionChanged(False)`` when a client was
        connected.
        """
        try:
            self._stop()
        except Exception as exc:  # also a button slot or an unload step: never raise
            _log(tr("WSJT-X listener error: {error}").format(error=exc), MSG_WARNING)

    def _stop(self) -> None:
        was_running = self._socket is not None
        self._clients.clear()
        self._last_adif = None
        for timer in (self._alive_timer, self._drain_timer):
            with suppress(RuntimeError):  # the timers die with this object
                timer.stop()
        try:
            self._close_socket()
        finally:  # the state is reset and reported even if closing failed
            if was_running:
                _log(tr("Stopped listening for WSJT-X messages"), MSG_INFO)
            if self._connected:
                with suppress(RuntimeError):  # no signal to emit when the object is gone
                    self._set_connected(False)

    def is_running(self) -> bool:
        """True while the socket is bound."""
        return self._socket is not None

    def is_connected(self) -> bool:
        """True while at least one client sent a message within the timeout."""
        return self._connected

    def address(self) -> str:
        """The address given to :meth:`start` (normalized), ``""`` when not running."""
        return self._address if self._socket is not None else ""

    def port(self) -> int:
        """The UDP port listened on, 0 when not running."""
        return self._port if self._socket is not None else 0

    # ------------------------------------------------------------------ start and stop

    def _start(self, address: str, port: int) -> bool:
        self._close_socket()  # a connectionChanged slot run by stop() may have started one
        text = address.strip() if isinstance(address, str) else ""
        parsed = _parse_address(text)
        if parsed is None or parsed[0] == _IPV6:
            template = (
                tr(
                    "Invalid WSJT-X address {address}: use an IPv4 address such as 127.0.0.1 "
                    "or a multicast address such as {example}"
                )
                if parsed is None
                else tr(
                    "IPv6 addresses are not supported for WSJT-X: {address}. Use an IPv4 "
                    "address such as 127.0.0.1 or a multicast address such as {example}"
                )
            )
            self._report_error(template.format(address=text, example=_EXAMPLE_MULTICAST))
            return False
        kind, normalized = parsed
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            self._report_error(
                tr("Invalid UDP port {port}: use a number from 1 to 65535").format(port=port)
            )
            return False

        socket = QUdpSocket(self)
        try:
            return self._listen(socket, kind, normalized, port)
        except BaseException:
            if socket is not self._socket:  # never leave a bound socket behind
                self._dispose(socket)
            raise

    def _listen(self, socket: QUdpSocket, kind: str, normalized: str, port: int) -> bool:
        """Bind ``socket``, join the group for a multicast address, start receiving."""
        flags = BIND_SHARE_ADDRESS | BIND_REUSE_ADDRESS_HINT
        if not socket.bind(QHostAddress(HOST_ANY_IPV4), port, flags):
            message = self._bind_error(socket, port)
            self._dispose(socket)
            self._report_error(message)
            return False
        group = None
        interfaces: list[QNetworkInterface | None] = []
        if kind == _MULTICAST:
            group = QHostAddress(normalized)
            interfaces = _join_group(socket, group)
            if not interfaces:
                message = tr("Could not join the multicast group {address}: {error}").format(
                    address=normalized, error=socket.errorString()
                )
                self._dispose(socket)
                self._report_error(message)
                return False

        socket.readyRead.connect(self._on_ready_read)
        self._socket, self._group, self._interfaces = socket, group, interfaces
        self._address, self._port = normalized, port
        self._ignored_logged = False
        if group is None:
            _log(tr("Listening for WSJT-X messages on UDP port {port}").format(port=port), MSG_INFO)
        else:
            _log(
                tr(
                    "Listening for WSJT-X messages on UDP port {port}, multicast group {address}"
                ).format(port=port, address=normalized),
                MSG_INFO,
            )
        if socket.hasPendingDatagrams():  # arrived before readyRead was connected
            self._drain_timer.start()
        return True

    def _bind_error(self, socket: QUdpSocket, port: int) -> str:
        if socket.error() == SOCKET_ERROR_ADDRESS_IN_USE:
            return tr(
                "UDP port {port} is already in use, probably by another program that receives "
                "WSJT-X messages (a logger, JTAlert, GridTracker). Only one program can receive "
                "on a port unless all of them use multicast: set the same multicast address, "
                "for example {example}, in WSJT-X ({menu}) and in the HamQ settings."
            ).format(port=port, example=_EXAMPLE_MULTICAST, menu=_WSJTX_UDP_SETTINGS)
        return tr(
            "Cannot listen on UDP port {port}: {error}. If another program receives WSJT-X "
            "messages on this port, set the same multicast address, for example {example}, in "
            "WSJT-X ({menu}) and in the HamQ settings."
        ).format(
            port=port,
            error=socket.errorString().rstrip(". "),
            example=_EXAMPLE_MULTICAST,
            menu=_WSJTX_UDP_SETTINGS,
        )

    def _close_socket(self) -> None:
        socket, self._socket = self._socket, None
        group, self._group = self._group, None
        interfaces, self._interfaces = self._interfaces, []
        if socket is None:
            return
        with suppress(TypeError, RuntimeError):
            socket.readyRead.disconnect(self._on_ready_read)
        if group is not None:
            for interface in interfaces:
                with suppress(RuntimeError):
                    if interface is None:
                        socket.leaveMulticastGroup(group)
                    else:
                        socket.leaveMulticastGroup(group, interface)
        self._dispose(socket)

    @staticmethod
    def _dispose(socket: QUdpSocket) -> None:
        with suppress(RuntimeError):  # already deleted with its parent
            socket.close()  # releases the port at once
            socket.deleteLater()

    def _report_error(self, message: str) -> None:
        _log(message, MSG_WARNING)
        with suppress(RuntimeError):  # the Qt object was deleted: the log has the message
            self.errorOccurred.emit(message)

    # ------------------------------------------------------------------ datagrams

    def _on_ready_read(self) -> None:
        try:
            self._read_pending()
        except Exception as exc:  # never let an exception escape into Qt
            _log(tr("WSJT-X listener error: {error}").format(error=exc), MSG_WARNING)

    def _read_pending(self) -> None:
        socket = self._socket
        if socket is None:
            return
        for _ in range(max(1, self.MAX_DATAGRAMS_PER_PASS)):
            if not socket.hasPendingDatagrams():
                return
            datagram = socket.receiveDatagram()
            try:
                self._handle(socket, datagram)
            except Exception as exc:  # one bad message must not stop the others
                _log(tr("WSJT-X listener error: {error}").format(error=exc), MSG_WARNING)
            if self._socket is not socket:  # a slot stopped or restarted the listener
                return
        if socket.hasPendingDatagrams():
            self._drain_timer.start()  # continue in the next pass of the event loop

    def _handle(self, socket: QUdpSocket, datagram: QNetworkDatagram) -> None:
        message = decode(bytes(datagram.data()))
        if message is None:
            self._note_ignored(datagram)
            return
        client = message.get("client") or ""
        kind = message.get("type")
        if kind == "close":
            self._close_client(client)
            return
        self._mark_alive(client)
        if self._socket is not socket:  # a connectionChanged slot stopped the listener
            return
        if kind == "heartbeat":
            self.heartbeatReceived.emit(message)
        elif kind == "status":
            self.statusReceived.emit(message)
        elif kind == "logged_adif":
            adif = message.get("adif")
            if adif and adif.strip() and not self._is_duplicate(client, adif):
                self.adifLogged.emit(client, adif)
        # "other": QSO Logged (5) repeats Logged ADIF; decodes and the rest are not used

    def _note_ignored(self, datagram: QNetworkDatagram) -> None:
        if self._ignored_logged:
            return
        self._ignored_logged = True
        sender = f"{datagram.senderAddress().toString()}:{datagram.senderPort()}"
        _log(
            tr(
                "Ignored a UDP datagram that is not a WSJT-X message (from {sender}); further "
                "ones are not logged"
            ).format(sender=sender),
            MSG_INFO,
        )

    def _is_duplicate(self, client: str, adif: str) -> bool:
        now = time.monotonic()
        last, self._last_adif = self._last_adif, (client, adif, now)
        return (
            last is not None
            and last[0] == client
            and last[1] == adif
            and now - last[2] < self.DUPLICATE_WINDOW_S
        )

    # ------------------------------------------------------------------ clients

    def _mark_alive(self, client: str) -> None:
        self._clients[client] = time.monotonic()
        self._schedule_alive_check()
        if not self._connected:
            _log(tr("WSJT-X connected: {client}").format(client=_display(client)), MSG_INFO)
            self._set_connected(True)

    def _close_client(self, client: str) -> None:
        self._clients.pop(client, None)
        _log(tr("WSJT-X closed: {client}").format(client=_display(client)), MSG_INFO)
        self.clientClosed.emit(client)
        self._schedule_alive_check()
        if not self._clients and self._connected:
            self._set_connected(False)

    def _timeout_s(self) -> float:
        milliseconds = self._client_timeout_ms or self.CLIENT_TIMEOUT_MS
        return max(1, int(milliseconds)) / 1000.0

    def _schedule_alive_check(self) -> None:
        """Run :meth:`_on_alive_timeout` when the least recently heard client expires."""
        if not self._clients:
            self._alive_timer.stop()
            return
        expires = min(self._clients.values()) + self._timeout_s()
        self._alive_timer.start(max(1, int((expires - time.monotonic()) * 1000) + 1))

    def _on_alive_timeout(self) -> None:
        try:
            now = time.monotonic()
            limit = self._timeout_s()
            gone = [client for client, seen in self._clients.items() if now - seen >= limit]
            for client in gone:
                del self._clients[client]
                _log(
                    tr("No messages from {client} for {seconds} s").format(
                        client=_display(client), seconds=f"{limit:g}"
                    ),
                    MSG_INFO,
                )
            self._schedule_alive_check()  # a timer that fired early just waits again
            if not self._clients and self._connected:
                self._set_connected(False)
        except Exception as exc:  # never let an exception escape into Qt
            _log(tr("WSJT-X listener error: {error}").format(error=exc), MSG_WARNING)

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected  # first: the state is right even if emitting fails
        self.connectionChanged.emit(connected)
