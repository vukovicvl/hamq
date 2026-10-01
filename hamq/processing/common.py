"""Shared base class and helpers of the HamQ Processing algorithms.

Every algorithm derives from :class:`HamQAlgorithm`: translated texts through
``hamq.core.i18n.tr`` (never Qt's translator), an icon from ``resources/icons`` and one
of the two stable groups, ``maidenhead`` and ``log``.

The log algorithms (Import ADIF, Recalculate) share the parameters for my QTH
locator and cty.dat (defaults from :class:`hamq.settings.HamQSettings`), the cty.dat
loading, the warning report (the first ones to the Processing feedback, all of them
to the QGIS message log) and the notification ``events().dataChanged(path)``, which
:meth:`QgsProcessingAlgorithm.postProcessAlgorithm` sends from the main thread.
QGIS runs ``processAlgorithm`` in a worker thread, so nothing there touches the
project or the GUI.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsApplication,
    QgsMessageLog,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication, QThread
from qgis.PyQt.QtGui import QIcon

from ..core import maidenhead
from ..core.i18n import tr, tr_noop
from ..events import events
from ..gui import icon_path
from ..net import cty_download
from ..qgis_io import layers
from ..qgis_io.compat import MSG_WARNING
from ..settings import HamQSettings

if TYPE_CHECKING:
    from ..core.cty import CtyDatabase

__all__ = [
    "FEEDBACK_WARNINGS",
    "GROUP_LOG",
    "GROUP_MAIDENHEAD",
    "GROUP_NAMES",
    "LOG_TAG",
    "MY_GRID",
    "USE_CTY",
    "HamQAlgorithm",
    "add_station_parameters",
    "check_station_grid",
    "editing_layers",
    "file_name",
    "in_main_thread",
    "is_qgis_process",
    "load_cty",
    "log",
    "notify_data_changed",
    "report_warnings",
    "settings_value",
    "split_tags",
    "station_grid",
]

#: Tag of HamQ messages in the QGIS message log.
LOG_TAG = "HamQ"
#: Stable group ids (``groupId()``); the names shown are translated.
GROUP_MAIDENHEAD, GROUP_LOG = "maidenhead", "log"
GROUP_NAMES = {
    GROUP_MAIDENHEAD: tr_noop("Maidenhead locators"),
    GROUP_LOG: tr_noop("QSO log"),
}
#: How many warnings go to the Processing feedback; all of them go to the message log.
FEEDBACK_WARNINGS = 20

#: Parameter names shared by the log algorithms.
MY_GRID, USE_CTY = "MY_GRID", "USE_CTY"


class HamQAlgorithm(QgsProcessingAlgorithm):
    """Base of the HamQ algorithms: translated texts, icon and group.

    Subclasses set :attr:`ICON` (file in ``resources/icons``) and :attr:`GROUP_ID`
    and implement ``name``, ``displayName``, ``shortHelpString``, ``initAlgorithm``
    and ``processAlgorithm``. ``createInstance`` returns a new object of the subclass.
    """

    ICON = "hamq.svg"
    GROUP_ID = GROUP_MAIDENHEAD

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator (not Qt's)."""
        return tr(text)

    def createInstance(self) -> QgsProcessingAlgorithm:
        """A new instance of the same algorithm class."""
        return type(self)()

    def group(self) -> str:
        """Translated name of the algorithm group."""
        return tr(GROUP_NAMES[self.GROUP_ID])

    def groupId(self) -> str:
        """Stable group id: ``maidenhead`` or ``log``."""
        return self.GROUP_ID

    def icon(self) -> QIcon:
        """Algorithm icon."""
        return QIcon(icon_path(self.ICON))

    def svgIconPath(self) -> str:
        """Path of the algorithm SVG icon."""
        return icon_path(self.ICON)


# --- threads, logging, notification ---------------------------------------------------------


def in_main_thread() -> bool:
    """True in the thread of the Qt application (the GUI thread)."""
    app = QCoreApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


def is_qgis_process() -> bool:
    """True when the algorithm runs in the ``qgis_process`` command line tool."""
    try:
        return QgsApplication.platform() == "qgis_process"
    except Exception:  # no QgsApplication: nothing to load layers into either
        return True


def log(message: str, level: Any = None) -> None:
    """Log ``message`` in the HamQ tab of the QGIS message log (warning by default)."""
    QgsMessageLog.logMessage(message, LOG_TAG, MSG_WARNING if level is None else level)


def notify_data_changed(path: str) -> None:
    """Emit ``events().dataChanged(path)``: the panel and the layers of ``path`` refresh.

    Called from ``postProcessAlgorithm`` (the main thread). From another thread Qt
    queues the call into the main thread, because the events object lives there.
    Never raises: the algorithms also run in ``qgis_process`` and from scripts,
    where nothing of the plugin GUI exists (nobody listens then).
    """
    try:
        events().dataChanged.emit(path)
    except Exception as exc:  # a finished import must not fail because of a listener
        log(
            tr(
                "The HamQ panel and layers could not be told about the changes in {path}: {error}"
            ).format(path=path, error=exc)
        )


def editing_layers(path: str) -> bool:
    """True when a QSO layer of the GeoPackage ``path`` in the project is in edit mode.

    Only the main thread may look at the project: elsewhere this returns False.
    """
    if not path or not in_main_thread():
        return False
    try:
        return any(layer.isEditable() for layer in layers.matching_layers(path))
    except Exception:  # the check is a courtesy; never fail an algorithm for it
        return False


def report_warnings(feedback: Any, warnings: Sequence[str], title: str) -> None:
    """Send the first :data:`FEEDBACK_WARNINGS` warnings to ``feedback`` and all of them,
    under ``title``, to the QGIS message log (one message)."""
    if not warnings:
        return
    for message in warnings[:FEEDBACK_WARNINGS]:
        feedback.pushWarning(message)
    rest = len(warnings) - FEEDBACK_WARNINGS
    if rest > 0:
        feedback.pushWarning(
            tr("More warnings in the QGIS message log (HamQ tab): {count}").format(count=rest)
        )
    log("\n".join([title, *warnings]))


# --- settings and parameters ----------------------------------------------------------------


def settings_value(name: str, fallback: Any) -> Any:
    """``HamQSettings().<name>``; ``fallback`` when the settings cannot be read."""
    try:
        return getattr(HamQSettings(), name)
    except Exception:  # defaults only; an algorithm must load without its settings
        return fallback


def add_station_parameters(algorithm: QgsProcessingAlgorithm) -> None:
    """Add ``MY_GRID`` (default: my locator in the settings, may be empty) and ``USE_CTY``
    (default True) to ``algorithm``."""
    algorithm.addParameter(
        QgsProcessingParameterString(
            MY_GRID,
            tr("My QTH locator"),
            defaultValue=settings_value("my_grid", ""),
            optional=True,
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterBoolean(
            USE_CTY,
            tr("Use cty.dat for DXCC data and positions missing in the log"),
            defaultValue=True,
        )
    )


def station_grid(text: object) -> str:
    """My locator from the ``MY_GRID`` value: ``""`` when empty, else the normalized
    locator (``kn04FT`` -> ``KN04ft``, 10 characters cut to 8).

    Raises ``QgsProcessingException`` with a translated message for an invalid locator.
    """
    value = "" if text is None else str(text).strip()
    if not value:
        return ""
    try:
        return maidenhead.normalize(value)
    except ValueError:
        raise QgsProcessingException(
            tr("QTH locator {locator} is not a valid Maidenhead locator (e.g. KN04ft).").format(
                locator=value
            )
        ) from None


def check_station_grid(
    algorithm: QgsProcessingAlgorithm, parameters: dict[str, Any], context: Any
) -> tuple[bool, str]:
    """``checkParameterValues`` part for ``MY_GRID``: ``(False, message)`` when invalid."""
    try:
        station_grid(algorithm.parameterAsString(parameters, MY_GRID, context))
    except QgsProcessingException as exc:
        return False, str(exc)
    return True, ""


def load_cty(use_cty: bool, feedback: Any) -> CtyDatabase | None:
    """The cached cty.dat database when ``use_cty`` is set; a warning when there is none.

    Uses ``net.cty_download.load_cached_cty`` (no network, safe in worker threads).
    """
    if not use_cty:
        return None
    database = cty_download.load_cached_cty()
    if database is None:
        feedback.pushWarning(
            tr(
                "cty.dat has not been downloaded, so DXCC data and positions are not looked up "
                "from the callsigns. Download it in the HamQ settings (DXCC data)."
            )
        )
        return None
    feedback.pushInfo(tr("cty.dat loaded, DXCC entities: {count}").format(count=len(database)))
    return database


def split_tags(text: str) -> list[str]:
    """``"ADIF, log, QSO"`` -> ``["ADIF", "log", "QSO"]`` (tags of an algorithm)."""
    return [tag.strip() for tag in text.split(",") if tag.strip()]


def file_name(path: str) -> str:
    """The file name of ``path`` (``/logs/wsjtx_log.adi`` -> ``wsjtx_log.adi``)."""
    return os.path.basename(os.path.normpath(path)) if path else ""
