"""Processing algorithm ``hamq:import_adif``: an ADIF log into the HamQ GeoPackage.

Steps (``processAlgorithm``, a worker thread when started from the toolbox):

1. ``core.adif.read_adi(INPUT)``;
2. ``core.qso.records_to_qsos`` with my locator (``MY_GRID``, default: the HamQ
   settings), the cached cty.dat (``USE_CTY``) and ``source = "adif:<file name>"``;
3. ``qgis_io.gpkg.ensure_gpkg(GPKG)`` and ``qgis_io.gpkg.insert_qsos``, which skips
   duplicates (same ``dedup_key``) and writes the QSO points and their paths.

Outputs: ``IMPORTED``, ``DUPLICATES``, ``SKIPPED`` (records without CALL or a valid
date and time, plus QSOs that could not be saved) and ``GPKG``. Warnings of the
three steps go to the feedback (the first :data:`common.FEEDBACK_WARNINGS`) and all
of them to the QGIS message log. ``postProcessAlgorithm`` (main thread) adds the QSO
layers to the project when ``LOAD_LAYERS`` is set (not in ``qgis_process``) and
emits ``events().dataChanged(GPKG)``. A canceled import keeps the QSOs saved before
the cancel and announces them at once, because Processing skips
``postProcessAlgorithm`` for a canceled task.
"""

from __future__ import annotations

from typing import Any

from qgis.core import (
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
)

from ..core.adif import read_adi
from ..core.i18n import tr, tr_noop
from ..core.qso import Station, records_to_qsos
from ..qgis_io import gpkg, layers
from ..qgis_io.compat import FILE_BEHAVIOR_FILE
from .common import (
    GROUP_LOG,
    MY_GRID,
    USE_CTY,
    HamQAlgorithm,
    add_station_parameters,
    check_station_grid,
    editing_layers,
    file_name,
    in_main_thread,
    is_qgis_process,
    load_cty,
    notify_data_changed,
    report_warnings,
    settings_value,
    split_tags,
    station_grid,
)

__all__ = ["ImportAdifAlgorithm"]

# The warning of qgis_io.gpkg.insert_qsos for layers in edit mode: insert_qsos can only
# check the project in the main thread, so from a worker thread this algorithm adds it.
_EDITING_WARNING = tr_noop(
    "The QSO layers are in edit mode. The new QSOs were saved in the GeoPackage "
    "and show up after you save or discard your edits."
)


class ImportAdifAlgorithm(HamQAlgorithm):
    """Import an ADIF (.adi) log into the HamQ GeoPackage, with deduplication."""

    INPUT = "INPUT"
    GPKG = "GPKG"
    MY_GRID = MY_GRID
    USE_CTY = USE_CTY
    LOAD_LAYERS = "LOAD_LAYERS"
    IMPORTED = "IMPORTED"
    DUPLICATES = "DUPLICATES"
    SKIPPED = "SKIPPED"
    ICON = "import_adif.svg"
    GROUP_ID = GROUP_LOG

    def name(self) -> str:
        return "import_adif"

    def displayName(self) -> str:
        return tr("Import ADIF")

    def shortHelpString(self) -> str:
        return tr(
            "Imports an ADIF log ({extension}) into the HamQ GeoPackage: a point for each QSO "
            "at the position of the other station (LAT/LON, else the center of the locator in "
            "GRIDSQUARE, else the DXCC entity from cty.dat) and the geodesic path from my QTH. "
            "QSOs that are already in the GeoPackage (same callsign, minute, band and mode) "
            "are skipped as duplicates, so importing the same file again adds nothing. "
            "Records without CALL or with an invalid date or time are skipped and reported. "
            "My QTH is MY_LAT/MY_LON or MY_GRIDSQUARE of each record, else My QTH locator."
        ).format(extension=".adi")

    def tags(self) -> list[str]:
        return split_tags(tr("ADIF, log, QSO, import, WSJT-X, GeoPackage, ham radio"))

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT,
                tr("ADIF file"),
                behavior=FILE_BEHAVIOR_FILE,
                # patterns as placeholders: the Cyrillic transliteration must not touch them
                fileFilter=";;".join(
                    (
                        tr("ADIF files ({pattern})").format(pattern="*.adi *.adif *.ADI *.ADIF"),
                        tr("All files ({pattern})").format(pattern="*"),
                    )
                ),
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.GPKG,
                tr("GeoPackage with the QSO log"),
                fileFilter=tr("GeoPackage files ({pattern})").format(pattern="*.gpkg"),
                defaultValue=settings_value("gpkg_path", None),
            )
        )
        add_station_parameters(self)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LOAD_LAYERS, tr("Add the QSO layers to the project"), defaultValue=True
            )
        )
        self.addOutput(QgsProcessingOutputNumber(self.IMPORTED, tr("Imported QSOs")))
        self.addOutput(QgsProcessingOutputNumber(self.DUPLICATES, tr("Duplicates")))
        self.addOutput(QgsProcessingOutputNumber(self.SKIPPED, tr("Skipped records")))

    def checkParameterValues(self, parameters: dict[str, Any], context: Any) -> tuple[bool, str]:
        ok, message = super().checkParameterValues(parameters, context)
        if not ok:
            return ok, message
        return check_station_grid(self, parameters, context)

    def prepareAlgorithm(self, parameters: dict[str, Any], context: Any, feedback: Any) -> bool:
        # Main thread: the project may only be looked at here (edit mode of the layers).
        self._changed_path = ""
        self._load_layers = False
        try:
            path = self.parameterAsFileOutput(parameters, self.GPKG, context)
        except Exception:  # processAlgorithm reports a bad value
            path = ""
        self._editing = editing_layers(path)
        return True

    def processAlgorithm(
        self, parameters: dict[str, Any], context: Any, feedback: Any
    ) -> dict[str, Any]:
        self._changed_path = ""
        adif_path = self.parameterAsFile(parameters, self.INPUT, context)
        gpkg_path = self.parameterAsFileOutput(parameters, self.GPKG, context)
        if not adif_path:
            raise QgsProcessingException(tr("No ADIF file was given"))
        if not gpkg_path:
            raise QgsProcessingException(tr("No GeoPackage file is set"))
        grid = station_grid(self.parameterAsString(parameters, self.MY_GRID, context))
        use_cty = self.parameterAsBoolean(parameters, self.USE_CTY, context)
        self._load_layers = self.parameterAsBoolean(parameters, self.LOAD_LAYERS, context)
        steps = QgsProcessingMultiStepFeedback(3, feedback)

        # 1. read
        steps.setProgressText(tr("Reading the ADIF file"))
        try:
            document = read_adi(adif_path)
        except OSError as exc:
            raise QgsProcessingException(
                tr("The ADIF file {path} could not be read: {error}").format(
                    path=adif_path, error=exc.strerror or exc
                )
            ) from exc
        total = len(document.records)
        feedback.pushInfo(tr("Records in the ADIF file: {count}").format(count=total))
        if not total:
            feedback.pushWarning(tr("No QSO records found in {file}").format(file=adif_path))
        if feedback.isCanceled():
            return {}

        # 2. convert
        steps.setCurrentStep(1)
        steps.setProgressText(tr("Checking the QSOs"))
        cty = load_cty(use_cty, feedback)
        qsos, qso_warnings = records_to_qsos(
            document.records,
            station=Station(grid=grid),
            cty=cty,
            source="adif:" + file_name(adif_path),
        )
        if feedback.isCanceled():
            return {}

        # 3. save
        steps.setCurrentStep(2)
        steps.setProgressText(tr("Saving the QSOs"))
        try:
            gpkg.ensure_gpkg(gpkg_path)
            result = gpkg.insert_qsos(gpkg_path, qsos, feedback=steps)
        except gpkg.GpkgError as exc:
            raise QgsProcessingException(str(exc)) from exc
        self._changed_path = gpkg_path

        skipped = total - len(qsos) + result.failed
        warnings = [*document.warnings, *qso_warnings, *result.warnings]
        if result.inserted and getattr(self, "_editing", False) and not in_main_thread():
            warnings.insert(0, tr(_EDITING_WARNING))
        report_warnings(
            feedback,
            warnings,
            tr("Warnings while importing {file}:").format(file=adif_path),
        )
        feedback.pushInfo(
            tr("Imported: {imported}, duplicates: {duplicates}, skipped: {skipped}").format(
                imported=result.inserted, duplicates=result.duplicates, skipped=skipped
            )
        )
        if result.canceled:
            feedback.pushWarning(
                tr("Import canceled. The QSOs saved before stay in the GeoPackage: {count}").format(
                    count=result.inserted
                )
            )
            # Processing does not call postProcessAlgorithm for a canceled task: announce
            # the saved QSOs now (queued into the main thread when this is a worker).
            self._changed_path = ""
            if result.inserted:
                notify_data_changed(gpkg_path)
        return {
            self.IMPORTED: result.inserted,
            self.DUPLICATES: result.duplicates,
            self.SKIPPED: skipped,
            self.GPKG: gpkg_path,
        }

    def postProcessAlgorithm(self, context: Any, feedback: Any) -> dict[str, Any]:
        path = getattr(self, "_changed_path", "")
        if not path:
            return {}
        self._changed_path = ""
        if getattr(self, "_load_layers", False) and in_main_thread() and not is_qgis_process():
            try:
                layers.load_layers(path)
            except Exception as exc:  # the import itself succeeded
                message = tr("The QSO layers could not be added to the project: {error}").format(
                    error=exc
                )
                if feedback is not None:
                    feedback.pushWarning(message)
        notify_data_changed(path)
        return {}
