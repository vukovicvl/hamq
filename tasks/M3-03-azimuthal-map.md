# M3-03: Azimuthal equidistant map centered on my QTH

**Milestone:** M3 (optional item "Azimuthal map")
**Status:** done
**Skills:** pyqgis-plugin, geodesy, maidenhead

## Goal
One switch turns the project into the ham radio "great circle map": an azimuthal
equidistant projection centered on my QTH locator, with distance rings and azimuth
lines. Switched off, the project is as it was.

## Scope
- `hamq/gui/azimuthal.py`
- `hamq/i18n/sr_Latn/gui_azimuthal.json`
- `tests/qgis/test_azimuthal.py`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: `LABEL_PLACEMENT_LINE` in the
  shared block "M6-02 / M0-03 / M3-03"

## Out of scope
- The checkable action in `plugin.py` (integration; see Notes).
- `CHANGELOG.md`.

## Checklist
- [x] `AzimuthalMap(iface, settings)`: `enable()` stores the project CRS (and the canvas
      CRS and extent) and sets `QgsCoordinateReferenceSystem.fromProj(core.geo.aeqd_proj(...))`
      for the center of `my_grid`
- [x] Helper memory layer in that CRS, in the layer tree group "HamQ": distance rings
      every 2500 km up to 20 000 km labelled "2500 km" ..., azimuth lines every 30°
      labelled "0°" ...
- [x] `disable()` restores the CRS (and extent) and removes the helper (and the "HamQ"
      group when the map created it and it is empty); safe twice
- [x] `is_enabled()`; without a valid `my_grid`: translated message-bar warning,
      `enable()` returns False, nothing changes
- [x] Follows the project: cleared / another project opened -> off; the user picks
      another CRS -> off, their CRS stays; a new locator saved -> re-centered
- [x] `enabledChanged(bool)` keeps a checkable action in sync, also when switching on fails
- [x] `retranslate()` renames the helper; `cleanup()` disables and disconnects, safe twice;
      slots never raise into Qt
- [x] Catalog `gui_azimuthal.json`

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` pass on the files of this task
- [x] `scripts/test_qgis.sh all -k test_azimuthal.py` passes on local 4.2, 3.44, 4.0, 3.34
- [x] enable/disable restores the CRS and removes the helper; 8 rings and 12 azimuth lines

## Result

### What changed
- `hamq/gui/azimuthal.py` (new):
  - `AzimuthalMap(iface, settings=None, parent=None)` (QObject) with the signal
    `enabledChanged(bool)` and `enable() -> bool`, `disable()`,
    `set_enabled(bool) -> bool` (slot for a checkable action), `is_enabled()`,
    `center_locator()`, `crs()`, `helper_layer()`, `retranslate()`, `cleanup()`.
  - Module constants and helpers: `GROUP_NAME = "HamQ"`, `RING_STEP_KM = 2500`,
    `RING_MAX_KM = 20000`, `AZIMUTH_STEP_DEG = 30`, `RING_KIND` / `AZIMUTH_KIND`,
    `HELPER_FIELDS`, `ring_distances()`, `azimuths()`.
  - CRS: `+proj=aeqd ... +datum=WGS84 +units=km` from `core.geo.aeqd_proj` for the center
    of `my_grid`. The map units are km, so the helper geometry is plain polar
    coordinates.
  - Helper layer "Azimuthal map grid (KN04ft)" (translated): a memory LineString layer
    with the fields `kind`, `value` and `label`.
    - Features: 8 rings of 360 vertices and 12 lines from the center to 20 000 km.
    - Style: dashed grey lines; labels follow the lines, with a white buffer.
    - It goes at the end of the "HamQ" group, so QSO layers draw above it. The group is
      created at the top of the tree when missing.
  - The view is ±21 000 km (the whole world disk).
  - Calling `enable()` again, or saving a new locator, re-centers the map and keeps the
    original CRS for the restore.
  - A failure while switching never leaves the project half switched: it is logged and
    the state is restored. Restoring always sets the saved CRS and view back, even when
    removing the helper fails (that error is logged too).
  - `set_enabled()` emits `enabledChanged` with the unchanged state when switching on
    fails, so a connected checkable action unchecks itself.
- `hamq/i18n/sr_Latn/gui_azimuthal.json`: 4 strings, all used. The labels ("2500 km",
  "30°") are numbers and SI symbols and read the same in every language.
- `tests/qgis/test_azimuthal.py`: 19 tests:
  - enable/disable restore the CRS (3857, 4326, 3035), the canvas CRS and the view
    center, and remove the helper and the group;
  - a failure while switching on (helper layer not created, and also with the restore
    failing halfway) leaves the CRS as it was, no layer, the map off, the errors logged;
  - ring and line counts, labels, geometry, labelling settings;
  - the rings are true distances from the QTH (`QgsDistanceArea`, WGS84, within 0.1 %
    up to 10 000 km);
  - an invalid or empty locator (message in English and Serbian);
  - an existing "HamQ" group is kept; enabling twice; a new locator re-centers;
  - the project is cleared; the user picks another CRS; the user removes the helper;
  - the language change renames the helper; `set_enabled`; a checkable action follows
    every change; cleanup restores and disconnects (`events()` and project signals).

### Commands and outcomes
```
scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_language.py or test_settings_dialog.py
    or test_locator_search.py or test_azimuthal.py or test_compat.py"
  local  QGIS 4.2.1 (Qt 6.10)        PASS  285 passed  (test_azimuthal.py: 19 of them)
  3.44   qgis/qgis:3.44-trixie       PASS  285 passed
  4.0    qgis/qgis:4.0-trixie        PASS  285 passed
  3.34   camptocamp/qgis-server:3.34 PASS  285 passed  (no skips: qgis.gui is available)
whole tests/qgis on the host QGIS 4.2                -> 712 passed, 2 skipped (live Hamlib only)
python3 -m pytest tests/core -q                      -> 3172 passed, 8 xfailed
ruff check / ruff format --check                     -> clean
```

### Manual checks still needed
- In QGIS desktop (3.34 and 4.x), check how these render in the azimuthal CRS:
  - the labels;
  - an OSM basemap and a world countries layer (areas near the antipode are distorted by
    nature);
  - the QSO paths.
- Check the status bar CRS and that the action stays in sync when the CRS is changed in
  Project Properties.

## Notes
- Integration (plugin.py owner), checked by a scratch test through the registry:
  ```python
  self.azimuthal = AzimuthalMap(self.iface, settings, self.iface.mainWindow())
  action = self.add_action("azimuthal.svg", tr_noop("Azimuthal map"),
                           self.azimuthal.set_enabled, checkable=True)
  self.connect_signal(self.azimuthal.enabledChanged, action.setChecked)
  self.add_cleanup(self.azimuthal.cleanup)   # restores the CRS on unload
  ```
  ("Azimuthal map" then belongs in `plugin.json`.)
- Known limitation (not verified in QGIS desktop): the helper is a temporary memory
  layer, so its features are not saved with a project. If the project is saved while the
  map is on, it keeps the azimuthal CRS and the (then empty) helper layer. On reopening,
  the map is not "on"; the user sets the CRS back and removes the layer by hand.
