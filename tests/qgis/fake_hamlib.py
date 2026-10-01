"""In-process fake ``rigctld`` / ``rotctld`` for the Hamlib client tests.

:class:`FakeHamlib` listens on 127.0.0.1 with a ``QTcpServer`` in the test (main)
thread, so the client under test and the fake share one Qt event loop; tests run
it with :func:`wait_until` / :func:`pump`. Replies use the exact extended-response
formats captured from Hamlib 4.6.2 ``rigctld -m 1`` / ``rotctld -m 1``
(docs/ARCHITECTURE.md, "core/hamlib.py")::

    +f            -> 'get_freq:\\nFrequency: 14074000\\nRPRT 0\\n'
    +m            -> 'get_mode:\\nMode: USB\\nPassband: 2400\\nRPRT 0\\n'
    +F 14074000   -> 'set_freq: 14074000\\nRPRT 0\\n'
    +M USB 0      -> 'set_mode: USB 0\\nRPRT 0\\n'
    +t            -> 'get_ptt:\\nRPRT -11\\n'
    +p            -> 'get_pos:\\nAzimuth: 123.00\\nElevation: 0.00\\nRPRT 0\\n'
    +P 123.5 10   -> 'set_pos: 123.5 10\\nRPRT 0\\n'
    +S            -> 'stop:\\nRPRT 0\\n'
    +F abc        -> 'set_freq: abc\\nRPRT -1\\n'

Like the real daemons (checked against the ``hamq/hamlib-dummy`` image) it answers
only its own commands: anything else gets no reply at all (``rotctld`` swallows
``+f``, ``rigctld`` reads ``+p`` as ``get_parm`` waiting for its argument), and
``+M USB`` without a passband takes the next line as the passband
(``+f`` -> ``'set_mode: USB +f\\nRPRT -1\\n'``). A position outside -180..450 /
0..90 gives ``RPRT -21`` as on the dummy rotator.

Switches that make it misbehave (set them at any time):

* ``mute``: never answer anything; ``mute_commands``: never answer these letters;
* ``errors``: ``{"m": -11}`` answers ``+m`` with ``RPRT -11`` (header, no fields);
* ``replies``: ``{"f": "get_freq:\\n"}`` sends that text instead of the reply (here
  the header without ``RPRT`` that rigctld prints when the rig reports an I/O error);
* ``stray``: text sent before every reply (stray ``RPRT`` lines, foreign headers);
* ``chunk_size`` / ``chunk_gap_ms``: send replies in packets of that many bytes;
* ``delay_ms``: answer that much later;
* ``close_on``: ``{"f": "get_freq:\\n"}`` sends that text for ``+f`` and closes the
  connection (rigctld after a hard rig error it cannot recover from);
* :meth:`close_clients` (daemon drops everyone), :meth:`stop` (daemon gone,
  connections refused), :meth:`restart` (back on the same port).

:attr:`received` lists every command line received, in order.

:class:`ThreadedFakeHamlib` answers from its own thread instead, so it keeps
answering while a test blocks the Qt (main) thread, as a real daemon does.
"""

from __future__ import annotations

import socket as pysocket
import threading
import time
from collections.abc import Callable
from contextlib import suppress

from qgis.PyQt.QtCore import QCoreApplication, QEvent, QObject, QTimer
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from hamq.core.hamlib import MODES

_RIG_COMMANDS = frozenset("fFmMt")
_ROT_COMMANDS = frozenset("pPS")
# Passband the dummy rig chooses for "+M <mode> 0".
_DEFAULT_PASSBAND = {"CW": 500, "CWR": 500, "FM": 15000, "WFM": 230000, "AM": 8000}


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Run the Qt event loop until ``predicate()`` is true or ``timeout`` seconds passed."""
    deadline = time.monotonic() + timeout
    while True:
        QCoreApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.002)


def pump(seconds: float) -> None:
    """Run the Qt event loop for ``seconds``."""
    wait_until(lambda: False, seconds)


def drain_deleted() -> None:
    """Process pending events and ``deleteLater()`` deletions."""
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


class FakeHamlib(QObject):
    """A fake ``rigctld`` (``kind="rig"``) or ``rotctld`` (``kind="rot"``) on 127.0.0.1."""

    def __init__(self, kind: str = "rig") -> None:
        super().__init__()
        if kind not in ("rig", "rot"):
            raise ValueError(f"kind must be 'rig' or 'rot', not {kind!r}")
        self.kind = kind
        self.received: list[str] = []
        self.connections = 0  # connections accepted so far
        # radio / rotator state
        self.freq_hz = 14074000
        self.mode = "USB"
        self.passband = 2400
        self.mode_reported: str | None = None  # how the daemon spells the mode ("FM-D")
        self.azimuth = 123.0
        self.elevation = 0.0
        # misbehaviour switches
        self.mute = False
        self.mute_commands: set[str] = set()
        self.errors: dict[str, int] = {}
        self.replies: dict[str, str] = {}
        self.stray = ""
        self.chunk_size = 0
        self.chunk_gap_ms = 1
        self.delay_ms = 0
        self.close_on: dict[str, str] = {}
        # plumbing
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._clients: list[QTcpSocket] = []
        self._buffers: dict[QTcpSocket, bytes] = {}
        self._swallow: dict[QTcpSocket, str] = {}  # '+M <mode>' waiting for its passband
        self._outgoing: list[tuple[QTcpSocket, bytearray]] = []
        self._pump = QTimer(self)
        self._pump.timeout.connect(self._pump_once)
        self._port = 0

    # ------------------------------------------------------------------ lifecycle

    @property
    def port(self) -> int:
        """The listening port (kept across :meth:`stop` / :meth:`restart`)."""
        return self._port

    @property
    def client_count(self) -> int:
        """Connections currently open."""
        return len(self._clients)

    def start(self, port: int = 0) -> int:
        """Listen on 127.0.0.1:``port`` (0 = any free port); return the port."""
        if not self._server.listen(QHostAddress("127.0.0.1"), port):
            raise RuntimeError(f"cannot listen on port {port}: {self._server.errorString()}")
        self._port = self._server.serverPort()
        return self._port

    def stop(self) -> None:
        """Stop listening and drop every connection (connections are refused now)."""
        self._server.close()
        self.close_clients(abort=True)
        self._pump.stop()

    def restart(self) -> None:
        """:meth:`stop`, then listen on the same port again."""
        self.stop()
        self.start(self._port)

    def close_clients(self, abort: bool = False) -> None:
        """Close every connection (``abort``: reset instead of an orderly close)."""
        for socket in list(self._clients):
            self._drop(socket, abort=abort)

    # ------------------------------------------------------------------ connections

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self.connections += 1
            self._clients.append(socket)
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_client_gone(s))

    def _on_client_gone(self, socket: QTcpSocket) -> None:
        if socket in self._clients:
            self._drop(socket, abort=False)

    def _drop(self, socket: QTcpSocket, abort: bool) -> None:
        if socket in self._clients:
            self._clients.remove(socket)
        self._buffers.pop(socket, None)
        self._swallow.pop(socket, None)
        self._outgoing = [(s, data) for s, data in self._outgoing if s is not socket]
        try:
            socket.readyRead.disconnect()
            socket.disconnected.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            if abort:
                socket.abort()
            else:
                socket.disconnectFromHost()
            socket.deleteLater()
        except RuntimeError:
            pass

    def _on_ready_read(self, socket: QTcpSocket) -> None:
        if socket not in self._clients:
            return
        data = self._buffers.get(socket, b"") + bytes(socket.readAll())
        *lines, rest = data.split(b"\n")
        self._buffers[socket] = rest
        for raw in lines:
            if socket not in self._clients:  # closed by close_on
                return
            self._handle_line(socket, raw.decode("utf-8", "replace").rstrip("\r"))

    # ------------------------------------------------------------------ protocol

    def _handle_line(self, socket: QTcpSocket, line: str) -> None:
        self.received.append(line)
        pending_mode = self._swallow.pop(socket, None)
        if pending_mode is not None:  # this line is the passband of '+M <mode>'
            self._reply(socket, self._set_mode(f"{pending_mode} {line}"))
            return
        if self.mute or len(line) < 2 or not line.startswith("+"):
            return
        letter, args = line[1], line[2:].strip()
        own = _RIG_COMMANDS if self.kind == "rig" else _ROT_COMMANDS
        if letter not in own or letter in self.mute_commands:
            return
        if letter in self.close_on:
            self._write(socket, self.close_on[letter].encode("utf-8"))
            self._drop(socket, abort=False)
            return
        if letter == "M" and len(args.split()) == 1:  # no passband: wait for the next line
            self._swallow[socket] = args
            return
        reply = self.replies.get(letter)
        if reply is None:
            reply = self._answer(letter, args)
        if reply is not None:
            self._reply(socket, self.stray + reply)

    def _answer(self, letter: str, args: str) -> str | None:
        code = self.errors.get(letter)
        if letter == "f":
            if code:
                return f"get_freq:\nRPRT {code}\n"
            return f"get_freq:\nFrequency: {self.freq_hz}\nRPRT 0\n"
        if letter == "m":
            if code:
                return f"get_mode:\nRPRT {code}\n"
            mode = self.mode_reported or self.mode
            return f"get_mode:\nMode: {mode}\nPassband: {self.passband}\nRPRT 0\n"
        if letter == "F":
            if code:
                return f"set_freq: {args}\nRPRT {code}\n"
            try:
                self.freq_hz = int(float(args))
            except ValueError:
                return f"set_freq: {args}\nRPRT -1\n"
            return f"set_freq: {args}\nRPRT 0\n"
        if letter == "M":
            if code:
                return f"set_mode: {args}\nRPRT {code}\n"
            return self._set_mode(args)
        if letter == "t":
            return "get_ptt:\nRPRT -11\n"
        if letter == "p":
            if code:
                return f"get_pos:\nRPRT {code}\n"
            return (
                f"get_pos:\nAzimuth: {self.azimuth:.2f}\nElevation: {self.elevation:.2f}\nRPRT 0\n"
            )
        if letter == "P":
            if code:
                return f"set_pos: {args}\nRPRT {code}\n"
            try:
                azimuth, elevation = (float(part) for part in args.split())
            except ValueError:
                return f"set_pos: {args}\nRPRT -1\n"
            if not (-180.0 <= azimuth <= 450.0 and 0.0 <= elevation <= 90.0):
                return f"set_pos: {args}\nRPRT -21\n"
            self.azimuth, self.elevation = azimuth, elevation
            return f"set_pos: {args}\nRPRT 0\n"
        if letter == "S":
            if code:
                return f"stop:\nRPRT {code}\n"
            return "stop:\nRPRT 0\n"
        return None

    def _set_mode(self, args: str) -> str:
        parts = args.split()
        try:
            mode, width = parts[0], int(parts[1])
        except (IndexError, ValueError):
            return f"set_mode: {args}\nRPRT -1\n"
        if mode in MODES:  # rigctld 4.6 answers RPRT 0 to unknown names and ignores them
            self.mode = mode
            if width == 0:
                self.passband = _DEFAULT_PASSBAND.get(mode, 2400)
            elif width > 0:
                self.passband = width
        return f"set_mode: {args}\nRPRT 0\n"

    # ------------------------------------------------------------------ sending

    def _reply(self, socket: QTcpSocket, text: str) -> None:
        data = text.encode("utf-8")
        if self.delay_ms > 0:
            QTimer.singleShot(self.delay_ms, lambda: self._send(socket, data))
        else:
            self._send(socket, data)

    def _send(self, socket: QTcpSocket, data: bytes) -> None:
        if socket not in self._clients:
            return
        if self.chunk_size <= 0:
            self._write(socket, data)
            return
        for queued_socket, buffer in self._outgoing:
            if queued_socket is socket:
                buffer.extend(data)
                break
        else:
            self._outgoing.append((socket, bytearray(data)))
        if not self._pump.isActive():
            self._pump.start(self.chunk_gap_ms)

    def _pump_once(self) -> None:
        for socket, buffer in self._outgoing:
            piece = bytes(buffer[: self.chunk_size])
            del buffer[: self.chunk_size]
            self._write(socket, piece)
        self._outgoing = [(socket, buffer) for socket, buffer in self._outgoing if buffer]
        if not self._outgoing:
            self._pump.stop()

    @staticmethod
    def _write(socket: QTcpSocket, data: bytes) -> None:
        try:
            socket.write(data)
            socket.flush()
        except RuntimeError:  # deleted meanwhile
            pass


class ThreadedFakeHamlib:
    """A minimal ``rigctld`` / ``rotctld`` on 127.0.0.1 that answers from its own threads.

    :class:`FakeHamlib` shares the test's Qt event loop, so it cannot answer while the
    test blocks the Qt (GUI) thread. This one uses plain Python sockets and one thread
    per connection, so the replies arrive while the GUI thread is blocked, as with the
    real daemons. It answers ``+f``, ``+m``, ``+F``, ``+M <mode> <passband>`` (rig) or
    ``+p``, ``+P``, ``+S`` (rotator) in the formats of :class:`FakeHamlib`, and nothing
    else.
    """

    def __init__(self, kind: str = "rig") -> None:
        if kind not in ("rig", "rot"):
            raise ValueError(f"kind must be 'rig' or 'rot', not {kind!r}")
        self.kind = kind
        self.received: list[str] = []
        self.connections = 0
        self.freq_hz = 14074000
        self.mode = "USB"
        self.passband = 2400
        self.azimuth = 123.0
        self.elevation = 0.0
        self.port = 0
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._server: pysocket.socket | None = None
        self._clients: list[pysocket.socket] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> int:
        """Listen on a free 127.0.0.1 port; return the port."""
        server = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        server.settimeout(0.05)
        self._server = server
        self.port = server.getsockname()[1]
        self._spawn(self._accept, server)
        return self.port

    def stop(self) -> None:
        """Close the server and every connection; wait for the threads."""
        self._stopped.set()
        with self._lock:
            sockets = [s for s in (self._server, *self._clients) if s is not None]
            self._server, self._clients = None, []
        for sock in sockets:
            with suppress(OSError):
                sock.close()
        for thread in self._threads:
            thread.join(2.0)

    def _spawn(self, target: Callable[[pysocket.socket], None], sock: pysocket.socket) -> None:
        thread = threading.Thread(target=target, args=(sock,), daemon=True)
        self._threads.append(thread)
        thread.start()

    def _accept(self, server: pysocket.socket) -> None:
        while not self._stopped.is_set():
            try:
                client, _address = server.accept()
            except pysocket.timeout:
                continue
            except OSError:
                return
            client.settimeout(0.05)
            with self._lock:
                self.connections += 1
                self._clients.append(client)
            self._spawn(self._serve, client)

    def _serve(self, client: pysocket.socket) -> None:
        buffer = b""
        while not self._stopped.is_set():
            try:
                data = client.recv(4096)
            except pysocket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            *lines, buffer = (buffer + data).split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8", "replace").rstrip("\r")
                with self._lock:
                    self.received.append(line)
                    reply = self._answer(line)
                if reply is not None:
                    try:
                        client.sendall(reply.encode("utf-8"))
                    except OSError:
                        return

    def _answer(self, line: str) -> str | None:
        if len(line) < 2 or not line.startswith("+"):
            return None
        letter, args = line[1], line[2:].strip()
        if self.kind == "rig":
            if letter == "f":
                return f"get_freq:\nFrequency: {self.freq_hz}\nRPRT 0\n"
            if letter == "m":
                return f"get_mode:\nMode: {self.mode}\nPassband: {self.passband}\nRPRT 0\n"
            if letter == "F":
                self.freq_hz = int(float(args))
                return f"set_freq: {args}\nRPRT 0\n"
            if letter == "M":
                mode, width = args.split()
                self.mode = mode
                if int(width) > 0:
                    self.passband = int(width)
                return f"set_mode: {args}\nRPRT 0\n"
            return None
        if letter == "p":
            return (
                f"get_pos:\nAzimuth: {self.azimuth:.2f}\nElevation: {self.elevation:.2f}\nRPRT 0\n"
            )
        if letter == "P":
            self.azimuth, self.elevation = (float(part) for part in args.split())
            return f"set_pos: {args}\nRPRT 0\n"
        if letter == "S":
            return "stop:\nRPRT 0\n"
        return None
