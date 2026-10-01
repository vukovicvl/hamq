# M1-02: Locator search in the toolbar

**Milestone:** M1
**Status:** done
**Skills:** pyqgis-plugin, maidenhead

## Goal
Type a Maidenhead locator (`KN04ft`) in the HamQ toolbar, press Enter, and the map
centers on that cell, zooms to it and highlights it for a few seconds.

## Scope
- `hamq/gui/locator_search.py`
- `hamq/i18n/sr_Latn/gui_locator_search.json`
- `tests/qgis/test_locator_search.py`

## Out of scope
- Adding the widget to the toolbar in `plugin.py` (integration; see Notes).
- `CHANGELOG.md`.

## Checklist
- [x] `LocatorSearchWidget(iface)`: `QLineEdit`, placeholder "Locator, e.g. KN04ft",
      at most 10 characters, locator icon, clear button
- [x] Enter -> `maidenhead.normalize` (a 10-character locator is cut to 8), the line edit
      shows the normalized text
- [x] Cell bounds transformed from EPSG:4326 to the canvas (= project) CRS; the view is
      the cell with one neighbouring cell on every side, so the zoom follows the level
- [x] Highlight with a `QgsRubberBand` for about 3 s; a new search replaces it; the band
      is deleted, not only taken off the scene
- [x] Invalid input: red line edit + warning in the message bar; typing clears the error
- [x] `retranslate()` on `events().languageChanged`; `cleanup()` safe twice; slots
      never raise into Qt
- [x] Catalog `gui_locator_search.json`

## Acceptance criteria
- [x] `pytest tests/core -q` passes
- [x] `ruff check` / `ruff format --check` pass on the files of this task
- [x] `scripts/test_qgis.sh all -k test_locator_search.py` passes on local 4.2, 3.44, 4.0, 3.34
- [x] The canvas center lies inside the cell for EPSG:4326 and EPSG:3857 projects (and an
      azimuthal, a UTM and a LAEA project)

## Result

### What changed
- `hamq/gui/locator_search.py` (new):
  - `LocatorSearchWidget(iface, parent=None)` with the signal `locatorFound(str)` and
    `search(text=None) -> bool`, `clear_highlight()`, `retranslate()`, `cleanup()`,
    `has_error()`, `is_highlighting()`, `highlight_geometry()` and the attribute
    `highlight_ms`.
  - Module helpers `view_rectangle(locator)` and `latitude_limit(crs)`; constants
    `HIGHLIGHT_MS = 3000` and `MAX_LENGTH = 10`.
  - Zoom per level: the view is the cell plus one cell on every side, clamped to the
    world. That is 60° x 30° for a field and 90" x 45" for an extended square. The view
    is centered on the cell center.
  - Mercator CRSs cannot show the poles. Their latitudes are limited to the CRS's area of
    use (Web Mercator: 85.0511°), so a polar cell is shown as close as the map goes. Its
    highlight is clipped, or left out when the whole cell lies beyond the limit. Other
    projections are not limited: a locator outside a UTM zone's area of use is still
    placed correctly.
  - The highlight polygon has 16 points per cell edge, so curved edges in projected
    CRSs are drawn right.
  - The rubber band belongs to C++ (`sip.ispyowned` is False): taking it off the scene
    alone would leak it, so it is deleted with `sip.delete`. It is not touched once its
    canvas is gone.
  - When the projection cannot show the cell (transform failure, non-finite result), a
    translated warning goes to the message bar and the map does not move.
- `hamq/i18n/sr_Latn/gui_locator_search.json`: 5 strings, all used.
- `tests/qgis/test_locator_search.py`: 39 tests:
  - 8 locators x {EPSG:4326, EPSG:3857}: the center lies in the cell and the whole cell
    is visible;
  - zoom per level; `view_rectangle`; Enter typed with QTest; 10 -> 8 characters;
  - the highlight contains the cell center and disappears after its time; a new search
    replaces it; the band is deleted; the canvas deleted under a live highlight;
  - invalid input (5 cases: warning level and text, map unchanged, error cleared by
    typing); empty input;
  - azimuthal, UTM 34N and LAEA Europe canvases; `latitude_limit`; a polar field in Web
    Mercator; retranslation; cleanup leaves no receivers and no rubber band.

### Commands and outcomes
```
scripts/test_qgis.sh all -q -p no:cacheprovider -k "test_language.py or test_settings_dialog.py
    or test_locator_search.py or test_azimuthal.py or test_compat.py"
  local  QGIS 4.2.1 (Qt 6.10)        PASS  285 passed  (test_locator_search.py: 39 of them)
  3.44   qgis/qgis:3.44-trixie       PASS  285 passed
  4.0    qgis/qgis:4.0-trixie        PASS  285 passed
  3.34   camptocamp/qgis-server:3.34 PASS  285 passed  (no skips: qgis.gui is available)
whole tests/qgis on the host QGIS 4.2                -> 712 passed, 2 skipped (live Hamlib only)
python3 -m pytest tests/core -q                      -> 3172 passed, 8 xfailed
ruff check / ruff format --check                     -> clean
```

### Manual checks still needed
- In QGIS desktop (3.34 and 4.x): the widget's width in the toolbar, the highlight
  color on a dark and a light basemap, the message bar warning, and the search with an
  OSM basemap (EPSG:3857) and with the azimuthal map on.

## Notes
- Integration (plugin.py owner):
  ```python
  search = LocatorSearchWidget(self.iface)
  self.toolbar.addWidget(search)        # deleted with the toolbar
  self.add_cleanup(search.cleanup)
  ```
- **Release pass (2026-10-01)** (GUI fixer): the toolbar field has a fixed width: as wide
  as its placeholder or a 10-character locator, plus the locator icon and the clear
  button, so it no longer stretches over the toolbar. Still open (gui): the "{text} is
  not a valid Maidenhead locator" warning is pushed to the message bar unescaped, so
  markup typed into the field is rendered (a typed `<` disappears); escape it with
  `html.escape(text, quote=False)`.
