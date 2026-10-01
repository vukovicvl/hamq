"""hamq.gui.locator_search.LocatorSearchWidget: center the canvas on a Maidenhead locator."""

from __future__ import annotations

import time

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPoint

from hamq.core import maidenhead
from hamq.core.geo import aeqd_proj
from hamq.core.i18n import LANG_EN, LANG_SR_CYRL, LANG_SR_LATN, set_language
from hamq.events import events
from hamq.gui.locator_search import (
    HIGHLIGHT_MS,
    MAX_LENGTH,
    LocatorSearchWidget,
    latitude_limit,
    view_rectangle,
)
from hamq.qgis_io import compat

gui = pytest.importorskip("qgis.gui", reason="qgis.gui is not available in this build")
QtTest = pytest.importorskip("qgis.PyQt.QtTest", reason="QtTest is not available in this build")

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


@pytest.fixture(autouse=True)
def no_slot_errors(log_messages):
    set_language(LANG_EN)
    yield
    set_language(LANG_EN)
    errors = [m for m, tag, level in log_messages if tag == "HamQ" and level == compat.MSG_CRITICAL]
    assert errors == []


@pytest.fixture
def canvas(iface, clean_project):
    canvas = iface.mapCanvas()
    canvas.resize(800, 600)
    return canvas


def set_crs(canvas, crs):
    """Set the project and canvas CRS (QGIS keeps them equal) and show 20 x 20 degrees."""
    QgsProject.instance().setCrs(crs)
    canvas.setDestinationCrs(crs)
    transform = QgsCoordinateTransform(WGS84, crs, QgsProject.instance())
    canvas.setExtent(transform.transformBoundingBox(QgsRectangle(-10, -10, 10, 10)))


@pytest.fixture
def widget(iface, canvas, process_events):
    widget = LocatorSearchWidget(iface)
    yield widget
    widget.cleanup()
    widget.deleteLater()
    process_events()


def to_wgs84(canvas, point):
    transform = QgsCoordinateTransform(
        canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance()
    )
    return transform.transform(point)


def rubber_bands(canvas):
    return [item for item in canvas.scene().items() if isinstance(item, gui.QgsRubberBand)]


def assert_centered(canvas, locator):
    lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
    center = to_wgs84(canvas, canvas.center())
    assert lon_min <= center.x() <= lon_max, (locator, center.toString())
    assert lat_min <= center.y() <= lat_max, (locator, center.toString())


CRS_CASES = ["EPSG:4326", "EPSG:3857"]
LOCATORS = ["KN04ft", "JN95wg", "KN", "KN04", "FM18lv55", "QF56od", "AA00aa", "RR99xx"]


@pytest.mark.parametrize("authid", CRS_CASES)
@pytest.mark.parametrize("locator", LOCATORS)
def test_search_centers_the_canvas_on_the_cell(widget, canvas, authid, locator):
    set_crs(canvas, QgsCoordinateReferenceSystem(authid))
    found = []
    widget.locatorFound.connect(found.append)
    widget.line_edit.setText(locator.lower())
    assert widget.search() is True
    assert widget.line_edit.text() == locator  # normalized
    assert found == [locator]
    assert not widget.has_error()
    if authid == "EPSG:3857" and locator in ("AA00aa", "RR99xx"):
        # beyond Web Mercator's 85.06 degrees: centered as close as the projection allows
        center = to_wgs84(canvas, canvas.center())
        lat, lon = maidenhead.to_latlon(locator)
        assert abs(center.x() - lon) < 1e-6
        assert 84.9 < abs(center.y()) <= 85.07
    else:
        assert_centered(canvas, locator)
        # the whole cell is visible
        lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
        transform = QgsCoordinateTransform(
            canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance()
        )
        visible = transform.transformBoundingBox(canvas.extent())
        assert visible.xMinimum() <= lon_min + 1e-9 and visible.xMaximum() >= lon_max - 1e-9
        if authid == "EPSG:4326" or abs(lat_max) < 85:
            assert visible.yMinimum() <= lat_min + 1e-9 and visible.yMaximum() >= lat_max - 1e-9
    polar = authid == "EPSG:3857" and locator in ("AA00aa", "RR99xx")
    assert widget.is_highlighting() is not polar  # nothing of a polar cell is on the map
    assert len(rubber_bands(canvas)) == (0 if polar else 1)


@pytest.mark.parametrize("locator", ["KN04ft", "KN", "JN58td25"])
def test_zoom_depends_on_the_level(widget, canvas, locator):
    set_crs(canvas, WGS84)
    assert widget.search(locator)
    lat_min, lon_min, lat_max, lon_max = maidenhead.to_bounds(locator)
    extent = canvas.extent()
    # the cell and its neighbours: three cells in the tighter direction (the canvas
    # widens the other direction to its aspect ratio), not the whole world
    cells = (extent.width() / (lon_max - lon_min), extent.height() / (lat_max - lat_min))
    assert min(cells) == pytest.approx(3.0)
    assert max(cells) < 12


def test_view_rectangle():
    assert view_rectangle("KN") == (30.0, 0.0, 60.0, 60.0)
    assert view_rectangle("AA") == (-90.0, -180.0, -70.0, -140.0)  # clamped to the world
    assert view_rectangle("KN04") == (43.0, 18.0, 46.0, 24.0)
    with pytest.raises(ValueError):
        view_rectangle("ZZ")


def test_enter_key_searches(widget, canvas):
    set_crs(canvas, QgsCoordinateReferenceSystem("EPSG:3857"))
    QtTest.QTest.keyClicks(widget.line_edit, "kn04ft")
    QtTest.QTest.keyClick(widget.line_edit, gui_key("Key_Return"))
    assert widget.line_edit.text() == "KN04ft"
    assert_centered(canvas, "KN04ft")


def gui_key(name):
    from qgis.PyQt.QtCore import Qt

    return getattr(Qt.Key, name)


def test_ten_characters_are_cut_to_eight(widget, canvas):
    set_crs(canvas, WGS84)
    assert widget.line_edit.maxLength() == MAX_LENGTH == 10
    assert widget.search("kn04ft12ab")
    assert widget.line_edit.text() == "KN04ft12"
    assert_centered(canvas, "KN04ft12")


def test_highlight_covers_the_cell_and_goes_away(widget, canvas, qgis_app):
    set_crs(canvas, QgsCoordinateReferenceSystem("EPSG:3857"))
    assert HIGHLIGHT_MS == 3000
    widget.highlight_ms = 50
    assert widget.search("JN95wg")
    geometry = widget.highlight_geometry()
    assert geometry is not None and not geometry.isEmpty()
    lat, lon = maidenhead.to_latlon("JN95wg")
    transform = QgsCoordinateTransform(
        WGS84, canvas.mapSettings().destinationCrs(), QgsProject.instance()
    )
    assert geometry.contains(transform.transform(QgsPointXY(lon, lat)))
    deadline = time.monotonic() + 5
    while widget.is_highlighting() and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.01)
    assert not widget.is_highlighting()
    assert rubber_bands(canvas) == []


def test_new_search_replaces_the_highlight(widget, canvas):
    set_crs(canvas, WGS84)
    assert widget.search("KN04ft")
    assert widget.search("JN58td")
    assert len(rubber_bands(canvas)) == 1


def test_cleared_rubber_band_is_deleted(widget, canvas):
    # a rubber band belongs to C++: taking it off the scene alone would leak it
    set_crs(canvas, WGS84)
    assert widget.search("KN04ft")
    (band,) = rubber_bands(canvas)
    assert not sip.ispyowned(band)
    widget.clear_highlight()
    assert sip.isdeleted(band)
    assert rubber_bands(canvas) == []
    widget.clear_highlight()  # nothing left: no error


class CanvasIface:
    """An interface whose map canvas the test owns (and deletes)."""

    def __init__(self, canvas, message_bar):
        self._canvas = canvas
        self._message_bar = message_bar

    def mapCanvas(self):
        return self._canvas

    def messageBar(self):
        return self._message_bar


def test_highlight_after_the_canvas_is_gone(iface, clean_project, qgis_app, process_events):
    # the canvas deletes its items without telling Python: the widget must not touch them
    canvas = gui.QgsMapCanvas()
    canvas.resize(400, 300)
    canvas.setDestinationCrs(WGS84)
    widget = LocatorSearchWidget(CanvasIface(canvas, iface.messageBar()))
    try:
        widget.highlight_ms = 10
        assert widget.search("KN04ft")
        assert widget.is_highlighting()
        sip.delete(canvas)
        deadline = time.monotonic() + 5
        while widget.is_highlighting() and time.monotonic() < deadline:
            qgis_app.processEvents()
            time.sleep(0.01)
        assert not widget.is_highlighting()
    finally:
        widget.cleanup()
        widget.deleteLater()
        process_events()


@pytest.mark.parametrize("text", ["ZZ99", "KN0", "KN04ft1", "12", "KN 04"])
def test_invalid_input(widget, iface, canvas, text):
    set_crs(canvas, WGS84)
    widget.search("KN04ft")
    extent = canvas.extent()
    iface.messageBar().clearWidgets()
    widget.line_edit.setText(text)
    assert widget.search() is False
    assert widget.has_error()
    assert widget.line_edit.styleSheet() != ""
    assert canvas.extent() == extent  # the map did not move
    items = iface.messageBar().items()
    assert len(items) == 1
    assert items[0].level() == compat.MSG_WARNING
    assert text in items[0].text() and "not a valid Maidenhead locator" in items[0].text()
    # typing clears the error
    QtTest.QTest.keyClicks(widget.line_edit, "x")
    assert not widget.has_error()
    assert widget.line_edit.styleSheet() == ""


def test_empty_input_does_nothing(widget, iface, canvas):
    set_crs(canvas, WGS84)
    extent = canvas.extent()
    iface.messageBar().clearWidgets()
    assert widget.search("   ") is False
    assert not widget.has_error()
    assert canvas.extent() == extent
    assert iface.messageBar().items() == []


def test_azimuthal_canvas(widget, canvas):
    lat, lon = maidenhead.to_latlon("KN04ft")
    set_crs(canvas, QgsCoordinateReferenceSystem.fromProj(aeqd_proj(lat, lon)))
    for locator in ("QF56od", "FM18lv", "KN04ft"):
        assert widget.search(locator)
        assert_centered(canvas, locator)


def test_latitude_limit():
    assert latitude_limit(QgsCoordinateReferenceSystem("EPSG:3857")) == pytest.approx(
        (-85.06, 85.06), abs=0.01
    )
    assert latitude_limit(QgsCoordinateReferenceSystem("EPSG:3395")) == pytest.approx((-80, 84))
    custom = QgsCoordinateReferenceSystem.fromProj("+proj=merc +datum=WGS84 +units=m +no_defs")
    south, north = latitude_limit(custom)
    assert -85.06 <= south <= -80 and 80 <= north <= 85.06
    for authid in ("EPSG:4326", "EPSG:32634", "EPSG:3035"):
        assert latitude_limit(QgsCoordinateReferenceSystem(authid)) is None


@pytest.mark.parametrize("authid", ["EPSG:32634", "EPSG:3035"])
def test_locator_outside_the_area_of_use_is_shown_where_it_is(widget, canvas, authid):
    # a UTM zone for Serbia (18..24 E) and LAEA Europe: Munich and Washington are outside
    # or at the edge of the area of use, yet the formulas place them correctly
    set_crs(canvas, QgsCoordinateReferenceSystem(authid))
    for locator in ("JN58td", "KN04ft"):
        assert widget.search(locator)
        assert_centered(canvas, locator)


def test_polar_field_in_web_mercator(widget, canvas):
    set_crs(canvas, QgsCoordinateReferenceSystem("EPSG:3857"))
    assert widget.search("JR")  # 80..90 N: the part up to 85.06 N is shown
    assert_centered(canvas, "JR")
    assert widget.is_highlighting()
    assert widget.search("RR99xx")  # wholly beyond 85.06 N: no highlight to draw
    assert not widget.is_highlighting()


def test_retranslates(widget):
    assert widget.line_edit.placeholderText() == "Locator, e.g. KN04ft"
    set_language(LANG_SR_LATN)
    events().languageChanged.emit(LANG_SR_LATN)
    assert widget.line_edit.placeholderText() == "Lokator, npr. KN04ft"
    assert "pritisnite Enter" in widget.line_edit.toolTip()


@pytest.mark.parametrize("code", [LANG_EN, LANG_SR_LATN, LANG_SR_CYRL])
def test_toolbar_field_keeps_a_compact_width(iface, process_events, code):
    """In a wide toolbar the field does not stretch over the whole row, and the
    placeholder still fits (it is longer in Serbian)."""
    from qgis.PyQt.QtWidgets import QMainWindow, QToolBar, QToolButton

    set_language(code)
    window = QMainWindow()
    window.resize(1400, 300)
    toolbar = QToolBar("HamQ", window)
    window.addToolBar(toolbar)
    toolbar.addAction("Panel")
    widget = LocatorSearchWidget(iface)
    toolbar.addWidget(widget)
    switch = QToolButton()  # like the language switch that follows the field
    switch.setText("EN")
    toolbar.addWidget(switch)
    window.show()
    process_events()
    try:
        edit = widget.line_edit
        assert edit.placeholderText()
        hint = edit.fontMetrics().horizontalAdvance(edit.placeholderText())
        assert edit.width() > hint + 2 * edit.height()  # room for the icons too
        assert widget.width() < 3 * hint  # the toolbar row is 1400 px wide
        gap = switch.mapTo(window, QPoint(0, 0)).x() - widget.mapTo(window, QPoint(0, 0)).x()
        assert gap < widget.width() + 32  # the next button follows the field directly
    finally:
        widget.cleanup()
        window.close()
        window.deleteLater()
        process_events()


def test_cleanup(iface, canvas, process_events):
    obj = events()
    before = obj.receivers(obj.languageChanged)
    widget = LocatorSearchWidget(iface)
    assert obj.receivers(obj.languageChanged) == before + 1
    set_crs(canvas, WGS84)
    assert widget.search("KN04ft")
    assert rubber_bands(canvas)
    widget.cleanup()
    widget.cleanup()
    assert rubber_bands(canvas) == []
    assert not widget.is_highlighting()
    assert obj.receivers(obj.languageChanged) == before
    widget.deleteLater()
    process_events()
