"""Qt5/Qt6 and QGIS 3.34 .. 4.x compatibility layer.

This module is the single place that resolves enum values and classes whose
names differ between QGIS 3.34, 3.44, 4.0 and 4.2 or between Qt5 (PyQt5) and
Qt6 (PyQt6). Other modules import the resolved names from here instead of
spelling out a version specific path::

    from hamq.qgis_io.compat import MSG_WARNING, WKB_POINT, QAction

Resolution rules:

* QGIS enums prefer the scoped ``Qgis.*`` name (most of them exist since QGIS
  3.36) and fall back to the legacy class-scoped name (QGIS 3.34).
* Qt enums always use the fully scoped spelling (``Qt.AlignmentFlag.AlignLeft``),
  which works on PyQt5 5.15 and PyQt6; the unscoped Qt5 spelling does not
  exist in Qt6.
* Classes that moved between Qt modules (``QAction``, ``QActionGroup``) are
  looked up in the Qt6 module first, then in the Qt5 one.

A name that cannot be resolved on the running version is ``None`` and listed
in :data:`MISSING`, so a single unknown enum never prevents the plugin from
loading. The QGIS test-suite (``tests/qgis/test_compat.py``) asserts that
``MISSING`` is empty and that every value has the expected type and numeric
value on QGIS 3.34, 3.44, 4.0 and 4.2.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsMapLayer,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsTask,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt import QtGui, QtWidgets
from qgis.PyQt.QtCore import QT_VERSION_STR, QIODevice, QMetaType, Qt, QVariant
from qgis.PyQt.QtNetwork import QAbstractSocket, QHostAddress, QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QMessageBox,
    QSizePolicy,
    QToolButton,
)

#: Every name resolved by this module, in definition order (used by the tests).
NAMES: list[str] = []
#: Names that could not be resolved on the running QGIS / Qt version.
MISSING: list[str] = []


def _resolve(name: str, *candidates: tuple[Any, ...]) -> Any:
    """Return the first candidate attribute chain that exists.

    Each candidate is ``(root, "attr", "attr", ...)``; ``(Qgis, "WkbType",
    "Point")`` means ``Qgis.WkbType.Point``. When no candidate resolves, the
    name is recorded in :data:`MISSING` and ``None`` is returned.
    """
    NAMES.append(name)
    for candidate in candidates:
        value = candidate[0]
        try:
            for attr in candidate[1:]:
                value = getattr(value, attr)
        except AttributeError:
            continue
        if value is not None:
            return value
    MISSING.append(name)
    return None


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

#: QGIS version as an integer, e.g. 34415 for 3.44.15, 40201 for 4.2.1.
QGIS_VERSION_INT: int = Qgis.versionInt()
#: Major version of the Qt library QGIS runs on (5 or 6).
QT_MAJOR: int = int(QT_VERSION_STR.split(".")[0])
#: True on Qt6 builds (QGIS 4.x).
IS_QT6: bool = QT_MAJOR >= 6

# ---------------------------------------------------------------------------
# Classes that moved between Qt modules
# ---------------------------------------------------------------------------

#: ``QAction``: QtGui in Qt6, QtWidgets in Qt5 (QGIS 3.x also re-exports it from QtGui).
QAction = _resolve("QAction", (QtGui, "QAction"), (QtWidgets, "QAction"))
#: ``QActionGroup``: QtGui in Qt6, QtWidgets in Qt5 (not re-exported by QGIS 3.34).
QActionGroup = _resolve("QActionGroup", (QtGui, "QActionGroup"), (QtWidgets, "QActionGroup"))

# ---------------------------------------------------------------------------
# Geometry types
# ---------------------------------------------------------------------------

#: WKB type Point (``Qgis.WkbType.Point``, legacy ``QgsWkbTypes.Point``).
WKB_POINT = _resolve("WKB_POINT", (Qgis, "WkbType", "Point"), (QgsWkbTypes, "Point"))
#: WKB type LineString.
WKB_LINESTRING = _resolve(
    "WKB_LINESTRING", (Qgis, "WkbType", "LineString"), (QgsWkbTypes, "LineString")
)
#: WKB type MultiLineString (QSO paths split at the antimeridian).
WKB_MULTILINESTRING = _resolve(
    "WKB_MULTILINESTRING",
    (Qgis, "WkbType", "MultiLineString"),
    (QgsWkbTypes, "MultiLineString"),
)
#: WKB type Polygon (Maidenhead grid cells).
WKB_POLYGON = _resolve("WKB_POLYGON", (Qgis, "WkbType", "Polygon"), (QgsWkbTypes, "Polygon"))
#: WKB type MultiPolygon.
WKB_MULTIPOLYGON = _resolve(
    "WKB_MULTIPOLYGON", (Qgis, "WkbType", "MultiPolygon"), (QgsWkbTypes, "MultiPolygon")
)
#: WKB type NoGeometry (attribute-only tables).
WKB_NO_GEOMETRY = _resolve(
    "WKB_NO_GEOMETRY", (Qgis, "WkbType", "NoGeometry"), (QgsWkbTypes, "NoGeometry")
)

#: Geometry type of point layers (``Qgis.GeometryType.Point``, legacy ``PointGeometry``).
GEOMETRY_POINT = _resolve(
    "GEOMETRY_POINT", (Qgis, "GeometryType", "Point"), (QgsWkbTypes, "PointGeometry")
)
#: Geometry type of line layers.
GEOMETRY_LINE = _resolve(
    "GEOMETRY_LINE", (Qgis, "GeometryType", "Line"), (QgsWkbTypes, "LineGeometry")
)
#: Geometry type of polygon layers.
GEOMETRY_POLYGON = _resolve(
    "GEOMETRY_POLYGON", (Qgis, "GeometryType", "Polygon"), (QgsWkbTypes, "PolygonGeometry")
)

#: Map layer type of vector layers (``Qgis.LayerType.Vector``, legacy ``VectorLayer``).
LAYER_TYPE_VECTOR = _resolve(
    "LAYER_TYPE_VECTOR", (Qgis, "LayerType", "Vector"), (QgsMapLayer, "VectorLayer")
)

#: Distance unit kilometers (``QgsDistanceArea.convertLengthMeasurement``).
DISTANCE_KILOMETERS = _resolve(
    "DISTANCE_KILOMETERS",
    (Qgis, "DistanceUnit", "Kilometers"),
    (QgsUnitTypes, "DistanceKilometers"),
)

# ---------------------------------------------------------------------------
# Processing (Qgis.Processing* enums exist since QGIS 3.36)
# ---------------------------------------------------------------------------

#: Source type: vector layers with point geometry.
SOURCE_VECTOR_POINT = _resolve(
    "SOURCE_VECTOR_POINT",
    (Qgis, "ProcessingSourceType", "VectorPoint"),
    (QgsProcessing, "SourceType", "TypeVectorPoint"),
    (QgsProcessing, "TypeVectorPoint"),
)
#: Source type: vector layers with line geometry.
SOURCE_VECTOR_LINE = _resolve(
    "SOURCE_VECTOR_LINE",
    (Qgis, "ProcessingSourceType", "VectorLine"),
    (QgsProcessing, "SourceType", "TypeVectorLine"),
    (QgsProcessing, "TypeVectorLine"),
)
#: Source type: vector layers with polygon geometry.
SOURCE_VECTOR_POLYGON = _resolve(
    "SOURCE_VECTOR_POLYGON",
    (Qgis, "ProcessingSourceType", "VectorPolygon"),
    (QgsProcessing, "SourceType", "TypeVectorPolygon"),
    (QgsProcessing, "TypeVectorPolygon"),
)
#: Source type: vector layers with any geometry.
SOURCE_VECTOR_ANY_GEOMETRY = _resolve(
    "SOURCE_VECTOR_ANY_GEOMETRY",
    (Qgis, "ProcessingSourceType", "VectorAnyGeometry"),
    (QgsProcessing, "SourceType", "TypeVectorAnyGeometry"),
    (QgsProcessing, "TypeVectorAnyGeometry"),
)

#: ``QgsProcessingParameterFile`` behavior: select a file (not a folder).
FILE_BEHAVIOR_FILE = _resolve(
    "FILE_BEHAVIOR_FILE",
    (Qgis, "ProcessingFileParameterBehavior", "File"),
    (QgsProcessingParameterFile, "Behavior", "File"),
    (QgsProcessingParameterFile, "File"),
)
#: ``QgsProcessingParameterFile`` behavior: select a folder.
FILE_BEHAVIOR_FOLDER = _resolve(
    "FILE_BEHAVIOR_FOLDER",
    (Qgis, "ProcessingFileParameterBehavior", "Folder"),
    (QgsProcessingParameterFile, "Behavior", "Folder"),
    (QgsProcessingParameterFile, "Folder"),
)

#: ``QgsProcessingParameterNumber`` type: integer.
NUMBER_INTEGER = _resolve(
    "NUMBER_INTEGER",
    (Qgis, "ProcessingNumberParameterType", "Integer"),
    (QgsProcessingParameterNumber, "Type", "Integer"),
    (QgsProcessingParameterNumber, "Integer"),
)
#: ``QgsProcessingParameterNumber`` type: double.
NUMBER_DOUBLE = _resolve(
    "NUMBER_DOUBLE",
    (Qgis, "ProcessingNumberParameterType", "Double"),
    (QgsProcessingParameterNumber, "Type", "Double"),
    (QgsProcessingParameterNumber, "Double"),
)

#: Algorithm flag: run in the main thread (``flags()`` returns
#: ``super().flags() | ALG_FLAG_NO_THREADING``).
ALG_FLAG_NO_THREADING = _resolve(
    "ALG_FLAG_NO_THREADING",
    (Qgis, "ProcessingAlgorithmFlag", "NoThreading"),
    (QgsProcessingAlgorithm, "Flag", "FlagNoThreading"),
    (QgsProcessingAlgorithm, "FlagNoThreading"),
)

#: Parameter flag: optional parameter.
PARAM_FLAG_OPTIONAL = _resolve(
    "PARAM_FLAG_OPTIONAL",
    (Qgis, "ProcessingParameterFlag", "Optional"),
    (QgsProcessingParameterDefinition, "Flag", "FlagOptional"),
    (QgsProcessingParameterDefinition, "FlagOptional"),
)
#: Parameter flag: shown under "Advanced parameters".
PARAM_FLAG_ADVANCED = _resolve(
    "PARAM_FLAG_ADVANCED",
    (Qgis, "ProcessingParameterFlag", "Advanced"),
    (QgsProcessingParameterDefinition, "Flag", "FlagAdvanced"),
    (QgsProcessingParameterDefinition, "FlagAdvanced"),
)

# ---------------------------------------------------------------------------
# Message levels (QgsMessageLog.logMessage, QgsMessageBar.pushMessage)
# ---------------------------------------------------------------------------

#: Message level Info.
MSG_INFO = _resolve("MSG_INFO", (Qgis, "MessageLevel", "Info"), (Qgis, "Info"))
#: Message level Warning.
MSG_WARNING = _resolve("MSG_WARNING", (Qgis, "MessageLevel", "Warning"), (Qgis, "Warning"))
#: Message level Critical.
MSG_CRITICAL = _resolve("MSG_CRITICAL", (Qgis, "MessageLevel", "Critical"), (Qgis, "Critical"))
#: Message level Success.
MSG_SUCCESS = _resolve("MSG_SUCCESS", (Qgis, "MessageLevel", "Success"), (Qgis, "Success"))

# ---------------------------------------------------------------------------
# Vector writing and reading
# ---------------------------------------------------------------------------

#: ``SaveVectorOptions.actionOnExistingFile``: replace the whole file.
WRITER_CREATE_OR_OVERWRITE_FILE = _resolve(
    "WRITER_CREATE_OR_OVERWRITE_FILE",
    (QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteFile"),
    (QgsVectorFileWriter, "CreateOrOverwriteFile"),
)
#: ``SaveVectorOptions.actionOnExistingFile``: add or replace one layer of a GeoPackage.
WRITER_CREATE_OR_OVERWRITE_LAYER = _resolve(
    "WRITER_CREATE_OR_OVERWRITE_LAYER",
    (QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer"),
    (QgsVectorFileWriter, "CreateOrOverwriteLayer"),
)
#: ``SaveVectorOptions.actionOnExistingFile``: append features to an existing layer.
WRITER_APPEND_TO_LAYER_NO_NEW_FIELDS = _resolve(
    "WRITER_APPEND_TO_LAYER_NO_NEW_FIELDS",
    (QgsVectorFileWriter, "ActionOnExistingFile", "AppendToLayerNoNewFields"),
    (QgsVectorFileWriter, "AppendToLayerNoNewFields"),
)
#: ``QgsVectorFileWriter.hasError()`` / ``writeAsVectorFormatV3()`` result: success.
WRITER_NO_ERROR = _resolve(
    "WRITER_NO_ERROR",
    (QgsVectorFileWriter, "WriterError", "NoError"),
    (QgsVectorFileWriter, "NoError"),
)

#: ``QgsFeatureSink.addFeatures(features, flags)``: skip per-feature bookkeeping.
SINK_FAST_INSERT = _resolve(
    "SINK_FAST_INSERT", (QgsFeatureSink, "Flag", "FastInsert"), (QgsFeatureSink, "FastInsert")
)

#: ``QgsFeatureRequest.setFlags``: do not fetch geometries.
REQUEST_NO_GEOMETRY = _resolve(
    "REQUEST_NO_GEOMETRY",
    (Qgis, "FeatureRequestFlag", "NoGeometry"),
    (QgsFeatureRequest, "Flag", "NoGeometry"),
    (QgsFeatureRequest, "NoGeometry"),
)

#: ``QgsTask(description, flags)``: the task can be canceled by the user.
TASK_CAN_CANCEL = _resolve(
    "TASK_CAN_CANCEL", (QgsTask, "Flag", "CanCancel"), (QgsTask, "CanCancel")
)

# ---------------------------------------------------------------------------
# Field types (QgsField takes QMetaType.Type since QGIS 3.38, QVariant.Type
# before; the QVariant constructor is gone in QGIS 4). See qgis_io/fields.py.
# ---------------------------------------------------------------------------

#: True when ``QgsField`` is created with ``QMetaType.Type`` values.
FIELD_TYPES_USE_QMETATYPE: bool = QGIS_VERSION_INT >= 33800

if FIELD_TYPES_USE_QMETATYPE:
    #: Field type for 32-bit integers.
    FIELD_TYPE_INT = _resolve("FIELD_TYPE_INT", (QMetaType, "Type", "Int"))
    #: Field type for doubles.
    FIELD_TYPE_REAL = _resolve("FIELD_TYPE_REAL", (QMetaType, "Type", "Double"))
    #: Field type for strings.
    FIELD_TYPE_TEXT = _resolve("FIELD_TYPE_TEXT", (QMetaType, "Type", "QString"))
    #: Field type for date and time (stored as UTC ISO 8601 in GeoPackage).
    FIELD_TYPE_DATETIME = _resolve("FIELD_TYPE_DATETIME", (QMetaType, "Type", "QDateTime"))
else:  # QGIS 3.34 .. 3.36
    FIELD_TYPE_INT = _resolve("FIELD_TYPE_INT", (QVariant, "Int"))
    FIELD_TYPE_REAL = _resolve("FIELD_TYPE_REAL", (QVariant, "Double"))
    FIELD_TYPE_TEXT = _resolve("FIELD_TYPE_TEXT", (QVariant, "String"))
    FIELD_TYPE_DATETIME = _resolve("FIELD_TYPE_DATETIME", (QVariant, "DateTime"))

# ---------------------------------------------------------------------------
# Qt widgets (fully scoped enums work on PyQt5 5.15 and PyQt6)
# ---------------------------------------------------------------------------

#: Dock widget areas (``iface.addDockWidget(area, dock)``).
DOCK_LEFT = _resolve("DOCK_LEFT", (Qt, "DockWidgetArea", "LeftDockWidgetArea"))
DOCK_RIGHT = _resolve("DOCK_RIGHT", (Qt, "DockWidgetArea", "RightDockWidgetArea"))
DOCK_BOTTOM = _resolve("DOCK_BOTTOM", (Qt, "DockWidgetArea", "BottomDockWidgetArea"))
#: All dock areas (``QDockWidget.setAllowedAreas``).
DOCK_ALL = _resolve("DOCK_ALL", (Qt, "DockWidgetArea", "AllDockWidgetAreas"))

#: ``QDialogButtonBox`` standard buttons (combine with ``|``).
DIALOG_OK = _resolve("DIALOG_OK", (QDialogButtonBox, "StandardButton", "Ok"))
DIALOG_CANCEL = _resolve("DIALOG_CANCEL", (QDialogButtonBox, "StandardButton", "Cancel"))
DIALOG_CLOSE = _resolve("DIALOG_CLOSE", (QDialogButtonBox, "StandardButton", "Close"))
DIALOG_SAVE = _resolve("DIALOG_SAVE", (QDialogButtonBox, "StandardButton", "Save"))
DIALOG_APPLY = _resolve("DIALOG_APPLY", (QDialogButtonBox, "StandardButton", "Apply"))
DIALOG_HELP = _resolve("DIALOG_HELP", (QDialogButtonBox, "StandardButton", "Help"))

#: ``QDialog.exec()`` results. Both are int-compatible enums on PyQt5 and PyQt6,
#: so ``dialog.exec() == DIALOG_ACCEPTED`` works on both.
DIALOG_ACCEPTED = _resolve("DIALOG_ACCEPTED", (QDialog, "DialogCode", "Accepted"))
DIALOG_REJECTED = _resolve("DIALOG_REJECTED", (QDialog, "DialogCode", "Rejected"))

#: ``QMessageBox`` standard buttons (``QMessageBox.question(...) == MSGBOX_YES``).
MSGBOX_OK = _resolve("MSGBOX_OK", (QMessageBox, "StandardButton", "Ok"))
MSGBOX_YES = _resolve("MSGBOX_YES", (QMessageBox, "StandardButton", "Yes"))
MSGBOX_NO = _resolve("MSGBOX_NO", (QMessageBox, "StandardButton", "No"))
MSGBOX_CANCEL = _resolve("MSGBOX_CANCEL", (QMessageBox, "StandardButton", "Cancel"))

#: ``QHeaderView.setSectionResizeMode`` modes.
HEADER_STRETCH = _resolve("HEADER_STRETCH", (QHeaderView, "ResizeMode", "Stretch"))
HEADER_RESIZE_TO_CONTENTS = _resolve(
    "HEADER_RESIZE_TO_CONTENTS", (QHeaderView, "ResizeMode", "ResizeToContents")
)
HEADER_INTERACTIVE = _resolve("HEADER_INTERACTIVE", (QHeaderView, "ResizeMode", "Interactive"))

#: ``QAbstractItemView.setEditTriggers``: read-only views.
EDIT_NO_TRIGGERS = _resolve(
    "EDIT_NO_TRIGGERS", (QAbstractItemView, "EditTrigger", "NoEditTriggers")
)
#: ``QAbstractItemView.setSelectionBehavior``: select whole rows.
SELECT_ROWS = _resolve("SELECT_ROWS", (QAbstractItemView, "SelectionBehavior", "SelectRows"))
#: ``QAbstractItemView.setSelectionMode`` modes.
SELECTION_SINGLE = _resolve(
    "SELECTION_SINGLE", (QAbstractItemView, "SelectionMode", "SingleSelection")
)
SELECTION_NONE = _resolve("SELECTION_NONE", (QAbstractItemView, "SelectionMode", "NoSelection"))

#: ``QSizePolicy`` policies.
SIZE_FIXED = _resolve("SIZE_FIXED", (QSizePolicy, "Policy", "Fixed"))
SIZE_MINIMUM = _resolve("SIZE_MINIMUM", (QSizePolicy, "Policy", "Minimum"))
SIZE_PREFERRED = _resolve("SIZE_PREFERRED", (QSizePolicy, "Policy", "Preferred"))
SIZE_EXPANDING = _resolve("SIZE_EXPANDING", (QSizePolicy, "Policy", "Expanding"))
SIZE_MINIMUM_EXPANDING = _resolve(
    "SIZE_MINIMUM_EXPANDING", (QSizePolicy, "Policy", "MinimumExpanding")
)

#: Alignment flags (combine with ``|``).
ALIGN_LEFT = _resolve("ALIGN_LEFT", (Qt, "AlignmentFlag", "AlignLeft"))
ALIGN_RIGHT = _resolve("ALIGN_RIGHT", (Qt, "AlignmentFlag", "AlignRight"))
ALIGN_HCENTER = _resolve("ALIGN_HCENTER", (Qt, "AlignmentFlag", "AlignHCenter"))
ALIGN_VCENTER = _resolve("ALIGN_VCENTER", (Qt, "AlignmentFlag", "AlignVCenter"))
ALIGN_CENTER = _resolve("ALIGN_CENTER", (Qt, "AlignmentFlag", "AlignCenter"))
ALIGN_TOP = _resolve("ALIGN_TOP", (Qt, "AlignmentFlag", "AlignTop"))

#: ``QToolButton.setPopupMode``: the menu opens on click (language switch button).
TOOLBUTTON_INSTANT_POPUP = _resolve(
    "TOOLBUTTON_INSTANT_POPUP", (QToolButton, "ToolButtonPopupMode", "InstantPopup")
)
#: ``QToolButton.setToolButtonStyle`` styles.
TOOLBUTTON_TEXT_ONLY = _resolve(
    "TOOLBUTTON_TEXT_ONLY", (Qt, "ToolButtonStyle", "ToolButtonTextOnly")
)
TOOLBUTTON_TEXT_BESIDE_ICON = _resolve(
    "TOOLBUTTON_TEXT_BESIDE_ICON", (Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")
)

#: First item data role free for application data (int-compatible on PyQt5 and PyQt6).
USER_ROLE = _resolve("USER_ROLE", (Qt, "ItemDataRole", "UserRole"))
#: ``QLabel.setTextFormat``: rich text.
TEXT_RICH = _resolve("TEXT_RICH", (Qt, "TextFormat", "RichText"))
#: ``QLabel.setTextInteractionFlags``: selectable text and clickable links.
TEXT_BROWSER_INTERACTION = _resolve(
    "TEXT_BROWSER_INTERACTION", (Qt, "TextInteractionFlag", "TextBrowserInteraction")
)
#: Cursor shapes (map tools, busy indicator).
CURSOR_CROSS = _resolve("CURSOR_CROSS", (Qt, "CursorShape", "CrossCursor"))
CURSOR_WAIT = _resolve("CURSOR_WAIT", (Qt, "CursorShape", "WaitCursor"))

# ---------------------------------------------------------------------------
# Qt network and I/O
# ---------------------------------------------------------------------------

#: ``QHostAddress`` for binding on all IPv4 interfaces (``0.0.0.0``).
HOST_ANY_IPV4 = _resolve("HOST_ANY_IPV4", (QHostAddress, "SpecialAddress", "AnyIPv4"))
#: ``QHostAddress`` for the IPv4 loopback (``127.0.0.1``).
HOST_LOCALHOST = _resolve("HOST_LOCALHOST", (QHostAddress, "SpecialAddress", "LocalHost"))

#: ``QUdpSocket.bind`` flags: let other programs bind the same port (WSJT-X multicast).
BIND_SHARE_ADDRESS = _resolve("BIND_SHARE_ADDRESS", (QAbstractSocket, "BindFlag", "ShareAddress"))
BIND_REUSE_ADDRESS_HINT = _resolve(
    "BIND_REUSE_ADDRESS_HINT", (QAbstractSocket, "BindFlag", "ReuseAddressHint")
)

#: ``QAbstractSocket.state()`` values.
SOCKET_UNCONNECTED = _resolve(
    "SOCKET_UNCONNECTED", (QAbstractSocket, "SocketState", "UnconnectedState")
)
SOCKET_CONNECTING = _resolve(
    "SOCKET_CONNECTING", (QAbstractSocket, "SocketState", "ConnectingState")
)
SOCKET_CONNECTED = _resolve("SOCKET_CONNECTED", (QAbstractSocket, "SocketState", "ConnectedState"))
SOCKET_BOUND = _resolve("SOCKET_BOUND", (QAbstractSocket, "SocketState", "BoundState"))
#: ``QAbstractSocket.error()`` when the UDP port is already taken.
SOCKET_ERROR_ADDRESS_IN_USE = _resolve(
    "SOCKET_ERROR_ADDRESS_IN_USE", (QAbstractSocket, "SocketError", "AddressInUseError")
)

#: ``QNetworkReply.attribute``: HTTP status code.
NET_ATTR_HTTP_STATUS = _resolve(
    "NET_ATTR_HTTP_STATUS", (QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")
)
#: ``QNetworkRequest.setAttribute``: redirect policy (``FollowRedirectsAttribute`` is gone in Qt6).
NET_ATTR_REDIRECT_POLICY = _resolve(
    "NET_ATTR_REDIRECT_POLICY", (QNetworkRequest, "Attribute", "RedirectPolicyAttribute")
)
#: Follow redirects unless they go from HTTPS to HTTP.
NET_REDIRECT_NO_LESS_SAFE = _resolve(
    "NET_REDIRECT_NO_LESS_SAFE", (QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy")
)
#: ``QNetworkRequest.setHeader``: User-Agent.
NET_HEADER_USER_AGENT = _resolve(
    "NET_HEADER_USER_AGENT", (QNetworkRequest, "KnownHeaders", "UserAgentHeader")
)
#: ``QNetworkReply.error()`` values.
NET_NO_ERROR = _resolve("NET_NO_ERROR", (QNetworkReply, "NetworkError", "NoError"))
NET_OPERATION_CANCELED = _resolve(
    "NET_OPERATION_CANCELED", (QNetworkReply, "NetworkError", "OperationCanceledError")
)

#: ``QIODevice.open`` modes (``QFile``, ``QBuffer``).
IO_READ_ONLY = _resolve("IO_READ_ONLY", (QIODevice, "OpenModeFlag", "ReadOnly"))
IO_WRITE_ONLY = _resolve("IO_WRITE_ONLY", (QIODevice, "OpenModeFlag", "WriteOnly"))
IO_TEXT = _resolve("IO_TEXT", (QIODevice, "OpenModeFlag", "Text"))

# ---------------------------------------------------------------------------
# Signals whose signature changed
# ---------------------------------------------------------------------------


def connect_message_log(slot: Callable[[str, str, Any], None]) -> Callable[[], None]:
    """Call ``slot(message, tag, level)`` for every ``QgsMessageLog`` message.

    QGIS 4 emits ``messageReceivedWithFormat(message, tag, level, format)`` and
    no longer the three-argument ``messageReceived``; QGIS 3 has only the
    latter. Returns a function that disconnects ``slot`` again.
    """
    log = QgsApplication.messageLog()
    signal = getattr(log, "messageReceivedWithFormat", None)
    if signal is None:  # QGIS 3.x
        signal = log.messageReceived

        def handler(message: str, tag: str, level: Any) -> None:
            slot(message, tag, level)

    else:  # QGIS 4.x

        def handler(message: str, tag: str, level: Any, _text_format: Any = None) -> None:
            slot(message, tag, level)

    signal.connect(handler)

    def disconnect() -> None:
        with contextlib.suppress(TypeError, RuntimeError):
            signal.disconnect(handler)

    return disconnect
