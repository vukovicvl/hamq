"""HamQ: QGIS plugin for amateur radio operators."""

from __future__ import annotations


def classFactory(iface):  # noqa: N802 (QGIS plugin entry point name)
    """Create the plugin instance. Called by QGIS when the plugin is loaded."""
    # QGIS records the modules a plugin imports (qgis.utils._import) and unloadPlugin()
    # removes exactly those from sys.modules. It records only the module an import
    # statement returns, so a package that imports merely pass through
    # ("from .processing.provider import ...") would stay in sys.modules after an
    # unload, keep the modules of that load alive and be reused by the next load.
    # Importing every subpackage by name here records them all.
    from . import core, gui, net, processing, qgis_io  # noqa: F401
    from .plugin import HamQPlugin

    return HamQPlugin(iface)
