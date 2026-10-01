"""hamq.net.hamlib_client: RigClient / RotatorClient against a fake rigctld / rotctld.

The fake (tests/qgis/fake_hamlib.py) runs in the same Qt event loop and replays the
exact reply formats of Hamlib 4.6.2. Short timers keep the tests fast: 50 ms polls,
300 ms command timeout, 150 ms between reconnection attempts.

Optional real-daemon tests run when HAMQ_RIGCTLD / HAMQ_ROTCTLD are set to
``host:port`` (e.g. the ``hamq/hamlib-dummy`` image: ``rigctld -m 1`` on 4532,
``rotctld -m 1`` on 4533). They change the frequency, mode and rotator position:
use dummy devices.
"""

from __future__ import annotations

import os
import time

import pytest
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEventLoop, QObject, QTimer
from qgis.PyQt.QtNetwork import QTcpSocket

from hamq.core.hamlib import ResponseParser, error_message
from hamq.core.i18n import current_language, set_language
from hamq.net import hamlib_client
from hamq.net.hamlib_client import HamlibClient, RigClient, RotatorClient
from hamq.qgis_io.compat import MSG_CRITICAL, MSG_INFO, MSG_WARNING, NET_PROXY_NONE

from .fake_hamlib import FakeHamlib, drain_deleted, pump, wait_until

HOST = "127.0.0.1"
FAST = {"timeout_ms": 300, "reconnect_ms": 150}
FULL_STATE = {"freq_hz": 14074000, "mode": "USB", "passband": 2400}
UNKNOWN_STATE = {"freq_hz": None, "mode": None, "passband": None}


class Recorder:
    """Arguments of every emission of a signal, in order."""

    def __init__(self, signal) -> None:
        self.calls: list[tuple] = []
        signal.connect(self._record)

    def _record(self, *args) -> None:
        self.calls.append(args)

    @property
    def values(self) -> list:
        return [call[0] if len(call) == 1 else call for call in self.calls]


class RecordingParser(ResponseParser):
    """ResponseParser that keeps every chunk it was fed."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[object] = []

    def feed(self, data):
        self.chunks.append(data)
        return super().feed(data)


class FakeClock:
    """Replaces ``time.monotonic`` of a client: the rate limits do not depend on timing."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def english():
    previous = current_language()
    set_language("en")
    yield
    set_language(previous)


@pytest.fixture
def rig_daemon(qgis_app):
    fake = FakeHamlib("rig")
    fake.start()
    yield fake
    fake.stop()
    drain_deleted()


@pytest.fixture
def rot_daemon(qgis_app):
    fake = FakeHamlib("rot")
    fake.start()
    yield fake
    fake.stop()
    drain_deleted()


@pytest.fixture
def make_client(qgis_app):
    """Factory for clients with fast timers; every client is stopped afterwards."""
    clients: list[HamlibClient] = []

    def factory(cls=RigClient, poll_ms=50, **kwargs):
        options = dict(FAST)
        options.update(kwargs)
        client = cls(poll_ms=poll_ms, **options)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.stop()
    drain_deleted()


def timers_active(client: HamlibClient) -> list[str]:
    names = ("_poll_timer", "_watchdog", "_reconnect_timer")
    return [name for name in names if getattr(client, name).isActive()]


def start_connected(client: HamlibClient, fake: FakeHamlib) -> None:
    client.start(HOST, fake.port)
    assert wait_until(client.is_connected), "the client did not connect to the fake daemon"


# --------------------------------------------------------------------------- rig basics


def test_rig_connects_and_reports_state(rig_daemon, make_client):
    client = make_client()
    connected = Recorder(client.connectedChanged)
    states = Recorder(client.stateChanged)
    errors = Recorder(client.errorOccurred)
    assert not client.is_running() and not client.is_connected()
    assert client.state() == UNKNOWN_STATE
    assert client.address() == ""

    client.start(HOST, rig_daemon.port)
    assert client.is_running() and not client.is_connected()
    assert wait_until(lambda: client.state() == FULL_STATE)
    assert client.is_connected()
    assert connected.values == [True]
    assert states.values[-1] == FULL_STATE
    assert errors.values == []
    assert rig_daemon.received[:2] == ["+f", "+m"]
    assert client.address() == f"{HOST}:{rig_daemon.port}"
    assert client._socket.proxy().type() == NET_PROXY_NONE


def test_state_is_emitted_only_on_change(rig_daemon, make_client):
    client = make_client()
    states = Recorder(client.stateChanged)
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)
    states.calls.clear()
    polls = len(rig_daemon.received)
    pump(0.4)
    assert len(rig_daemon.received) >= polls + 6, "polling stopped"
    assert states.values == []

    rig_daemon.freq_hz = 7074000
    assert wait_until(lambda: states.values != [])
    pump(0.2)
    assert states.values == [{"freq_hz": 7074000, "mode": "USB", "passband": 2400}]
    # the emitted dict is a copy: changing it does not change the client
    states.values[0]["freq_hz"] = 1
    assert client.state()["freq_hz"] == 7074000


def test_daemon_spelling_and_frequency_above_32_bits(rig_daemon, make_client):
    rig_daemon.mode_reported = "FM-D"  # Hamlib 4.6 reports PKTFM like this
    rig_daemon.freq_hz = 10_368_100_000  # 3 cm band, above 2**31 Hz
    client = make_client()
    states = Recorder(client.stateChanged)
    client.start(HOST, rig_daemon.port)
    expected = {"freq_hz": 10_368_100_000, "mode": "PKTFM", "passband": 2400}
    assert wait_until(lambda: client.state() == expected)
    assert states.values[-1] == expected


def test_set_frequency_is_followed_by_an_immediate_poll(rig_daemon, make_client):
    client = make_client(poll_ms=60_000)  # no timer polls: every poll is a refresh
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)
    rig_daemon.received.clear()

    client.set_frequency(7074000)
    assert wait_until(lambda: client.state()["freq_hz"] == 7074000)
    pump(0.05)
    assert rig_daemon.received == ["+F 7074000", "+f", "+m"]

    rig_daemon.received.clear()
    client.set_frequency(3573000.4)  # rounded to Hz by the core builder
    assert wait_until(lambda: client.state()["freq_hz"] == 3573000)
    assert rig_daemon.received[0] == "+F 3573000"


def test_set_mode_always_sends_the_passband(rig_daemon, make_client):
    client = make_client(poll_ms=60_000)
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)
    rig_daemon.received.clear()

    client.set_mode("LSB")
    assert wait_until(lambda: client.state()["mode"] == "LSB")
    client.set_mode("CW", 500)
    assert wait_until(
        lambda: client.state() == {"freq_hz": 14074000, "mode": "CW", "passband": 500}
    )
    client.set_mode("PKTUSB", -1)  # keep the passband
    assert wait_until(lambda: client.state()["mode"] == "PKTUSB")
    pump(0.05)
    sent = [line for line in rig_daemon.received if line.startswith("+M")]
    assert sent == ["+M LSB 0", "+M CW 500", "+M PKTUSB -1"]
    assert rig_daemon.received[:3] == ["+M LSB 0", "+m", "+f"]
    # the daemon never swallowed a line (that would have timed out and reconnected)
    assert connected.values == [True]
    assert errors.values == []
    assert client.state() == {"freq_hz": 14074000, "mode": "PKTUSB", "passband": 500}


def test_set_commands_go_ahead_of_queued_polls(rig_daemon, make_client):
    rig_daemon.delay_ms = 100
    client = make_client(poll_ms=20)
    start_connected(client, rig_daemon)

    def poll_waiting() -> bool:  # +f in flight, +m waiting in the queue
        in_flight = client._in_flight
        waiting = [command.header for command in client._polls]
        return in_flight is not None and in_flight.header == "get_freq" and waiting == ["get_mode"]

    assert wait_until(poll_waiting)
    client.set_frequency(7074000)
    received = rig_daemon.received
    assert wait_until(lambda: "+F 7074000" in received[:-2])
    index = received.index("+F 7074000")
    assert received[index - 1 : index + 3] == ["+f", "+F 7074000", "+f", "+m"]


def test_a_slow_daemon_never_piles_up_polls(rig_daemon, make_client):
    rig_daemon.delay_ms = 120
    client = make_client(poll_ms=10, timeout_ms=1000)
    started = time.monotonic()
    client.start(HOST, rig_daemon.port)
    for _ in range(20):
        pump(0.05)
        assert len(client._polls) + (client._in_flight is not None) <= 2
    elapsed = time.monotonic() - started
    lines = list(rig_daemon.received)
    # one command in flight at a time: at most one command per 120 ms
    assert 4 <= len(lines) <= elapsed / 0.12 + 1, (elapsed, lines)
    assert all(a != b for a, b in zip(lines, lines[1:])), lines  # +f and +m alternate
    assert client.is_connected()


# --------------------------------------------------------------------------- robustness


def test_no_reply_times_out_and_reconnects(rig_daemon, make_client):
    client = make_client()
    connected = Recorder(client.connectedChanged)
    states = Recorder(client.stateChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)

    rig_daemon.mute = True
    assert wait_until(lambda: connected.values == [True, False], timeout=2.0)
    assert not client.is_connected() and client.is_running()
    assert client.state() == UNKNOWN_STATE
    assert states.values[-1] == UNKNOWN_STATE
    assert errors.values == [
        f"rigctld at {HOST}:{rig_daemon.port} did not reply within 0.3 s; reconnecting"
    ]
    assert wait_until(lambda: rig_daemon.connections >= 2, timeout=2.0)
    assert wait_until(lambda: rig_daemon.client_count == 1)

    rig_daemon.mute = False
    assert wait_until(lambda: client.state() == FULL_STATE, timeout=3.0)
    assert connected.values == [True, False, True]
    assert rig_daemon.client_count == 1  # the old connections were closed


def test_stray_replies_are_ignored(rig_daemon, make_client):
    # what rigctld prints after a command it misunderstood (docs/ARCHITECTURE.md)
    rig_daemon.stray = "set_split_mode: bogus +F\nRPRT -1\nRPRT -18\nRPRT -1\n"
    client = make_client()
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: client.state() == FULL_STATE)
    client.set_frequency(7074000)
    assert wait_until(lambda: client.state()["freq_hz"] == 7074000)
    pump(0.2)
    assert connected.values == [True]
    assert errors.values == []


def test_header_without_rprt_times_out_and_reconnects(rig_daemon, make_client):
    # rigctld prints the header and no RPRT when the rig reports an I/O error
    client = make_client()
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    rig_daemon.replies = {"f": "get_freq:\n"}
    assert wait_until(lambda: connected.values == [True, False], timeout=2.0)
    assert errors.values == [
        f"rigctld at {HOST}:{rig_daemon.port} did not reply within 0.3 s; reconnecting"
    ]
    assert client.state() == UNKNOWN_STATE
    rig_daemon.replies = {}
    assert wait_until(lambda: client.state() == FULL_STATE, timeout=3.0)
    assert connected.values == [True, False, True]


def test_crlf_and_decimal_replies(rig_daemon, make_client):
    rig_daemon.replies = {
        "f": "get_freq:\r\nFrequency: 7074000.000000\r\nRPRT 0\r\n",
        "m": "\r\nget_mode:\r\nMode: PKTUSB\r\nPassband: 3000\r\nRPRT 0\r\n",
    }
    client = make_client()
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rig_daemon.port)
    expected = {"freq_hz": 7074000, "mode": "PKTUSB", "passband": 3000}
    assert wait_until(lambda: client.state() == expected)
    assert errors.values == []


def test_replies_split_across_packets(rig_daemon, make_client):
    rig_daemon.chunk_size = 3
    client = make_client(timeout_ms=2000)
    parser = RecordingParser()
    client._parser = parser
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: client.state() == FULL_STATE)
    client.set_mode("AM", 6000)
    assert wait_until(lambda: client.state()["mode"] == "AM")
    assert client.state() == {"freq_hz": 14074000, "mode": "AM", "passband": 6000}
    assert all(type(chunk) is bytes for chunk in parser.chunks)  # never a QByteArray
    assert len(parser.chunks) >= 10
    assert min(len(chunk) for chunk in parser.chunks) <= 3


def test_remote_close_is_handled_like_a_timeout(rig_daemon, make_client):
    client = make_client()
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)

    # rigctld after a hard rig error it cannot recover from: header, then close
    rig_daemon.close_on = {"f": "get_freq:\n"}
    assert wait_until(lambda: connected.values == [True, False])
    assert errors.values == [
        f"rigctld at {HOST}:{rig_daemon.port} closed the connection; reconnecting"
    ]
    assert client.state() == UNKNOWN_STATE

    rig_daemon.close_on = {}
    assert wait_until(lambda: client.state() == FULL_STATE)
    assert connected.values == [True, False, True]
    assert rig_daemon.connections == 2


def test_lost_set_command_is_reported(rig_daemon, make_client):
    client = make_client(poll_ms=60_000)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    rig_daemon.close_on = {"F": ""}  # the daemon dies while setting the frequency
    client.set_frequency(7074000)
    assert wait_until(lambda: len(errors.values) == 2)
    assert errors.values == [
        f"rigctld at {HOST}:{rig_daemon.port} closed the connection; reconnecting",
        "Setting the frequency failed: the connection to rigctld was lost",
    ]


def test_daemon_restart_and_rate_limited_connection_errors(rig_daemon, make_client):
    client = make_client()
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    port = rig_daemon.port

    rig_daemon.stop()  # daemon gone: connections refused from now on
    assert wait_until(lambda: connected.values == [True, False])
    pump(0.8)  # several reconnection attempts
    refused = [e for e in errors.values if e.startswith("Could not connect")]
    assert refused == [f"Could not connect to rigctld at {HOST}:{port}: Connection refused"]
    assert len(errors.values) == 2  # the lost connection, then one "refused"
    assert client.is_running() and not client.is_connected()

    rig_daemon.start(port)
    assert wait_until(lambda: client.is_connected(), timeout=3.0)
    assert wait_until(lambda: client.state() == FULL_STATE)

    # the same outage again within a minute: status changes, no repeated messages
    before = list(errors.values)
    rig_daemon.restart()
    assert wait_until(lambda: connected.values == [True, False, True, False, True], timeout=3.0)
    assert errors.values == before


def test_recurring_problems_are_reported_again_after_the_interval(rig_daemon, make_client):
    clock = FakeClock()
    client = make_client()
    client._clock = clock
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    rig_daemon.close_clients()
    assert wait_until(lambda: len(errors.values) == 1)
    assert wait_until(client.is_connected)

    clock.now += 59  # again within a minute: not reported
    rig_daemon.close_clients()
    assert wait_until(lambda: connected.values == [True, False, True, False, True])
    assert len(errors.values) == 1

    clock.now += 2  # a minute after the report: reported again
    rig_daemon.close_clients()
    assert wait_until(lambda: len(errors.values) == 2)
    assert errors.values[0] == errors.values[1]
    assert errors.values[0] == (
        f"rigctld at {HOST}:{rig_daemon.port} closed the connection; reconnecting"
    )


def test_poll_errors_are_rate_limited(rig_daemon, make_client):
    rig_daemon.errors = {"m": -11}
    client = make_client()
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: client.state()["freq_hz"] == 14074000)
    pump(0.5)  # about ten failed +m polls
    assert errors.values == ["Reading the mode failed: Feature not available"]
    assert errors.values[0].endswith(error_message(-11))
    assert client.state() == {"freq_hz": 14074000, "mode": None, "passband": None}
    assert client.is_connected()  # an error reply is still an answer

    rig_daemon.errors = {}
    assert wait_until(lambda: client.state() == FULL_STATE)
    rig_daemon.errors = {"m": -11}  # back within a minute: not reported again
    assert wait_until(lambda: client.state()["mode"] is None)
    pump(0.2)
    assert len(errors.values) == 1


def test_a_problem_that_returns_and_persists_is_reported_after_the_interval(
    rig_daemon, make_client
):
    rig_daemon.errors = {"m": -11}
    clock = FakeClock()
    client = make_client(error_interval_ms=60_000)
    client._clock = clock
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: len(errors.values) == 1)
    rig_daemon.errors = {}
    assert wait_until(lambda: client.state()["mode"] == "USB")  # the problem ended
    clock.now += 30
    rig_daemon.errors = {"m": -11}  # back within the minute, then it stays
    assert wait_until(lambda: client.state()["mode"] is None)
    pump(0.2)
    assert len(errors.values) == 1  # too soon after the first report
    clock.now += 31  # the minute has passed and it still fails: reported once more
    assert wait_until(lambda: len(errors.values) == 2)
    pump(0.3)  # still failing: not again
    assert errors.values == ["Reading the mode failed: Feature not available"] * 2


def test_command_queued_while_connecting_is_reported_when_connecting_fails(rig_daemon, make_client):
    port = rig_daemon.port
    rig_daemon.stop()
    client = make_client()
    errors = Recorder(client.errorOccurred)
    client.start(HOST, port)
    client.set_frequency(7074000)  # accepted while connecting
    assert wait_until(lambda: len(errors.values) == 2)
    assert errors.values == [
        f"Could not connect to rigctld at {HOST}:{port}: Connection refused",
        "Setting the frequency failed: not connected to rigctld",
    ]


def test_set_command_errors_are_always_reported(rig_daemon, make_client):
    rig_daemon.errors = {"F": -1}
    client = make_client()
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    client.set_frequency(7074000)
    assert wait_until(lambda: len(errors.values) == 1)
    client.set_frequency(7074000)
    assert wait_until(lambda: len(errors.values) == 2)
    assert errors.values == ["Setting the frequency failed: Invalid parameter"] * 2
    assert client.is_connected()


def test_rig_client_on_the_rotator_port_never_connects(rot_daemon, make_client):
    # rotctld swallows '+f' without a reply: the client times out and retries
    client = make_client()
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rot_daemon.port)
    assert wait_until(lambda: rot_daemon.connections >= 3, timeout=3.0)
    assert connected.values == []
    assert errors.values == [
        f"rigctld at {HOST}:{rot_daemon.port} did not reply within 0.3 s; reconnecting"
    ]


def test_connect_timeout_or_error_on_an_unreachable_address(make_client):
    client = make_client()
    errors = Recorder(client.errorOccurred)
    client.start("192.0.2.1", 4532)  # TEST-NET-1: never answers
    assert wait_until(lambda: errors.values != [], timeout=3.0)
    assert errors.values[0].startswith("Could not connect to rigctld at 192.0.2.1:4532: ")
    assert client.is_running() and not client.is_connected()
    client.stop()
    assert timers_active(client) == []


# --------------------------------------------------------------------------- lifecycle


def test_stop_start_cycles_release_everything(rig_daemon, make_client, process_events):
    client = make_client()
    connected = Recorder(client.connectedChanged)
    states = Recorder(client.stateChanged)
    for cycle in range(3):
        client.start(HOST, rig_daemon.port)
        assert wait_until(lambda: client.state() == FULL_STATE)
        client.stop()
        assert connected.values[-2:] == [True, False]
        assert states.values[-1] == UNKNOWN_STATE
        assert client.state() == UNKNOWN_STATE
        assert not client.is_running() and not client.is_connected()
        assert timers_active(client) == []
        client.stop()  # a second stop does nothing
        assert len(connected.values) == 2 * (cycle + 1)
        assert wait_until(lambda: rig_daemon.client_count == 0)
        process_events()
        assert client.findChildren(QTcpSocket) == []
        received, connections = len(rig_daemon.received), rig_daemon.connections
        pump(0.3)  # nothing is sent and nothing reconnects after stop()
        assert (len(rig_daemon.received), rig_daemon.connections) == (received, connections)
    assert rig_daemon.connections == 3


def test_stop_while_waiting_to_reconnect(rig_daemon, make_client):
    client = make_client(reconnect_ms=100_000)
    start_connected(client, rig_daemon)
    rig_daemon.stop()
    assert wait_until(lambda: not client.is_connected())
    assert timers_active(client) == ["_reconnect_timer"]
    client.stop()
    assert timers_active(client) == []
    assert not client.is_running()


def test_nested_event_loop_in_a_slot_does_not_delete_the_emitting_socket(
    rig_daemon, make_client, process_events
):
    # A GUI slot may open a modal dialog (a nested event loop) while the client is
    # inside a socket signal. A timeout in that loop drops the socket; it must not
    # be deleted before the socket's own signal emission has returned.
    client = make_client(poll_ms=20, timeout_ms=100)
    seen: dict[str, object] = {}

    def modal_dialog(state):
        if seen or state["freq_hz"] is None:
            return
        seen["socket"] = client._socket
        rig_daemon.mute = True
        loop = QEventLoop()
        QTimer.singleShot(400, loop.quit)
        loop.exec()  # the reply timeout and the reconnection happen in here
        seen["deleted"] = sip.isdeleted(seen["socket"])
        seen["dropped"] = client._socket is not seen["socket"]

    client.stateChanged.connect(modal_dialog)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: "deleted" in seen, timeout=3.0)
    assert seen["dropped"], "the link was not dropped inside the nested loop"
    assert seen["deleted"] is False
    rig_daemon.mute = False
    assert wait_until(client.is_connected, timeout=3.0)
    client.stop()
    process_events()
    assert sip.isdeleted(seen["socket"])  # deleted once the emission returned
    assert client.findChildren(QTcpSocket) == []


def test_replies_arriving_during_a_nested_event_loop_are_read(rig_daemon, make_client):
    # Qt does not emit readyRead again while a readyRead slot runs: replies that arrive
    # while a receiver of our signals runs a nested event loop (a modal dialog) are only
    # buffered. They must still be read, or every command would time out.
    client = make_client(poll_ms=50, timeout_ms=300)
    connected = Recorder(client.connectedChanged)
    errors = Recorder(client.errorOccurred)
    seen: dict[str, object] = {}

    def modal_dialog(state):
        if seen or state["freq_hz"] is None:
            return
        seen["before"] = len(rig_daemon.received)
        rig_daemon.freq_hz = 7074000
        loop = QEventLoop()
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
        seen["after"] = len(rig_daemon.received)
        seen["state"] = client.state()

    client.stateChanged.connect(modal_dialog)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: "after" in seen, timeout=4.0)
    assert errors.values == []
    assert connected.values == [True]
    assert seen["after"] - seen["before"] >= 8  # polling went on inside the loop ...
    assert seen["state"]["freq_hz"] == 7074000  # ... and its replies were read
    pump(0.2)
    assert client.is_connected() and rig_daemon.connections == 1


def test_stop_inside_a_signal_slot(rig_daemon, make_client, process_events):
    # e.g. the user disables the radio in a dialog opened from a stateChanged slot
    client = make_client()
    connected = Recorder(client.connectedChanged)
    states = Recorder(client.stateChanged)

    def stop_on_first_state(state):
        if state["freq_hz"] is not None:
            client.stop()

    client.stateChanged.connect(stop_on_first_state)
    client.start(HOST, rig_daemon.port)
    assert wait_until(lambda: not client.is_running(), timeout=3.0)
    assert connected.values == [True, False]
    assert states.values == [{"freq_hz": 14074000, "mode": None, "passband": None}, UNKNOWN_STATE]
    assert timers_active(client) == []
    process_events()
    assert client.findChildren(QTcpSocket) == []
    assert wait_until(lambda: rig_daemon.client_count == 0)
    pump(0.2)
    assert rig_daemon.received == ["+f"]  # nothing was sent after stop()


def test_restart_in_a_disconnect_slot_still_reports_the_unknown_state(rig_daemon, make_client):
    client = make_client(reconnect_ms=100_000)  # only the slot reconnects
    connected = Recorder(client.connectedChanged)
    states = Recorder(client.stateChanged)

    def reconnect_at_once(is_connected):
        if not is_connected:
            client.start(HOST, rig_daemon.port)  # same address while waiting: retry now

    client.connectedChanged.connect(reconnect_at_once)
    start_connected(client, rig_daemon)
    assert wait_until(lambda: client.state() == FULL_STATE)
    states.calls.clear()
    rig_daemon.close_clients()
    assert wait_until(lambda: connected.values == [True, False, True], timeout=3.0)
    assert states.values[0] == UNKNOWN_STATE  # not skipped because the slot restarted
    assert wait_until(lambda: client.state() == FULL_STATE)
    assert rig_daemon.connections == 2


def test_stop_after_the_parent_deleted_the_client(rig_daemon, log_messages, process_events):
    parent = QObject()
    client = RigClient(50, parent, **FAST)
    start_connected(client, rig_daemon)
    parent.deleteLater()
    process_events()
    assert sip.isdeleted(client)
    assert wait_until(lambda: rig_daemon.client_count == 0)  # the socket went with it
    client.stop()
    client.stop()
    assert not client.is_running() and not client.is_connected()
    assert [m for m, tag, _ in log_messages if tag == "HamQ" and "Unexpected" in m] == []


def test_start_again_keeps_or_replaces_the_connection(rig_daemon, make_client):
    other = FakeHamlib("rig")
    other.freq_hz = 7074000
    other.start()
    try:
        client = make_client()
        connected = Recorder(client.connectedChanged)
        start_connected(client, rig_daemon)
        client.start(f" {HOST} ", rig_daemon.port)  # same address: nothing happens
        pump(0.1)
        assert rig_daemon.connections == 1 and connected.values == [True]

        client.start(HOST, other.port)  # another address: restart there
        assert wait_until(lambda: client.state()["freq_hz"] == 7074000)
        assert connected.values == [True, False, True]
        assert wait_until(lambda: rig_daemon.client_count == 0)
        assert other.connections == 1
    finally:
        other.stop()


def test_start_again_while_waiting_retries_at_once(rig_daemon, make_client):
    client = make_client(reconnect_ms=100_000)
    port = rig_daemon.port
    rig_daemon.stop()
    client.start(HOST, port)
    assert wait_until(lambda: timers_active(client) == ["_reconnect_timer"])
    rig_daemon.start(port)
    client.start(HOST, port)
    assert wait_until(client.is_connected)


def test_commands_are_refused_when_not_connected(rig_daemon, make_client):
    client = make_client(reconnect_ms=100_000)
    errors = Recorder(client.errorOccurred)
    client.set_frequency(7074000)
    client.set_mode("USB")
    assert errors.values == [
        "Setting the frequency failed: not connected to rigctld",
        "Setting the mode failed: not connected to rigctld",
    ]
    start_connected(client, rig_daemon)
    rig_daemon.stop()
    assert wait_until(lambda: not client.is_connected())
    errors.calls.clear()
    client.set_frequency(7074000)  # waiting to reconnect: refused as well
    assert errors.values == ["Setting the frequency failed: not connected to rigctld"]


def test_commands_wait_while_connecting(rig_daemon, make_client):
    client = make_client(poll_ms=60_000)
    client.start(HOST, rig_daemon.port)
    client.set_frequency(7074000)  # still connecting: queued, sent first
    assert wait_until(lambda: client.state()["freq_hz"] == 7074000)
    assert rig_daemon.received[0] == "+F 7074000"


def test_poll_interval_can_change(rig_daemon, make_client):
    client = make_client(poll_ms=60_000)
    assert client.poll_interval() == 60_000
    start_connected(client, rig_daemon)
    pump(0.2)
    before = len(rig_daemon.received)
    assert before == 2  # only the first poll
    client.set_poll_interval(20)
    assert client.poll_interval() == 20
    assert wait_until(lambda: len(rig_daemon.received) >= before + 6)


# --------------------------------------------------------------------------- API checks


def test_arguments_are_checked(make_client):
    with pytest.raises(ValueError):
        RigClient(poll_ms=0)
    with pytest.raises(TypeError):
        RigClient(poll_ms=True)
    with pytest.raises(TypeError):
        RigClient(QObject())  # the parent goes second (or by keyword)
    with pytest.raises(ValueError):
        RotatorClient(poll_ms=1000, timeout_ms=-1)
    client = make_client()
    errors = Recorder(client.errorOccurred)
    with pytest.raises(ValueError):
        client.start("  ", 4532)
    with pytest.raises(ValueError):
        client.start(HOST, 0)
    with pytest.raises(ValueError):
        client.start(HOST, 65536)
    with pytest.raises(TypeError):
        client.start(HOST, "4532")
    with pytest.raises(TypeError):
        client.start(None, 4532)
    with pytest.raises(ValueError):
        client.set_frequency(0)
    with pytest.raises(TypeError):
        client.set_frequency("14074000")
    with pytest.raises(ValueError):
        client.set_mode("usb")  # rigctld would answer RPRT 0 and ignore it
    with pytest.raises(ValueError):
        client.set_mode("USB", -2)
    with pytest.raises(ValueError):
        client.set_poll_interval(0)
    rotator = make_client(RotatorClient)
    with pytest.raises(ValueError):
        rotator.set_position(float("nan"))
    with pytest.raises(TypeError):
        rotator.set_position("90")
    assert errors.values == [] and not client.is_running()


def test_parent_owns_the_client(qgis_app, process_events):
    parent = QObject()
    client = RigClient(250, parent)
    assert client.parent() is parent and client.poll_interval() == 250
    keyword = RotatorClient(parent=parent)
    assert keyword.poll_interval() == 1000
    assert (keyword.address(), keyword.position()) == ("", None)
    parent.deleteLater()
    process_events()


def test_exceptions_in_slots_are_logged(rig_daemon, make_client, log_messages):
    class Broken(RigClient):
        def _handle_response(self, command, reply):
            raise RuntimeError("boom in a slot")

    client = make_client(Broken)
    client.start(HOST, rig_daemon.port)
    assert wait_until(
        lambda: any("boom in a slot" in message for message, _, _ in log_messages), timeout=3.0
    )
    critical = [m for m, tag, level in log_messages if "boom" in m]
    assert all(
        tag == "HamQ" and level == MSG_CRITICAL for m, tag, level in log_messages if "boom" in m
    )
    assert critical[0].startswith("Unexpected error in the Hamlib client: ")
    pump(0.2)  # the client keeps polling after the failed slot
    assert len(rig_daemon.received) > 3
    client.stop()
    assert timers_active(client) == []


def test_errors_and_connection_are_logged(rig_daemon, make_client, log_messages):
    rig_daemon.errors = {"F": -11}
    client = make_client()
    start_connected(client, rig_daemon)
    client.set_frequency(7074000)
    assert wait_until(lambda: any("Setting the frequency" in m for m, _, _ in log_messages))
    ours = [(m, level) for m, tag, level in log_messages if tag == "HamQ"]
    assert (f"Connected to rigctld at {HOST}:{rig_daemon.port}", MSG_INFO) in ours
    assert ("Setting the frequency failed: Feature not available", MSG_WARNING) in ours


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("sr_Latn", "Podešavanje frekvencije nije uspelo: HamQ nije povezan sa servisom rigctld"),
        ("sr_Cyrl", "Подешавање фреквенције није успело: HamQ није повезан са сервисом rigctld"),
    ],
)
def test_messages_follow_the_language(make_client, language, expected):
    client = make_client()
    errors = Recorder(client.errorOccurred)
    set_language(language)
    client.set_frequency(7074000)
    assert errors.values == [expected]


def test_error_messages_of_hamlib_codes_are_translated(rig_daemon, make_client):
    rig_daemon.errors = {"M": -9}
    client = make_client()
    errors = Recorder(client.errorOccurred)
    start_connected(client, rig_daemon)
    set_language("sr_Latn")
    client.set_mode("USB")
    assert wait_until(lambda: errors.values != [])
    assert errors.values == ["Podešavanje vrste rada nije uspelo: Uređaj je odbio komandu"]


def test_module_exports():
    assert set(hamlib_client.__all__) >= {"HamlibClient", "RigClient", "RotatorClient"}
    assert hamlib_client.DEFAULT_TIMEOUT_MS == 2000
    assert hamlib_client.DEFAULT_RECONNECT_MS == 5000
    assert hamlib_client.DEFAULT_POLL_MS == 1000


# --------------------------------------------------------------------------- rotator


def test_rotator_reports_position_changes(rot_daemon, make_client):
    client = make_client(RotatorClient)
    positions = Recorder(client.positionChanged)
    connected = Recorder(client.connectedChanged)
    client.start(HOST, rot_daemon.port)
    assert wait_until(lambda: client.position() == (123.0, 0.0))
    assert positions.values == [(123.0, 0.0)]
    pump(0.3)
    assert positions.values == [(123.0, 0.0)]  # unchanged: not emitted again

    rot_daemon.azimuth, rot_daemon.elevation = 200.5, 12.25
    assert wait_until(lambda: len(positions.values) == 2)
    assert positions.values[-1] == (200.5, 12.25)
    assert all(isinstance(v, float) for v in positions.values[-1])

    client.stop()
    assert client.position() is None
    assert connected.values == [True, False]


def test_rotator_set_position_and_stop(rot_daemon, make_client):
    client = make_client(RotatorClient, poll_ms=60_000)
    positions = Recorder(client.positionChanged)
    start_connected(client, rot_daemon)
    rot_daemon.received.clear()

    client.set_position(123.456, 10)
    assert wait_until(lambda: positions.values[-1:] == [(123.46, 10.0)])
    client.stop_rotation()
    assert wait_until(lambda: len(rot_daemon.received) >= 4)
    assert rot_daemon.received == ["+P 123.46 10.0", "+p", "+S", "+p"]


def test_stop_rotation_cancels_a_waiting_turn_and_goes_first(rot_daemon, make_client):
    rot_daemon.delay_ms = 100
    client = make_client(RotatorClient, poll_ms=60_000)
    start_connected(client, rot_daemon)
    rot_daemon.received.clear()

    client.set_position(10)  # in flight
    client.set_position(20)  # waiting ...
    client.set_position(30)  # ... replaced by the newest target
    assert [c.text for c in client._commands] == ["+P 30.0 0.0\n"]
    client.stop_rotation()  # cancels the waiting turn
    assert [c.text for c in client._commands] == ["+S\n"]
    assert wait_until(lambda: "+S" in rot_daemon.received)
    pump(0.3)
    assert rot_daemon.received[:3] == ["+P 10.0 0.0", "+S", "+p"]
    assert "+P 20.0 0.0" not in rot_daemon.received
    assert "+P 30.0 0.0" not in rot_daemon.received


def test_rotator_limit_error(rot_daemon, make_client):
    client = make_client(RotatorClient)
    errors = Recorder(client.errorOccurred)
    start_connected(client, rot_daemon)
    client.set_position(500)
    assert wait_until(lambda: errors.values != [])
    assert errors.values == ["Turning the rotator failed: Limit exceeded"]
    client.stop_rotation()
    client.stop()
    client.stop_rotation()
    assert errors.values[-1] == "Stopping the rotator failed: not connected to rotctld"


def test_rotator_poll_error_keeps_polling(rot_daemon, make_client):
    rot_daemon.errors = {"p": -5}
    client = make_client(RotatorClient)
    positions = Recorder(client.positionChanged)
    errors = Recorder(client.errorOccurred)
    client.start(HOST, rot_daemon.port)
    assert wait_until(lambda: errors.values != [])
    assert errors.values == ["Reading the rotator position failed: Communication timed out"]
    assert client.position() is None and client.is_connected()
    rot_daemon.errors = {}
    assert wait_until(lambda: positions.values == [(123.0, 0.0)])


# --------------------------------------------------------------------------- real daemons


def _address(variable: str) -> tuple[str, int] | None:
    value = os.environ.get(variable, "").strip()
    if not value:
        return None
    host, _, port = value.rpartition(":")
    return host.strip("[]"), int(port)


REAL_RIG = _address("HAMQ_RIGCTLD")
REAL_ROT = _address("HAMQ_ROTCTLD")


@pytest.mark.skipif(REAL_RIG is None, reason="set HAMQ_RIGCTLD=host:port (e.g. rigctld -m 1)")
def test_real_rigctld(qgis_app):
    client = RigClient(poll_ms=200)
    errors = Recorder(client.errorOccurred)
    try:
        client.start(*REAL_RIG)
        assert wait_until(lambda: None not in client.state().values(), timeout=5.0), errors.values
        original = client.state()
        client.set_frequency(14074000)
        assert wait_until(lambda: client.state()["freq_hz"] == 14074000, timeout=5.0)
        client.set_mode("PKTUSB", 3000)
        assert wait_until(
            lambda: client.state()["mode"] == "PKTUSB" and client.state()["passband"] == 3000,
            timeout=5.0,
        )
        client.set_mode("PKTFM", 0)  # reported by Hamlib 4.6 as FM-D
        assert wait_until(lambda: client.state()["mode"] == "PKTFM", timeout=5.0)
        assert errors.values == []
        client.set_mode(original["mode"], original["passband"])
        client.set_frequency(original["freq_hz"])
        assert wait_until(lambda: client.state() == original, timeout=5.0)
    finally:
        client.stop()
    assert not client.is_connected()


@pytest.mark.skipif(REAL_ROT is None, reason="set HAMQ_ROTCTLD=host:port (e.g. rotctld -m 1)")
def test_real_rotctld(qgis_app):
    client = RotatorClient(poll_ms=200)
    errors = Recorder(client.errorOccurred)
    positions = Recorder(client.positionChanged)
    try:
        client.start(*REAL_ROT)
        assert wait_until(lambda: client.position() is not None, timeout=5.0), errors.values
        start_az = client.position()[0]
        target = start_az + 40.0 if start_az < 300 else start_az - 40.0
        client.set_position(target, 0.0)
        assert wait_until(lambda: len(positions.values) >= 2, timeout=5.0)  # it moves
        client.stop_rotation()
        pump(0.6)
        stopped = client.position()
        pump(0.8)
        assert client.position() == stopped  # it stopped
        assert stopped[0] != target
        client.set_position(500.0)  # outside the range of the dummy (-180..450)
        assert wait_until(lambda: errors.values != [], timeout=5.0)
        assert errors.values == ["Turning the rotator failed: Limit exceeded"]
    finally:
        client.stop()
    assert not client.is_connected()
