"""hamq.qgis_io.compat resolves every name on QGIS 3.34, 3.44, 4.0 and 4.2."""

from __future__ import annotations

import enum

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsTask,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QBuffer, QObject
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtNetwork import QHostAddress, QNetworkRequest, QUdpSocket
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from hamq.qgis_io import compat
from hamq.qgis_io.fields import make_fields

# name -> (accepted numeric values, accepted enum type names). Numeric values are
# the stable C++ values; RedirectPolicyAttribute differs between Qt5 and Qt6.
EXPECTED = {
    "WKB_POINT": ({1}, {"WkbType"}),
    "WKB_LINESTRING": ({2}, {"WkbType"}),
    "WKB_MULTILINESTRING": ({5}, {"WkbType"}),
    "WKB_POLYGON": ({3}, {"WkbType"}),
    "WKB_MULTIPOLYGON": ({6}, {"WkbType"}),
    "WKB_NO_GEOMETRY": ({100}, {"WkbType"}),
    "GEOMETRY_POINT": ({0}, {"GeometryType"}),
    "GEOMETRY_LINE": ({1}, {"GeometryType"}),
    "GEOMETRY_POLYGON": ({2}, {"GeometryType"}),
    "LAYER_TYPE_VECTOR": ({0}, {"LayerType"}),
    "DISTANCE_KILOMETERS": ({1}, {"DistanceUnit"}),
    "SOURCE_VECTOR_POINT": ({0}, {"ProcessingSourceType", "SourceType"}),
    "SOURCE_VECTOR_LINE": ({1}, {"ProcessingSourceType", "SourceType"}),
    "SOURCE_VECTOR_POLYGON": ({2}, {"ProcessingSourceType", "SourceType"}),
    "SOURCE_VECTOR_ANY_GEOMETRY": ({-1}, {"ProcessingSourceType", "SourceType"}),
    "FILE_BEHAVIOR_FILE": ({0}, {"ProcessingFileParameterBehavior", "Behavior"}),
    "FILE_BEHAVIOR_FOLDER": ({1}, {"ProcessingFileParameterBehavior", "Behavior"}),
    "NUMBER_INTEGER": ({0}, {"ProcessingNumberParameterType", "Type"}),
    "NUMBER_DOUBLE": ({1}, {"ProcessingNumberParameterType", "Type"}),
    "ALG_FLAG_NO_THREADING": ({64}, {"ProcessingAlgorithmFlag", "Flag"}),
    "PARAM_FLAG_OPTIONAL": ({8}, {"ProcessingParameterFlag", "Flag"}),
    "PARAM_FLAG_ADVANCED": ({2}, {"ProcessingParameterFlag", "Flag"}),
    "MSG_INFO": ({0}, {"MessageLevel"}),
    "MSG_WARNING": ({1}, {"MessageLevel"}),
    "MSG_CRITICAL": ({2}, {"MessageLevel"}),
    "MSG_SUCCESS": ({3}, {"MessageLevel"}),
    "WRITER_CREATE_OR_OVERWRITE_FILE": ({0}, {"ActionOnExistingFile"}),
    "WRITER_CREATE_OR_OVERWRITE_LAYER": ({1}, {"ActionOnExistingFile"}),
    "WRITER_APPEND_TO_LAYER_NO_NEW_FIELDS": ({2}, {"ActionOnExistingFile"}),
    "WRITER_NO_ERROR": ({0}, {"WriterError"}),
    "SINK_FAST_INSERT": ({2}, {"Flag"}),
    "REQUEST_NO_GEOMETRY": ({1}, {"FeatureRequestFlag", "Flag"}),
    "TASK_CAN_CANCEL": ({2}, {"Flag"}),
    "FIELD_TYPE_INT": ({2}, {"Type"}),
    "FIELD_TYPE_REAL": ({6}, {"Type"}),
    "FIELD_TYPE_TEXT": ({10}, {"Type"}),
    "FIELD_TYPE_DATETIME": ({16}, {"Type"}),
    "DOCK_LEFT": ({1}, {"DockWidgetArea"}),
    "DOCK_RIGHT": ({2}, {"DockWidgetArea"}),
    "DOCK_BOTTOM": ({8}, {"DockWidgetArea"}),
    "DOCK_ALL": ({15}, {"DockWidgetArea"}),
    "DIALOG_OK": ({0x400}, {"StandardButton"}),
    "DIALOG_CANCEL": ({0x400000}, {"StandardButton"}),
    "DIALOG_CLOSE": ({0x200000}, {"StandardButton"}),
    "DIALOG_SAVE": ({0x800}, {"StandardButton"}),
    "DIALOG_APPLY": ({0x2000000}, {"StandardButton"}),
    "DIALOG_HELP": ({0x1000000}, {"StandardButton"}),
    "DIALOG_ACCEPTED": ({1}, {"DialogCode"}),
    "DIALOG_REJECTED": ({0}, {"DialogCode"}),
    "MSGBOX_OK": ({0x400}, {"StandardButton"}),
    "MSGBOX_YES": ({0x4000}, {"StandardButton"}),
    "MSGBOX_NO": ({0x10000}, {"StandardButton"}),
    "MSGBOX_CANCEL": ({0x400000}, {"StandardButton"}),
    "HEADER_STRETCH": ({1}, {"ResizeMode"}),
    "HEADER_RESIZE_TO_CONTENTS": ({3}, {"ResizeMode"}),
    "HEADER_INTERACTIVE": ({0}, {"ResizeMode"}),
    "EDIT_NO_TRIGGERS": ({0}, {"EditTrigger"}),
    "SELECT_ROWS": ({1}, {"SelectionBehavior"}),
    "SELECTION_SINGLE": ({1}, {"SelectionMode"}),
    "SELECTION_NONE": ({0}, {"SelectionMode"}),
    "SIZE_FIXED": ({0}, {"Policy"}),
    "SIZE_MINIMUM": ({1}, {"Policy"}),
    "SIZE_PREFERRED": ({5}, {"Policy"}),
    "SIZE_EXPANDING": ({7}, {"Policy"}),
    "SIZE_MINIMUM_EXPANDING": ({3}, {"Policy"}),
    "ALIGN_LEFT": ({0x1}, {"AlignmentFlag"}),
    "ALIGN_RIGHT": ({0x2}, {"AlignmentFlag"}),
    "ALIGN_HCENTER": ({0x4}, {"AlignmentFlag"}),
    "ALIGN_VCENTER": ({0x80}, {"AlignmentFlag"}),
    "ALIGN_CENTER": ({0x84}, {"AlignmentFlag"}),
    "ALIGN_TOP": ({0x20}, {"AlignmentFlag"}),
    "TOOLBUTTON_INSTANT_POPUP": ({2}, {"ToolButtonPopupMode"}),
    "TOOLBUTTON_TEXT_ONLY": ({1}, {"ToolButtonStyle"}),
    "TOOLBUTTON_TEXT_BESIDE_ICON": ({2}, {"ToolButtonStyle"}),
    "USER_ROLE": ({0x100}, {"ItemDataRole"}),
    "TEXT_RICH": ({1}, {"TextFormat"}),
    "TEXT_BROWSER_INTERACTION": ({13}, {"TextInteractionFlag"}),
    "CURSOR_CROSS": ({2}, {"CursorShape"}),
    "CURSOR_WAIT": ({3}, {"CursorShape"}),
    "HOST_ANY_IPV4": ({6}, {"SpecialAddress"}),
    "HOST_LOCALHOST": ({2}, {"SpecialAddress"}),
    "BIND_SHARE_ADDRESS": ({0x1}, {"BindFlag"}),
    "BIND_REUSE_ADDRESS_HINT": ({0x4}, {"BindFlag"}),
    "SOCKET_UNCONNECTED": ({0}, {"SocketState"}),
    "SOCKET_CONNECTING": ({2}, {"SocketState"}),
    "SOCKET_CONNECTED": ({3}, {"SocketState"}),
    "SOCKET_BOUND": ({4}, {"SocketState"}),
    "SOCKET_ERROR_ADDRESS_IN_USE": ({8}, {"SocketError"}),
    "SOCKET_ERROR_REMOTE_CLOSED": ({1}, {"SocketError"}),  # M7-02
    "NET_PROXY_NONE": ({2}, {"ProxyType"}),  # M7-02
    "NET_ATTR_CACHE_LOAD_CONTROL": ({4}, {"Attribute"}),  # M4-03
    "NET_ATTR_CACHE_SAVE_CONTROL": ({5}, {"Attribute"}),  # M4-03
    "NET_CACHE_ALWAYS_NETWORK": ({0}, {"CacheLoadControl"}),  # M4-03
    "NETIF_IS_UP": ({0x1}, {"InterfaceFlag"}),  # M5-02
    "NETIF_IS_LOOPBACK": ({0x8}, {"InterfaceFlag"}),  # M5-02
    "NETIF_CAN_MULTICAST": ({0x20}, {"InterfaceFlag"}),  # M5-02
    "TOOLBUTTON_MENU_BUTTON_POPUP": ({1}, {"ToolButtonPopupMode"}),  # M6-02
    "LABEL_PLACEMENT_LINE": ({2}, {"LabelPlacement"}),  # M3-03
    "FILE_DIALOG_DONT_CONFIRM_OVERWRITE": ({0x4}, {"Option"}),  # M0-03
    "LABEL_PLACEMENT_OVER_POINT": ({1}, {"LabelPlacement"}),  # M3-02
    "LABEL_PROPERTY_SHOW": ({15}, {"Property"}),  # M3-02
    "STYLE_CATEGORY_SYMBOLOGY": ({2}, {"StyleCategory"}),  # M3-02
    "STYLE_CATEGORY_LABELING": ({8}, {"StyleCategory"}),  # M3-02
    "BRUSH_NONE": ({0}, {"BrushStyle"}),  # M3-02
    "NET_ATTR_HTTP_STATUS": ({0}, {"Attribute"}),
    "NET_ATTR_REDIRECT_POLICY": ({22, 25}, {"Attribute"}),
    "NET_REDIRECT_NO_LESS_SAFE": ({1}, {"RedirectPolicy"}),
    "NET_HEADER_USER_AGENT": ({7}, {"KnownHeaders"}),
    "NET_NO_ERROR": ({0}, {"NetworkError"}),
    "NET_OPERATION_CANCELED": ({5}, {"NetworkError"}),
    "IO_READ_ONLY": ({0x1}, {"OpenModeFlag"}),
    "IO_WRITE_ONLY": ({0x2}, {"OpenModeFlag"}),
    "IO_TEXT": ({0x10}, {"OpenModeFlag"}),
}
CLASSES = {"QAction": "QAction", "QActionGroup": "QActionGroup"}


def numeric(value) -> int:
    """C++ value of a PyQt5 (int subclass) or PyQt6 / QGIS (enum.Enum) enum member."""
    return value.value if isinstance(value, enum.Enum) else int(value)


def test_nothing_missing():
    assert compat.MISSING == []


def test_every_name_has_an_expectation():
    assert sorted(compat.NAMES) == sorted(list(EXPECTED) + list(CLASSES))
    assert len(compat.NAMES) == len(set(compat.NAMES))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_value_and_type(name):
    value = getattr(compat, name)
    values, type_names = EXPECTED[name]
    assert value is not None
    assert numeric(value) in values
    assert type(value).__name__ in type_names


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_moved_classes(name, qgis_app):
    cls = getattr(compat, name)
    assert isinstance(cls, type)
    assert cls.__name__ == CLASSES[name]
    assert isinstance(cls(QObject()), QObject)


def test_versions():
    assert compat.QGIS_VERSION_INT >= 33400
    assert compat.QT_MAJOR in (5, 6)
    assert compat.IS_QT6 == (compat.QT_MAJOR == 6)
    assert compat.FIELD_TYPES_USE_QMETATYPE == (compat.QGIS_VERSION_INT >= 33800)


# --- QGIS core: geometry and layers -------------------------------------------


@pytest.mark.parametrize(
    ("uri", "wkb", "geometry"),
    [
        ("Point?crs=EPSG:4326", "WKB_POINT", "GEOMETRY_POINT"),
        ("LineString?crs=EPSG:4326", "WKB_LINESTRING", "GEOMETRY_LINE"),
        ("MultiLineString?crs=EPSG:4326", "WKB_MULTILINESTRING", "GEOMETRY_LINE"),
        ("Polygon?crs=EPSG:4326", "WKB_POLYGON", "GEOMETRY_POLYGON"),
        ("MultiPolygon?crs=EPSG:4326", "WKB_MULTIPOLYGON", "GEOMETRY_POLYGON"),
    ],
)
def test_layer_types(uri, wkb, geometry):
    layer = QgsVectorLayer(uri, "test", "memory")
    assert layer.isValid()
    assert layer.wkbType() == getattr(compat, wkb)
    assert layer.geometryType() == getattr(compat, geometry)
    assert layer.type() == compat.LAYER_TYPE_VECTOR


def test_no_geometry_layer():
    layer = QgsVectorLayer("None", "table", "memory")
    assert layer.wkbType() == compat.WKB_NO_GEOMETRY


def test_distance_kilometers():
    area = QgsDistanceArea()
    area.setSourceCrs(
        QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext()
    )
    area.setEllipsoid("WGS84")
    assert area.convertLengthMeasurement(1500.0, compat.DISTANCE_KILOMETERS) == pytest.approx(1.5)


# --- Processing ----------------------------------------------------------------


def test_source_types_in_parameter():
    types = [
        compat.SOURCE_VECTOR_POINT,
        compat.SOURCE_VECTOR_LINE,
        compat.SOURCE_VECTOR_POLYGON,
        compat.SOURCE_VECTOR_ANY_GEOMETRY,
    ]
    param = QgsProcessingParameterFeatureSource("INPUT", "Input", types)
    assert [numeric(t) for t in param.dataTypes()] == [0, 1, 2, -1]


def test_file_behavior_in_parameter():
    param = QgsProcessingParameterFile(
        "INPUT", "ADIF file", behavior=compat.FILE_BEHAVIOR_FILE, extension="adi"
    )
    assert param.behavior() == compat.FILE_BEHAVIOR_FILE
    folder = QgsProcessingParameterFile("DIR", "Folder", behavior=compat.FILE_BEHAVIOR_FOLDER)
    assert folder.behavior() == compat.FILE_BEHAVIOR_FOLDER


def test_number_types_in_parameter():
    integer = QgsProcessingParameterNumber("N", "N", type=compat.NUMBER_INTEGER, defaultValue=4)
    double = QgsProcessingParameterNumber("X", "X", type=compat.NUMBER_DOUBLE, defaultValue=0.5)
    assert integer.dataType() == compat.NUMBER_INTEGER
    assert double.dataType() == compat.NUMBER_DOUBLE


def test_parameter_flags():
    param = QgsProcessingParameterNumber("N", "N", type=compat.NUMBER_INTEGER)
    param.setFlags(param.flags() | compat.PARAM_FLAG_ADVANCED | compat.PARAM_FLAG_OPTIONAL)
    assert param.flags() & compat.PARAM_FLAG_ADVANCED
    assert param.flags() & compat.PARAM_FLAG_OPTIONAL


class _NoThreadAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "no_thread"

    def displayName(self):
        return "No thread"

    def createInstance(self):
        return _NoThreadAlgorithm()

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        return {}

    def flags(self):
        return super().flags() | compat.ALG_FLAG_NO_THREADING


def test_no_threading_flag():
    algorithm = _NoThreadAlgorithm()
    assert algorithm.flags() & compat.ALG_FLAG_NO_THREADING


# --- Messages, tasks, features --------------------------------------------------


def test_message_levels(log_messages):
    for name in ("MSG_INFO", "MSG_WARNING", "MSG_CRITICAL", "MSG_SUCCESS"):
        QgsMessageLog.logMessage(name, "HamQTest", getattr(compat, name))
    received = [(message, level) for message, tag, level in log_messages if tag == "HamQTest"]
    assert [message for message, _level in received] == [
        "MSG_INFO",
        "MSG_WARNING",
        "MSG_CRITICAL",
        "MSG_SUCCESS",
    ]
    for message, level in received:
        assert level == getattr(compat, message)


def test_connect_message_log_disconnects(qgis_app):
    received = []
    disconnect = compat.connect_message_log(lambda *args: received.append(args))
    QgsMessageLog.logMessage("first", "HamQTest", compat.MSG_INFO)
    disconnect()
    disconnect()  # a second call is harmless
    QgsMessageLog.logMessage("second", "HamQTest", compat.MSG_INFO)
    assert [args[:2] for args in received] == [("first", "HamQTest")]


class _Task(QgsTask):
    def run(self):
        return True


def test_task_can_cancel():
    task = _Task("test", compat.TASK_CAN_CANCEL)
    assert task.canCancel()


def _memory_points(count: int) -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "points", "memory")
    layer.dataProvider().addAttributes(list(make_fields([("call", "text"), ("n", "int")])))
    layer.updateFields()
    features = []
    for i in range(count):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(20.0 + i, 44.0)))
        feature.setAttributes([f"YU{i}AB", i])
        features.append(feature)
    ok, _ = layer.dataProvider().addFeatures(features, compat.SINK_FAST_INSERT)
    assert ok
    return layer


def test_fast_insert():
    layer = _memory_points(3)
    assert layer.featureCount() == 3
    assert [f["call"] for f in layer.getFeatures()] == ["YU0AB", "YU1AB", "YU2AB"]


def test_no_geometry_request(tmp_gpkg):
    # The memory provider ignores the hint; the OGR (GeoPackage) provider honors it.
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "qso"
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        _memory_points(2), tmp_gpkg, QgsProject.instance().transformContext(), options
    )
    assert result[0] == compat.WRITER_NO_ERROR, result[1]
    layer = QgsVectorLayer(f"{tmp_gpkg}|layername=qso", "qso", "ogr")
    request = QgsFeatureRequest().setFlags(compat.REQUEST_NO_GEOMETRY)
    assert request.flags() & compat.REQUEST_NO_GEOMETRY
    features = list(layer.getFeatures(request))
    assert [f["call"] for f in features] == ["YU0AB", "YU1AB"]
    assert not any(f.hasGeometry() for f in features)
    assert all(f.hasGeometry() for f in layer.getFeatures())


def test_vector_file_writer_actions(tmp_gpkg):
    context = QgsProject.instance().transformContext()
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    fields = make_fields([("call", "text"), ("n", "int")])

    def create(layer_name, wkb, action):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.actionOnExistingFile = action
        writer = QgsVectorFileWriter.create(tmp_gpkg, fields, wkb, crs, context, options)
        assert writer.hasError() == compat.WRITER_NO_ERROR, writer.errorMessage()
        del writer  # closes the file

    create("qso", compat.WKB_POINT, compat.WRITER_CREATE_OR_OVERWRITE_FILE)
    create("qso_path", compat.WKB_MULTILINESTRING, compat.WRITER_CREATE_OR_OVERWRITE_LAYER)

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "qso"
    options.actionOnExistingFile = compat.WRITER_APPEND_TO_LAYER_NO_NEW_FIELDS
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        _memory_points(2), tmp_gpkg, context, options
    )
    assert result[0] == compat.WRITER_NO_ERROR, result[1]

    qso = QgsVectorLayer(f"{tmp_gpkg}|layername=qso", "qso", "ogr")
    path = QgsVectorLayer(f"{tmp_gpkg}|layername=qso_path", "qso_path", "ogr")
    assert qso.isValid() and path.isValid()
    assert qso.featureCount() == 2
    assert path.wkbType() == compat.WKB_MULTILINESTRING


# --- Qt widgets ------------------------------------------------------------------


def test_dock_areas(qgis_app):
    window = QMainWindow()
    dock = QDockWidget("dock")
    dock.setAllowedAreas(compat.DOCK_ALL)
    window.addDockWidget(compat.DOCK_RIGHT, dock)
    assert window.dockWidgetArea(dock) == compat.DOCK_RIGHT
    window.addDockWidget(compat.DOCK_LEFT, dock)
    assert window.dockWidgetArea(dock) == compat.DOCK_LEFT
    window.addDockWidget(compat.DOCK_BOTTOM, dock)
    assert window.dockWidgetArea(dock) == compat.DOCK_BOTTOM


def test_dialog_buttons_and_codes(qgis_app):
    box = QDialogButtonBox(compat.DIALOG_OK | compat.DIALOG_CANCEL | compat.DIALOG_HELP)
    assert box.button(compat.DIALOG_OK) is not None
    assert box.button(compat.DIALOG_CANCEL) is not None
    assert box.button(compat.DIALOG_CLOSE) is None
    box.setStandardButtons(compat.DIALOG_SAVE | compat.DIALOG_APPLY | compat.DIALOG_CLOSE)
    assert box.button(compat.DIALOG_SAVE) is not None
    dialog = QDialog()
    dialog.done(compat.DIALOG_ACCEPTED)
    assert dialog.result() == compat.DIALOG_ACCEPTED
    dialog.done(compat.DIALOG_REJECTED)
    assert dialog.result() == compat.DIALOG_REJECTED


def test_message_box_buttons(qgis_app):
    box = QMessageBox()
    box.setStandardButtons(compat.MSGBOX_YES | compat.MSGBOX_NO | compat.MSGBOX_CANCEL)
    assert box.button(compat.MSGBOX_YES) is not None
    assert box.button(compat.MSGBOX_OK) is None
    assert box.standardButton(box.button(compat.MSGBOX_NO)) == compat.MSGBOX_NO


def test_item_view_enums(qgis_app):
    table = QTableWidget(2, 3)
    header = table.horizontalHeader()
    for mode in (
        compat.HEADER_STRETCH,
        compat.HEADER_RESIZE_TO_CONTENTS,
        compat.HEADER_INTERACTIVE,
    ):
        header.setSectionResizeMode(mode)
        assert header.sectionResizeMode(0) == mode
    table.setEditTriggers(compat.EDIT_NO_TRIGGERS)
    assert table.editTriggers() == compat.EDIT_NO_TRIGGERS
    table.setSelectionBehavior(compat.SELECT_ROWS)
    assert table.selectionBehavior() == compat.SELECT_ROWS
    table.setSelectionMode(compat.SELECTION_SINGLE)
    assert table.selectionMode() == compat.SELECTION_SINGLE
    table.setSelectionMode(compat.SELECTION_NONE)
    assert table.selectionMode() == compat.SELECTION_NONE
    item = QTableWidgetItem("x")
    item.setData(compat.USER_ROLE, "payload")
    table.setItem(0, 0, item)
    assert table.item(0, 0).data(compat.USER_ROLE) == "payload"


def test_size_policies(qgis_app):
    for horizontal, vertical in (
        (compat.SIZE_EXPANDING, compat.SIZE_PREFERRED),
        (compat.SIZE_FIXED, compat.SIZE_MINIMUM),
        (compat.SIZE_MINIMUM_EXPANDING, compat.SIZE_FIXED),
    ):
        policy = QSizePolicy(horizontal, vertical)
        assert policy.horizontalPolicy() == horizontal
        assert policy.verticalPolicy() == vertical


def test_label_alignment_and_text(qgis_app):
    label = QLabel("x")
    label.setAlignment(compat.ALIGN_RIGHT | compat.ALIGN_VCENTER)
    assert label.alignment() & compat.ALIGN_RIGHT
    assert label.alignment() & compat.ALIGN_VCENTER
    label.setAlignment(compat.ALIGN_CENTER)
    assert label.alignment() & compat.ALIGN_HCENTER
    label.setAlignment(compat.ALIGN_LEFT | compat.ALIGN_TOP)
    assert label.alignment() & compat.ALIGN_TOP
    label.setTextFormat(compat.TEXT_RICH)
    assert label.textFormat() == compat.TEXT_RICH
    label.setTextInteractionFlags(compat.TEXT_BROWSER_INTERACTION)
    assert label.textInteractionFlags() == compat.TEXT_BROWSER_INTERACTION


def test_tool_button_and_cursor(qgis_app):
    button = QToolButton()
    button.setPopupMode(compat.TOOLBUTTON_INSTANT_POPUP)
    assert button.popupMode() == compat.TOOLBUTTON_INSTANT_POPUP
    button.setToolButtonStyle(compat.TOOLBUTTON_TEXT_ONLY)
    assert button.toolButtonStyle() == compat.TOOLBUTTON_TEXT_ONLY
    button.setToolButtonStyle(compat.TOOLBUTTON_TEXT_BESIDE_ICON)
    assert button.toolButtonStyle() == compat.TOOLBUTTON_TEXT_BESIDE_ICON
    assert QCursor(compat.CURSOR_CROSS).shape() == compat.CURSOR_CROSS
    assert QCursor(compat.CURSOR_WAIT).shape() == compat.CURSOR_WAIT


# --- Qt network and I/O -------------------------------------------------------------


def test_host_addresses():
    assert QHostAddress(compat.HOST_ANY_IPV4).toString() == "0.0.0.0"
    assert QHostAddress(compat.HOST_LOCALHOST).toString() == "127.0.0.1"


def test_udp_bind_flags_and_states(qgis_app):
    socket = QUdpSocket()
    assert socket.state() == compat.SOCKET_UNCONNECTED
    flags = compat.BIND_SHARE_ADDRESS | compat.BIND_REUSE_ADDRESS_HINT
    assert socket.bind(QHostAddress(compat.HOST_LOCALHOST), 0, flags)
    assert socket.state() == compat.SOCKET_BOUND
    socket.close()
    assert socket.state() == compat.SOCKET_UNCONNECTED
    # the constants used by TCP clients compare with socket states as well
    assert compat.SOCKET_CONNECTED != compat.SOCKET_CONNECTING


def test_address_in_use_error(qgis_app):
    first = QUdpSocket()
    assert first.bind(QHostAddress(compat.HOST_LOCALHOST), 0)
    second = QUdpSocket()
    assert not second.bind(QHostAddress(compat.HOST_LOCALHOST), first.localPort())
    assert second.error() == compat.SOCKET_ERROR_ADDRESS_IN_USE
    first.close()


def test_network_request_attributes():
    request = QNetworkRequest()
    request.setAttribute(compat.NET_ATTR_REDIRECT_POLICY, compat.NET_REDIRECT_NO_LESS_SAFE)
    assert numeric_or_int(request.attribute(compat.NET_ATTR_REDIRECT_POLICY)) == 1
    request.setHeader(compat.NET_HEADER_USER_AGENT, "HamQ/0.1.0")
    assert request.header(compat.NET_HEADER_USER_AGENT) == "HamQ/0.1.0"
    assert request.attribute(compat.NET_ATTR_HTTP_STATUS) is None


def numeric_or_int(value) -> int:
    return numeric(value) if isinstance(value, enum.Enum) else int(value)


def test_io_open_modes(tmp_path, qgis_app):
    from qgis.PyQt.QtCore import QFile

    buffer = QBuffer()
    assert buffer.open(compat.IO_WRITE_ONLY)
    assert buffer.openMode() & compat.IO_WRITE_ONLY
    buffer.write(b"cty")
    buffer.close()
    path = tmp_path / "cty.dat"
    path.write_text("line 1\nline 2\n", encoding="utf-8")
    handle = QFile(str(path))
    assert handle.open(compat.IO_READ_ONLY | compat.IO_TEXT)
    assert bytes(handle.readAll()).splitlines() == [b"line 1", b"line 2"]
    handle.close()


# --- M7-02: Hamlib TCP clients ------------------------------------------------------


def test_tcp_remote_close_and_no_proxy(qgis_app):
    import time

    from qgis.PyQt.QtNetwork import QNetworkProxy, QTcpServer, QTcpSocket

    server = QTcpServer()
    assert server.listen(QHostAddress(compat.HOST_LOCALHOST), 0)
    client = QTcpSocket()
    client.setProxy(QNetworkProxy(compat.NET_PROXY_NONE))
    assert client.proxy().type() == compat.NET_PROXY_NONE
    errors = []
    client.errorOccurred.connect(errors.append)
    client.connectToHost("127.0.0.1", server.serverPort())
    assert client.waitForConnected(5000)
    assert server.waitForNewConnection(5000)
    server.nextPendingConnection().disconnectFromHost()
    deadline = time.monotonic() + 5
    while not errors and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.002)
    assert errors == [compat.SOCKET_ERROR_REMOTE_CLOSED]
    assert client.error() == compat.SOCKET_ERROR_REMOTE_CLOSED
    client.abort()
    server.close()


# --- M4-03 / M5-02: cty.dat download and WSJT-X multicast ---------------------------


def test_network_cache_attributes():
    request = QNetworkRequest()
    assert request.attribute(compat.NET_ATTR_CACHE_LOAD_CONTROL) is None
    request.setAttribute(compat.NET_ATTR_CACHE_LOAD_CONTROL, compat.NET_CACHE_ALWAYS_NETWORK)
    request.setAttribute(compat.NET_ATTR_CACHE_SAVE_CONTROL, False)
    assert numeric_or_int(request.attribute(compat.NET_ATTR_CACHE_LOAD_CONTROL)) == 0
    assert request.attribute(compat.NET_ATTR_CACHE_SAVE_CONTROL) is False


def test_network_interface_flags(qgis_app):
    from qgis.PyQt.QtNetwork import QNetworkInterface

    interfaces = QNetworkInterface.allInterfaces()
    loopback = [i for i in interfaces if i.flags() & compat.NETIF_IS_LOOPBACK]
    assert loopback, [i.name() for i in interfaces]
    assert all(i.flags() & compat.NETIF_IS_UP for i in loopback)
    for interface in interfaces:
        flags = interface.flags()
        assert bool(flags & compat.NETIF_CAN_MULTICAST) in (True, False)
        assert bool(flags & (compat.NETIF_CAN_MULTICAST | compat.NETIF_IS_LOOPBACK)) == bool(
            (flags & compat.NETIF_CAN_MULTICAST) or (flags & compat.NETIF_IS_LOOPBACK)
        )


# --- M6-02 / M0-03 / M3-03: language button, settings dialog, azimuthal labels ------


def test_menu_button_popup(qgis_app):
    button = QToolButton()
    button.setPopupMode(compat.TOOLBUTTON_MENU_BUTTON_POPUP)
    assert button.popupMode() == compat.TOOLBUTTON_MENU_BUTTON_POPUP
    assert button.popupMode() != compat.TOOLBUTTON_INSTANT_POPUP


def test_label_placement_line(qgis_app):
    from qgis.core import QgsPalLayerSettings

    settings = QgsPalLayerSettings()
    settings.placement = compat.LABEL_PLACEMENT_LINE
    assert settings.placement == compat.LABEL_PLACEMENT_LINE


def test_file_dialog_dont_confirm_overwrite(qgis_app):
    from qgis.PyQt.QtWidgets import QFileDialog

    dialog = QFileDialog()
    assert not dialog.testOption(compat.FILE_DIALOG_DONT_CONFIRM_OVERWRITE)
    dialog.setOption(compat.FILE_DIALOG_DONT_CONFIRM_OVERWRITE, True)
    assert dialog.testOption(compat.FILE_DIALOG_DONT_CONFIRM_OVERWRITE)
    dialog.deleteLater()


# --- M2-03 / M3-02: default styles -----------------------------------------------------


def test_label_placement_over_point_and_show_property(qgis_app):
    from qgis.core import QgsPalLayerSettings, QgsProperty

    settings = QgsPalLayerSettings()
    settings.placement = compat.LABEL_PLACEMENT_OVER_POINT
    assert settings.placement == compat.LABEL_PLACEMENT_OVER_POINT
    properties = settings.dataDefinedProperties()
    properties.setProperty(compat.LABEL_PROPERTY_SHOW, QgsProperty.fromExpression("1 = 1"))
    settings.setDataDefinedProperties(properties)
    assert settings.dataDefinedProperties().isActive(compat.LABEL_PROPERTY_SHOW)
    assert (
        settings.dataDefinedProperties().property(compat.LABEL_PROPERTY_SHOW).expressionString()
        == "1 = 1"
    )


def test_style_categories(tmp_path, qgis_app):
    from qgis.core import QgsVectorLayer

    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=band:string", "styled", "memory")
    layer.setFieldAlias(0, "Band alias")
    path = str(tmp_path / "style.qml")
    categories = compat.STYLE_CATEGORY_SYMBOLOGY | compat.STYLE_CATEGORY_LABELING
    _message, ok = layer.saveNamedStyle(path, categories=categories)
    assert ok
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert 'styleCategories="Symbology|Labeling"' in text
    assert "Band alias" not in text
    other = QgsVectorLayer("Point?crs=EPSG:4326&field=band:string", "other", "memory")
    _message, ok = other.loadNamedStyle(path, categories=categories)
    assert ok
    assert other.attributeAlias(0) == ""


def test_brush_none(qgis_app):
    from qgis.core import QgsFillSymbol

    symbol = QgsFillSymbol.createSimple({"style": "no"})
    assert symbol.symbolLayer(0).brushStyle() == compat.BRUSH_NONE
