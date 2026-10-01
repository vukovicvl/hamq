# M1-03: Processing algorithms "Locator to point" and "Generate Maidenhead grid"

**Milestone:** M1
**Status:** done
**Skills:** pyqgis-plugin, maidenhead, geodesy

## Goal
Two Processing algorithms in the `hamq` provider, group `maidenhead`: Maidenhead locators
to center points, and the Maidenhead grid (field, square, subsquare, extended square) as
polygons for an extent, in EPSG:4326, translated, and fast enough for Europe at square level
(< 2 s). The provider registers all four HamQ algorithms (see M2-04 for the log algorithms).

## Scope
- `hamq/processing/alg_locator_to_point.py`, `hamq/processing/alg_grid.py` (new)
- `hamq/processing/common.py` (new, shared base class and helpers; see Notes: deviation)
- `hamq/processing/provider.py` (`ALGORITHMS` filled)
- `hamq/i18n/sr_Latn/processing_alg_locator_to_point.json`, `processing_alg_grid.json`,
  `processing_common.json` (new)
- `tests/qgis/test_processing.py` (shared with M2-04)
- `tasks/M1-03-processing-maidenhead.md`

## Out of scope
- Toolbar locator search (M1-02), the grid default style itself (M3-02, used here).
- `tests/qgis/test_plugin_load.py` (M0-01), `CHANGELOG.md`, `docs/ARCHITECTURE.md`: see Notes.

## Checklist
- [x] `hamq:locator_to_point`: `LOCATORS` (multi-line text; spaces, commas, semicolons, new
      lines) -> `OUTPUT` (Point, EPSG:4326) with `locator` (normalized), `precision`, `lat`,
      `lon`; invalid tokens `reportError(..., fatalError=False)` and skipped; a 10-character
      locator cut to 8 with a warning; none valid -> `QgsProcessingException`
- [x] `hamq:maidenhead_grid`: `EXTENT` (any CRS, converted with the run's transform context,
      clamped to the world), `LEVEL` enum (Field / Square / Subsquare / Extended, default
      Square), `OUTPUT` (Polygon, EPSG:4326) with `locator`; refused above
      `core.maidenhead.MAX_GRID_CELLS` with a translated message (smaller extent or coarser
      level); progress, cancel between batches
- [x] Extent conversion that works the same on 3.34 .. 4.2: antimeridian split, poles,
      Web Mercator wider than the world, azimuthal map beyond the antipode
- [x] A grid layer that Processing loads gets the HamQ grid style (layer post-processor
      that outlives the algorithm copy `run()` deletes)
- [x] `displayName`, `group` ("Maidenhead locators" / stable id `maidenhead`), help, tags,
      parameter descriptions and level names through `tr()`; icons `locator.svg`, `grid.svg`
- [x] Provider: `ALGORITHMS = [LocatorToPoint, MaidenheadGrid, ImportAdif, Recalculate]`
- [x] Serbian (Latin) catalogs; Cyrillic transliteration checked by eye
- [x] Tests on QGIS 4.2 (host), 3.44, 4.0, 3.34 (Docker); self-review

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes (also `HAMQ_STRICT_I18N=1` for the catalog guard)
- [x] `ruff check hamq tests` and `ruff format --check hamq tests` clean
- [x] Square-level grid for Europe (-25..45 E, 34..72 N, 1368 cells) in < 2 s: 13 ms (4.2),
      22 ms (3.44), 12 ms (4.0), 21 ms (3.34)
- [x] `scripts/test_qgis.sh all -k test_processing` passes on all four targets

## Result

### What changed
- `alg_locator_to_point.py`: `LocatorToPointAlgorithm` (`hamq:locator_to_point`), helpers
  `split_locators(text)`, `POINT_FIELDS`.
- `alg_grid.py`: `MaidenheadGridAlgorithm` (`hamq:maidenhead_grid`), `LEVELS`,
  `LEVEL_NAMES`, `GRID_FIELDS`, `extent_to_wgs84(rect, crs, transform_context)` and
  `grid_styler()`.
  - Extent: `QgsCoordinateTransform.transformBoundingBox` was not usable. On QGIS 3.34,
    `handle180Crossover=True` gives `xMinimum > xMaximum` for a world-wide Web Mercator
    extent (8.69..-8.69) and for an azimuthal world extent. Without the flag, an extent
    across the antimeridian becomes the complementary near-world box on 4.2 (-156..168
    instead of 168..204). So the extent is converted point by point: 64 points per edge
    and 9x9 inside.
    - Longitudes are followed along the edges, so a range past 180 is split into two parts
      and a range of 360 or more gives the whole world.
    - A pole counts as inside when the pole and 4 points at 89.9° lie in the extent. That
      check comes first, because PROJ's ellipsoidal aeqd does not fail beyond the
      antipode: it wraps.
    - Geographic CRSs (and no CRS) are clamped in degrees.
  - Cells are written in batches of 2000 with `FastInsert`.
  - Style: QGIS's `run()` executes a copy of the algorithm and deletes it before Processing
    loads the result layer. A post-processor stored on `self` was already dead (verified:
    `postProcessLayer` never ran). The post-processor is therefore one module-level object,
    created under a lock and set in `postProcessAlgorithm`.
- `common.py`: `HamQAlgorithm` base (tr, icon, group, `createInstance`), groups
  `maidenhead` / `log`, the shared log-algorithm helpers (see M2-04).
- `provider.py`: the four classes in `ALGORITHMS` (toolbox order), docstring.
- Catalogs: `processing_common.json` (9), `processing_alg_locator_to_point.json` (9),
  `processing_alg_grid.json` (16). Texts shared with other modules use their translation
  (`My QTH locator`, `QTH locator {locator} is not a valid ...`).
- `tests/qgis/test_processing.py`, M1 part: provider and translated names (Latin and
  Cyrillic, back to English); parameters/outputs; run-time messages in Serbian; locator to
  point:
  - centers;
  - invalid tokens;
  - 10 -> 8 characters;
  - nothing valid;
  - empty value;
  - GeoPackage output.

  `extent_to_wgs84` cases:
  - 4326 clamp;
  - no CRS;
  - outside the world;
  - Web Mercator, and Web Mercator wider than the world;
  - EPSG:3832 across the antimeridian;
  - EPSG:3995 around the pole and beside it;
  - HamQ aeqd beyond the antipode and near the QTH.

  Grid algorithm cases:
  - all four levels exactly equal to `core.maidenhead.iter_cells` (bounds bit-identical);
  - Web Mercator extent;
  - Pacific split (`AI AJ BI BJ RI RJ`);
  - polar map;
  - world in 3857 (324 fields);
  - clamping;
  - outside the world;
  - over the limit;
  - cancel;
  - the style on a layer loaded by `processing.runAndLoadResults`;
  - Europe timing (slow).

### Commands and outcomes
- `scripts/test_qgis.sh all -q -p no:cacheprovider -rs -k test_processing` (final run, M1 + M2
  tests together):

  | target | environment | result |
  |---|---|---|
  | local | host QGIS 4.2.1 (Qt 6.10) | PASS: 65 passed |
  | 3.44 | qgis/qgis:3.44-trixie (Qt5) | PASS: 65 passed |
  | 4.0 | qgis/qgis:4.0-trixie (Qt6) | PASS: 65 passed |
  | 3.34 | camptocamp/qgis-server:3.34 (Qt5) | PASS: 64 passed, 1 skipped (the `qgis_process` smoke test: the image's `qgis_process` cannot load `libQt53DExtras.so.5`) |

- `scripts/test_qgis.sh all -s -k "test_processing and (under_2_s or under_10_s)"`: Europe
  squares 13 / 22 / 12 / 21 ms (4.2 / 3.44 / 4.0 / 3.34).
- `scripts/test_qgis.sh local -q` (whole `tests/qgis`): 933 passed, 2 skipped (live Hamlib),
  **2 failed in `tests/qgis/test_plugin_load.py`** (M0 asserts an empty `ALGORITHMS`, see
  Notes).
- `python3 -m pytest tests/core -q`: 3236 passed, 8 xfailed.
  `HAMQ_STRICT_I18N=1 python3 -m pytest tests/core/test_i18n_catalog.py -q`: 296 passed (at
  the time of the run; other agents add catalogs in parallel).
- `ruff check hamq tests`, `ruff format --check hamq tests`: clean.

### Manual checks still needed
- QGIS desktop 3.34 and 4.x with the plugin loaded:
  - the toolbox shows "HamQ" with the groups "Maidenhead locators" and "QSO log";
  - the language switch renames them;
  - "Generate Maidenhead grid" with "Use Current Map Canvas Extent" in a Web Mercator
    project and with the HamQ azimuthal map;
  - the loaded grid layer has the locator labels.
- Batch mode and the graphical modeler with both algorithms (not exercised by the tests).

## Notes
- **`tests/qgis/test_plugin_load.py` (M0-01, not mine) now fails twice** because it was
  written for an empty provider:
  - `test_provider_properties` asserts `provider_module.ALGORITHMS == []` and
    `provider.algorithms() == []`.
  - `test_provider_loads_algorithm_classes` asserts `provider.algorithms() == []` after
    `monkeypatch.undo()`.

  Suggested change: assert
  `sorted(a.id() for a in provider.algorithms()) == ["hamq:import_adif",
  "hamq:locator_to_point", "hamq:maidenhead_grid", "hamq:recalculate"]` in the first, and
  `len(loaded.provider.algorithms()) == len(provider_module.ALGORITHMS)` in the second.
  `test_toolbox_shows_provider_only_with_algorithms` still passes (it monkeypatches the list).
- **Deviation: `hamq/processing/common.py`** is a new file outside the listed scope. It
  holds the base class and the helpers the four algorithms share. Without it they would
  have to import each other or duplicate about 150 lines. It lives in the processing
  package, which only this task edits, with its own catalog `processing_common.json`
  (matches `processing_*.json`).
- `LEVEL` is an index (0..3) for `qgis_process` and scripts (language independent).
- An extent given without a CRS in a project without one is taken as EPSG:4326 degrees.
- A Web Mercator extent wider than the world wraps (all longitudes), as QGIS 3.44 / 4.x
  transform it. A 4326 extent past 180 is clamped, because a 4326 map shows nothing there.
- **CHANGELOG.md** (not edited): "Processing: Locator to point and Generate Maidenhead grid
  (field to extended square, any extent CRS, antimeridian and poles, grid style) (M1-03)."
