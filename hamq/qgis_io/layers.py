"""The QSO log in the QGIS project: find, load, refresh and translate its layers.

:func:`load_layers` adds the ``qso`` and ``qso_path`` layers of a GeoPackage to a "HamQ"
group at the top of the layer tree (QSOs above the paths), named in the interface language
("QSOs", "QSO paths"), with the default style and translated field aliases.
:func:`find_layers` finds layers of the file by their source (a layer added by hand counts
too), :func:`refresh_layers` makes them show what the file holds now.

Layers HamQ loaded remember, in custom properties, the name and aliases HamQ gave them.
:func:`retranslate_layers` (after a language change) translates those again, unless the
user has changed them since. The plugin wires the process-wide signals with
:func:`connect_events`::

    plugin.add_cleanup(layers.connect_events())   # dataChanged -> refresh_layers,
                                                  # languageChanged -> retranslate_layers

Everything here works on ``QgsProject.instance()`` and must run in the main thread.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable

from qgis.core import QgsMessageLog, QgsProject, QgsProviderRegistry, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, QThread

from ..core.i18n import tr, tr_noop
from . import compat
from .gpkg import PATH_LAYER, QSO_LAYER, GpkgError, ensure_gpkg, layer_uri
from .styles import apply_default_style, retranslate_style

__all__ = [
    "FIELD_ALIASES",
    "GROUP_NAME",
    "LAYER_NAMES",
    "apply_field_aliases",
    "connect_events",
    "find_layers",
    "load_layers",
    "matching_layers",
    "refresh_layers",
    "retranslate_layers",
]

#: Layer tree group of the HamQ layers (a product name, not translated).
GROUP_NAME = "HamQ"
#: Name of each layer in the project, marked for translation.
LAYER_NAMES = {QSO_LAYER: tr_noop("QSOs"), PATH_LAYER: tr_noop("QSO paths")}
#: Field alias of each column of the ``qso`` and ``qso_path`` layers, marked for translation.
FIELD_ALIASES = {
    "call": tr_noop("Callsign"),
    "qso_datetime": tr_noop("Date and time (UTC)"),
    "band": tr_noop("Band"),
    "mode": tr_noop("Mode"),
    "submode": tr_noop("Submode"),
    "freq_mhz": tr_noop("Frequency (MHz)"),
    "rst_sent": tr_noop("RST sent"),
    "rst_rcvd": tr_noop("RST received"),
    "gridsquare": tr_noop("Locator"),
    "my_gridsquare": tr_noop("My locator"),
    "dxcc": tr_noop("DXCC"),
    "country": tr_noop("Country"),
    "cont": tr_noop("Continent"),
    "cq_zone": tr_noop("CQ zone"),
    "itu_zone": tr_noop("ITU zone"),
    "distance_km": tr_noop("Distance (km)"),
    "bearing_deg": tr_noop("Bearing (°)"),
    "loc_source": tr_noop("Position source"),
    "source": tr_noop("Source"),
    "dedup_key": tr_noop("Duplicate key"),
    "adif_extra": tr_noop("Other ADIF fields"),
    "qso_fid": tr_noop("QSO ID"),
}

_KINDS = (QSO_LAYER, PATH_LAYER)
_STYLE_OF_KIND = {QSO_LAYER: "qso", PATH_LAYER: "qso_path"}
# Custom layer properties (saved with the project).
_KIND_PROPERTY = "hamq/kind"  # "qso" | "qso_path": a layer HamQ loaded
_NAME_PROPERTY = "hamq/name"  # the (translated) name HamQ gave the layer
_ALIASES_PROPERTY = "hamq/aliases"  # JSON {field: alias} of the aliases HamQ set
_LOG_TAG = "HamQ"


def find_layers(path: str) -> tuple[QgsVectorLayer | None, QgsVectorLayer | None]:
    """The ``(qso, qso_path)`` layers of the GeoPackage ``path`` in the project.

    Layers are matched by their data source (OGR provider, same file, layer name ``qso`` /
    ``qso_path``), so layers the user added by hand count too. When a layer is in the
    project more than once, the one in the "HamQ" group wins, then one without a filter
    (subset string), then the one highest in the layer tree. Invalid layers (a file that
    went away) are skipped. ``None`` when missing.
    """
    found: dict[str, QgsVectorLayer] = {}
    for layer in _ordered_layers(_layers_of(path)):
        kind = _kind_of(layer)
        if kind is not None and kind not in found and layer.isValid():
            found[kind] = layer
    return found.get(QSO_LAYER), found.get(PATH_LAYER)


def matching_layers(path: str) -> list[QgsVectorLayer]:
    """Every ``qso`` and ``qso_path`` layer of the GeoPackage ``path`` in the project."""
    return _layers_of(path)


def load_layers(path: str) -> tuple[QgsVectorLayer, QgsVectorLayer]:
    """Add the QSO and path layers of ``path`` to the project; returns ``(qso, qso_path)``.

    Creates the GeoPackage when it does not exist yet (``gpkg.ensure_gpkg``). Layers that
    are already in the project (:func:`find_layers`) are returned as they are, so calling
    this twice adds nothing. New layers go into the "HamQ" group at the top of the layer
    tree (created when missing): QSOs first, then the paths. They get their translated
    name, the default style and translated field aliases. Raises ``gpkg.GpkgError`` when
    the file cannot be created or a layer cannot be loaded. Main thread only.
    """
    ensure_gpkg(path)
    project = QgsProject.instance()
    qso_layer, path_layer = find_layers(path)
    # Both new layers are made first, so a failure adds nothing to the project.
    new_qso = _new_layer(path, QSO_LAYER) if qso_layer is None else None
    new_path = _new_layer(path, PATH_LAYER) if path_layer is None else None
    if new_qso is None and new_path is None:
        return qso_layer, path_layer
    root = project.layerTreeRoot()
    group = root.findGroup(GROUP_NAME)
    if group is None:
        group = root.insertGroup(0, GROUP_NAME)
    if new_path is not None:
        project.addMapLayer(new_path, False)
        group.addLayer(new_path)
        path_layer = new_path
    if new_qso is not None:
        project.addMapLayer(new_qso, False)
        group.insertLayer(0, new_qso)
        qso_layer = new_qso
    return qso_layer, path_layer


def refresh_layers(path: str) -> None:
    """Make the project layers of ``path`` show what the GeoPackage holds now.

    Every valid ``qso`` / ``qso_path`` layer of the file reloads its data, updates its
    extent and repaints. Layers in edit mode are left alone, so an edit session is never
    disturbed; they show the new data once the edits are saved or discarded. Does nothing
    outside the main thread (project layers belong to it).
    """
    if not _in_main_thread():
        return
    for layer in _layers_of(path):
        if not layer.isValid() or layer.isEditable():
            continue
        layer.reload()
        layer.updateExtents()
        layer.triggerRepaint()


def apply_field_aliases(layer: QgsVectorLayer) -> None:
    """Set the translated aliases of :data:`FIELD_ALIASES` on the fields of ``layer``.

    Fields without an entry are not touched, and neither is an alias the user set: an
    alias is replaced only when it is empty or still the one HamQ set last time (kept in
    a custom layer property). Call it again after a language change
    (:func:`retranslate_layers` does).
    """
    fields = layer.fields()
    previous = _json_property(layer, _ALIASES_PROPERTY)
    applied: dict[str, str] = {}
    for index in range(fields.count()):
        name = fields.at(index).name()
        source = FIELD_ALIASES.get(name.lower())
        if source is None:
            continue
        current = layer.attributeAlias(index) or ""
        if current and current != previous.get(name):
            continue  # set by the user
        alias = tr(source)
        if alias != current:
            layer.setFieldAlias(index, alias)
        applied[name] = alias
    layer.setCustomProperty(
        _ALIASES_PROPERTY, json.dumps(applied, ensure_ascii=False, sort_keys=True)
    )


def retranslate_layers() -> None:
    """Translate the names, field aliases and legend labels HamQ gave project layers.

    For every layer HamQ loaded (or set aliases on): the name is translated again unless
    the user renamed the layer, aliases go through :func:`apply_field_aliases` and the
    "Other bands" legend label through ``styles.retranslate_style``. Main thread only.
    """
    if not _in_main_thread():
        return
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        kind = layer.customProperty(_KIND_PROPERTY)
        if kind in LAYER_NAMES:
            given = layer.customProperty(_NAME_PROPERTY)
            if isinstance(given, str) and layer.name() == given:
                name = tr(LAYER_NAMES[kind])
                if name != given:
                    layer.setName(name)
                    layer.setCustomProperty(_NAME_PROPERTY, name)
        if layer.customProperty(_ALIASES_PROPERTY) is not None:
            apply_field_aliases(layer)
        retranslate_style(layer)


def connect_events() -> Callable[[], None]:
    """Keep the project layers up to date: ``events().dataChanged(path)`` refreshes the
    layers of ``path`` and ``events().languageChanged`` retranslates them.

    Returns a function that disconnects both again; it is safe to call more than once.
    Register it with ``plugin.add_cleanup(layers.connect_events())``.
    """
    from ..events import events

    hub = events()
    connections = [
        (hub.dataChanged, _on_data_changed),
        (hub.languageChanged, _on_language_changed),
    ]
    for signal, slot in connections:
        signal.connect(slot)

    def disconnect() -> None:
        while connections:
            signal, slot = connections.pop()
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)

    return disconnect


# --- slots -------------------------------------------------------------------------------------


def _on_data_changed(path: str) -> None:
    try:
        refresh_layers(path)
    except Exception as exc:  # a slot must never let an exception reach Qt
        _log(tr("The HamQ layers could not be refreshed: {error}").format(error=exc))


def _on_language_changed(language: str) -> None:
    try:
        retranslate_layers()
    except Exception as exc:  # a slot must never let an exception reach Qt
        _log(tr("The HamQ layers could not be translated: {error}").format(error=exc))


# --- helpers -----------------------------------------------------------------------------------


def _new_layer(path: str, kind: str) -> QgsVectorLayer:
    """A styled layer of ``path``. A default style the user saved in the GeoPackage (QGIS
    ``layer_styles`` table) wins over the HamQ default style."""
    name = tr(LAYER_NAMES[kind])
    options = QgsVectorLayer.LayerOptions()
    options.loadDefaultStyle = False
    layer = QgsVectorLayer(layer_uri(path, kind), name, "ogr", options)
    if not layer.isValid():
        raise GpkgError(
            tr("Layer {layer} of {path} could not be loaded").format(layer=kind, path=path)
        )
    layer.setCustomProperty(_KIND_PROPERTY, kind)
    layer.setCustomProperty(_NAME_PROPERTY, name)
    _message, has_own_style = layer.loadDefaultStyle()
    if not has_own_style:
        apply_default_style(layer, _STYLE_OF_KIND[kind])
    apply_field_aliases(layer)
    if kind == QSO_LAYER:
        layer.setDisplayExpression('"call"')
    return layer


def _layers_of(path: str) -> list[QgsVectorLayer]:
    """Project layers whose source is the ``qso`` or ``qso_path`` layer of ``path``."""
    target = _file_key(path)
    result = []
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "ogr":
            continue
        source = _decode_source(layer.source())
        if source is None:
            continue
        file_path, layer_name = source
        if layer_name not in _KINDS:
            continue
        if _file_key(file_path) == target or _same_file(file_path, path):
            result.append(layer)
    return result


def _ordered_layers(layers: list[QgsVectorLayer]) -> list[QgsVectorLayer]:
    """``layers`` with those in the HamQ group first, then unfiltered ones, then in layer
    tree order."""
    root = QgsProject.instance().layerTreeRoot()
    position: dict[str, int] = {}
    in_group: set[str] = set()
    for rank, node in enumerate(root.findLayers()):
        layer_id = node.layerId()
        position.setdefault(layer_id, rank)
        parent = node.parent()
        if parent is not None and parent.name() == GROUP_NAME:  # the root has no name
            in_group.add(layer_id)
    unknown = len(position)
    return sorted(
        layers,
        key=lambda layer: (
            layer.id() not in in_group,
            bool(layer.subsetString()),
            position.get(layer.id(), unknown),
        ),
    )


def _kind_of(layer: QgsVectorLayer) -> str | None:
    source = _decode_source(layer.source())
    return None if source is None else source[1]


def _decode_source(source: str) -> tuple[str, str] | None:
    """``(file path, lower-case layer name)`` of an OGR source; ``None`` without a name."""
    parts = QgsProviderRegistry.instance().decodeUri("ogr", source)
    file_path = parts.get("path")
    layer_name = parts.get("layerName")
    if not isinstance(file_path, str) or not file_path or not isinstance(layer_name, str):
        return None
    return file_path, layer_name.lower()


def _file_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _same_file(first: str, second: str) -> bool:
    """Same file on disk (also on case-insensitive file systems); False when one is missing."""
    try:
        return os.path.samefile(first, second)
    except (OSError, ValueError):
        return False


def _json_property(layer: QgsVectorLayer, key: str) -> dict[str, str]:
    text = layer.customProperty(key)
    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def _in_main_thread() -> bool:
    app = QCoreApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


def _log(message: str) -> None:
    QgsMessageLog.logMessage(message, _LOG_TAG, compat.MSG_WARNING)
