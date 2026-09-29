"""HamQ plugin: actions, menu, toolbar and Processing provider (a thin layer).

QGIS calls :meth:`HamQPlugin.initGui` after loading the plugin and
:meth:`HamQPlugin.unload` before unloading or reloading it. ``unload()`` undoes
everything ``initGui()`` did, through the registries kept here:

* actions created with :meth:`HamQPlugin.add_action` (menu, toolbar, texts);
* dock widgets added with :meth:`HamQPlugin.add_dock_widget`;
* signal connections made with :meth:`HamQPlugin.connect_signal`;
* teardown callbacks registered with :meth:`HamQPlugin.add_cleanup`
  (controller, WSJT-X listener, Hamlib clients, language manager, ...).

Texts are English source strings marked with ``tr_noop("...")``; they are
translated when an action is created and again by :meth:`retranslate_ui`
whenever ``events().languageChanged`` fires, so the interface switches language
without restarting QGIS.
"""

from __future__ import annotations

import configparser
import html
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qgis.core import QgsApplication, QgsMessageLog
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDockWidget, QMessageBox, QToolBar

from .core.i18n import tr, tr_noop
from .events import events
from .gui import get_icon
from .processing.provider import HamQProvider
from .qgis_io.compat import DOCK_RIGHT, MSG_WARNING, QAction

#: Directory of the plugin package (where metadata.txt lives).
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
#: Plugin menu title. A product name: not translated, and it must stay the same
#: between addPluginToMenu() and removePluginMenu().
MENU_TITLE = "&HamQ"
#: Toolbar title (View > Toolbars) and object name (saved window state).
TOOLBAR_TITLE = "HamQ"
TOOLBAR_OBJECT_NAME = "HamQToolbar"
#: Tag of HamQ messages in the QGIS log panel.
LOG_TAG = "HamQ"


def plugin_metadata() -> dict[str, str]:
    """Return the ``[general]`` section of ``metadata.txt`` (keys keep their case)."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep "qgisMinimumVersion" as written
    try:
        parser.read(os.path.join(PLUGIN_DIR, "metadata.txt"), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return {}
    if not parser.has_section("general"):
        return {}
    return dict(parser.items("general"))


@dataclass
class _ActionEntry:
    """An action created by :meth:`HamQPlugin.add_action` and where it was added."""

    action: QAction
    text: str  # untranslated source text
    tooltip: str | None  # untranslated source tooltip
    in_menu: bool
    in_toolbar: bool


class HamQPlugin:
    """The HamQ QGIS plugin object returned by ``classFactory(iface)``."""

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self.provider: HamQProvider | None = None
        self.toolbar: QToolBar | None = None
        self.about_action: QAction | None = None
        self._actions: list[_ActionEntry] = []
        self._docks: list[QDockWidget] = []
        self._connections: list[tuple[Any, Callable[..., Any]]] = []
        self._cleanups: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initProcessing(self) -> None:
        """Register the Processing provider (also called by ``qgis_process``)."""
        if self.provider is not None:
            return
        provider = HamQProvider()
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider
        else:
            self._log(self.tr("Could not register the HamQ Processing provider"), MSG_WARNING)

    def initGui(self) -> None:
        """Create the provider, toolbar, menu actions and signal connections."""
        self.initProcessing()

        self.toolbar = self.iface.addToolBar(TOOLBAR_TITLE)
        self.toolbar.setObjectName(TOOLBAR_OBJECT_NAME)

        self._create_actions()

        # Components added by later milestones go here, each registering its
        # teardown with add_cleanup() / add_dock_widget() / connect_signal():
        # language manager and switch (M6), dock panel and controller (M4, M5),
        # Hamlib clients and rotator map tool (M7).

        self.connect_signal(events().languageChanged, self._on_language_changed)
        self.retranslate_ui()

    def unload(self) -> None:
        """Undo everything :meth:`initGui` did. Safe to call more than once."""
        self._disconnect_all()
        self._run_cleanups()
        self._remove_docks()
        self._remove_actions()
        self._remove_toolbar()
        self._remove_provider()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create_actions(self) -> None:
        """Create the menu and toolbar actions (later milestones add theirs here)."""
        self.about_action = self.add_action(
            "hamq.svg",
            tr_noop("About HamQ"),
            self.show_about,
            object_name="HamQAboutAction",
        )

    def add_action(
        self,
        icon: str | QIcon | None,
        text: str,
        callback: Callable[..., Any] | None = None,
        *,
        add_to_menu: bool = True,
        add_to_toolbar: bool = True,
        checkable: bool = False,
        checked: bool = False,
        tooltip: str | None = None,
        enabled: bool = True,
        object_name: str | None = None,
    ) -> QAction:
        """Create an action, add it to the HamQ menu and/or toolbar and record it.

        ``icon`` is a file name in ``resources/icons`` or a ``QIcon``. ``text``
        and ``tooltip`` are untranslated English sources marked at the call site
        with ``tr_noop("...")``; they are translated now and again by
        :meth:`retranslate_ui`. For checkable actions ``callback`` receives the
        checked state. :meth:`unload` removes and deletes the action.
        """
        action = QAction(self.iface.mainWindow())
        if isinstance(icon, QIcon):
            action.setIcon(icon)
        elif icon:
            action.setIcon(get_icon(icon))
        if object_name:
            action.setObjectName(object_name)
        action.setCheckable(checkable)
        if checkable:
            action.setChecked(checked)
        action.setEnabled(enabled)
        if callback is not None:
            action.triggered.connect(callback)

        entry = _ActionEntry(
            action=action,
            text=text,
            tooltip=tooltip,
            in_menu=add_to_menu,
            in_toolbar=add_to_toolbar and self.toolbar is not None,
        )
        self._apply_texts(entry)
        if entry.in_menu:
            self.iface.addPluginToMenu(MENU_TITLE, action)
        if entry.in_toolbar:
            self.toolbar.addAction(action)
        self._actions.append(entry)
        return action

    def actions(self) -> list[QAction]:
        """Actions created by :meth:`add_action`, in creation order."""
        return [entry.action for entry in self._actions]

    def _apply_texts(self, entry: _ActionEntry) -> None:
        # entry.text / entry.tooltip were marked with tr_noop() at the call site.
        entry.action.setText(tr(entry.text))
        if entry.tooltip:
            entry.action.setToolTip(tr(entry.tooltip))

    def _remove_actions(self) -> None:
        while self._actions:
            entry = self._actions.pop()
            try:
                if entry.in_menu:
                    self.iface.removePluginMenu(MENU_TITLE, entry.action)
                if entry.in_toolbar and self.toolbar is not None:
                    self.toolbar.removeAction(entry.action)
                entry.action.deleteLater()
            except RuntimeError:  # the C++ object is already gone
                pass
        self.about_action = None

    # ------------------------------------------------------------------
    # Toolbar, docks, provider
    # ------------------------------------------------------------------

    def add_dock_widget(self, dock: QDockWidget, area: Any = DOCK_RIGHT) -> None:
        """Add ``dock`` to the QGIS main window; :meth:`unload` removes and deletes it."""
        self.iface.addDockWidget(area, dock)
        self._docks.append(dock)

    def _remove_docks(self) -> None:
        while self._docks:
            dock = self._docks.pop()
            try:
                self.iface.removeDockWidget(dock)
                dock.deleteLater()
            except RuntimeError:
                pass

    def _remove_toolbar(self) -> None:
        if self.toolbar is None:
            return
        try:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
        except RuntimeError:
            pass
        self.toolbar = None

    def _remove_provider(self) -> None:
        if self.provider is None:
            return
        # The registry owns (and deletes) the provider once it was added.
        try:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        except RuntimeError:  # already deleted, e.g. while QGIS shuts down
            pass
        self.provider = None

    # ------------------------------------------------------------------
    # Signals and teardown
    # ------------------------------------------------------------------

    def connect_signal(self, signal: Any, slot: Callable[..., Any]) -> None:
        """Connect ``signal`` to ``slot``; :meth:`unload` disconnects it."""
        signal.connect(slot)
        self._connections.append((signal, slot))

    def _disconnect_all(self) -> None:
        while self._connections:
            signal, slot = self._connections.pop()
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):  # already disconnected / sender deleted
                pass

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        """Register a teardown callback; :meth:`unload` runs them last-in first-out."""
        self._cleanups.append(callback)

    def _run_cleanups(self) -> None:
        while self._cleanups:
            callback = self._cleanups.pop()
            try:
                callback()
            except Exception as exc:  # unload must finish whatever a component does
                self._log(
                    self.tr("Cleanup on unload failed: {error}").format(error=exc), MSG_WARNING
                )

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    def tr(self, text: str) -> str:
        """Translate ``text`` with the HamQ translator (``hamq.core.i18n``)."""
        return tr(text)

    def retranslate_ui(self) -> None:
        """Re-apply the texts of every recorded action in the current language."""
        for entry in self._actions:
            try:
                self._apply_texts(entry)
            except RuntimeError:
                pass

    def _on_language_changed(self, language: str) -> None:
        self.retranslate_ui()
        if self.provider is not None:
            self.provider.refreshAlgorithms()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def about_text(self) -> str:
        """Rich text of the About box (name, version, description, link, credits)."""
        meta = plugin_metadata()
        esc = html.escape
        paragraphs = [
            "<b>HamQ</b><br>"
            + esc(self.tr("Version {version}").format(version=meta.get("version", "?"))),
            esc(
                self.tr(
                    "Amateur radio tools for QGIS: Maidenhead locators, QSO log map, "
                    "DXCC statistics and live QSOs from WSJT-X."
                )
            ),
        ]
        author = meta.get("author", "")
        if author:
            paragraphs.append(esc(self.tr("Author: {author}").format(author=author)))
        license_name = meta.get("license", "")
        if license_name:
            paragraphs.append(esc(self.tr("License: {license}").format(license=license_name)))
        repository = meta.get("repository", "")
        if repository:
            paragraphs.append(
                esc(self.tr("Source code and issue tracker:"))
                + f'<br><a href="{esc(repository)}">{esc(repository)}</a>'
            )
        paragraphs.append(
            "<b>"
            + esc(self.tr("Credits"))
            + "</b><br>"
            + esc(
                self.tr(
                    "DXCC data: cty.dat by Jim Reisert, AD1C (country-files.com), "
                    "downloaded on first use and not bundled with the plugin."
                )
            )
            + "<br>"
            + esc(self.tr("WSJT-X UDP protocol: Joe Taylor, K1JT, and the WSJT Development Group."))
        )
        return "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)

    def show_about(self) -> None:
        """Show the About box."""
        QMessageBox.about(self.iface.mainWindow(), self.tr("About HamQ"), self.about_text())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log(message: str, level: Any) -> None:
        QgsMessageLog.logMessage(message, LOG_TAG, level)
