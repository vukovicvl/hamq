# M3-02: Geodesic QSO paths and default styles

**Milestone:** M3
**Status:** done
**Skills:** pyqgis-plugin, geodesy, adif

## Goal
Draw every QSO as the WGS84 geodesic from my QTH, split at the antimeridian, and give the
HamQ layers default styles: QSO points and paths coloured by band with a colour-blind-aware
palette, and the Maidenhead grid with locator labels where they fit. Ship the styles as
`.qml` files generated on the oldest supported QGIS.

## Scope
- `hamq/qgis_io/gpkg.py`: `geodesic_path` and the path writing (shared with M2-03)
- `hamq/qgis_io/styles.py`, `hamq/resources/styles/{qso,qso_path,grid}.qml`
- `scripts/make_styles.py`
- `tests/qgis/test_styles.py`; geodesic tests in `tests/qgis/test_gpkg.py`
- `hamq/i18n/sr_Latn/qgis_io_styles.json`
- `hamq/qgis_io/compat.py`, `tests/qgis/test_compat.py`: block "M2-03 / M3-02"
- `tasks/M3-02-paths-and-styles.md`

## Out of scope
- The grid Processing algorithm (it should give its layer a text field `locator` and call
  `styles.apply_default_style(layer, "grid")`), the azimuthal map (M3-03).

## Checklist
- [x] `geodesic_path(my_lat, my_lon, lat, lon)`: `QgsDistanceArea.geodesicLine` (WGS84,
      ~100 km step, `breakLine=True`) as MultiLineString; `None` for missing / invalid
      points, the same place (< 1 m) and (nearly) antipodal points (`core.geo.is_antipodal`)
- [x] Work-arounds for `geodesicLine` (QGIS 3.34 to 4.2):
      - a path shorter than the step got a vertex one step beyond its end (a 100 km detour
        on a 79 m path): the interval is at most half the length;
      - near a pole a step can jump the antimeridian unsplit (152.7° → -115.9°): such
        segments are split at ±180° (interpolated latitude);
      - a path starting or ending exactly on ±180° got a zero-length part: dropped.
- [x] `BAND_COLORS` for the 15 common bands (160 m - 23 cm), grey for other bands and NULL;
      chosen and tested with CIEDE2000 under normal vision and simulated protan / deutan /
      tritan vision (Machado 2009): every pair ≥ 6.3 in all four, busy bands and grey ≥ 8;
      contrast ≥ 2:1 on white
- [x] `apply_default_style(layer, kind)` for `qso` (points by band, dark outline),
      `qso_path` (semi-transparent lines by band), `grid` (no fill, thin outline, locator
      labels shown only from the scale where they fit: data-defined Show with
      `@map_scale`); loads `resources/styles/<kind>.qml` (Symbology | Labeling only), else
      builds in code; wrong kind / geometry type → `ValueError`
- [x] "Other bands" legend label translated, again on `retranslate_style` (a label the user
      changed is kept). Release pass: the Layers panel did not show the new label until the
      layer was redrawn; the style now emits `legendChanged` when a label changes
- [x] `scripts/make_styles.py` generates the `.qml` files from the code on QGIS 3.34
      (Docker), reproducibly (fixed Qt hash seed, stable UUIDs, no font family);
      `--check` compares
- [x] Tests: Beograd-Sydney ~15 676 km ± 0.5 %, trans-Pacific split (≥ 2 parts, no segment
      over 180°), near-pole paths, antimeridian endpoints, short paths, styles load on all
      four versions and match the code

## Acceptance criteria
- [x] `python3 -m pytest tests/core -q` passes; `ruff check` / `ruff format --check` clean
- [x] Beograd → Sydney path: ≈ 15 676 km measured on WGS84 (± 0.5 %), one part, vertices
      ≤ 100.5 km apart
- [x] A Pacific path is split at the antimeridian and never crosses the whole map
- [x] The `.qml` files load on QGIS 3.34, 3.44, 4.0 and 4.2 and equal the code style

## Result

### What changed
- `geodesic_path` and the path writing in `hamq/qgis_io/gpkg.py` (see M2-03 for storage).
- `hamq/qgis_io/styles.py` (new). Contract: `BAND_COLORS`, `apply_default_style`.
  Additions: `OTHER_BAND_COLOR`, `OTHER_BANDS_LABEL`, `STYLE_KINDS`, `STYLES_DIR`,
  `GRID_LABEL_FIELDS` (`locator`, `grid`, `gridsquare`, `name`), `GRID_LABEL_SCALES`
  (2: 1:200M, 4: 1:15M, 6: 1:500k, 8: 1:40k), `build_default_style`, `retranslate_style`,
  `style_path`, `style_categories`.
- `hamq/resources/styles/qso.qml`, `qso_path.qml`, `grid.qml`, generated on QGIS 3.34.15
  with `scripts/make_styles.py` (new).
- `hamq/i18n/sr_Latn/qgis_io_styles.json` (2 strings).
- `tests/qgis/test_styles.py` (37 tests).
- Compat constants (block "M2-03 / M3-02"): `LABEL_PLACEMENT_OVER_POINT`,
  `LABEL_PROPERTY_SHOW`, `STYLE_CATEGORY_SYMBOLOGY`, `STYLE_CATEGORY_LABELING`,
  `BRUSH_NONE`, `DATE_FORMAT_ISO_MS`.

### Band colours
160m #8C564B, 80m #AA3377, 60m #999933, 40m #0072B2, 30m #33BBEE, 20m #CC3311,
17m #DDAA33, 15m #228833, 12m #CC6677, 10m #648FFF, 6m #EE8866, 4m #009988, 2m #785EF0,
70cm #CC79A7, 23cm #AA4499, other #9E9E9E (from Okabe-Ito, Paul Tol and IBM CVD-safe sets).

### Commands and outcomes
- Styles: `docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen
  -v "$PWD":/app:ro -v "$PWD/hamq/resources/styles":/out -w /app camptocamp/qgis-server:3.34
  python3 scripts/make_styles.py --output /out` (twice: identical output);
  `... make_styles.py --check`: exit 0 (exit 1 against an empty folder).
- Tests: see M2-03 (same final run, all four targets PASS, 312 passed each).
- The QGIS `geodesicLine` behaviour was probed on 3.34, 3.44, 4.0 and 4.2 (identical).

### Manual checks still needed
- Desktop QGIS: look at the band colours on light and dark base maps, the path
  transparency, and the grid labels while zooming (fields always, squares below 1:15M,
  subsquares below 1:500k).

## Notes
- The labels of the grid style use the default font of each installation: the generator
  removes the font family from the `.qml` (a missing font would make QGIS substitute or
  try to download one).
- `qso_path.distance_km` / `bearing_deg` are the QSO's spherical values (core.geo, as the
  contract computes them), the line itself is the WGS84 geodesic; the difference is below
  0.6 % (Beograd-Sydney: 15 679.7 km spherical, 15 676.1 km on WGS84, measured again in
  the release pass with `QgsDistanceArea`; the largest difference, +0.56 %, is on short
  north-south paths near the equator). PLAN.md now says so in the data model, so that a
  QGIS `$length` of a path is not taken for a wrong `distance_km`.
- **Release pass (2026-10-01):** `styles.apply_default_style` / `retranslate_style`
  change the category labels in place, which QGIS does not notice, so the Layers panel
  kept the old "Other bands" text after a language switch (the earlier claim that it
  follows the switch held only for a redrawn legend). They now emit
  `layer.legendChanged`, once per layer and only when a label really changed;
  `tests/qgis/test_styles.py` checks the legend text after a switch.
