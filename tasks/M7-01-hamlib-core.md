# M7-01: Hamlib core (rigctld / rotctld protocol)

**Milestone:** M7
**Status:** done
**Skills:** hamlib

## Goal
Pure-Python protocol layer for the Hamlib daemons `rigctld` / `rotctld`: command
builders, an incremental parser for extended (`+`) replies, reply helpers, translated
Hamlib error messages and the compass bearing -> rotator azimuth mapping, exactly as
in `docs/ARCHITECTURE.md` "core/hamlib.py".

## Scope
- `hamq/core/hamlib.py`
- `tests/core/test_hamlib.py`
- `hamq/i18n/sr_Latn/core_hamlib.json`
- `tasks/M7-01-hamlib-core.md`

## Out of scope
- `hamq/net/hamlib_client.py` (QTcpSocket client, queue, timeouts, reconnect)
- GUI: Radio tab, rotator map tool, manual QSO dialog
- `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `PLAN.md`, `pyproject.toml` (not mine in this run)

## Checklist
- [x] Tests first (`tests/core/test_hamlib.py` failed on import before the module existed)
- [x] `HamlibResponse` (+ `ok`) and `ResponseParser.feed()` / `reset()`: bytes or str,
      UTF-8 with replacement (also split multi-byte sequences), partial lines, CRLF,
      blank lines, stray `RPRT`, never raises, bounded memory
- [x] Builders `cmd_get_freq`, `cmd_set_freq`, `cmd_get_mode`, `cmd_set_mode`,
      `cmd_get_pos`, `cmd_set_pos`, `cmd_stop`; degrees with 1..2 decimals, half up
- [x] `expected_command()` for every builder (plus the long form `+\name`)
- [x] `parse_freq`, `parse_mode`, `parse_pos`
- [x] `error_message()` for every code of `enum rig_errcode_e` (0..22), checked in the
      Hamlib source; generic message with the code for unknown codes
- [x] `MODES` checked against `rig_parse_mode()` (`src/misc.c`, `mode_str[]`) and a live daemon
- [x] `rotator_target()` for 0..360, 0..450, -180..180, 180..540, 0..180, normalization,
      closest to the current azimuth, edges, brute-force cross-check
- [x] Serbian (Latin) catalog `hamq/i18n/sr_Latn/core_hamlib.json`, glossary terms
- [x] Python 3.9 run in Docker
- [x] Real-daemon check against `hamq/hamlib-dummy`

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` and `ruff format --check` pass for the files in scope
- [x] Every captured reply in ARCHITECTURE.md parses: whole, byte by byte, at every
      split point, with CRLF, with blank lines, all concatenated
- [x] Works against real `rigctld -m 1` / `rotctld -m 1` (47/47 checks)

## Result

### What changed
- `hamq/core/hamlib.py` (new): the full contract API. No names or signatures differ
  from the contract. Additions are private only (`_MAX_LINE = 4096`, `_MAX_FIELDS = 256`,
  helpers, and `ResponseParser._buffered_chars()` for the memory test).
- `tests/core/test_hamlib.py` (new): 460 tests.
- `hamq/i18n/sr_Latn/core_hamlib.json` (new): 24 strings (23 error texts + the generic one).

### Behaviour the net / GUI code can rely on
- `ResponseParser`:
  - A header line (`^[a-z][a-z0-9_]*:`) always starts a new reply and drops an unfinished
    one. rigctld prints the header and then no `RPRT` when the rig returns `-RIG_EIO`
    (it tries to reopen the rig instead; `tests/rigctl_parse.c`, `tests/rigctld.c`).
  - `Key: value` lines become fields. The key starts with a letter; the value may be
    empty or contain `:`.
  - `RPRT n` ends the reply. Without a reply in progress it becomes a reply with
    command `""`.
  - Every other line is ignored, e.g. the mode list that `+M ?` prints.
  - Lines over 4096 characters are dropped whole, including an `RPRT` at their end.
  - At most 256 fields are kept per reply; an existing key is still overwritten.
- Suggested use in the client: after each command, accept the first reply whose `command`
  is `expected_command(cmd)`. Treat anything else (including `""`) as stray. On a
  timeout, call `reset()`.
- `cmd_set_mode` is strict: the mode must be exactly one of `MODES`. The live check shows
  why this matters: rigctld 4.6 answers `RPRT 0` to `+M usb 0` or `+M DD 0` and leaves
  the mode unchanged. Passband `-1` (`RIG_PASSBAND_NOCHANGE`) is allowed; below -1 raises
  `ValueError`.
- `parse_mode` returns `MODES` names. Hamlib 4.6 reports PKTFM as `FM-D` and PKTAM as
  `AM-D` (the first alias in its table); these, plus `USB-D`, `LSB-D`, `CW-R` and
  `RTTY-R`, are mapped back to the canonical names. Matching is case-insensitive. An
  unknown mode is returned as reported (not `None`).
- `parse_freq` returns `None` for a value <= 0. The dummy accepts `+F 0` and then
  reports `Frequency: 0`. `14074000.000000` and a decimal comma are accepted.
- `cmd_set_freq(7074000.4)` gives `'+F 7074000\n'` (rounded to Hz).
- Builders raise `ValueError` for hz <= 0, non-finite numbers and unknown modes. They
  raise `TypeError` for non-numbers and `bool`.
- `rotator_target`:
  - With `current_az=None` it picks the equivalent closest to the plain 0..360 bearing.
  - A tie goes to the less wound equivalent.
  - `min_az > max_az` returns `None`. Non-finite arguments raise `ValueError`.
  - It is O(1): huge ranges do not loop.
- `error_message` ignores the sign, like Hamlib's `rigerror()`. Unknown codes give
  `"Unknown Hamlib error (code {code})"`.
- `expected_command` returns `""` for unknown commands.

### Commands and outcomes
```
python3 -m pytest -p no:cacheprovider tests/core/test_hamlib.py -q     -> 460 passed (host Python 3.14)
docker run --rm -v .../hamq:/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 python:3.9-slim \
  sh -c "pip install -q pytest && python -m pytest -p no:cacheprovider tests/core/test_hamlib.py -q"
                                                                        -> 460 passed (Python 3.9)
python3 -m pytest -p no:cacheprovider tests/core -q                    -> 1272 passed (whole core suite at that moment)
ruff check hamq/core/hamlib.py tests/core/test_hamlib.py               -> All checks passed!
ruff format --check hamq/core/hamlib.py tests/core/test_hamlib.py      -> 2 files already formatted
python3 <scratchpad>/real_daemon_check.py                              -> RESULT: 47 passed, 0 failed
```

### Real-daemon check
A scratch script (not in the repo) did the following:
- Started `hamq/hamlib-dummy` as a uniquely named container (`hamq-m7-core-check-<ts>`)
  on free `127.0.0.1` ports.
- Sent every builder's command over a plain Python socket.
- Fed the replies to `ResponseParser` in random 1..7 byte pieces and checked the results
  with `expected_command` / `parse_*` / `error_message` / `rotator_target`.
- Removed the container with `docker rm -f`; `docker ps -a` shows nothing left.

It was run twice, the second time on the final code; both runs gave 47/47. The
transcript is condensed below (per-command "single reply with header" checks left out):

```
container: rigctld Hamlib 4.6.2 2025-02-09T21:03:50Z SHA=870364; rotctl(d), Hamlib 4.6.2
== rigctld -m 1 ==
rig >>> '+f\n'            <<< b'get_freq:\nFrequency: 145000000\nRPRT 0\n'   [OK] get_freq ok -- freq=145000000
rig >>> '+F 14074000\n'   <<< b'set_freq: 14074000\nRPRT 0\n'                [OK] set_freq echo -- 14074000
rig >>> '+f\n'            <<< b'get_freq:\nFrequency: 14074000\nRPRT 0\n'    [OK] parse_freq == 14074000
                                                                             [OK] cmd_set_freq(7074000.4) -> '+F 7074000\n'
rig >>> '+F 7074000\n'    <<< b'set_freq: 7074000\nRPRT 0\n'                 [OK] set_freq ok
rig >>> '+f\n'            <<< b'get_freq:\nFrequency: 7074000\nRPRT 0\n'     [OK] parse_freq == 7074000
rig >>> '+m\n'            <<< b'get_mode:\nMode: FM\nPassband: 15000\nRPRT 0\n'   [OK] ('FM', 15000)
rig >>> '+M USB 2400\n'   <<< b'set_mode: USB 2400\nRPRT 0\n'                [OK] set_mode echo -- USB 2400
rig >>> '+m\n'            <<< b'get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n'   [OK] ('USB', 2400)
rig >>> '+M PKTFM 0\n'    <<< b'set_mode: PKTFM 0\nRPRT 0\n'
rig >>> '+m\n'            <<< b'get_mode:\nMode: FM-D\nPassband: 2400\nRPRT 0\n'  [OK] FM-D mapped back to PKTFM
-- set/get every mode in MODES (38), sentinel mode in between --
   daemon spelled differently: {'PKTFM': 'FM-D', 'PKTAM': 'AM-D'}           [OK] all 38 MODES accepted and read back
rig >>> '+M usb 0\n'      <<< b'set_mode: usb 0\nRPRT 0\n'
rig >>> '+m\n'            <<< b'get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n'   [OK] control: 'usb' -> RPRT 0, mode unchanged
rig >>> '+t\n'            <<< b'get_ptt:\nRPRT -11\n'                        [OK] RPRT -11 -> 'Feature not available'
rig >>> '+X_bogus\n'      <<< b''                                            [OK] no reply within 2 s (client must time out)
   (parser.reset(), as the client does on timeout)
rig >>> '+F abc\n'        <<< b'set_split_mode: bogus +F\nRPRT -1\n'  then  b'RPRT -18\nRPRT -1\n'
   => [('set_split_mode', 'bogus +F', -1), ('', '', -18), ('', '', -1)]      [OK] stray sequence parsed
rig >>> '+f\n'            <<< b'get_freq:\nFrequency: 7074000\nRPRT 0\n'     [OK] resync
                                                                             [OK] cmd_set_freq(0) -> ValueError
== rotctld -m 1 (range -180..450, el 0..90, moves ~6 deg/s) ==
rot >>> '+p\n'            <<< b'get_pos:\nAzimuth: 0.00\nElevation: 0.00\nRPRT 0\n'   [OK] (0.0, 0.0)
rot >>> '+P 123.5 10.0\n' <<< b'set_pos: 123.5 10.0\nRPRT 0\n'              [OK] echo '123.5 10.0'
   polling +p ... reached (123.5, 10.0) after 21.0 s                         [OK]
   rotator_target(300, -180, 450, current_az=123.5) == 300.0                 [OK]
rot >>> '+P 300.0 0.0\n'  <<< b'set_pos: 300.0 0.0\nRPRT 0\n'               [OK]
rot >>> '+S\n'            <<< b'stop:\nRPRT 0\n'                             [OK] stop ok
rot >>> '+p\n' (twice, 1 s apart) <<< b'get_pos:\nAzimuth: 132.50\nElevation: 1.00\nRPRT 0\n'  [OK] stopped
rot >>> '+P 500.0 0.0\n'  <<< b'set_pos: 500.0 0.0\nRPRT -21\n'             [OK] -21 -> 'Limit exceeded'
   rotator_target(270, 0, 180) is None (nothing sent)                        [OK]
rot >>> '+P 12.35 0.0\n'  <<< b'set_pos: 12.35 0.0\nRPRT 0\n'               [OK] cmd_set_pos(12.345, 0)
$ docker rm -f hamq-m7-core-check-<ts> -> container removed: True
RESULT: 47 passed, 0 failed
```

### Manual checks still needed
- None for this core module. The GUI and net parts are separate M7 tasks.

## Notes
- **Image version.** `hamq/hamlib-dummy` runs Hamlib **4.6.2** (Debian
  `libhamlib-utils 4.6.2-1+b1`, `rigctld --version`), not 4.6.5 as ARCHITECTURE.md and
  the task say. I diffed the Hamlib 4.6.2 and 4.6.5 sources: `enum rig_errcode_e`,
  `rigerror_table` and `mode_str[]` are identical, so nothing in the module depends on
  the difference. Hamlib master has the same error codes. Its mode table only adds
  `USBD1..3` / `LSBD1..3`; these are left out of `MODES` because 4.6 does not accept
  them. The contract's "Hamlib 4.6.5" label may need a correction (orchestrator's call).
- **Where the stray-RPRT capture comes from.** The contract's `+F abc` capture
  (`set_split_mode: bogus +F / RPRT -1 / RPRT -18 / RPRT -1`) only happens after a
  preceding `+X_bogus`. rigctld reads `X` as `set_split_mode` and waits for its
  arguments, and the next line supplies them. `+F abc` alone gives
  `set_freq: abc\nRPRT -1\n`. Both are covered by tests and both were reproduced live.
- **Dummy behaviour useful for M7 net/GUI tests:**
  - Initial state: 145000000 Hz, FM 15000.
  - `rigctld -m 1` answers `+t` with `RPRT -11`.
  - Rotator range is -180..450 az and 0..90 el, moving at about 6 deg/s. Out-of-range
    positions give `RPRT -21`.
  - Right after `+P`, `+p` still reports the old position.
- **CHANGELOG.md** (AGENTS.md "Definition of done") was not updated: it is outside this
  task's file scope. Suggested entry under `## Unreleased`:
  "Hamlib protocol core (`core/hamlib.py`): rigctld/rotctld command builders, incremental
  extended-response parser, translated Hamlib error messages, rotator azimuth mapping
  for 0..450 / -180..180 / 180..540 ranges."
