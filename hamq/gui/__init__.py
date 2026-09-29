"""GUI of HamQ: dock panel, dialogs, toolbar widgets and map tools.

Keep logic out of GUI code: widgets call into ``hamq.core`` and ``hamq.qgis_io``.
Every widget with text implements ``retranslate()`` and listens to
``hamq.events.events().languageChanged``.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtGui import QIcon

#: Directory with the plugin SVG icons.
ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons"
)


def icon_path(name: str) -> str:
    """Return the absolute path of the icon file ``name`` (e.g. ``"hamq.svg"``)."""
    return os.path.join(ICONS_DIR, name)


def get_icon(name: str) -> QIcon:
    """Return a ``QIcon`` for the icon file ``name`` in ``resources/icons``."""
    return QIcon(icon_path(name))
