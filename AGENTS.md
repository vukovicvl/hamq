# AGENTS.md

Instructions for AI coding agents working in this repository.

## Project

HamQ is a QGIS plugin for amateur radio operators. It maps QSO logs (ADIF),
works with Maidenhead locators, resolves DXCC entities from callsigns and
receives live QSOs from WSJT-X over UDP.

The plan and milestones are in `PLAN.md`. Work items are in `tasks/`.

## Workflow

1. Read `PLAN.md` and the task file you were given in `tasks/`.
2. Read the relevant skills in `.github/skills/` before writing code.
3. Work only inside the scope of the task file. If something outside the
   scope is broken, note it at the end of the task file under `## Notes`.
4. Write or update tests first for anything in `hamq/core/`.
5. Run the checks (see Commands). Do not report a task as done if they fail.
6. Update the task file: tick the checklist, fill `## Result`.

## Architecture rules

- `hamq/core/` is pure Python 3.9+. **No `qgis` or `PyQt` imports there.**
  All parsing, math and protocol decoding lives here and is tested with pytest.
- `hamq/qgis_io/` converts core objects to QGIS layers and features.
- `hamq/processing/` holds the Processing provider and algorithms.
- `hamq/gui/` holds docks, dialogs and map tools. Keep logic out of GUI code.
- `hamq/net/` holds network code (cty.dat download, UDP, later Hamlib).
- No third-party packages. Stdlib plus what ships with QGIS only.

## Qt5 / Qt6 compatibility

The plugin must run on QGIS 3.34+ (Qt5) and QGIS 4.x (Qt6).

- Import Qt only via `qgis.PyQt` (`from qgis.PyQt.QtCore import ...`).
- Use fully scoped enums: `Qt.AlignmentFlag.AlignLeft`, not `Qt.AlignLeft`.
- Use `dialog.exec()`, not `exec_()`.
- `QAction` comes from `qgis.PyQt.QtGui` in Qt6. Use
  `from qgis.PyQt.QtGui import QAction` with a fallback to `QtWidgets`.
- Use `Qgis.*` enums from `qgis.core` where available (3.34+ names).

## Conventions

- Python style: PEP 8, type hints on public functions, docstrings on public API.
- Format with `ruff format`, lint with `ruff check`.
- All times are UTC. Store as ISO 8601 in GeoPackage.
- Callsigns are stored uppercase and stripped.
- Coordinates are EPSG:4326, lat/lon order only in core code, x/y order in QGIS code.
- User-facing strings go through `self.tr()` for translation.
- Never block the UI thread for more than ~100 ms. Use `QgsTask` for imports,
  async sockets for network.
- Log with `QgsMessageLog.logMessage(msg, "HamQ", level)`, not `print`.

## Commands

```bash
# unit tests (core, no QGIS needed)
pytest tests/core -q

# lint and format
ruff check hamq tests
ruff format --check hamq tests

# QGIS integration tests (Docker)
docker run --rm -v "$PWD":/app -w /app qgis/qgis:latest \
  sh -c "pip install pytest --break-system-packages -q && xvfb-run -a pytest tests/qgis -q"

# link plugin into local QGIS profile for manual testing (Linux)
ln -sfn "$PWD/hamq" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/hamq

# build release zip
python scripts/package.py
```

## Definition of done

- Task checklist complete.
- `pytest tests/core` and `ruff check` pass.
- New core code has tests, including at least one edge case.
- No new `qgis`/`PyQt` import inside `hamq/core/`.
- Plugin loads without errors in QGIS (manual check noted in task file if GUI changed).
- `CHANGELOG.md` updated under `## Unreleased`.

## Do not

- Do not add dependencies or vendor external code without asking.
- Do not bundle `cty.dat` in the repository.
- Do not change the GeoPackage schema without updating `PLAN.md` and adding a migration.
- Do not rewrite unrelated files for style.
