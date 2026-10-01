"""hamq.net.wsjtx_listener: WsjtxListener with real UDP datagrams (M5-02).

Datagrams are built with the core encoder (``hamq.core.wsjtx.encode_*``) or taken from
``tests/fixtures/wsjtx`` and sent from a plain Python socket to 127.0.0.1 (or to the
multicast group 224.0.0.1 where the platform allows it, or to an address of this computer
on another interface for the bind address tests); the test processes Qt events until the
listener's signals arrive.
"""

from __future__ import annotations

import socket
import struct
import time
from pathlib import Path

import pytest
from qgis.core import QgsApplication

from hamq.core import i18n, wsjtx
from hamq.net import wsjtx_listener
from hamq.net.wsjtx_listener import WsjtxListener
from hamq.qgis_io.compat import MSG_INFO, MSG_WARNING, NET_PROXY_NONE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wsjtx"
PACKETS = sorted(FIXTURES.glob("*.bin")) + sorted((FIXTURES / "captured").glob("*.bin"))
LOOPBACK = "127.0.0.1"
GROUP = "224.0.0.1"
SIGNALS = (
    "heartbeatReceived",
    "statusReceived",
    "adifLogged",
    "clientClosed",
    "connectionChanged",
    "errorOccurred",
)
ADIF = (
    "<adif_ver:5>3.1.0<programid:6>WSJT-X<EOH>\n<call:6>YU7ABC <gridsquare:4>JN95 "
    "<mode:3>FT8 <qso_date:8>20260915 <time_on:6>184500 <band:3>40m <freq:8>7.075234 <eor>"
)


# --------------------------------------------------------------------------- helpers


def wait_until(predicate, timeout=5.0):
    """Process Qt events until ``predicate()`` is true; False after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        QgsApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.002)


def pause(seconds):
    wait_until(lambda: False, timeout=seconds)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


def lan_address():
    """An IPv4 address of this computer that is not a loopback address, or ``None``."""
    from qgis.PyQt.QtNetwork import QNetworkInterface

    for address in QNetworkInterface.allAddresses():
        text = address.toString()
        if text and ":" not in text and not address.isLoopback():
            return text
    return None


def interface_broadcasts():
    """IPv4 broadcast addresses of the network interfaces of this computer, loopback last."""
    from qgis.PyQt.QtNetwork import QNetworkInterface

    found = []
    for interface in QNetworkInterface.allInterfaces():
        for entry in interface.addressEntries():
            text = entry.broadcast().toString()
            if text and ":" not in text and text != "255.255.255.255":
                found.append((entry.ip().isLoopback(), text))
    return [text for _loopback, text in sorted(found)]


class Recorder:
    """Every signal of a listener as ``(name, args)``, in emission order."""

    def __init__(self, listener):
        self.events = []
        for name in SIGNALS:
            getattr(listener, name).connect(
                lambda *args, _name=name: self.events.append((_name, args))
            )

    def of(self, name):
        return [args for event, args in self.events if event == name]

    def names(self):
        return [event for event, _args in self.events]


@pytest.fixture
def make_listener(qgis_app):
    """``make(**kwargs)`` -> ``(listener, recorder)``; every listener is stopped afterwards."""
    listeners = []

    def make(**kwargs):
        listener = WsjtxListener(**kwargs)
        listeners.append(listener)
        return listener, Recorder(listener)

    yield make
    for listener in listeners:
        listener.stop()
    pause(0.01)


@pytest.fixture
def sender():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        yield sock


@pytest.fixture
def english():
    i18n.set_language(i18n.LANG_EN)
    yield
    i18n.set_language(i18n.LANG_EN)


def start(make_listener, address=LOOPBACK, **kwargs):
    listener, recorder = make_listener(**kwargs)
    port = free_port()
    assert listener.start(address, port), recorder.of("errorOccurred")
    return listener, recorder, port


def send_and_sync(sender, recorder, port, *packets, host=LOOPBACK):
    """Send ``packets``, then a heartbeat of the client ``SYNC``; wait until it arrived.

    Datagrams from one socket over the loopback interface arrive in order, so everything
    sent before the sync heartbeat has been handled when it shows up. Returns the events
    before the ``SYNC`` heartbeat, which include the ``connectionChanged(True)`` it causes
    when no client was connected.
    """
    for packet in packets:
        sender.sendto(packet, (host, port))
    sender.sendto(wsjtx.encode_heartbeat("SYNC"), (host, port))
    assert wait_until(
        lambda: any(args[0]["client"] == "SYNC" for args in recorder.of("heartbeatReceived"))
    )
    index = next(
        i
        for i, (name, args) in enumerate(recorder.events)
        if name == "heartbeatReceived" and args[0]["client"] == "SYNC"
    )
    return recorder.events[:index]


# --------------------------------------------------------------------------- messages


def test_heartbeat_status_adif_and_close(make_listener, sender, english):
    listener, recorder, port = start(make_listener)
    assert listener.is_running() and not listener.is_connected()
    assert (listener.address(), listener.port()) == (LOOPBACK, port)

    sender.sendto(wsjtx.encode_heartbeat("WSJT-X"), (LOOPBACK, port))
    assert wait_until(lambda: recorder.of("heartbeatReceived"))
    ((heartbeat,),) = recorder.of("heartbeatReceived")
    assert heartbeat["client"] == "WSJT-X" and heartbeat["version"] == "2.7.0"
    assert recorder.names() == ["connectionChanged", "heartbeatReceived"]
    assert recorder.of("connectionChanged") == [(True,)]
    assert listener.is_connected()

    status = wsjtx.encode_status(
        "WSJT-X", 7_074_000, "FT8", None, report="-10", de_call="YU1ABC", de_grid="KN04ft"
    )
    sender.sendto(status, (LOOPBACK, port))
    assert wait_until(lambda: recorder.of("statusReceived"))
    assert recorder.of("statusReceived") == [(wsjtx.decode(status),)]
    ((message,),) = recorder.of("statusReceived")
    assert message["freq_hz"] == 7_074_000 and message["dx_call"] is None

    sender.sendto(wsjtx.encode_logged_adif("WSJT-X", ADIF), (LOOPBACK, port))
    assert wait_until(lambda: recorder.of("adifLogged"))
    assert recorder.of("adifLogged") == [("WSJT-X", ADIF)]

    sender.sendto(wsjtx.encode_close("WSJT-X"), (LOOPBACK, port))
    assert wait_until(lambda: recorder.of("clientClosed"))
    assert recorder.of("clientClosed") == [("WSJT-X",)]
    assert recorder.names()[-2:] == ["clientClosed", "connectionChanged"]
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    assert not listener.is_connected()
    assert listener.is_running()
    assert recorder.of("errorOccurred") == []


def _expected_events(packet):
    """The signals a packet must produce, from the core decoder's view of it."""
    message = wsjtx.decode(packet)
    kind = None if message is None else message["type"]
    client = "" if message is None else message["client"] or ""
    if kind == "heartbeat":
        return [("heartbeatReceived", (message,))]
    if kind == "status":
        return [("statusReceived", (message,))]
    if kind == "logged_adif":
        return [("adifLogged", (client, message["adif"]))]
    if kind == "close":
        return [("clientClosed", (client,))]
    return []  # None (garbage) and "other" (QSO Logged, type 5)


@pytest.mark.parametrize("path", PACKETS, ids=lambda path: path.relative_to(FIXTURES).as_posix())
def test_fixture_datagrams(make_listener, sender, path):
    packet = path.read_bytes()
    message = wsjtx.decode(packet)
    listener, recorder, port = start(make_listener)
    before_sync = send_and_sync(sender, recorder, port, packet)
    # Every WSJT-X message marks its client alive (connectionChanged first); garbage does
    # nothing; a close from a client never seen only reports the close. Otherwise the SYNC
    # heartbeat is the first message and connects.
    alive = message is not None and message["type"] != "close"
    expected = [("connectionChanged", (True,))] if alive else []
    expected += _expected_events(packet)
    if not alive:
        expected.append(("connectionChanged", (True,)))  # caused by SYNC
    assert before_sync == expected
    signals = [event for event in before_sync if event[0] != "connectionChanged"]
    if path.name == "logged_adif.bin":
        assert "Đorđe" in signals[0][1][1]
    if path.name == "logged_adif_latin1.bin":
        assert "Jürgen" in signals[0][1][1]
    if path.name in ("qso_logged.bin", "bad_magic.bin"):
        assert signals == []


def test_garbage_is_ignored_and_logged_once(make_listener, sender, log_messages, english):
    listener, recorder, port = start(make_listener)
    heartbeat = wsjtx.encode_heartbeat("WSJT-X")
    garbage = [
        b"",
        b"\x00",
        b"hello",
        heartbeat[:11],  # incomplete header
        heartbeat[:14],  # client id cut
        (FIXTURES / "bad_magic.bin").read_bytes(),
        wsjtx.Writer().header(wsjtx.HEARTBEAT, "WSJT-X", schema=1).u32(3).getvalue(),
        bytes(range(256)) * 200,  # 51 200 bytes
    ]
    before_sync = send_and_sync(sender, recorder, port, *garbage)
    assert before_sync == [("connectionChanged", (True,))]  # caused by SYNC only
    assert listener.is_running() and listener.is_connected()
    ignored = [
        message
        for message, tag, level in log_messages
        if tag == "HamQ" and message.startswith("Ignored a UDP datagram")
    ]
    assert len(ignored) == 1
    assert "(from 127.0.0.1:" in ignored[0]
    assert not [entry for entry in log_messages if entry[1] == "HamQ" and entry[2] == MSG_WARNING]


def test_null_client_id(make_listener, sender):
    listener, recorder, port = start(make_listener)
    before_sync = send_and_sync(
        sender,
        recorder,
        port,
        wsjtx.encode_heartbeat(None),
        wsjtx.encode_logged_adif(None, ADIF),
        wsjtx.encode_close(None),
    )
    assert [name for name, _args in before_sync] == [
        "connectionChanged",
        "heartbeatReceived",
        "adifLogged",
        "clientClosed",
        "connectionChanged",
        "connectionChanged",  # SYNC
    ]
    assert [args for name, args in before_sync if name == "connectionChanged"] == [
        (True,),
        (False,),
        (True,),
    ]
    assert before_sync[1][1][0]["client"] is None
    assert before_sync[2][1] == ("", ADIF)
    assert before_sync[3][1] == ("",)


def test_empty_or_missing_adif_is_not_reported(make_listener, sender):
    listener, recorder, port = start(make_listener)
    before_sync = send_and_sync(
        sender,
        recorder,
        port,
        wsjtx.encode_logged_adif("WSJT-X", ""),
        wsjtx.encode_logged_adif("WSJT-X", "   \n"),
        wsjtx.Writer().header(wsjtx.LOGGED_ADIF, "WSJT-X").utf8(None).getvalue(),
    )
    assert [name for name, _args in before_sync] == ["connectionChanged"]


def test_duplicate_adif_is_reported_once(make_listener, sender):
    listener, recorder, port = start(make_listener)
    same = wsjtx.encode_logged_adif("WSJT-X", ADIF)
    other = ADIF.replace("YU7ABC", "YU7XYZ")
    send_and_sync(
        sender,
        recorder,
        port,
        same,
        same,  # e.g. multicast sent on two interfaces
        wsjtx.encode_logged_adif("WSJT-X", other),
        wsjtx.encode_logged_adif("JTDX", other),
    )
    assert recorder.of("adifLogged") == [("WSJT-X", ADIF), ("WSJT-X", other), ("JTDX", other)]

    listener.DUPLICATE_WINDOW_S = 0.0
    recorder.events.clear()
    send_and_sync(sender, recorder, port, same, same)
    assert recorder.of("adifLogged") == [("WSJT-X", ADIF), ("WSJT-X", ADIF)]


def test_many_datagrams_are_handled_in_several_passes(make_listener, sender):
    listener, recorder, port = start(make_listener)
    listener.MAX_DATAGRAMS_PER_PASS = 3
    passes = []
    original = listener._read_pending

    def counting():
        passes.append(1)
        original()

    listener._read_pending = counting
    for number in range(40):
        sender.sendto(wsjtx.encode_heartbeat(f"CLIENT {number}"), (LOOPBACK, port))
    assert wait_until(lambda: len(recorder.of("heartbeatReceived")) == 40)
    clients = [args[0]["client"] for args in recorder.of("heartbeatReceived")]
    assert clients == [f"CLIENT {number}" for number in range(40)]
    assert len(passes) >= 40 // 3


# --------------------------------------------------------------------------- connection


def test_client_times_out(make_listener, sender, log_messages, english):
    listener, recorder, port = start(make_listener, client_timeout_ms=300)
    sender.sendto(wsjtx.encode_heartbeat("WSJT-X"), (LOOPBACK, port))
    assert wait_until(listener.is_connected)
    connected_at = time.monotonic()
    assert wait_until(lambda: not listener.is_connected())
    assert time.monotonic() - connected_at >= 0.25
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    assert listener.is_running()
    assert ("No messages from WSJT-X for 0.3 s", "HamQ", MSG_INFO) in log_messages


def test_traffic_keeps_the_client_alive(make_listener, sender):
    listener, recorder, port = start(make_listener, client_timeout_ms=1000)
    until = time.monotonic() + 2.0
    while time.monotonic() < until:  # a message every 0.1 s, well inside 1 s
        sender.sendto(wsjtx.encode_status("WSJT-X", 14_074_000, "FT8"), (LOOPBACK, port))
        pause(0.1)
    assert recorder.of("connectionChanged") == [(True,)]
    assert len(recorder.of("statusReceived")) >= 10
    assert wait_until(lambda: not listener.is_connected(), timeout=5)
    assert recorder.of("connectionChanged") == [(True,), (False,)]


def test_several_clients(make_listener, sender):
    listener, recorder, port = start(make_listener, client_timeout_ms=1000)
    send_and_sync(
        sender,
        recorder,
        port,
        wsjtx.encode_heartbeat("WSJT-X - IC7300"),
        wsjtx.encode_heartbeat("JTDX"),
        wsjtx.encode_close("JTDX"),
    )
    assert recorder.of("clientClosed") == [("JTDX",)]
    assert recorder.of("connectionChanged") == [(True,)]  # WSJT-X and SYNC are still there
    assert wait_until(lambda: not listener.is_connected(), timeout=5)
    assert recorder.of("connectionChanged") == [(True,), (False,)]


# --------------------------------------------------------------------------- start and stop


def test_port_in_use_reports_a_clear_error(make_listener, log_messages, english):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other_program:
        other_program.bind(("0.0.0.0", 0))  # no SO_REUSEADDR: the port is taken
        port = other_program.getsockname()[1]
        listener, recorder = make_listener()
        assert not listener.start(LOOPBACK, port)
        assert not listener.is_running()
        ((message,),) = recorder.of("errorOccurred")
        assert message.startswith(f"UDP port {port} is already in use")
        assert "multicast address, for example 224.0.0.1" in message
        assert "File > Settings > Reporting > UDP Server" in message
        assert (message, "HamQ", MSG_WARNING) in log_messages
    assert listener.start(LOOPBACK, port)  # free again


def test_error_messages_are_translated(make_listener):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other_program:
        other_program.bind(("0.0.0.0", 0))
        port = other_program.getsockname()[1]
        listener, recorder = make_listener()
        i18n.set_language(i18n.LANG_SR_LATN)
        try:
            assert not listener.start(LOOPBACK, port)
        finally:
            i18n.set_language(i18n.LANG_EN)
    ((message,),) = recorder.of("errorOccurred")
    assert message.startswith(f"UDP port {port} je već zauzet")
    assert "File > Settings > Reporting > UDP Server" in message


def test_other_bind_errors(make_listener, english):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(("0.0.0.0", 1))
        except PermissionError:
            pass
        else:
            pytest.skip("privileged UDP ports are open to this user here")
    listener, recorder = make_listener()
    assert not listener.start(LOOPBACK, 1)
    ((message,),) = recorder.of("errorOccurred")
    assert message.startswith("Cannot listen on UDP port 1: ")
    assert "224.0.0.1" in message


@pytest.mark.parametrize(
    ("address", "port", "expected"),
    [
        ("not an address", 2237, "Invalid WSJT-X address not an address: "),
        ("224.0.0", 2237, "Invalid WSJT-X address 224.0.0: "),
        ("::1", 2237, "IPv6 addresses are not supported for WSJT-X: ::1."),
        ("ff02::1", 2237, "IPv6 addresses are not supported for WSJT-X: ff02::1."),
        (LOOPBACK, 0, "Invalid UDP port 0: use a number from 1 to 65535"),
        (LOOPBACK, 65536, "Invalid UDP port 65536: "),
        (LOOPBACK, -1, "Invalid UDP port -1: "),
        (LOOPBACK, "2237", "Invalid UDP port 2237: "),
        (LOOPBACK, True, "Invalid UDP port True: "),
        (LOOPBACK, 2237.0, "Invalid UDP port 2237.0: "),
    ],
)
def test_invalid_address_or_port(make_listener, address, port, expected, english):
    listener, recorder = make_listener()
    assert listener.start(address, port) is False
    assert not listener.is_running()
    ((message,),) = recorder.of("errorOccurred")
    assert message.startswith(expected)


@pytest.mark.parametrize(
    ("address", "normalized"), [("localhost", LOOPBACK), ("", "0.0.0.0"), (" 0.0.0.0 ", "0.0.0.0")]
)
def test_other_unicast_addresses(make_listener, sender, address, normalized):
    listener, recorder, port = start(make_listener, address=address)
    assert listener.address() == normalized
    send_and_sync(sender, recorder, port)


def test_stop_releases_everything_and_can_be_repeated(make_listener, sender):
    listener, recorder = make_listener()
    listener.stop()  # before start: harmless
    port = free_port()
    assert listener.start(LOOPBACK, port)
    send_and_sync(sender, recorder, port)
    assert listener.is_connected()
    listener.stop()
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    assert not listener.is_running() and not listener.is_connected()
    assert (listener.address(), listener.port()) == ("", 0)
    listener.stop()
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as exclusive:
        exclusive.bind(("0.0.0.0", port))  # the port was released at once
    sender.sendto(wsjtx.encode_heartbeat("LATE"), (LOOPBACK, port))
    pause(0.1)
    assert all(args[0]["client"] != "LATE" for args in recorder.of("heartbeatReceived"))
    assert listener.start(LOOPBACK, port)  # and can be started again


def test_start_while_running_restarts(make_listener, sender):
    listener, recorder, first = start(make_listener)
    send_and_sync(sender, recorder, first)
    second = free_port()
    assert listener.start(LOOPBACK, second)
    assert listener.port() == second
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as exclusive:
        exclusive.bind(("0.0.0.0", first))  # the first port is free
    recorder.events.clear()
    send_and_sync(sender, recorder, second)
    assert recorder.of("connectionChanged") == [(True,)]


def test_stop_from_a_slot(make_listener, sender):
    listener, recorder, port = start(make_listener)
    listener.heartbeatReceived.connect(lambda message: listener.stop())
    for number in range(3):
        sender.sendto(wsjtx.encode_heartbeat(f"CLIENT {number}"), (LOOPBACK, port))
    assert wait_until(lambda: not listener.is_running())
    pause(0.1)
    assert [args[0]["client"] for args in recorder.of("heartbeatReceived")] == ["CLIENT 0"]
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    assert recorder.of("errorOccurred") == []


def test_stop_never_raises(make_listener, sender, monkeypatch, log_messages, english):
    listener, recorder, port = start(make_listener, client_timeout_ms=300)
    send_and_sync(sender, recorder, port)

    def broken(socket):
        raise ValueError("close exploded")

    monkeypatch.setattr(WsjtxListener, "_dispose", staticmethod(broken))
    listener.stop()  # e.g. connected to a button: nothing may escape into Qt
    assert ("WSJT-X listener error: close exploded", "HamQ", MSG_WARNING) in log_messages
    assert not listener.is_running() and not listener.is_connected()
    assert recorder.of("connectionChanged") == [(True,), (False,)]
    assert not listener._alive_timer.isActive() and not listener._drain_timer.isActive()
    monkeypatch.undo()
    listener.stop()
    assert recorder.of("connectionChanged") == [(True,), (False,)]


def test_a_deleted_listener_never_raises(make_listener, sender, log_messages, english):
    from qgis.PyQt import sip

    listener, recorder, port = start(make_listener)
    send_and_sync(sender, recorder, port)
    sip.delete(listener)  # e.g. its parent went away before the plugin called stop()
    listener.stop()
    listener.stop()
    assert not listener.is_running() and not listener.is_connected()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as exclusive:
        exclusive.bind(("0.0.0.0", port))  # the socket was deleted with its parent
    assert listener.start(LOOPBACK, port) is False  # reported in the log, never raised
    assert any(
        message.startswith("The WSJT-X listener could not be started: ")
        for message, tag, level in log_messages
        if tag == "HamQ" and level == MSG_WARNING
    )
    assert not [entry for entry in log_messages if "listener error" in entry[0]]


def test_exceptions_never_escape_into_qt(make_listener, sender, monkeypatch, log_messages):
    listener, recorder, port = start(make_listener)

    def broken(data):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(wsjtx_listener, "decode", broken)
    sender.sendto(wsjtx.encode_heartbeat("WSJT-X"), (LOOPBACK, port))
    assert wait_until(lambda: any("decoder exploded" in entry[0] for entry in log_messages))
    assert listener.is_running()
    assert recorder.events == []
    monkeypatch.undo()
    send_and_sync(sender, recorder, port)


def test_unexpected_start_error_releases_the_port(make_listener, monkeypatch, english):
    def broken(socket, group):
        raise RuntimeError("join exploded")

    monkeypatch.setattr(wsjtx_listener, "_join_group", broken)
    listener, recorder = make_listener()
    port = free_port()
    assert listener.start(GROUP, port) is False
    assert recorder.of("errorOccurred") == [
        ("The WSJT-X listener could not be started: join exploded",)
    ]
    assert not listener.is_running()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as exclusive:
        exclusive.bind(("0.0.0.0", port))  # the socket was closed


def test_join_failure_is_reported(make_listener, monkeypatch, english):
    monkeypatch.setattr(wsjtx_listener, "_join_group", lambda socket, group: [])
    listener, recorder = make_listener()
    port = free_port()
    assert not listener.start(GROUP, port)
    assert not listener.is_running()
    ((message,),) = recorder.of("errorOccurred")
    assert message.startswith("Could not join the multicast group 224.0.0.1: ")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as exclusive:
        exclusive.bind(("0.0.0.0", port))  # the socket was closed


# --------------------------------------------------------------------------- bind address
#
# The listener receives only what is sent to the address set in the HamQ settings. With
# the default 127.0.0.1 nobody else on the network can send QSOs into the log.


def test_the_default_address_listens_on_loopback_only(make_listener, sender):
    listener, recorder, port = start(make_listener)  # 127.0.0.1
    assert listener._socket.localAddress().toString() == LOOPBACK
    lan = lan_address()
    if lan is None:
        pytest.skip("this computer has no IPv4 address besides the loopback address")
    sender.sendto(wsjtx.encode_logged_adif("REMOTE", ADIF), (lan, port))
    send_and_sync(sender, recorder, port)
    pause(0.2)
    assert recorder.of("adifLogged") == []
    sender.sendto(wsjtx.encode_logged_adif("LOCAL", ADIF), (LOOPBACK, port))
    assert wait_until(lambda: recorder.of("adifLogged") == [("LOCAL", ADIF)])


@pytest.mark.parametrize("address", ["", "0.0.0.0"])
def test_any_address_listens_on_every_interface(make_listener, sender, address):
    listener, recorder, port = start(make_listener, address=address)
    assert listener._socket.localAddress().toString() == "0.0.0.0"
    lan = lan_address()
    if lan is None:
        pytest.skip("this computer has no IPv4 address besides the loopback address")
    sender.sendto(wsjtx.encode_logged_adif("LAN", ADIF), (lan, port))
    assert wait_until(lambda: recorder.of("adifLogged") == [("LAN", ADIF)])


def test_an_address_of_this_computer_listens_on_that_address(make_listener, sender):
    lan = lan_address()
    if lan is None:
        pytest.skip("this computer has no IPv4 address besides the loopback address")
    listener, recorder, port = start(make_listener, address=lan)
    assert listener._socket.localAddress().toString() == lan
    sender.sendto(wsjtx.encode_logged_adif("LOOPBACK", ADIF), (LOOPBACK, port))
    send_and_sync(sender, recorder, port, host=lan)
    pause(0.2)
    assert recorder.of("adifLogged") == []
    sender.sendto(wsjtx.encode_logged_adif("LAN", ADIF), (lan, port))
    assert wait_until(lambda: recorder.of("adifLogged") == [("LAN", ADIF)])


def test_an_address_of_another_computer_is_reported(make_listener, log_messages, english):
    listener, recorder = make_listener()
    port = free_port()
    assert not listener.start("192.0.2.1", port)  # TEST-NET-1: never an address of ours
    assert not listener.is_running()
    ((message,),) = recorder.of("errorOccurred")
    assert message == (
        f"Cannot listen on 192.0.2.1 (UDP port {port}): 192.0.2.1 is not an address of this "
        "computer. In the HamQ settings, enter the address WSJT-X sends to (File > Settings "
        "> Reporting > UDP Server), usually 127.0.0.1."
    )
    assert (message, "HamQ", MSG_WARNING) in log_messages


def test_broadcast_addresses_listen_on_every_interface(make_listener, sender):
    addresses = ["255.255.255.255", *interface_broadcasts()[:2]]
    for address in addresses:
        listener, recorder, port = start(make_listener, address=address)
        assert listener.address() == address
        assert listener._socket.localAddress().toString() == "0.0.0.0"
        send_and_sync(sender, recorder, port)
        listener.stop()


def test_the_socket_bypasses_the_proxy(make_listener):
    # Qt would look up the system proxy (synchronously, in the GUI thread) for nothing.
    listener, recorder, port = start(make_listener)
    assert listener._socket.proxy().type() == NET_PROXY_NONE


# --------------------------------------------------------------------------- current error


def test_current_error_is_shown_again_in_the_new_language(make_listener):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other_program:
        other_program.bind(("0.0.0.0", 0))
        port = other_program.getsockname()[1]
        listener, recorder = make_listener()
        assert listener.current_error() == ""
        assert not listener.start(LOOPBACK, port)
        ((message,),) = recorder.of("errorOccurred")
        assert listener.current_error() == message
        assert message.startswith(f"UDP port {port} is already in use")
        try:
            i18n.set_language(i18n.LANG_SR_LATN)
            latin = listener.current_error()
            assert latin.startswith(f"UDP port {port} je već zauzet")
            assert "File > Settings > Reporting > UDP Server" in latin
            i18n.set_language(i18n.LANG_SR_CYRL)
            cyrillic = listener.current_error()
            assert cyrillic.startswith(f"UDP порт {port} је већ заузет")
            assert "File > Settings > Reporting > UDP Server" in cyrillic  # WSJT-X's own menu
        finally:
            i18n.set_language(i18n.LANG_EN)
        assert listener.current_error() == message
        listener.stop()  # still the problem of the last start
        assert listener.current_error() == message
    assert listener.start(LOOPBACK, port)  # the port is free now
    assert listener.current_error() == ""
    assert len(recorder.of("errorOccurred")) == 1


@pytest.mark.parametrize(
    ("address", "port", "latin"),
    [
        ("not an address", 2237, "Neispravna WSJT-X adresa not an address: "),
        (LOOPBACK, 0, "Neispravan UDP port 0: unesite broj od 1 do 65535"),
        ("192.0.2.1", 2237, "Nije moguće slušati na adresi 192.0.2.1 (UDP port 2237): "),
    ],
)
def test_current_error_of_other_start_problems(make_listener, address, port, latin, english):
    listener, recorder = make_listener()
    assert not listener.start(address, port)
    ((message,),) = recorder.of("errorOccurred")
    assert listener.current_error() == message
    try:
        i18n.set_language(i18n.LANG_SR_LATN)
        assert listener.current_error().startswith(latin)
    finally:
        i18n.set_language(i18n.LANG_EN)


def test_current_error_of_an_unexpected_start_error(make_listener, monkeypatch, english):
    monkeypatch.setattr(wsjtx_listener, "_join_group", lambda socket, group: 1 / 0)
    listener, recorder = make_listener()
    assert not listener.start(GROUP, free_port())
    ((message,),) = recorder.of("errorOccurred")
    assert message == "The WSJT-X listener could not be started: division by zero"
    assert listener.current_error() == message
    i18n.set_language(i18n.LANG_SR_LATN)
    assert listener.current_error() == (
        "Slušanje WSJT-X poruka nije moguće pokrenuti: division by zero"
    )


# --------------------------------------------------------------------------- multicast


def _multicast_socket(port):
    """A plain socket in GROUP on ``port`` that shares the port (SO_REUSEADDR)."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    receiver.bind(("", port))
    membership = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0"))
    receiver.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    receiver.settimeout(2.0)
    return receiver


@pytest.fixture
def multicast_sender():
    """A socket that sends to GROUP; skips when this platform cannot loop multicast back."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    port = free_port()
    try:
        with _multicast_socket(port) as probe:
            sock.sendto(b"probe", (GROUP, port))
            probe.recvfrom(64)
    except OSError as exc:
        sock.close()
        pytest.skip(f"multicast to {GROUP} does not work here: {exc}")
    yield sock
    sock.close()


def test_multicast_group(make_listener, multicast_sender):
    port = free_port()
    with _multicast_socket(port) as other_program:  # e.g. JTAlert on the same port
        listener, recorder = make_listener()
        assert listener.start(GROUP, port), recorder.of("errorOccurred")
        assert listener.address() == GROUP
        packet = wsjtx.encode_logged_adif("WSJT-X", ADIF)
        before_sync = send_and_sync(multicast_sender, recorder, port, packet, host=GROUP)
        assert [event for event in before_sync if event[0] == "adifLogged"] == [
            ("adifLogged", ("WSJT-X", ADIF))
        ]
        assert other_program.recvfrom(4096)[0] == packet  # both programs got it

        listener.stop()  # leaves the group; the other program keeps receiving
        multicast_sender.sendto(b"after stop", (GROUP, port))
        assert other_program.recvfrom(4096)[0] == wsjtx.encode_heartbeat("SYNC")
        assert other_program.recvfrom(4096)[0] == b"after stop"

        assert listener.start(GROUP, port)  # a new membership works again
        recorder.events.clear()
        send_and_sync(multicast_sender, recorder, port, host=GROUP)
