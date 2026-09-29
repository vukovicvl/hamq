"""Processing provider ``hamq``.

Algorithms are registered by adding their classes to :data:`ALGORITHMS`; the
provider creates one instance of each in :meth:`HamQProvider.loadAlgorithms`.
After a language change the plugin calls ``provider.refreshAlgorithms()`` so the
toolbox shows the translated names.
"""

from __future__ import annotations

from qgis.core import QgsProcessingAlgorithm, QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from ..core.i18n import tr
from ..gui import icon_path

PROVIDER_ID = "hamq"
PROVIDER_ICON = "hamq.svg"

#: Algorithm classes of the provider, in toolbox order.
ALGORITHMS: list[type[QgsProcessingAlgorithm]] = []


class HamQProvider(QgsProcessingProvider):
    """Processing provider with the HamQ algorithms (id ``hamq``)."""

    def id(self) -> str:
        """Provider id used in algorithm ids (``hamq:import_adif``)."""
        return PROVIDER_ID

    def name(self) -> str:
        """Short provider name shown in the Processing toolbox."""
        return "HamQ"

    def longName(self) -> str:
        """Longer, translated provider description."""
        return self.tr("HamQ amateur radio tools")

    def icon(self) -> QIcon:
        """Provider icon."""
        return QIcon(icon_path(PROVIDER_ICON))

    def svgIconPath(self) -> str:
        """Path of the provider SVG icon."""
        return icon_path(PROVIDER_ICON)

    def loadAlgorithms(self) -> None:
        """Add one instance of every class in :data:`ALGORITHMS`."""
        for algorithm_class in ALGORITHMS:
            self.addAlgorithm(algorithm_class())

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator (not Qt's)."""
        return tr(text)
