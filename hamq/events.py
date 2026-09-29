"""Process-wide HamQ signals.

Components do not hold references to each other to learn about changes; they
connect to the single :class:`HamQEvents` object returned by :func:`events`:

* ``dataChanged(path)``: the content of the GeoPackage ``path`` changed
  (import finished, WSJT-X QSO written, recalculation done);
* ``languageChanged(language)``: the interface language changed; the argument
  is the resolved language code (``en``, ``sr_Latn`` or ``sr_Cyrl``);
* ``settingsChanged()``: the HamQ settings were saved.

Connections made by the plugin and its widgets must be disconnected when the
plugin unloads.
"""

from __future__ import annotations

import threading

from qgis.PyQt.QtCore import QCoreApplication, QObject, pyqtSignal


class HamQEvents(QObject):
    """Signals shared by the plugin, docks, dialogs and Processing algorithms."""

    #: Path of the GeoPackage whose content changed.
    dataChanged = pyqtSignal(str)
    #: Resolved language code after a language change.
    languageChanged = pyqtSignal(str)
    #: The settings were saved.
    settingsChanged = pyqtSignal()


_instance: HamQEvents | None = None
_lock = threading.Lock()


def events() -> HamQEvents:
    """Return the process-wide :class:`HamQEvents` singleton.

    The first call must happen in the main (GUI) thread. The object is moved to
    the application thread anyway, so queued signal delivery to GUI slots keeps
    working even if a worker thread happens to call first.
    """
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                instance = HamQEvents()
                app = QCoreApplication.instance()
                if app is not None and instance.thread() is not app.thread():
                    instance.moveToThread(app.thread())
                _instance = instance
    return _instance
