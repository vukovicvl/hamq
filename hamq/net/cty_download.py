"""Download and local cache of the AD1C country files (cty.dat, cty.csv) for DXCC lookups.

The country files by Jim Reisert AD1C (https://www.country-files.com) are never bundled with
HamQ. :class:`CtyManager` downloads them on request and keeps them in the QGIS profile:
``settings.cty_cache_path()`` (``<profile>/hamq/cty.dat``) and ``cty.csv`` next to it.

A download (:meth:`CtyManager.download`):

1. requests both files at once through ``QgsNetworkAccessManager.instance()``, so the QGIS
   proxy and SSL settings apply; the QGIS network cache is bypassed. Nothing blocks: the
   replies arrive through the Qt event loop;
2. is aborted when no data arrived for ``TIMEOUT_MS`` or a file is larger than
   ``MAX_BYTES``; :meth:`CtyManager.cancel` aborts it at once;
3. checks the files in a background thread: cty.dat must contain at least
   ``MIN_ENTITIES`` entities and cty.csv must give a DXCC code to at least as many (an
   HTML error page, a truncated file or an unrelated csv fail the check);
4. only then writes both to temporary files next to the cache and moves them over it
   with ``os.replace``. The parsed database becomes the cached one at once and the date
   (UTC, ISO 8601) is stored in ``HamQSettings.cty_downloaded``.

Every download that starts ends with exactly one ``downloadFinished(ok, message)`` with a
translated message. On any failure the cached files stay as they were. When the server
cannot be reached (no internet: host not found, refused, timed out) the message says so in
plain words; Qt's English error text goes to the HamQ log only.

:func:`load_cached_cty` reads the cache without network access and without Qt objects, so
Processing algorithms and ``QgsTask`` workers can call it. The parsed database is kept per
cache path and reused while the files do not change.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from qgis.core import QgsMessageLog, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..core.cty import DEFAULT_CTY_CSV_URL, DEFAULT_CTY_URL, CtyDatabase
from ..core.i18n import tr
from ..qgis_io.compat import (
    MSG_INFO,
    MSG_SUCCESS,
    MSG_WARNING,
    NET_ATTR_CACHE_LOAD_CONTROL,
    NET_ATTR_CACHE_SAVE_CONTROL,
    NET_ATTR_HTTP_STATUS,
    NET_ATTR_REDIRECT_POLICY,
    NET_CACHE_ALWAYS_NETWORK,
    NET_CONNECTION_REFUSED,
    NET_HOST_NOT_FOUND,
    NET_NETWORK_SESSION_FAILED,
    NET_NO_ERROR,
    NET_OPERATION_CANCELED,
    NET_REDIRECT_NO_LESS_SAFE,
    NET_TEMPORARY_NETWORK_FAILURE,
    NET_TIMEOUT,
    NET_UNKNOWN_NETWORK_ERROR,
)
from ..settings import HamQSettings, cty_cache_path

__all__ = ["CtyManager", "clear_cty_cache", "csv_path_for", "load_cached_cty"]

_LOG_TAG = "HamQ"
_DAT = "cty.dat"
_CSV = "cty.csv"
# Release marker of the country files: "=VER20260915" (an exact call under Canada).
_VERSION_RE = re.compile(r"=VER(\d{4})(\d{2})(\d{2})\b")
# QNetworkReply errors that mean the server cannot be reached (no internet, server down).
_NO_CONNECTION = (
    NET_CONNECTION_REFUSED,
    NET_HOST_NOT_FOUND,
    NET_TIMEOUT,
    NET_TEMPORARY_NETWORK_FAILURE,
    NET_NETWORK_SESSION_FAILED,
    NET_UNKNOWN_NETWORK_ERROR,
)

# Parsed databases by cache path: {key: (signature of the files, database or None)}. The UI
# thread and worker threads may load at the same time: the lock makes the second caller wait
# for the first parse instead of parsing again. Nothing is logged while the lock is held.
_cache: dict[str, tuple[Any, CtyDatabase | None]] = {}
_cache_lock = threading.Lock()


def _log(message: str, level: Any) -> None:
    QgsMessageLog.logMessage(message, _LOG_TAG, level)


# --------------------------------------------------------------------------- cached loader


def csv_path_for(dat_path: str) -> str:
    """cty.csv next to ``dat_path``: ``.../cty.dat`` -> ``.../cty.csv``."""
    return os.path.splitext(dat_path)[0] + ".csv"


def _cache_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _stat(path: str) -> tuple[int, int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def _signature(dat_path: str, csv_path: str) -> tuple[Any, Any] | None:
    """Identity of the cached files; ``None`` when there is no cty.dat."""
    dat = _stat(dat_path)
    return None if dat is None else (dat, _stat(csv_path))


def load_cached_cty(path: str | None = None) -> CtyDatabase | None:
    """Return the database parsed from the cached cty.dat (and cty.csv), or ``None``.

    ``path`` is the cty.dat file, by default ``settings.cty_cache_path()``; cty.csv next to
    it adds the ADIF DXCC codes when it exists. No network access and no Qt objects: safe in
    Processing algorithms and ``QgsTask`` workers. The database is parsed once per path and
    returned again while neither file changes; a download through :class:`CtyManager`
    replaces it at once. ``None`` when there is no cty.dat or when it cannot be read or
    contains no entity (logged once per file version). Never raises.
    """
    messages: list[tuple[str, Any]] = []
    try:
        database = _load(path or cty_cache_path(), messages)
    except Exception as exc:  # never raise into worker threads or slots
        database = None
        messages.append((tr("cty.dat could not be loaded: {error}").format(error=exc), MSG_WARNING))
    for message, level in messages:
        _log(message, level)
    return database


def clear_cty_cache() -> None:
    """Forget every parsed database: the next :func:`load_cached_cty` parses the files again."""
    with _cache_lock:
        _cache.clear()


def _load(path: str, messages: list[tuple[str, Any]]) -> CtyDatabase | None:
    dat_path = os.path.abspath(path)
    csv_path = csv_path_for(dat_path)
    key = _cache_key(dat_path)
    with _cache_lock:
        signature = _signature(dat_path, csv_path)
        if signature is None:
            _cache.pop(key, None)
            return None
        cached = _cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        has_csv = signature[1] is not None
        database = _parse_files(dat_path, csv_path if has_csv else None, messages)
        _cache[key] = (signature, database)
        return database


def _parse_files(
    dat_path: str, csv_path: str | None, messages: list[tuple[str, Any]]
) -> CtyDatabase | None:
    try:
        database = CtyDatabase.from_files(dat_path, csv_path)
    except OSError as exc:
        messages.append((tr("cty.dat could not be read: {error}").format(error=exc), MSG_WARNING))
        return None
    if not len(database):
        messages.append(
            (tr("The saved cty.dat contains no entities; download it again"), MSG_WARNING)
        )
        return None
    if database.warnings:
        messages.append(
            (
                tr("cty.dat was loaded with warnings ({count}), the first one: {warning}").format(
                    count=len(database.warnings), warning=database.warnings[0]
                ),
                MSG_WARNING,
            )
        )
    return database


# --------------------------------------------------------------------------- check and save


@dataclass
class _Outcome:
    """Result of checking and saving a download (built in the worker thread, no Qt)."""

    ok: bool
    problem: str = ""  # "dat", "csv" or "save" when not ok
    entities: int = 0
    with_code: int = 0
    version: str = ""
    error: str = ""


def _decode(data: bytes) -> str:
    """Text of a downloaded file, decoded as ``CtyDatabase.from_files`` decodes a file."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _release(dat_text: str) -> str:
    """Release date of the files (``=VER20260915`` -> ``2026-09-15``), ``""`` without one."""
    found = _VERSION_RE.search(dat_text)
    return "-".join(found.groups()) if found else ""


def _install(dat: bytes, csv: bytes, dat_path: str, minimum: int) -> _Outcome:
    """Check a download and, when it is valid, move it over the cache (worker thread)."""
    dat_text = _decode(dat)
    database = CtyDatabase.from_text(dat_text, _decode(csv))
    entities = len(database)
    if entities < minimum:
        return _Outcome(False, "dat", entities=entities)
    with_code = sum(1 for entity in database.entities if entity.dxcc is not None)
    if with_code < minimum:
        return _Outcome(False, "csv", entities=entities, with_code=with_code)
    try:
        _replace_files(dat_path, dat, csv, database)
    except OSError as exc:
        return _Outcome(False, "save", error=str(exc))
    return _Outcome(True, entities=entities, with_code=with_code, version=_release(dat_text))


def _replace_files(dat_path: str, dat: bytes, csv: bytes, database: CtyDatabase) -> None:
    """Write both files next to the cache first, then move them over it and cache ``database``.

    The old files stay when anything fails before the first ``os.replace``; temporary files
    are always removed.
    """
    csv_path = csv_path_for(dat_path)
    os.makedirs(os.path.dirname(dat_path), exist_ok=True)
    temporary: list[str] = []
    try:
        new_dat = _write_temporary(dat_path, dat)
        temporary.append(new_dat)
        new_csv = _write_temporary(csv_path, csv)
        temporary.append(new_csv)
        with _cache_lock:
            os.replace(new_dat, dat_path)
            temporary.remove(new_dat)
            os.replace(new_csv, csv_path)
            temporary.remove(new_csv)
            _cache[_cache_key(dat_path)] = (_signature(dat_path, csv_path), database)
    finally:
        for name in temporary:
            with suppress(OSError):
                os.remove(name)


def _write_temporary(target: str, data: bytes) -> str:
    """Write ``data`` to a new hidden file next to ``target`` and return its path."""
    directory, name = os.path.split(target)
    handle, path = tempfile.mkstemp(prefix=f".{name}.", suffix=".part", dir=directory)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(OSError):
            os.remove(path)
        raise
    return path


class _Job:
    """Runs ``function`` in a daemon thread; the UI thread polls :attr:`done`.

    The thread touches no Qt object, so nothing can go wrong when the plugin unloads while
    it runs: its result is simply not read.
    """

    def __init__(self, function: Callable[[], Any], name: str) -> None:
        self.result: Any = None
        self.error: Exception | None = None
        self.done = threading.Event()
        self._function = function
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            self.result = self._function()
        except Exception as exc:
            self.error = exc
        finally:
            self.done.set()


# --------------------------------------------------------------------------- manager


class CtyManager(QObject):
    """Downloads cty.dat and cty.csv into the local cache and serves the parsed database.

    ``cache_path`` is the cty.dat file (cty.csv is kept next to it), by default
    ``settings.cty_cache_path()``; ``dat_url`` and ``csv_url`` default to the AD1C Big CTY
    files. Call :meth:`cleanup` before the object goes away (plugin unload). All methods are
    for the UI thread; :func:`load_cached_cty` is the thread-safe reader.
    """

    #: ``(ok, message)``: a download ended; the message is translated.
    downloadFinished = pyqtSignal(bool, str)

    #: A download replaces the cache only if cty.dat has at least this many entities and
    #: cty.csv gives a DXCC code to at least as many (the Big CTY of 15 September 2026 has
    #: 346 entities, all with a code).
    MIN_ENTITIES = 300
    #: Abort the transfer when no data arrived for this long (milliseconds).
    TIMEOUT_MS = 30_000
    #: Abort when a file is larger than this (bytes). Each file has about 300 KB.
    MAX_BYTES = 8 * 1024 * 1024
    #: How often the UI thread looks for the result of the background check (milliseconds).
    POLL_MS = 20

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        cache_path: str | None = None,
        dat_url: str = DEFAULT_CTY_URL,
        csv_url: str = DEFAULT_CTY_CSV_URL,
    ) -> None:
        super().__init__(parent)
        self._cache_path = os.path.abspath(cache_path) if cache_path else ""
        self._urls = ((_DAT, dat_url), (_CSV, csv_url))
        self._replies: dict[str, QNetworkReply] = {}
        self._data: dict[str, bytes] = {}
        self._job: _Job | None = None
        self._preload: _Job | None = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    # ------------------------------------------------------------------ public API

    def cache_path(self) -> str:
        """The cached cty.dat file (cty.csv is next to it)."""
        return self._cache_path or cty_cache_path()

    def database(self) -> CtyDatabase | None:
        """The parsed cache, ``None`` when nothing was downloaded (see :func:`load_cached_cty`).

        The first call after start-up or after the files changed parses them (about 0.1 s)
        unless :meth:`preload` has done that in the background.
        """
        return load_cached_cty(self.cache_path())

    def is_available(self) -> bool:
        """True when a downloaded cty.dat is in the cache (the file is not parsed here)."""
        path = self.cache_path()
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def is_downloading(self) -> bool:
        """True from :meth:`download` until ``downloadFinished`` is emitted."""
        return bool(self._replies) or self._job is not None

    def download(self) -> None:
        """Download cty.dat and cty.csv in the background and replace the cache when valid.

        Returns at once; ``downloadFinished`` reports the result. Ignored (nothing emitted)
        while a download is running.
        """
        if self.is_downloading():
            _log(tr("A cty.dat download is already running"), MSG_INFO)
            return
        try:
            self._start_transfer()
        except Exception as exc:  # e.g. no network access manager: report, never raise
            self._abort_transfer()
            self._finish(False, self._failure(tr("unexpected error: {error}").format(error=exc)))

    def cancel(self) -> None:
        """Abort a running transfer; ``downloadFinished(False, ...)`` is emitted at once.

        Does nothing when no transfer is running, and during the short check that follows a
        complete transfer (its result is then reported as usual).
        """
        if not self._replies:
            return
        try:
            self._abort_transfer()
        except Exception as exc:  # never raise into a button slot; still report the end
            _log(tr("unexpected error: {error}").format(error=exc), MSG_WARNING)
        self._finish(False, self._with_cache_note(tr("cty.dat download canceled.")))

    def preload(self) -> None:
        """Parse the cache in a background thread (optional).

        Afterwards the first :meth:`database` call in the UI thread returns at once. Does
        nothing without a cache or while a preload is running.
        """
        if self._preload is not None or not self.is_available():
            return
        path = self.cache_path()
        job = _Job(lambda: load_cached_cty(path), "HamQ cty.dat load")
        try:
            job.start()
        except RuntimeError as exc:  # no thread available: database() parses on first use
            _log(tr("cty.dat could not be loaded: {error}").format(error=exc), MSG_WARNING)
            return
        self._preload = job
        self._poll.start()

    def cleanup(self) -> None:
        """Abort a transfer and stop the timers without emitting ``downloadFinished``.

        Safe to call more than once and after the Qt object was deleted; never raises. The
        manager can be used again afterwards. A check that already runs in a background
        thread still finishes: it only ever saves a valid download, and its result is no
        longer reported.
        """
        try:
            self._abort_transfer()
        except Exception as exc:  # an unload step or a slot: never raise
            _log(tr("unexpected error: {error}").format(error=exc), MSG_WARNING)
        self._job = None
        self._preload = None
        with suppress(RuntimeError):
            self._poll.stop()

    # ------------------------------------------------------------------ transfer

    def _start_transfer(self) -> None:
        self._data = {}
        manager = QgsNetworkAccessManager.instance()
        for name, url in self._urls:
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(NET_ATTR_REDIRECT_POLICY, NET_REDIRECT_NO_LESS_SAFE)
            request.setAttribute(NET_ATTR_CACHE_LOAD_CONTROL, NET_CACHE_ALWAYS_NETWORK)
            request.setAttribute(NET_ATTR_CACHE_SAVE_CONTROL, False)
            reply = manager.get(request)
            self._replies[name] = reply
            reply.finished.connect(self._on_reply_finished)
            reply.downloadProgress.connect(self._on_progress)
        self._timeout.start(self.TIMEOUT_MS)
        urls = ", ".join(url for _name, url in self._urls)
        _log(tr("Downloading cty.dat and cty.csv: {urls}").format(urls=urls), MSG_INFO)

    def _on_progress(self, received: int, total: int) -> None:
        try:
            if not self._replies:
                return
            if max(received, total) > self.MAX_BYTES:
                self._fail(self._too_large())
                return
            self._timeout.start(self.TIMEOUT_MS)  # restart: the timeout is for inactivity
        except Exception as exc:
            self._unexpected(exc)

    def _on_timeout(self) -> None:
        try:
            if self._replies:
                seconds = f"{self.TIMEOUT_MS / 1000:g}"
                self._fail(tr("no data from the server for {seconds} s").format(seconds=seconds))
        except Exception as exc:
            self._unexpected(exc)

    def _on_reply_finished(self) -> None:
        try:
            self._collect_replies()
        except Exception as exc:
            self._unexpected(exc)

    def _collect_replies(self) -> None:
        for name, reply in list(self._replies.items()):
            if not reply.isFinished():
                continue
            del self._replies[name]
            self._detach(reply)
            problem = self._reply_problem(name, reply)
            if problem is None:
                data = bytes(reply.readAll())
                if len(data) > self.MAX_BYTES:
                    problem = self._too_large()
                else:
                    self._data[name] = data
            reply.deleteLater()
            if problem is not None:
                self._fail(problem)
                return
        if not self._replies and len(self._data) == len(self._urls):
            self._timeout.stop()
            self._start_check()

    def _reply_problem(self, name: str, reply: QNetworkReply) -> str | None:
        """Why a finished reply is unusable (translated), ``None`` when it is fine."""
        error = reply.error()
        status = reply.attribute(NET_ATTR_HTTP_STATUS)
        if error == NET_NO_ERROR:
            if status is None or status == 200:  # no status: not HTTP (file://, tests)
                return None
            return tr("{file}: unexpected HTTP status {status}").format(file=name, status=status)
        if isinstance(status, int) and status >= 400:
            return tr("{file}: the server answered with HTTP status {status}").format(
                file=name, status=status
            )
        if error == NET_OPERATION_CANCELED:
            # HamQ disconnects before it aborts, so QGIS aborted this one (network timeout)
            return tr("{file}: the request was aborted by the network timeout of QGIS").format(
                file=name
            )
        if error in _NO_CONNECTION:
            # Say what to do; Qt's text (English, e.g. "Host ... not found") is for the log.
            _log(f"{name}: {reply.errorString()}", MSG_INFO)
            host = reply.url().host() or reply.request().url().host() or name
            return tr(
                "the server {host} cannot be reached; check the internet connection or try "
                "again later"
            ).format(host=host)
        return f"{name}: {reply.errorString()}"

    def _too_large(self) -> str:
        size = max(1, self.MAX_BYTES // (1024 * 1024))
        return tr("a file is larger than {size} MB").format(size=size)

    def _detach(self, reply: QNetworkReply) -> None:
        with suppress(RuntimeError):  # the reply may already be deleted
            for signal, slot in (
                (reply.finished, self._on_reply_finished),
                (reply.downloadProgress, self._on_progress),
            ):
                with suppress(TypeError):
                    signal.disconnect(slot)

    def _abort_transfer(self) -> None:
        """Abort the requests silently: each reply is disconnected before ``abort()``, which
        emits ``finished`` synchronously, so nothing re-enters this object."""
        with suppress(RuntimeError):
            self._timeout.stop()
        replies, self._replies = self._replies, {}
        self._data = {}
        for reply in replies.values():
            self._detach(reply)
            with suppress(RuntimeError):
                reply.abort()
                reply.deleteLater()

    def _fail(self, reason: str) -> None:
        self._abort_transfer()
        self._finish(False, self._failure(reason))

    def _unexpected(self, exc: Exception) -> None:
        """An error in HamQ code inside a slot: end the download (if any) instead of raising."""
        reason = tr("unexpected error: {error}").format(error=exc)
        if self.is_downloading():
            self._abort_transfer()
            self._job = None
            self._finish(False, self._failure(reason))
        else:
            _log(reason, MSG_WARNING)

    # ------------------------------------------------------------------ check and result

    def _start_check(self) -> None:
        dat, csv = self._data[_DAT], self._data[_CSV]
        self._data = {}
        path, minimum = self.cache_path(), self.MIN_ENTITIES
        job = _Job(lambda: _install(dat, csv, path, minimum), "HamQ cty.dat check")
        self._job = job  # set first: if start() fails, _unexpected() still reports the end
        job.start()
        self._poll.start()

    def _on_poll(self) -> None:
        try:
            if self._preload is not None and self._preload.done.is_set():
                self._preload = None  # load_cached_cty() logs its own problems
            job = self._job
            if job is not None and job.done.is_set():
                self._job = None
                self._report(job)
            if self._job is None and self._preload is None:
                self._poll.stop()
        except Exception as exc:
            self._unexpected(exc)

    def _report(self, job: _Job) -> None:
        """Emit the one ``downloadFinished`` of a download whose check has ended."""
        try:
            ok, message = self._outcome_message(job)
        except Exception as exc:
            ok, message = False, self._failure(tr("unexpected error: {error}").format(error=exc))
        self._finish(ok, message)

    def _outcome_message(self, job: _Job) -> tuple[bool, str]:
        outcome = job.result
        if job.error is not None or not isinstance(outcome, _Outcome):
            error = job.error if job.error is not None else outcome
            return False, self._failure(tr("unexpected error: {error}").format(error=error))
        if not outcome.ok:
            return False, self._failure(self._check_problem(outcome))
        try:
            HamQSettings().cty_downloaded = datetime.now(timezone.utc).date().isoformat()
        except Exception as exc:  # the files are saved; only the date is missing
            _log(tr("unexpected error: {error}").format(error=exc), MSG_WARNING)
        if outcome.version:
            message = tr("cty.dat downloaded (version {version}), entities: {count}").format(
                version=outcome.version, count=outcome.entities
            )
        else:
            message = tr("cty.dat downloaded, entities: {count}").format(count=outcome.entities)
        return True, message

    def _check_problem(self, outcome: _Outcome) -> str:
        if outcome.problem == "dat":
            return tr(
                "the downloaded cty.dat is not valid (entities: {count}, at least {minimum} "
                "expected)"
            ).format(count=outcome.entities, minimum=self.MIN_ENTITIES)
        if outcome.problem == "csv":
            return tr(
                "the downloaded cty.csv does not match cty.dat (DXCC codes for {count} of "
                "{total} entities)"
            ).format(count=outcome.with_code, total=outcome.entities)
        return tr("the files could not be saved: {error}").format(error=outcome.error)

    def _failure(self, reason: str) -> str:
        message = tr("cty.dat download failed: {reason}.").format(reason=reason.rstrip(". "))
        return self._with_cache_note(message)

    def _with_cache_note(self, message: str) -> str:
        if self.is_available():
            return message + " " + tr("The previously downloaded files are still used.")
        return message

    def _finish(self, ok: bool, message: str) -> None:
        _log(message, MSG_SUCCESS if ok else MSG_WARNING)
        with suppress(RuntimeError):  # the Qt object was deleted: the log has the message
            self.downloadFinished.emit(ok, message)
