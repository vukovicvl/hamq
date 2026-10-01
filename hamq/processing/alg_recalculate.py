"""Processing algorithm ``hamq:recalculate``: distances, bearings, paths and DXCC data again.

Runs ``qgis_io.gpkg.recalculate`` on the GeoPackage ``GPKG`` with my locator
(``MY_GRID``, default: the HamQ settings) and the cached cty.dat (``USE_CTY``), for
example after the QTH locator was set or changed or cty.dat was downloaded. QSOs
logged with their own QTH keep it unless ``FORCE_STATION`` is set (every QSO then
gets my locator). Outputs: ``UPDATED`` (QSOs changed) and ``GPKG``.
``postProcessAlgorithm`` (main thread) emits ``events().dataChanged(GPKG)``.
"""

from __future__ import annotations

import os
from typing import Any

from qgis.core import (
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
)

from ..core.i18n import tr, tr_noop
from ..core.qso import Station
from ..qgis_io import gpkg
from ..qgis_io.compat import FILE_BEHAVIOR_FILE
from .common import (
    GROUP_LOG,
    MY_GRID,
    USE_CTY,
    HamQAlgorithm,
    add_station_parameters,
    check_station_grid,
    editing_layers,
    in_main_thread,
    load_cty,
    notify_data_changed,
    settings_value,
    split_tags,
    station_grid,
)

__all__ = ["RecalculateAlgorithm"]

# The warning of qgis_io.gpkg.recalculate for layers in edit mode: recalculate can only
# check the project in the main thread, so from a worker thread this algorithm adds it.
_EDITING_WARNING = tr_noop(
    "The QSO layers are in edit mode. The recalculated values were saved in the "
    "GeoPackage; saving your edits may overwrite some of them."
)


class RecalculateAlgorithm(HamQAlgorithm):
    """Recalculate distance, bearing, path and missing DXCC data of every QSO."""

    GPKG = "GPKG"
    MY_GRID = MY_GRID
    USE_CTY = USE_CTY
    FORCE_STATION = "FORCE_STATION"
    UPDATED = "UPDATED"
    ICON = "refresh.svg"
    GROUP_ID = GROUP_LOG

    def name(self) -> str:
        return "recalculate"

    def displayName(self) -> str:
        return tr("Recalculate distances and DXCC data")

    def shortHelpString(self) -> str:
        return tr(
            "Recalculates distance, bearing and path of every QSO in the GeoPackage, for "
            "example after you set or changed your QTH locator or downloaded cty.dat. QSOs "
            "logged with their own QTH (MY_LAT/MY_LON or MY_GRIDSQUARE) keep it, unless "
            "my locator is to be used for every QSO. Missing DXCC data and positions are "
            "filled in from cty.dat; values from the log are kept."
        )

    def tags(self) -> list[str]:
        return split_tags(tr("distance, bearing, path, DXCC, cty.dat, QTH, ham radio"))

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        self.addParameter(
            QgsProcessingParameterFile(
                self.GPKG,
                tr("GeoPackage with the QSO log"),
                behavior=FILE_BEHAVIOR_FILE,
                # "All files" too: Processing matches the patterns case-sensitively, and the
                # log may be called LOG.GPKG or have no extension (the import accepts both)
                fileFilter=";;".join(
                    (
                        tr("GeoPackage files ({pattern})").format(pattern="*.gpkg *.GPKG"),
                        tr("All files ({pattern})").format(pattern="*"),
                    )
                ),
                defaultValue=settings_value("gpkg_path", None),
            )
        )
        add_station_parameters(self)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.FORCE_STATION,
                tr("Use my locator for every QSO, also for QSOs logged with another QTH"),
                defaultValue=False,
            )
        )
        self.addOutput(QgsProcessingOutputNumber(self.UPDATED, tr("Updated QSOs")))
        self.addOutput(QgsProcessingOutputFile(self.GPKG, tr("GeoPackage with the QSO log")))

    def checkParameterValues(self, parameters: dict[str, Any], context: Any) -> tuple[bool, str]:
        ok, message = super().checkParameterValues(parameters, context)
        if not ok:
            return ok, message
        return check_station_grid(self, parameters, context)

    def prepareAlgorithm(self, parameters: dict[str, Any], context: Any, feedback: Any) -> bool:
        # Main thread: the project may only be looked at here (edit mode of the layers).
        self._changed_path = ""
        try:
            path = self.parameterAsFile(parameters, self.GPKG, context)
        except Exception:  # processAlgorithm reports a bad value
            path = ""
        self._editing = editing_layers(path)
        return True

    def processAlgorithm(
        self, parameters: dict[str, Any], context: Any, feedback: Any
    ) -> dict[str, Any]:
        self._changed_path = ""
        path = self.parameterAsFile(parameters, self.GPKG, context)
        if not path:
            raise QgsProcessingException(tr("No GeoPackage file is set"))
        if not os.path.exists(path):  # a folder is reported by gpkg
            raise QgsProcessingException(
                tr("The GeoPackage {path} does not exist").format(path=path)
            )
        grid = station_grid(self.parameterAsString(parameters, self.MY_GRID, context))
        use_cty = self.parameterAsBoolean(parameters, self.USE_CTY, context)
        force = self.parameterAsBoolean(parameters, self.FORCE_STATION, context)
        cty = load_cty(use_cty, feedback)
        if feedback.isCanceled():
            return {}
        if getattr(self, "_editing", False) and not in_main_thread():
            feedback.pushWarning(tr(_EDITING_WARNING))
        try:
            # warnings are logged and sent to feedback.pushWarning by recalculate itself
            updated = gpkg.recalculate(path, Station(grid=grid), cty, feedback, force_station=force)
        except gpkg.GpkgError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if feedback.isCanceled() and not updated:
            # recalculate stops only before it writes: nothing was changed
            feedback.pushWarning(tr("Recalculation canceled, nothing was changed"))
            return {}
        feedback.pushInfo(tr("Updated QSOs: {count}").format(count=updated))
        if feedback.isCanceled():
            # canceled while writing, which recalculate finishes: announce the changes now,
            # Processing does not call postProcessAlgorithm for a canceled task
            notify_data_changed(path)
        else:
            self._changed_path = path
        return {self.UPDATED: updated, self.GPKG: path}

    def postProcessAlgorithm(self, context: Any, feedback: Any) -> dict[str, Any]:
        path = getattr(self, "_changed_path", "")
        if path:
            self._changed_path = ""
            notify_data_changed(path)
        return {}
