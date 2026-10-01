"""Processing algorithm ``hamq:locator_to_point``: Maidenhead locators -> center points.

The ``LOCATORS`` text holds one or more locators separated by spaces, commas,
semicolons or new lines (``KN04ft, JN95wg; FM18lv``). Each valid locator gives a
point at the center of its cell (EPSG:4326) with the fields ``locator`` (canonical
form, ``KN04ft``), ``precision`` (number of characters), ``lat`` and ``lon``. A
10-character locator is cut to 8 with a warning; an invalid one is reported
(``reportError(..., fatalError=False)``) and skipped. When no locator is valid the
algorithm fails.
"""

from __future__ import annotations

import re
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterString,
)

from ..core import maidenhead
from ..core.i18n import tr
from ..qgis_io import compat
from ..qgis_io.fields import make_fields
from .common import GROUP_MAIDENHEAD, HamQAlgorithm, split_tags

__all__ = ["POINT_FIELDS", "LocatorToPointAlgorithm", "split_locators"]

#: Fields of the output points as ``(name, kind)`` (see ``qgis_io.fields``).
POINT_FIELDS: tuple[tuple[str, str], ...] = (
    ("locator", "text"),
    ("precision", "int"),
    ("lat", "real"),
    ("lon", "real"),
)

_SEPARATORS = re.compile(r"[\s,;]+")


def split_locators(text: object) -> list[str]:
    """The tokens of ``text`` separated by whitespace, commas or semicolons, in order."""
    if not isinstance(text, str):
        return []
    return [token for token in _SEPARATORS.split(text) if token]


class LocatorToPointAlgorithm(HamQAlgorithm):
    """Points at the center of Maidenhead locator cells."""

    LOCATORS = "LOCATORS"
    OUTPUT = "OUTPUT"
    ICON = "locator.svg"
    GROUP_ID = GROUP_MAIDENHEAD

    def name(self) -> str:
        return "locator_to_point"

    def displayName(self) -> str:
        return tr("Locator to point")

    def shortHelpString(self) -> str:
        return tr(
            "Creates a point at the center of the cell of each Maidenhead locator. Enter one "
            "or more locators with 2, 4, 6 or 8 characters (e.g. KN04ft), separated by "
            "spaces, commas, semicolons or new lines. A 10-character locator is cut to 8 "
            "characters. Invalid locators are reported and skipped. The points are in "
            "EPSG:4326, with the fields {fields}."
        ).format(fields=", ".join(name for name, _ in POINT_FIELDS))

    def tags(self) -> list[str]:
        return split_tags(tr("Maidenhead, locator, QTH, point, ham radio"))

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        self.addParameter(
            QgsProcessingParameterString(self.LOCATORS, tr("Maidenhead locators"), multiLine=True)
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, tr("Locator points"), type=compat.SOURCE_VECTOR_POINT
            )
        )

    def processAlgorithm(
        self, parameters: dict[str, Any], context: Any, feedback: Any
    ) -> dict[str, Any]:
        text = self.parameterAsString(parameters, self.LOCATORS, context)
        locators: list[str] = []
        for token in split_locators(text):
            try:
                locator = maidenhead.normalize(token)
            except ValueError:
                feedback.reportError(
                    tr("{text} is not a valid Maidenhead locator, skipped").format(text=token),
                    False,
                )
                continue
            if len(token) > len(locator):
                feedback.pushWarning(
                    tr("Locator {text} was cut to 8 characters: {locator}").format(
                        text=token, locator=locator
                    )
                )
            locators.append(locator)
        if not locators:
            raise QgsProcessingException(
                tr("No valid Maidenhead locator was given (2, 4, 6 or 8 characters, e.g. KN04ft)")
            )

        fields = make_fields(POINT_FIELDS)
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            compat.WKB_POINT,
            QgsCoordinateReferenceSystem("EPSG:4326"),
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        total = len(locators)
        written = 0
        for locator in locators:
            if feedback.isCanceled():
                break
            lat, lon = maidenhead.to_latlon(locator)
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            feature.setAttributes([locator, len(locator), lat, lon])
            if not sink.addFeature(feature, compat.SINK_FAST_INSERT):
                raise QgsProcessingException(self.writeFeatureError(sink, parameters, self.OUTPUT))
            written += 1
            feedback.setProgress(100.0 * written / total)
        feedback.pushInfo(tr("Points: {count}").format(count=written))
        return {self.OUTPUT: dest_id}
