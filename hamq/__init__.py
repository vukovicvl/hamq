"""HamQ: QGIS plugin for amateur radio operators."""


def classFactory(iface):  # noqa: N802 (QGIS plugin entry point name)
    """Create the plugin instance. Called by QGIS when the plugin is loaded."""
    from .plugin import HamQPlugin

    return HamQPlugin(iface)
