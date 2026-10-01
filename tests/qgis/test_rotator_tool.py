"""hamq.gui.rotator_tool.RotatorMapTool: map click -> bearing -> rotator azimuth, beam line."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
from qgis.PyQt.QtCore import QEvent, QPoint, Qt, QTimer
from qgis.PyQt.QtWidgets import QApplication, QMessageBox

from hamq.core import geo, i18n, maidenhead
from hamq.events import events
from hamq.gui.rotator_tool import RotatorMapTool, long_path_parts
from hamq.qgis_io.compat import (
    MSG_CRITICAL,
    MSG_INFO,
    MSG_WARNING,
    MSGBOX_CANCEL,
    MSGBOX_NO,
    MSGBOX_OK,
    MSGBOX_YES,
)

KN04FT = maidenhead.to_latlon("KN04ft")
SYDNEY = (-33.8688, 151.2093)
WASHINGTON = (38.8977, -77.0366)
CHATHAM = (-44.0, -176.5)  # the short path from KN04ft crosses the antimeridian


class Harness:
    """A tool with recording callbacks; ``qth``, ``limits`` and ``answer`` can be changed."""

    def __init__(self, canvas, *, confirmed=False, answer=True, **kwargs):
        self.qth = KN04FT
        self.limits = (0.0, 360.0)
        self.current = None
        self.answer = answer
        self.targets = []
        self.messages = []
        self.questions = []
        self.settings = SimpleNamespace(rot_confirmed=confirmed)
        self.tool = RotatorMapTool(
            canvas,
            lambda: self.qth,
            lambda: self.limits,
            self._on_target,
            self._confirm,
            notify=self.messages.append,
            get_current_az=lambda: self.current,
            settings=self.settings,
            **kwargs,
        )

    def _on_target(self, azimuth, bearing, distance_km, point):
        self.targets.append((azimuth, bearing, distance_km, point))

    def _confirm(self, message):
        self.questions.append(message)
        return self.answer


@pytest.fixture
def canvas(qgis_app, process_events):
    widget = QgsMapCanvas()
    widget.resize(800, 400)
    widget.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    widget.setExtent(QgsRectangle(-180, -90, 180, 90))
    yield widget
    widget.deleteLater()
    process_events()


@pytest.fixture
def harness(canvas):
    made = Harness(canvas)
    yield made
    made.tool.cleanup()


@pytest.fixture
def language():
    def switch(code: str) -> None:
        i18n.set_language(code)
        events().languageChanged.emit(code)

    yield switch
    switch("en")


def lonlat(latlon):
    return QgsPointXY(latlon[1], latlon[0])


def parts(geometry):
    return geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]


def test_click_on_sydney_from_kn04ft(harness):
    azimuth = harness.tool.aim_at(lonlat(SYDNEY))
    assert azimuth == pytest.approx(91.0, abs=0.5)
    assert len(harness.targets) == 1
    sent_az, bearing, distance, point = harness.targets[0]
    assert sent_az == azimuth
    assert bearing == pytest.approx(geo.bearing_deg(*KN04FT, *SYDNEY))
    assert distance == pytest.approx(15676.1, rel=0.005)
    assert (point.x(), point.y()) == pytest.approx((SYDNEY[1], SYDNEY[0]))
    assert harness.tool.last_target == harness.targets[0]
    # the first turn needed a confirmation
    assert len(harness.questions) == 1
    assert "91°" in harness.questions[0]
    assert "km" in harness.questions[0]
    assert harness.settings.rot_confirmed is True
    assert harness.messages == []


def test_click_in_a_projected_canvas(canvas):
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    made = Harness(canvas, confirmed=True)
    try:
        from qgis.core import QgsCoordinateTransform, QgsProject

        to_mercator = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsCoordinateReferenceSystem("EPSG:3857"),
            QgsProject.instance(),
        )
        azimuth = made.tool.aim_at(to_mercator.transform(lonlat(SYDNEY)))
        assert azimuth == pytest.approx(geo.bearing_deg(*KN04FT, *SYDNEY), abs=1e-6)
        point = made.targets[0][3]
        assert (point.x(), point.y()) == pytest.approx((SYDNEY[1], SYDNEY[0]), abs=1e-6)
        # the beam line is drawn in the canvas CRS
        line = parts(made.tool.beam_geometry())[0]
        assert abs(line[-1].x()) > 1e6  # metres, not degrees
    finally:
        made.tool.cleanup()


def test_point_in_another_crs(harness):
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    from qgis.core import QgsCoordinateTransform, QgsProject

    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsProject.instance()
    )
    azimuth = harness.tool.aim_at(xform.transform(lonlat(SYDNEY)), crs)
    assert azimuth == pytest.approx(geo.bearing_deg(*KN04FT, *SYDNEY), abs=1e-6)


def test_range_0_180_rejects_a_west_bearing(harness):
    harness.settings.rot_confirmed = True
    harness.limits = (0.0, 180.0)
    assert harness.tool.aim_at(lonlat(WASHINGTON)) is None  # bearing about 304
    assert harness.targets == []
    assert harness.tool.beam_geometry() is None
    assert harness.messages == ["The rotator cannot turn to azimuth 304°: its range is 0° to 180°."]
    # east is fine
    assert harness.tool.aim_at(lonlat(SYDNEY)) == pytest.approx(91.0, abs=0.5)


@pytest.mark.parametrize(
    ("limits", "current", "bearing", "expected"),
    [
        ((0.0, 450.0), 400.0, 50.0, 410.0),  # overlap: stay near the current position
        ((0.0, 450.0), 10.0, 50.0, 50.0),
        ((0.0, 450.0), None, 50.0, 50.0),
        ((-180.0, 180.0), None, 300.0, -60.0),  # south stop
        ((180.0, 540.0), None, 90.0, 450.0),
    ],
)
def test_bearing_is_mapped_into_the_rotator_range(harness, limits, current, bearing, expected):
    harness.settings.rot_confirmed = True
    harness.limits = limits
    harness.current = current
    lat, lon = geo.destination(*KN04FT, bearing, 2000.0)
    azimuth = harness.tool.aim_at(QgsPointXY(lon, lat))
    assert azimuth == pytest.approx(expected, abs=1e-6)
    assert harness.targets[-1][1] == pytest.approx(bearing, abs=1e-6)


@pytest.mark.parametrize(
    "limits", [(180.0, 0.0), (0.0, 0.0), (float("nan"), 360.0), (0.0,), None, "0-360"]
)
def test_invalid_rotator_range(harness, limits):
    harness.settings.rot_confirmed = True
    harness.limits = limits
    assert harness.tool.aim_at(lonlat(SYDNEY)) is None
    assert harness.targets == []
    assert len(harness.messages) == 1
    assert "is not valid; check it in Settings." in harness.messages[0]


def test_beam_line_follows_the_great_circle(harness):
    harness.tool.aim_at(lonlat(SYDNEY))
    geometry = harness.tool.beam_geometry()
    expected = geo.great_circle(*KN04FT, *SYDNEY, 100.0)
    lines = parts(geometry)
    assert len(lines) == len(expected) == 1
    assert len(lines[0]) == len(expected[0])
    assert (lines[0][0].x(), lines[0][0].y()) == pytest.approx((KN04FT[1], KN04FT[0]))
    assert (lines[0][-1].x(), lines[0][-1].y()) == pytest.approx((SYDNEY[1], SYDNEY[0]))
    for vertex, (lat, lon) in zip(lines[0], expected[0]):
        assert (vertex.x(), vertex.y()) == pytest.approx((lon, lat))


def test_beam_line_is_split_at_the_antimeridian(harness):
    harness.tool.aim_at(lonlat(CHATHAM))
    expected = geo.great_circle(*KN04FT, *CHATHAM, 100.0)
    lines = parts(harness.tool.beam_geometry())
    assert len(expected) == 2
    assert len(lines) == 2
    assert lines[0][-1].x() == pytest.approx(180.0)
    assert lines[1][0].x() == pytest.approx(-180.0)
    assert lines[0][-1].y() == pytest.approx(lines[1][0].y())
    # no segment jumps across the map
    for line in lines:
        for a, b in zip(line, line[1:]):
            assert abs(a.x() - b.x()) < 10


def test_step_km_sets_the_vertex_spacing(canvas):
    made = Harness(canvas, confirmed=True, step_km=1000.0)
    try:
        made.tool.aim_at(lonlat(SYDNEY))
        assert len(parts(made.tool.beam_geometry())[0]) == 17  # 15676 km / 1000 km + 1
    finally:
        made.tool.cleanup()


@pytest.mark.parametrize("step_km", [0.0, -5.0, float("nan"), float("inf")])
def test_invalid_step_is_a_programming_error(canvas, step_km):
    with pytest.raises(ValueError):
        RotatorMapTool(canvas, lambda: KN04FT, lambda: (0, 360), lambda *a: None, step_km=step_km)


def test_no_qth(harness):
    harness.qth = None
    assert harness.tool.aim_at(lonlat(SYDNEY)) is None
    assert harness.messages == [
        "Set your QTH locator in Settings to turn the antenna from the map."
    ]
    assert harness.targets == []
    assert harness.questions == []
    assert harness.tool.beam_geometry() is None


@pytest.mark.parametrize("qth", [(float("nan"), 20.0), (95.0, 20.0), ("44", "20"), (44.0,)])
def test_unusable_qth(harness, qth):
    harness.qth = qth
    assert harness.tool.aim_at(lonlat(SYDNEY)) is None
    assert len(harness.messages) == 1


def test_click_on_my_qth(harness):
    assert harness.tool.aim_at(lonlat(KN04FT)) is None
    assert harness.messages == ["This point is at your QTH; click farther away."]
    assert harness.targets == []


def test_click_on_the_antipode(harness):
    antipode = QgsPointXY(KN04FT[1] - 180.0, -KN04FT[0])
    assert harness.tool.aim_at(antipode) is None
    assert harness.messages == [
        "This point is at the antipode of your QTH: every direction leads there."
    ]


def test_click_outside_the_world(harness):
    assert harness.tool.aim_at(QgsPointXY(20.0, 120.0)) is None
    assert harness.messages == [
        "The clicked point could not be converted to latitude and longitude."
    ]


def test_click_beyond_the_date_line_wraps(harness):
    harness.settings.rot_confirmed = True
    harness.tool.aim_at(QgsPointXY(SYDNEY[1] - 360.0, SYDNEY[0]))
    point = harness.targets[0][3]
    assert point.x() == pytest.approx(SYDNEY[1])


def test_declined_confirmation_sends_nothing(harness):
    harness.answer = False
    assert harness.tool.aim_at(lonlat(SYDNEY)) is None
    assert harness.targets == []
    assert harness.settings.rot_confirmed is False
    assert harness.tool.beam_geometry() is None
    # asked again next time, then remembered
    harness.answer = True
    assert harness.tool.aim_at(lonlat(SYDNEY)) is not None
    assert harness.tool.aim_at(lonlat(WASHINGTON)) is not None
    assert len(harness.questions) == 2
    assert len(harness.targets) == 2


def test_no_question_when_already_confirmed(canvas):
    made = Harness(canvas, confirmed=True)
    try:
        assert made.tool.aim_at(lonlat(SYDNEY)) is not None
        assert made.questions == []
    finally:
        made.tool.cleanup()


def test_mouse_clicks(harness, canvas):
    harness.settings.rot_confirmed = True
    pixel = canvas.getCoordinateTransform().transform(lonlat(SYDNEY))
    position = QPoint(round(pixel.x()), round(pixel.y()))
    release = QgsMapMouseEvent(
        canvas,
        QEvent.Type.MouseButtonRelease,
        position,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    harness.tool.canvasReleaseEvent(release)
    assert len(harness.targets) == 1
    assert harness.targets[0][1] == pytest.approx(91.0, abs=1.0)
    assert harness.tool.beam_geometry() is not None
    right = QgsMapMouseEvent(
        canvas,
        QEvent.Type.MouseButtonRelease,
        position,
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    harness.tool.canvasReleaseEvent(right)
    assert harness.tool.beam_geometry() is None
    assert len(harness.targets) == 1


def test_deactivate_clears_the_beam(harness, canvas):
    canvas.setMapTool(harness.tool)
    assert canvas.mapTool() is harness.tool
    harness.tool.aim_at(lonlat(SYDNEY))
    assert harness.tool.beam_geometry() is not None
    canvas.unsetMapTool(harness.tool)
    assert harness.tool.beam_geometry() is None


def test_errors_are_logged_not_raised(canvas, log_messages):
    def broken(*args):
        raise RuntimeError("rotator exploded")

    tool = RotatorMapTool(
        canvas,
        lambda: KN04FT,
        lambda: (0.0, 360.0),
        broken,
        lambda message: True,
        notify=lambda message: None,
        settings=SimpleNamespace(rot_confirmed=True),
    )
    try:
        assert tool.aim_at(lonlat(SYDNEY)) == pytest.approx(91.0, abs=0.5)
        critical = [m for m in log_messages if m[1] == "HamQ" and m[2] == MSG_CRITICAL]
        assert len(critical) == 1
        assert "rotator exploded" in critical[0][0]
        # an exception inside a mouse event is logged too
        tool._get_station_latlon = broken
        event = QgsMapMouseEvent(
            canvas,
            QEvent.Type.MouseButtonRelease,
            QPoint(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        tool.canvasReleaseEvent(event)
        assert len([m for m in log_messages if m[2] == MSG_CRITICAL]) == 2
    finally:
        tool.cleanup()


def test_default_notify_logs_the_message(canvas, log_messages):
    tool = RotatorMapTool(
        canvas,
        lambda: None,
        lambda: (0.0, 360.0),
        lambda *args: None,
        settings=SimpleNamespace(rot_confirmed=True),
    )
    try:
        tool.aim_at(lonlat(SYDNEY))
        assert (
            "Set your QTH locator in Settings to turn the antenna from the map.",
            "HamQ",
            MSG_INFO,
        ) in log_messages
    finally:
        tool.cleanup()


def test_messages_are_translated(harness, language):
    language("sr_Latn")
    harness.qth = None
    harness.tool.aim_at(lonlat(SYDNEY))
    assert harness.messages == [
        "Unesite svoj QTH lokator u podešavanjima da biste antenu okretali klikom na mapu."
    ]
    language("sr_Cyrl")
    harness.qth = KN04FT
    harness.tool.aim_at(lonlat(SYDNEY))
    assert harness.questions[-1].startswith("Окренути антену на азимут 91°")


def test_cleanup_is_idempotent(harness, canvas):
    canvas.setMapTool(harness.tool)
    harness.tool.aim_at(lonlat(SYDNEY))
    items_with_beam = len(canvas.scene().items())
    harness.tool.cleanup()
    assert canvas.mapTool() is None
    assert harness.tool.beam_geometry() is None
    assert len(canvas.scene().items()) == items_with_beam - 1
    harness.tool.cleanup()
    # the tool still works after a cleanup: a new rubber band is made
    harness.settings.rot_confirmed = True
    assert harness.tool.aim_at(lonlat(SYDNEY)) is not None
    assert math.isclose(harness.targets[-1][1], harness.targets[0][1])


# --------------------------------------------------------------------------- long path


def vertices(geometry):
    return [(point.y(), point.x()) for line in parts(geometry) for point in line]


def path_length_km(lines) -> float:
    """Great-circle length of polylines given as lists of QgsPointXY (lon/lat)."""
    total = 0.0
    for line in lines:
        for a, b in zip(line, line[1:]):
            total += geo.distance_km(a.y(), a.x(), b.y(), b.x())
    return total


def test_long_path_turns_the_other_way(canvas):
    made = Harness(canvas, get_long_path=lambda: True)
    try:
        short_bearing = geo.bearing_deg(*KN04FT, *SYDNEY)
        short_distance = geo.distance_km(*KN04FT, *SYDNEY)
        azimuth = made.tool.aim_at(lonlat(SYDNEY))
        assert azimuth == pytest.approx((short_bearing + 180.0) % 360.0)
        sent_az, bearing, distance, point = made.targets[0]
        assert bearing == pytest.approx(271.0, abs=0.5)
        assert distance == pytest.approx(geo.long_path(short_distance, short_bearing)[0])
        assert distance == pytest.approx(40030.2 - 15676.1, rel=0.005)
        assert (point.x(), point.y()) == pytest.approx((SYDNEY[1], SYDNEY[0]))
        assert "long path" in made.questions[0]
        assert "271°" in made.questions[0]
        # the beam follows the long path: from my QTH, through the antipode of Sydney,
        # to Sydney, split where it crosses the antimeridian
        lines = parts(made.tool.beam_geometry())
        assert len(lines) == 2  # westward over the Americas, across the antimeridian
        assert (lines[0][0].x(), lines[0][0].y()) == pytest.approx((KN04FT[1], KN04FT[0]))
        assert lines[0][-1].x() == pytest.approx(-180.0)
        assert lines[1][0].x() == pytest.approx(180.0)
        assert (lines[-1][-1].x(), lines[-1][-1].y()) == pytest.approx((SYDNEY[1], SYDNEY[0]))
        assert path_length_km(lines) == pytest.approx(distance, rel=0.002)
        for line in lines:
            for a, b in zip(line, line[1:]):
                assert abs(a.x() - b.x()) < 10  # no segment jumps across the map
        antipode = (-SYDNEY[0], SYDNEY[1] - 180.0)
        nearest = min(
            geo.distance_km(lat, lon, *antipode) for lat, lon in vertices(made.tool.beam_geometry())
        )
        assert nearest < 100.0
    finally:
        made.tool.cleanup()


def test_long_path_follows_the_dock_checkbox(canvas):
    state = {"long": False}
    made = Harness(canvas, confirmed=True, get_long_path=lambda: state["long"])
    try:
        made.tool.aim_at(lonlat(SYDNEY))
        state["long"] = True
        made.tool.aim_at(lonlat(SYDNEY))
        bearings = [target[1] for target in made.targets]
        assert bearings[0] == pytest.approx(91.0, abs=0.5)
        assert bearings[1] == pytest.approx(271.0, abs=0.5)
    finally:
        made.tool.cleanup()


def test_long_path_outside_the_rotator_range(canvas):
    made = Harness(canvas, confirmed=True, get_long_path=lambda: True)
    try:
        made.limits = (0.0, 180.0)
        assert made.tool.aim_at(lonlat(SYDNEY)) is None
        assert made.messages == [
            "The rotator cannot turn to azimuth 271°: its range is 0° to 180°."
        ]
        assert made.tool.beam_geometry() is None
    finally:
        made.tool.cleanup()


def test_long_path_parts():
    lines = long_path_parts(*KN04FT, *CHATHAM, 100.0)
    short = geo.distance_km(*KN04FT, *CHATHAM)
    expected_length = geo.long_path(short, 0.0)[0]
    assert lines[0][0] == pytest.approx(KN04FT)
    assert lines[-1][-1] == pytest.approx(CHATHAM)
    total = 0.0
    for line in lines:
        assert len(line) >= 2
        for (lat1, lon1), (lat2, lon2) in zip(line, line[1:]):
            step = geo.distance_km(lat1, lon1, lat2, lon2)
            assert step <= 100.0 + 1e-6
            assert abs(lon1 - lon2) < 10
            total += step
    assert total == pytest.approx(expected_length, rel=0.001)
    # the long path leaves in the opposite direction of the short path
    first_bearing = geo.bearing_deg(*lines[0][0], *lines[0][1])
    assert first_bearing == pytest.approx(
        (geo.bearing_deg(*KN04FT, *CHATHAM) + 180.0) % 360.0, abs=0.01
    )
    # where it crosses the antimeridian, one part ends at +-180 and the next starts at -+180
    for left, right in zip(lines, lines[1:]):
        assert abs(left[-1][1]) == pytest.approx(180.0)
        assert right[0][1] == pytest.approx(-left[-1][1])
        assert right[0][0] == pytest.approx(left[-1][0])


def test_long_path_parts_without_a_great_circle():
    assert long_path_parts(*KN04FT, *KN04FT) == []
    assert long_path_parts(*KN04FT, -KN04FT[0], KN04FT[1] - 180.0) == []  # antipode


# --------------------------------------------------------------------------- defaults


def answer_message_box(button, seen):
    """Answer the next modal ``QMessageBox`` with the standard ``button``.

    What the user saw is appended to ``seen``: title, text, text format, icon, parent,
    default and escape button, and ``{standard button: text}`` of every button. Works
    whichever way the box is made (a static ``QMessageBox.question`` too) and never
    leaves a box open: a box without ``button`` is rejected and the problem recorded.
    """
    tries = []

    def attempt():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            tries.append(1)
            if len(tries) < 300:  # about 3 s
                QTimer.singleShot(10, attempt)
            return
        try:
            default = box.defaultButton()
            escape = box.escapeButton()
            seen.append(
                {
                    "title": box.windowTitle(),
                    "text": box.text(),
                    "format": box.textFormat(),
                    "icon": box.icon(),
                    "parent": box.parentWidget(),
                    "buttons": {box.standardButton(b): b.text() for b in box.buttons()},
                    "default": box.standardButton(default) if default is not None else None,
                    "escape": box.standardButton(escape) if escape is not None else None,
                }
            )
            box.button(button).click()
        except Exception as exc:  # never raise into Qt; never leave the box open
            seen.append({"error": repr(exc)})
            box.reject()

    QTimer.singleShot(0, attempt)


def default_tool(canvas, settings, targets):
    """A tool with the default confirmation (no ``confirm`` callback)."""
    return RotatorMapTool(
        canvas,
        lambda: KN04FT,
        lambda: (0.0, 360.0),
        lambda *args: targets.append(args),
        settings=settings,
    )


def test_default_confirmation_is_a_question_box(canvas):
    seen, targets = [], []
    settings = SimpleNamespace(rot_confirmed=False)
    tool = default_tool(canvas, settings, targets)
    try:
        answer_message_box(MSGBOX_YES, seen)
        assert tool.aim_at(lonlat(SYDNEY)) == pytest.approx(91.0, abs=0.5)
    finally:
        tool.cleanup()
    assert len(seen) == 1, seen
    box = seen[0]
    assert box["parent"] is canvas.window()
    assert box["title"] == "Turn the rotator"
    assert box["text"].startswith("Turn the antenna to azimuth 91°")
    assert box["format"] == Qt.TextFormat.PlainText
    assert box["icon"] == QMessageBox.Icon.Question
    assert box["buttons"] == {MSGBOX_YES: "Turn", MSGBOX_NO: "Cancel"}
    assert box["default"] == MSGBOX_NO  # Enter does not turn the antenna
    assert box["escape"] == MSGBOX_NO
    assert settings.rot_confirmed is True
    assert len(targets) == 1


def test_default_confirmation_no_sends_nothing(canvas):
    seen, targets = [], []
    settings = SimpleNamespace(rot_confirmed=False)
    tool = default_tool(canvas, settings, targets)
    try:
        answer_message_box(MSGBOX_NO, seen)
        assert tool.aim_at(lonlat(SYDNEY)) is None
        assert tool.beam_geometry() is None
    finally:
        tool.cleanup()
    assert len(seen) == 1 and "error" not in seen[0], seen
    assert settings.rot_confirmed is False
    assert targets == []


@pytest.mark.parametrize(
    ("code", "title", "turn", "cancel"),
    [
        ("sr_Latn", "Okretanje rotatora", "Okreni", "Otkaži"),
        ("sr_Cyrl", "Окретање ротатора", "Окрени", "Откажи"),
    ],
)
def test_default_confirmation_buttons_follow_the_hamq_language(
    canvas, language, code, title, turn, cancel
):
    """Qt has no Serbian catalog of its own: the box must not show "&Yes" / "&No"."""
    language(code)
    seen, targets = [], []
    tool = default_tool(canvas, SimpleNamespace(rot_confirmed=False), targets)
    try:
        answer_message_box(MSGBOX_YES, seen)
        assert tool.aim_at(lonlat(SYDNEY)) is not None
    finally:
        tool.cleanup()
    assert len(seen) == 1, seen
    assert seen[0]["title"] == title
    assert seen[0]["buttons"] == {MSGBOX_YES: turn, MSGBOX_NO: cancel}
    assert len(targets) == 1


@pytest.mark.parametrize(
    ("code", "names"),
    [
        ("en", {MSGBOX_YES: "Yes", MSGBOX_NO: "No", MSGBOX_CANCEL: "Cancel"}),
        ("sr_Latn", {MSGBOX_YES: "Da", MSGBOX_NO: "Ne", MSGBOX_CANCEL: "Otkaži"}),
        ("sr_Cyrl", {MSGBOX_YES: "Да", MSGBOX_NO: "Не", MSGBOX_CANCEL: "Откажи"}),
    ],
)
def test_message_box_question_names_every_button(qgis_app, language, code, names):
    """hamq.gui.message_box.MessageBox: the static question() of HamQ's message box."""
    from hamq.gui.message_box import MessageBox

    language(code)
    seen = []
    answer_message_box(MSGBOX_CANCEL, seen)
    buttons = MSGBOX_YES | MSGBOX_NO | MSGBOX_CANCEL
    assert MessageBox.question(None, "T", "<b>x</b>", buttons, MSGBOX_YES) == MSGBOX_CANCEL
    assert len(seen) == 1, seen
    assert seen[0]["buttons"] == names
    assert seen[0]["default"] == MSGBOX_YES
    assert seen[0]["escape"] == MSGBOX_CANCEL  # Cancel before No when the box has both
    assert seen[0]["format"] == Qt.TextFormat.PlainText
    assert seen[0]["text"] == "<b>x</b>"


@pytest.mark.parametrize(
    ("code", "ok"), [("en", "OK"), ("sr_Latn", "U redu"), ("sr_Cyrl", "У реду")]
)
def test_message_box_about_names_ok(qgis_app, language, code, ok):
    """MessageBox.about(): like QMessageBox.about() (rich text, one button), OK translated."""
    from hamq.gui.message_box import MessageBox

    language(code)
    seen = []
    answer_message_box(MSGBOX_OK, seen)
    assert MessageBox.about(None, "About HamQ", "<p><b>HamQ</b></p>") is None
    assert len(seen) == 1, seen
    assert seen[0]["title"] == "About HamQ"
    assert seen[0]["buttons"] == {MSGBOX_OK: ok}
    assert seen[0]["format"] == Qt.TextFormat.AutoText  # the About text is HTML


def test_default_notify_uses_the_message_bar(canvas, monkeypatch, log_messages):
    import qgis.utils

    pushed = []
    bar = SimpleNamespace(pushMessage=lambda *args: pushed.append(args))
    monkeypatch.setattr(qgis.utils, "iface", SimpleNamespace(messageBar=lambda: bar))
    tool = RotatorMapTool(
        canvas,
        lambda: None,
        lambda: (0.0, 360.0),
        lambda *args: None,
        settings=SimpleNamespace(rot_confirmed=True),
    )
    try:
        tool.aim_at(lonlat(SYDNEY))
        message = "Set your QTH locator in Settings to turn the antenna from the map."
        assert pushed == [("HamQ", message, MSG_WARNING, 6)]
        assert (message, "HamQ", MSG_INFO) in log_messages
    finally:
        tool.cleanup()


def test_clicks_are_also_emitted_like_any_emit_point_tool(harness, canvas):
    clicked = []
    harness.tool.canvasClicked.connect(lambda point, button: clicked.append((point, button)))
    harness.settings.rot_confirmed = True
    for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
        event = QgsMapMouseEvent(
            canvas,
            QEvent.Type.MouseButtonRelease,
            QPoint(400, 200),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )
        harness.tool.canvasReleaseEvent(event)
    assert [button for _, button in clicked] == [
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.RightButton,
    ]
    expected = canvas.getCoordinateTransform().toMapCoordinates(QPoint(400, 200))
    assert (clicked[0][0].x(), clicked[0][0].y()) == pytest.approx((expected.x(), expected.y()))
    assert len(harness.targets) == 1  # the left click also aimed


def test_too_small_step_still_turns_without_a_beam(canvas, log_messages):
    made = Harness(canvas, confirmed=True, step_km=0.001)
    try:
        assert made.tool.aim_at(lonlat(SYDNEY)) == pytest.approx(91.0, abs=0.5)
        assert made.tool.beam_geometry() is None
        assert len(made.targets) == 1
        assert [m for m in log_messages if m[2] == MSG_CRITICAL]
    finally:
        made.tool.cleanup()
