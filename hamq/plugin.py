"""HamQ plugin: actions, menu, toolbar and Processing provider (a thin layer).

QGIS calls :meth:`HamQPlugin.initGui` after loading the plugin and
:meth:`HamQPlugin.unload` before unloading or reloading it. ``unload()`` undoes
everything ``initGui()`` did, through the registries kept here:

* actions created with :meth:`HamQPlugin.add_action` (menu, toolbar, texts);
* dock widgets added with :meth:`HamQPlugin.add_dock_widget`;
* signal connections made with :meth:`HamQPlugin.connect_signal`;
* teardown callbacks registered with :meth:`HamQPlugin.add_cleanup`
  (controller, WSJT-X listener, Hamlib clients, language manager, ...).

The work is done by :class:`hamq.controller.HamQController` (created in ``initGui``,
imported there so that ``qgis_process``, which only calls :meth:`initProcessing`,
never loads the GUI): it owns the settings, the language manager, the WSJT-X listener,
the Hamlib clients, the azimuthal map, the rotator map tool and the panel. The plugin
adds the panel, the actions, the language menu and the toolbar widgets (locator
search, language switch) and keeps the checkable actions in sync with the controller.

Texts are English source strings marked with ``tr_noop("...")``; they are
translated when an action is created and again by :meth:`retranslate_ui`
whenever ``events().languageChanged`` fires, so the interface switches language
without restarting QGIS. The panel, the language menu, the toolbar widgets, open
dialogs, the HamQ layers (``qgis_io.layers.connect_events``) and the Processing
algorithms (``provider.refreshAlgorithms()``) follow the same signal.
"""

from __future__ import annotations

import configparser
import contextlib
import functools
import html
import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from qgis.core import QgsApplication, QgsMessageLog, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDockWidget, QMessageBox, QToolBar

from .core.i18n import tr, tr_noop
from .events import events
from .gui import get_icon
from .processing.provider import HamQProvider
from .qgis_io.compat import DOCK_RIGHT, LOG_PANEL_SHOWS_HTML, MSG_CRITICAL, MSG_WARNING, QAction

if TYPE_CHECKING:
    from .controller import HamQController
    from .gui.language import LanguageMenu, LanguageSwitchButton
    from .gui.locator_search import LocatorSearchWidget

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


def _log(message: str, level: Any) -> None:
    """Log ``message`` in the HamQ tab of the QGIS log panel.

    Escaped where the panel renders HTML (QGIS 3.34 to 3.40.6, ``LOG_PANEL_SHOWS_HTML``):
    an error text can quote a log file or the network, and ``<lambda>`` in a traceback
    would vanish.
    """
    if LOG_PANEL_SHOWS_HTML:
        message = html.escape(message, quote=False)
    QgsMessageLog.logMessage(message, LOG_TAG, level)


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Qt slot: an exception is logged instead of escaping into Qt."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except Exception as exc:  # a slot must never raise into Qt
            message = tr("Unexpected error in {slot}: {error}").format(
                slot=method.__qualname__, error=exc
            )
            _log(f"{message}\n{traceback.format_exc()}", MSG_CRITICAL)
            return None

    return wrapper


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
        self.controller: HamQController | None = None
        self.panel_action: QAction | None = None
        self.import_action: QAction | None = None
        self.listen_action: QAction | None = None
        self.log_qso_action: QAction | None = None
        self.point_action: QAction | None = None
        self.azimuthal_action: QAction | None = None
        self.grid_action: QAction | None = None
        self.locator_action: QAction | None = None
        self.recalculate_action: QAction | None = None
        self.cty_action: QAction | None = None
        self.settings_action: QAction | None = None
        self.about_action: QAction | None = None
        self.language_menu: LanguageMenu | None = None
        self.language_button: LanguageSwitchButton | None = None
        self.locator_search: LocatorSearchWidget | None = None
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
        """Create the controller, provider, panel, toolbar, menu and signal connections.

        When a step fails, everything made so far is undone before the error goes to QGIS.
        """
        try:
            self._init_gui()
        except Exception:
            self.unload()
            raise

    def _init_gui(self) -> None:
        # Imported here: qgis_process loads the plugin only for its Processing provider.
        from .controller import HamQController
        from .qgis_io import layers

        # The controller applies the interface language; connected first, so the
        # provider (also one registered before) and every action follow it.
        self.connect_signal(events().languageChanged, self._on_language_changed)
        self.controller = HamQController(self.iface, parent=self.iface.mainWindow())
        self.add_cleanup(self._release_controller)
        self.initProcessing()

        self.toolbar = self.iface.addToolBar(TOOLBAR_TITLE)
        self.toolbar.setObjectName(TOOLBAR_OBJECT_NAME)
        self.add_dock_widget(self.controller.dock)
        # dataChanged -> refresh the HamQ layers, languageChanged -> translate them again
        self.add_cleanup(layers.connect_events())
        self.connect_signal(QgsProject.instance().readProject, self._on_project_read)

        self._create_actions()
        self._connect_controller()
        self.retranslate_ui()
        self._on_project_read()  # HamQ layers of an open project, named in another language
        self.controller.start()

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
        """Create the menu and toolbar actions, the language menu and the toolbar widgets.

        Menu order: panel, import, WSJT-X, log QSO, rotator, azimuthal map, the three
        Processing dialogs, cty.dat, settings, language, about. The toolbar has the
        actions used while operating, then the locator search and the language switch.
        """
        dock = self.controller.dock
        self.panel_action = self.add_action(
            "panel.svg",
            tr_noop("Show HamQ panel"),
            self._on_panel_triggered,
            checkable=True,
            checked=not dock.isHidden(),
            tooltip=tr_noop("Show or hide the HamQ panel: statistics, WSJT-X and radio"),
            object_name="HamQPanelAction",
        )
        self.import_action = self.add_action(
            "import_adif.svg",
            tr_noop("Import ADIF..."),
            self._on_import_triggered,
            tooltip=tr_noop("Import an ADIF log into the HamQ GeoPackage"),
            object_name="HamQImportAdifAction",
        )
        self.listen_action = self.add_action(
            "wsjtx.svg",
            tr_noop("Listen to WSJT-X"),
            self._on_listen_triggered,
            checkable=True,
            tooltip=tr_noop("Receive QSOs and status from WSJT-X or JTDX over UDP"),
            object_name="HamQListenAction",
        )
        self.log_qso_action = self.add_action(
            "radio.svg",
            tr_noop("Log QSO..."),
            self._on_log_qso_triggered,
            add_to_toolbar=False,
            object_name="HamQLogQsoAction",
        )
        self.point_action = self.add_action(
            "rotator.svg",
            tr_noop("Point antenna on map"),
            self._on_point_triggered,
            checkable=True,
            tooltip=tr_noop("Click on the map to turn the antenna toward that point"),
            object_name="HamQPointAntennaAction",
        )
        self.azimuthal_action = self.add_action(
            "azimuthal.svg",
            tr_noop("Azimuthal map"),
            self._on_azimuthal_triggered,
            checkable=True,
            tooltip=tr_noop("Azimuthal equidistant map centered on your QTH"),
            object_name="HamQAzimuthalAction",
        )
        self.grid_action = self.add_action(
            "grid.svg",
            tr_noop("Maidenhead grid..."),
            self._on_grid_triggered,
            add_to_toolbar=False,
            object_name="HamQGridAction",
        )
        self.locator_action = self.add_action(
            "locator.svg",
            tr_noop("Locator to point..."),
            self._on_locator_triggered,
            add_to_toolbar=False,
            object_name="HamQLocatorToPointAction",
        )
        self.recalculate_action = self.add_action(
            "paths.svg",
            tr_noop("Recalculate distances and DXCC data..."),  # the algorithm's name
            self._on_recalculate_triggered,
            add_to_toolbar=False,
            object_name="HamQRecalculateAction",
        )
        self.cty_action = self.add_action(
            "refresh.svg",
            tr_noop("Download cty.dat"),
            self._on_cty_triggered,
            add_to_toolbar=False,
            object_name="HamQDownloadCtyAction",
        )
        self.settings_action = self.add_action(
            "settings.svg",
            tr_noop("Settings..."),
            self._on_settings_triggered,
            tooltip=tr_noop("HamQ settings"),
            object_name="HamQSettingsAction",
        )
        self._create_language_menu()
        self._create_toolbar_widgets()
        self.about_action = self.add_action(
            "hamq.svg",
            tr_noop("About HamQ"),
            self.show_about,
            add_to_toolbar=False,
            object_name="HamQAboutAction",
        )

    def _create_language_menu(self) -> None:
        """Plugins > HamQ > Language / Jezik (shared with the toolbar switch button)."""
        from .gui.language import build_language_menu

        menu = build_language_menu(self.controller.language_manager, self.iface.mainWindow())
        self.language_menu = menu
        self.add_cleanup(self._remove_language_menu)
        self.iface.addPluginToMenu(MENU_TITLE, menu.menuAction())

    def _remove_language_menu(self) -> None:
        menu, self.language_menu = self.language_menu, None
        if menu is None:
            return
        menu.cleanup()
        with contextlib.suppress(RuntimeError):  # the main window may be gone already
            self.iface.removePluginMenu(MENU_TITLE, menu.menuAction())
            menu.deleteLater()

    def _create_toolbar_widgets(self) -> None:
        """Locator search and the EN / SR / СР switch; the toolbar deletes them."""
        from .gui.language import LanguageSwitchButton
        from .gui.locator_search import LocatorSearchWidget

        self.toolbar.addSeparator()
        search = LocatorSearchWidget(self.iface)
        self.locator_search = search
        self.add_cleanup(self._release_locator_search)
        self.toolbar.addWidget(search)
        button = LanguageSwitchButton(self.controller.language_manager, menu=self.language_menu)
        self.language_button = button
        self.add_cleanup(self._release_language_button)
        self.toolbar.addWidget(button)

    def _release_locator_search(self) -> None:
        search, self.locator_search = self.locator_search, None
        if search is not None:
            search.cleanup()

    def _release_language_button(self) -> None:
        button, self.language_button = self.language_button, None
        if button is not None:
            button.cleanup()

    def _connect_controller(self) -> None:
        """Keep the checkable actions in sync with the controller, the map and the panel."""
        controller = self.controller
        self.connect_signal(controller.listeningChanged, self._sync_listen_action)
        self.connect_signal(controller.pointOnMapChanged, self._sync_point_action)
        self.connect_signal(controller.azimuthal.enabledChanged, self._sync_azimuthal_action)
        self.connect_signal(controller.dock.visibilityChanged, self._sync_panel_action)

    def _release_controller(self) -> None:
        controller, self.controller = self.controller, None
        if controller is None:
            return
        controller.cleanup()
        with contextlib.suppress(RuntimeError):  # deleted with the main window already
            controller.deleteLater()

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
        # Always set the tooltip: at startup QGIS's shortcuts manager turns the tooltip
        # of every main window action into fixed text ("<b>Log QSO</b>"). An empty one
        # makes Qt show the action's text again, in the current language.
        entry.action.setToolTip(tr(entry.tooltip) if entry.tooltip else "")

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
        self.panel_action = self.import_action = self.listen_action = None
        self.log_qso_action = self.point_action = self.azimuthal_action = None
        self.grid_action = self.locator_action = self.recalculate_action = None
        self.cty_action = self.settings_action = self.about_action = None

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

    @_guarded
    def _on_language_changed(self, language: str) -> None:
        self.retranslate_ui()
        if self.provider is not None:
            self.provider.refreshAlgorithms()

    # ------------------------------------------------------------------
    # Action callbacks and synchronisation
    # ------------------------------------------------------------------

    @_guarded
    def _on_panel_triggered(self, checked: bool = False) -> None:
        dock = self.controller.dock
        dock.setVisible(bool(checked))
        if checked:
            dock.raise_()
        self._sync_panel_action()

    @_guarded
    def _sync_panel_action(self, _visible: bool = False) -> None:
        # not isHidden(): a panel behind another tab is still open (visibilityChanged(False))
        if self.panel_action is not None and self.controller is not None:
            self.panel_action.setChecked(not self.controller.dock.isHidden())

    @_guarded
    def _on_import_triggered(self, _checked: bool = False) -> None:
        self.controller.import_adif()

    @_guarded
    def _on_listen_triggered(self, checked: bool = False) -> None:
        self.controller.set_listening(bool(checked))

    @_guarded
    def _sync_listen_action(self, listening: bool) -> None:
        if self.listen_action is not None:
            self.listen_action.setChecked(bool(listening))

    @_guarded
    def _on_log_qso_triggered(self, _checked: bool = False) -> None:
        self.controller.log_qso()

    @_guarded
    def _on_point_triggered(self, checked: bool = False) -> None:
        self.controller.set_point_on_map(bool(checked))

    @_guarded
    def _sync_point_action(self, active: bool) -> None:
        if self.point_action is not None:
            self.point_action.setChecked(bool(active))

    @_guarded
    def _on_azimuthal_triggered(self, checked: bool = False) -> None:
        self.controller.azimuthal.set_enabled(bool(checked))

    @_guarded
    def _sync_azimuthal_action(self, enabled: bool) -> None:
        if self.azimuthal_action is not None:
            self.azimuthal_action.setChecked(bool(enabled))

    @_guarded
    def _on_grid_triggered(self, _checked: bool = False) -> None:
        from .controller import ALG_MAIDENHEAD_GRID

        self.controller.open_algorithm_dialog(ALG_MAIDENHEAD_GRID)

    @_guarded
    def _on_locator_triggered(self, _checked: bool = False) -> None:
        from .controller import ALG_LOCATOR_TO_POINT

        self.controller.open_algorithm_dialog(ALG_LOCATOR_TO_POINT)

    @_guarded
    def _on_recalculate_triggered(self, _checked: bool = False) -> None:
        from .controller import ALG_RECALCULATE

        self.controller.open_algorithm_dialog(ALG_RECALCULATE)

    @_guarded
    def _on_cty_triggered(self, _checked: bool = False) -> None:
        self.controller.download_cty()

    @_guarded
    def _on_settings_triggered(self, _checked: bool = False) -> None:
        self.controller.show_settings()

    @_guarded
    def _on_project_read(self, *_args: Any) -> None:
        from .qgis_io import layers

        layers.retranslate_layers()  # a project saved in another language

    # ------------------------------------------------------------------
    # About
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

    @_guarded
    def show_about(self, _checked: bool = False) -> None:
        """Show the About box."""
        QMessageBox.about(self.iface.mainWindow(), self.tr("About HamQ"), self.about_text())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log(message: str, level: Any) -> None:
        _log(message, level)
