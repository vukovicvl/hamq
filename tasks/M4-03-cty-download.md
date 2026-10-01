# M4-03: cty.dat download manager and cached loader

**Milestone:** M4
**Status:** done
**Skills:** pyqgis-plugin, dxcc-cty

## Goal
Download the AD1C country files (cty.dat and cty.csv) in the background through the QGIS
network manager, check them, replace the local cache atomically, and serve the parsed
`CtyDatabase` to the UI thread (`CtyManager`) and to worker threads (`load_cached_cty`),
as specified in `docs/ARCHITECTURE.md` under "net". cty.dat is never bundled.

## Scope
- `hamq/net/cty_download.py`
- `tests/qgis/test_cty_download.py`
- `hamq/i18n/sr_Latn/net_cty_download.json`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: only the appended block
  "M4-03 / M5-02" (shared with M5-02)
- `tasks/M4-03-cty-download.md`

## Out of scope
- Wiring into the plugin and controller: creating the manager, calling `preload()` at
  start-up and `cleanup()` on unload, and a "Refresh cty.dat" action. This belongs to the
  controller and plugin owner.
- The settings dialog's "Download now" button (M0-03). It already uses `download()`,
  `downloadFinished`, `is_available()` and `HamQSettings.cty_downloaded`.
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md` (not mine in this run).

## Checklist
- [x] `CtyManager(QObject)` with the contract API: `downloadFinished(bool, str)`,
      `database()`, `is_available()`, `download()`
- [x] Cache at `settings.cty_cache_path()`, with cty.csv next to it; the URLs are
      `core.cty.DEFAULT_CTY_URL` / `DEFAULT_CTY_CSV_URL`
- [x] Async download through `QgsNetworkAccessManager.instance()` (QGIS proxy and SSL
      settings apply). Redirects are followed unless they downgrade from HTTPS. The QGIS
      network cache is bypassed and not filled.
- [x] Check before replacing the cache: cty.dat must have at least `MIN_ENTITIES` entities,
      and cty.csv must give a DXCC code to at least as many. `MIN_ENTITIES` is a class
      attribute, and tests lower it on the instance.
- [x] Atomic replace: temporary files next to the cache, `fsync`, `os.replace`. The old
      cache stays on every failure, and temporary files are always removed.
- [x] Download date (UTC, ISO 8601) stored in `HamQSettings.cty_downloaded` before
      `downloadFinished(True, ...)`
- [x] Exactly one `downloadFinished(ok, translated message)` per started download
- [x] Inactivity timeout (`TIMEOUT_MS = 30_000`, a QTimer that aborts), size limit
      (`MAX_BYTES`), `cancel()`, silent `cleanup()` that is safe to call twice
- [x] Never blocks: the check (parse) runs in a background thread that touches no Qt object
- [x] Never raises into Qt: every slot is wrapped; `download()`, `cancel()` and `cleanup()`
      also work after the Qt object was deleted
- [x] `load_cached_cty()`: module-level cached loader, lock-protected, no QObject, no
      network. The parsed database is replaced at once after a download and reloaded when
      the files change on disk.
- [x] Translated messages and the catalog `net_cty_download.json` (20 strings, glossary)
- [x] Compat constants `NET_ATTR_CACHE_LOAD_CONTROL`, `NET_ATTR_CACHE_SAVE_CONTROL`,
      `NET_CACHE_ALWAYS_NETWORK` with a test
- [x] Tests against a local `http.server` thread serving the fixture excerpt
- [x] Self-review (contract, AGENTS.md, skills, Qt5/Qt6, 3.34 API, leaks, slots, strings)

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes (includes the i18n catalog guard)
- [x] `ruff check` and `ruff format --check` pass for the files in scope
- [x] Download success, HTTP 404 (old cache kept), garbage content (old cache kept) and
      timeout tests pass on QGIS 4.2, 3.44, 4.0 and 3.34
- [x] A real download from country-files.com through `QgsNetworkAccessManager` works
      (scratch check, see Result)

## Result

### What changed
- `hamq/net/cty_download.py` (new): `CtyManager`, `load_cached_cty`, plus
  `clear_cty_cache` and `csv_path_for`. The contract names and signatures are unchanged.
  The additions are listed below.
- `tests/qgis/test_cty_download.py` (new): 30 tests.
- `hamq/i18n/sr_Latn/net_cty_download.json` (new): 20 strings. The Cyrillic
  transliteration was checked by eye (`cty.dat`, `cty.csv`, `HTTP`, `MB` are kept).
- `hamq/qgis_io/compat.py` / `tests/qgis/test_compat.py`: block "M4-03 / M5-02"
  (`NET_ATTR_CACHE_LOAD_CONTROL`, `NET_ATTR_CACHE_SAVE_CONTROL`, `NET_CACHE_ALWAYS_NETWORK`,
  plus the M5-02 `NETIF_*` flags) and the tests `test_network_cache_attributes` and
  `test_network_interface_flags`.

### Behaviour the controller / GUI can rely on
- `CtyManager(parent=None, *, cache_path=None, dat_url=DEFAULT_CTY_URL,
  csv_url=DEFAULT_CTY_CSV_URL)`. All methods are for the UI thread.
- `download()` returns at once (about 2.5 ms). While a download is running, a second call
  is ignored: it is logged and nothing is emitted, and the running download still emits its
  one `downloadFinished`. An unexpected error inside `download()` is reported through
  `downloadFinished(False, ...)` at once.
- Success message: `cty.dat downloaded (version 2026-09-15), entities: 346`. The version
  comes from the `=VERyyyymmdd` marker in the file; without one the message is
  `cty.dat downloaded, entities: N`.
- Failure message: `cty.dat download failed: <reason>.`, followed by
  `The previously downloaded files are still used.` when a cache exists. Reasons (all
  translated):
  - `<file>: the server answered with HTTP status 404`
  - `<file>: unexpected HTTP status <n>`
  - `<file>: <Qt error string>` (connection refused, DNS, TLS, ...)
  - `no data from the server for 30 s`
  - `a file is larger than 8 MB`
  - `the downloaded cty.dat is not valid (entities: N, at least 300 expected)`
  - `the downloaded cty.csv does not match cty.dat (DXCC codes for N of M entities)`
  - `the files could not be saved: <OSError>`
  - `unexpected error: <exception>`
- `cancel()` aborts the transfer and emits `downloadFinished(False, "cty.dat download
  canceled. ...")` at once. It does nothing when idle, and nothing during the short check
  that follows a complete transfer (that result is reported as usual).
- `cleanup()` aborts and stops the timers without emitting anything. It is safe to call
  twice and after the Qt object was deleted, and the manager can be used again afterwards.
  Register it with `plugin.add_cleanup(manager.cleanup)`.
- `database()` returns `load_cached_cty(cache_path())`. After a download it returns the
  database built by the check, without parsing again. The first call after start-up parses
  in the UI thread: 60-80 ms for the real Big CTY on the dev machine. Call `preload()` once
  at start-up to do that parse in a background thread.
- `is_available()` is true when a non-empty cty.dat exists. The file is not parsed.
- Extras: `is_downloading()`, `cache_path()`, `preload()`. Class attributes:
  `MIN_ENTITIES = 300`, `TIMEOUT_MS = 30_000` (inactivity; restarted by each progress
  signal), `MAX_BYTES = 8 MiB` per file, `POLL_MS = 20`.
- `load_cached_cty(path=None)` is safe in Processing algorithms and `QgsTask.run()`.
  - The database is cached per path. The key is `(mtime_ns, size, inode)` of cty.dat and
    cty.csv, so a cache replaced by another QGIS instance is parsed again.
  - Without cty.dat, or with a cty.dat that has no entities, it returns `None`. The
    problem is logged once per file version.
  - Without cty.csv the database has no DXCC codes.
  - It never raises. Concurrent first calls parse once; the other callers wait for that
    parse.
- Real data (Big CTY 2026-09-15): 346 entities, 346 of them with a DXCC code (6 WAE-only
  entities get their parent code), so `MIN_ENTITIES = 300` has a safe margin.

### Commands and outcomes
- `scripts/test_qgis.sh all -k "test_cty_download or test_wsjtx_listener or test_compat" -q -rs`
  (final run, after the self-review fixes):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2 (Qt6) | PASS: 232 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 231 passed, 1 skipped |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 231 passed, 1 skipped |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 231 passed, 1 skipped |

  The run covers 30 cty_download tests, 53 wsjtx_listener tests and all compat tests. The
  only skip is the M5-02 privileged-port test (in Docker, ports below 1024 can be bound
  without root). All cty_download tests ran on every target.
- `python3 -m pytest tests/core -q`: 2996 passed, 8 xfailed (other modules' documented
  xfails). In the last run, collection stopped at `tests/core/test_qso.py`. Another agent
  had written that test first, and its `hamq/core/qso.py` did not exist yet. With
  `--ignore=tests/core/test_qso.py` the result was the same 2996 passed, 8 xfailed, and
  `tests/core/test_i18n_catalog.py` passed 233 of 233.
- `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -k "cty_download or wsjtx_listener"`:
  14 passed (no unused catalog keys).
- `ruff check` / `ruff format --check` on the four files and compat / test_compat: clean.
- Real download, scratch script on host QGIS 4.2 with a throw-away profile and cache:
  `downloadFinished(True, "cty.dat downloaded (version 2026-09-15), entities: 346")`
  after 1.7 s. The cache folder held exactly `cty.csv` and `cty.dat`, `cty_downloaded` was
  `2026-10-01`, and `database()` returned 346 entities without parsing again.
  - The worst single event-loop pass during the transfer and check was 10 ms.
  - The first `get()` on a fresh `QNetworkAccessManager` takes about 150 ms. That is Qt's
    one-time SSL and proxy setup, which QGIS desktop has already done at start-up; the
    second and later downloads return in 2.5 ms.

### Manual checks still needed
- In QGIS desktop with a proxy configured (Settings > Options > Network), the download
  should go through the proxy.
- On Windows, a second QGIS instance holding cty.dat open makes `os.replace` fail. This
  should be reported as "the files could not be saved", and the old cache kept.
- Once the controller and settings dialog are wired: "Download now" in the dialog, and
  `preload()` at start-up.

## Notes
- Both files are required for a download to be accepted. cty.csv carries the ADIF DXCC
  codes; accepting a cty.dat without it would silently drop `dxcc` from new QSOs.
- The check uses a daemon `threading.Thread` polled by a 20 ms QTimer, not a `QgsTask`.
  The parse is short (about 0.1 s) and has no progress to show. The thread touches no Qt
  object and captures nothing of the `CtyManager`, so an unload during the check is
  harmless.
- If `cleanup()` runs during the check, the thread still installs a valid download, but
  `cty_downloaded` is not updated: there is no listener left to report to.
- cty.dat is replaced first, then cty.csv. If the second `os.replace` failed (same
  directory, so practically never), the new cty.dat stays with the old cty.csv. That pair
  still works for lookups, and the download is reported as failed.
- Temporary files (`.cty.dat.*.part`, hidden) are left only if the process dies between
  writing and replacing.
- `test_cty_files_are_not_bundled` fails if `cty.dat`, `cty.csv` or a `.part` file ever
  lands inside the `hamq/` package.
