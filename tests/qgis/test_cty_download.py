"""hamq.net.cty_download: CtyManager (download, check, cache) and load_cached_cty (M4-03).

Downloads come from a local ``http.server`` running in a thread that serves the cty.dat /
cty.csv test excerpt (20 entities), so ``MIN_ENTITIES`` is lowered for these tests. The
cache lives in ``tmp_path``; a few tests use the default path in the throw-away profile.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from qgis.core import QgsApplication

from hamq.core import i18n
from hamq.core.cty import CtyDatabase
from hamq.net import cty_download
from hamq.net.cty_download import CtyManager, clear_cty_cache, csv_path_for, load_cached_cty
from hamq.qgis_io.compat import MSG_INFO, MSG_WARNING
from hamq.settings import HamQSettings, cty_cache_path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cty"
DAT = (FIXTURES / "cty_excerpt.dat").read_bytes()
CSV = (FIXTURES / "cty_excerpt.csv").read_bytes()
ENTITIES = 20
# The cache as an earlier download left it: valid, but different bytes than the server's.
OLD_DAT = DAT.replace(b"# HamQ test fixture", b"# old cache - HamQ test fixture", 1)
SMALL_DAT = b"Serbia: 15: 28: EU: 44.00: -21.00: -1.0: YU:\n    4N,4O,YT,YU;\n"
HTML = b"<!DOCTYPE html><html><body><h1>Down for maintenance</h1></body></html>"
HOLD = -1  # route status: keep the connection open without answering until released


# --------------------------------------------------------------------------- helpers


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # keep the test output clean
        pass

    def do_GET(self):
        server = self.server
        server.requests.append(self.path)
        status, body, headers = server.routes.get(self.path, (404, b"not found", {}))
        if status == HOLD:
            server.release.wait(30)
            return
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes = {"/cty.dat": (200, DAT, {}), "/cty.csv": (200, CSV, {})}
        self.requests = []
        self.release = threading.Event()

    def handle_error(self, request, client_address):  # the client aborted: expected here
        pass

    def url(self, path):
        return f"http://127.0.0.1:{self.server_address[1]}{path}"


def wait_until(predicate, timeout=10.0):
    """Process Qt events until ``predicate()`` is true; False after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        QgsApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)


def write_cache(dat_path, dat=OLD_DAT, csv=CSV):
    os.makedirs(os.path.dirname(dat_path), exist_ok=True)
    Path(dat_path).write_bytes(dat)
    if csv is not None:
        Path(csv_path_for(dat_path)).write_bytes(csv)


def cache_files(manager):
    """Names in the cache folder: temporary files must never be left behind."""
    folder = Path(manager.cache_path()).parent
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def assert_old_cache_kept(manager):
    assert Path(manager.cache_path()).read_bytes() == OLD_DAT
    assert Path(csv_path_for(manager.cache_path())).read_bytes() == CSV
    assert cache_files(manager) == ["cty.csv", "cty.dat"]
    database = manager.database()
    assert database is not None and len(database) == ENTITIES
    assert HamQSettings().cty_downloaded == ""


def today():
    return datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def server():
    srv = _Server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.release.set()
    srv.shutdown()
    srv.server_close()
    thread.join(5)


@pytest.fixture
def make_manager(tmp_path, clean_settings, server):
    """``make(dat="/cty.dat", csv="/cty.csv", old_cache=True, **attributes)`` ->
    ``(manager, results)``. ``dat`` / ``csv`` are paths on the test server or full URLs;
    ``results`` collects the ``downloadFinished`` arguments."""
    managers = []

    def make(dat="/cty.dat", csv="/cty.csv", old_cache=True, **attributes):
        manager = CtyManager(
            cache_path=str(tmp_path / "profile" / "hamq" / "cty.dat"),
            dat_url=dat if "://" in dat else server.url(dat),
            csv_url=csv if "://" in csv else server.url(csv),
        )
        manager.MIN_ENTITIES = 10
        for name, value in attributes.items():
            setattr(manager, name, value)
        results = []
        manager.downloadFinished.connect(lambda ok, message: results.append((ok, message)))
        if old_cache:
            write_cache(manager.cache_path())
        managers.append(manager)
        return manager, results

    yield make
    for manager in managers:
        manager.cleanup()
    clear_cty_cache()


@pytest.fixture
def count_parses(monkeypatch):
    """Names of the threads in which ``CtyDatabase.from_files`` ran during the test."""
    calls = []
    original = CtyDatabase.from_files

    def counting(*args, **kwargs):
        calls.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(CtyDatabase, "from_files", counting)
    clear_cty_cache()
    yield calls
    clear_cty_cache()


@pytest.fixture
def english():
    i18n.set_language(i18n.LANG_EN)
    yield
    i18n.set_language(i18n.LANG_EN)


# --------------------------------------------------------------------------- downloads


def test_download_replaces_the_cache(make_manager, count_parses, english):
    manager, results = make_manager()
    old = manager.database()
    assert old is not None and len(old) == ENTITIES
    assert manager.is_available()
    before = today()

    manager.download()
    assert manager.is_downloading()
    assert wait_until(lambda: results)

    assert len(results) == 1
    ok, message = results[0]
    assert ok, message
    assert message == "cty.dat downloaded (version 2026-09-15), entities: 20"
    assert not manager.is_downloading()
    assert Path(manager.cache_path()).read_bytes() == DAT
    assert Path(csv_path_for(manager.cache_path())).read_bytes() == CSV
    assert cache_files(manager) == ["cty.csv", "cty.dat"]
    assert HamQSettings().cty_downloaded in {before, today()}

    parses = len(count_parses)
    database = manager.database()
    assert database is not old and len(database) == ENTITIES
    serbia = database.lookup("YU1AB")
    assert serbia.entity.name == "Serbia" and serbia.dxcc == 296
    # the database checked by the download is the cached one: nothing is parsed again
    assert load_cached_cty(manager.cache_path()) is database
    assert len(count_parses) == parses
    QgsApplication.processEvents()
    assert len(results) == 1


def test_first_download_without_cache(make_manager, english):
    manager, results = make_manager(old_cache=False)
    assert not manager.is_available()
    assert manager.database() is None
    manager.download()
    assert wait_until(lambda: results)
    assert results[0][0], results
    assert manager.is_available()
    assert len(manager.database()) == ENTITIES


def test_redirect_is_followed(make_manager, server, english):
    server.routes["/moved/cty.dat"] = (302, b"", {"Location": "/cty.dat"})
    manager, results = make_manager(dat="/moved/cty.dat")
    manager.download()
    assert wait_until(lambda: results)
    assert results[0][0], results
    assert Path(manager.cache_path()).read_bytes() == DAT


@pytest.mark.parametrize("missing", ["/cty.dat", "/cty.csv"])
def test_http_404_keeps_the_old_cache(make_manager, server, missing, english):
    del server.routes[missing]
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    assert len(results) == 1
    ok, message = results[0]
    assert not ok
    name = missing.lstrip("/")
    assert message == (
        f"cty.dat download failed: {name}: the server answered with HTTP status 404. "
        "The previously downloaded files are still used."
    )
    assert_old_cache_kept(manager)


def test_failure_without_cache_has_no_cache_note(make_manager, server, english):
    del server.routes["/cty.dat"]
    manager, results = make_manager(old_cache=False)
    manager.download()
    assert wait_until(lambda: results)
    assert results == [
        (False, "cty.dat download failed: cty.dat: the server answered with HTTP status 404.")
    ]
    assert not manager.is_available()
    assert cache_files(manager) == []


def test_garbage_dat_keeps_the_old_cache(make_manager, server, english):
    server.routes["/cty.dat"] = (200, HTML, {"Content-Type": "text/html"})
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert "the downloaded cty.dat is not valid (entities: 0, at least 10 expected)" in message
    assert_old_cache_kept(manager)


def test_too_few_entities_keep_the_old_cache(make_manager, server, english):
    server.routes["/cty.dat"] = (200, SMALL_DAT, {})
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    assert not results[0][0]
    assert "(entities: 1, at least 10 expected)" in results[0][1]
    assert_old_cache_kept(manager)


def test_unrelated_csv_keeps_the_old_cache(make_manager, server, english):
    server.routes["/cty.csv"] = (200, b"XX,Nowhere,999,EU,1,1,0,0,0,XX;\n", {})
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert "the downloaded cty.csv does not match cty.dat (DXCC codes for 0 of 20" in message
    assert_old_cache_kept(manager)


def test_timeout_keeps_the_old_cache(make_manager, server, english):
    server.routes["/cty.dat"] = (HOLD, b"", {})
    manager, results = make_manager(TIMEOUT_MS=300)
    start = time.monotonic()
    manager.download()
    assert wait_until(lambda: results)
    elapsed = time.monotonic() - start
    assert 0.25 <= elapsed < 5
    ok, message = results[0]
    assert not ok
    assert "cty.dat download failed: no data from the server for 0.3 s." in message
    assert not manager.is_downloading()
    assert_old_cache_kept(manager)


def test_unreachable_server_keeps_the_old_cache(make_manager, log_messages, english):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    manager, results = make_manager(dat=f"http://127.0.0.1:{closed_port}/cty.dat")
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert message == (
        "cty.dat download failed: the server 127.0.0.1 cannot be reached; check the internet "
        "connection or try again later. The previously downloaded files are still used."
    )
    # Qt's own (English) error text goes to the log only
    details = [
        text
        for text, tag, level in log_messages
        if tag == "HamQ" and level == MSG_INFO and text.startswith("cty.dat: ")
    ]
    assert len(details) == 1 and "refused" in details[0].lower(), details
    assert_old_cache_kept(manager)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (
            i18n.LANG_EN,
            "cty.dat download failed: the server nonexistent-host.invalid cannot be reached; "
            "check the internet connection or try again later.",
        ),
        (
            i18n.LANG_SR_LATN,
            "Preuzimanje cty.dat nije uspelo: server nonexistent-host.invalid nije dostupan; "
            "proverite pristup internetu ili pokušajte kasnije.",
        ),
        (
            i18n.LANG_SR_CYRL,
            "Преузимање cty.dat није успело: сервер nonexistent-host.invalid није доступан; "
            "проверите приступ интернету или покушајте касније.",
        ),
    ],
)
def test_no_internet_says_what_to_do(make_manager, language, expected):
    # The first download usually happens on first use: offline, the host name is not found.
    manager, results = make_manager(dat="http://nonexistent-host.invalid/cty.dat", old_cache=False)
    i18n.set_language(language)
    try:
        manager.download()
        assert wait_until(lambda: results)
    finally:
        i18n.set_language(i18n.LANG_EN)
    assert results == [(False, expected)]
    assert not manager.is_available()


def test_file_larger_than_the_limit_is_rejected(make_manager, server, english):
    server.routes["/cty.dat"] = (200, DAT + b"#" * (3 * 1024 * 1024), {})
    manager, results = make_manager(MAX_BYTES=1024 * 1024)
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert "a file is larger than 1 MB" in message
    assert_old_cache_kept(manager)


def test_cancel(make_manager, server, english):
    server.routes["/cty.dat"] = (HOLD, b"", {})
    manager, results = make_manager()
    manager.cancel()  # idle: nothing happens
    manager.download()
    assert wait_until(lambda: "/cty.dat" in server.requests)
    manager.cancel()
    assert results == [
        (False, "cty.dat download canceled. The previously downloaded files are still used.")
    ]
    assert not manager.is_downloading()
    manager.cancel()
    wait_until(lambda: False, timeout=0.2)
    assert len(results) == 1
    assert_old_cache_kept(manager)

    server.routes["/cty.dat"] = (200, DAT, {})
    manager.download()
    assert wait_until(lambda: len(results) == 2)
    assert results[1][0], results


def test_download_while_running_is_ignored(make_manager, server, english, log_messages):
    server.routes["/cty.dat"] = (HOLD, b"", {})
    manager, results = make_manager()
    manager.download()
    manager.download()
    assert any(message == "A cty.dat download is already running" for message, *_ in log_messages)
    manager.cancel()
    wait_until(lambda: False, timeout=0.2)
    assert len(results) == 1


def test_cleanup_is_silent_and_can_be_repeated(make_manager, server, english):
    server.routes["/cty.dat"] = (HOLD, b"", {})
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: "/cty.dat" in server.requests)
    manager.cleanup()
    manager.cleanup()
    wait_until(lambda: False, timeout=0.3)
    assert results == []
    assert not manager.is_downloading()
    server.routes["/cty.dat"] = (200, DAT, {})
    manager.download()  # still usable
    assert wait_until(lambda: results)
    assert results[0][0], results


def test_a_deleted_manager_never_raises(make_manager, server, log_messages, english):
    from qgis.PyQt import sip

    server.routes["/cty.dat"] = (HOLD, b"", {})
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: "/cty.dat" in server.requests)
    sip.delete(manager)  # e.g. its parent went away before the plugin called cleanup()
    manager.cleanup()
    manager.cleanup()
    wait_until(lambda: False, timeout=0.3)
    assert results == [] and not manager.is_downloading()
    manager.download()  # reported in the log, never raised
    assert any(
        message.startswith("cty.dat download failed: unexpected error: ")
        for message, tag, level in log_messages
        if tag == "HamQ" and level == MSG_WARNING
    )
    assert not manager.is_downloading()
    assert_old_cache_kept(manager)


def test_save_failure_keeps_the_old_cache(make_manager, monkeypatch, english):
    original = cty_download._write_temporary
    calls = []

    def fail_second(target, data):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied", target)
        return original(target, data)

    monkeypatch.setattr(cty_download, "_write_temporary", fail_second)
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert "the files could not be saved: [Errno 13] Permission denied" in message
    assert len(calls) == 2
    assert_old_cache_kept(manager)  # also: the first temporary file was removed


def test_unexpected_error_in_the_check_is_reported(make_manager, monkeypatch, english):
    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(cty_download, "_install", broken)
    manager, results = make_manager()
    manager.download()
    assert wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert "cty.dat download failed: unexpected error: boom." in message
    assert not manager.is_downloading()
    assert_old_cache_kept(manager)


def test_messages_are_translated(make_manager, server):
    del server.routes["/cty.dat"]
    manager, results = make_manager()
    i18n.set_language(i18n.LANG_SR_LATN)
    try:
        manager.download()
        assert wait_until(lambda: results)
    finally:
        i18n.set_language(i18n.LANG_EN)
    assert results[0][1] == (
        "Preuzimanje cty.dat nije uspelo: cty.dat: server je odgovorio HTTP statusom 404. "
        "I dalje se koriste ranije preuzeti fajlovi."
    )


def test_default_cache_path(clean_settings):
    manager = CtyManager()
    try:
        assert manager.cache_path() == cty_cache_path()
        assert manager.is_available() == os.path.isfile(cty_cache_path())
    finally:
        manager.cleanup()


# --------------------------------------------------------------------------- cached loader


def test_preload_parses_in_a_background_thread(make_manager, count_parses):
    manager, _results = make_manager()
    manager.preload()
    assert wait_until(lambda: count_parses)
    database = manager.database()
    assert database is not None and len(database) == ENTITIES
    assert count_parses == ["HamQ cty.dat load"]
    manager.preload()  # already parsed: the thread returns the cached database
    wait_until(lambda: False, timeout=0.2)
    assert count_parses == ["HamQ cty.dat load"]


def test_preload_without_cache_does_nothing(make_manager, count_parses):
    manager, _results = make_manager(old_cache=False)
    manager.preload()
    wait_until(lambda: False, timeout=0.2)
    assert count_parses == []
    assert manager.database() is None


def test_load_cached_cty_without_files(tmp_path):
    assert load_cached_cty(str(tmp_path / "cty.dat")) is None


def test_load_cached_cty_is_cached_and_follows_changes(tmp_path, count_parses):
    path = str(tmp_path / "cty.dat")
    write_cache(path, dat=DAT)
    first = load_cached_cty(path)
    assert first is not None and len(first) == ENTITIES
    assert first.lookup("9A1GS").dxcc == 497
    assert load_cached_cty(path) is first
    assert len(count_parses) == 1

    Path(path).write_bytes(SMALL_DAT)  # e.g. another QGIS instance downloaded
    second = load_cached_cty(path)
    assert second is not first and len(second) == 1
    assert load_cached_cty(path) is second

    os.remove(path)
    assert load_cached_cty(path) is None


def test_load_cached_cty_without_csv_has_no_dxcc_codes(tmp_path):
    path = str(tmp_path / "cty.dat")
    write_cache(path, dat=DAT, csv=None)
    database = load_cached_cty(path)
    assert database is not None and len(database) == ENTITIES
    assert database.lookup("YU1AB").dxcc is None
    Path(csv_path_for(path)).write_bytes(CSV)  # the csv appears: loaded again with codes
    assert load_cached_cty(path).lookup("YU1AB").dxcc == 296


def test_load_cached_cty_with_a_broken_file(tmp_path, log_messages, english):
    path = str(tmp_path / "cty.dat")
    write_cache(path, dat=HTML)
    assert load_cached_cty(path) is None
    assert load_cached_cty(path) is None
    warnings = [
        message
        for message, tag, level in log_messages
        if tag == "HamQ" and level == MSG_WARNING and "cty.dat" in message
    ]
    assert warnings == ["The saved cty.dat contains no entities; download it again"]


def test_load_cached_cty_never_raises(tmp_path, monkeypatch, log_messages, english):
    path = str(tmp_path / "cty.dat")
    write_cache(path, dat=DAT)

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(CtyDatabase, "from_files", broken)
    clear_cty_cache()
    assert load_cached_cty(path) is None
    assert ("cty.dat could not be loaded: disk on fire", "HamQ", MSG_WARNING) in [
        (message, tag, level) for message, tag, level in log_messages
    ]
    clear_cty_cache()


def test_load_cached_cty_default_path(clean_settings):
    path = cty_cache_path()
    write_cache(path, dat=DAT)
    try:
        clear_cty_cache()
        database = load_cached_cty()
        assert database is not None and len(database) == ENTITIES
        manager = CtyManager()
        assert manager.database() is database
        manager.cleanup()
    finally:
        for name in (path, csv_path_for(path)):
            if os.path.exists(name):
                os.remove(name)
        clear_cty_cache()
    assert load_cached_cty() is None


def test_load_cached_cty_from_many_threads(tmp_path, count_parses):
    path = str(tmp_path / "cty.dat")
    write_cache(path, dat=DAT)
    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        results.append(load_cached_cty(path))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(results) == 8
    assert results[0] is not None
    assert all(result is results[0] for result in results)
    assert len(count_parses) == 1  # the others waited for the first parse


def test_cty_files_are_not_bundled():
    package = Path(cty_download.__file__).resolve().parents[1]
    bundled = [
        str(path)
        for path in package.rglob("*")
        if path.name.lower() in ("cty.dat", "cty.csv") or path.suffix == ".part"
    ]
    assert bundled == []
