#!/usr/bin/env python3
"""Regenerate the README screenshots in ``docs/images`` with the real QGIS desktop and HamQ.

Usage::

    python3 scripts/make_screenshots.py [--qgis qgis] [--output docs/images] [--keep]

The script starts the QGIS desktop application without a display
(``QT_QPA_PLATFORM=offscreen``) with a throw-away profile in which HamQ (this checkout,
linked into the profile) is enabled, and runs itself inside QGIS (``qgis --code``). There
it imports the demo log ``docs/demo/demo_log.adi`` (``scripts/make_demo_log.py``) with
the plugin's *Import ADIF* algorithm onto QGIS's bundled world map and takes these
pictures of the real windows:

================================  ==========================================================
``qso-map.png``                   QGIS with the QSO paths by band, the band legend and the
                                  HamQ panel (English)
``settings-en.png``               the Station and the Radio and rotator tabs of the settings
                                  dialog (English)
``panel-statistics-sr-latn.png``  the Statistics tab of the panel (Srpski, latinica)
``panel-live-sr-cyrl.png``        the WSJT-X and Radio tabs (Српски, ћирилица)
``azimuthal-map.png``             the azimuthal equidistant map centred on KN04ft
================================  ==========================================================

Nothing outside a temporary folder is touched (not the user's QGIS profile either), and
HamQ needs no network for this: cty.dat is the test excerpt of ``tests/fixtures/cty`` (the
demo log carries its own DXCC data), and QGIS's own check for plugin updates is off. The
live parts are real traffic on 127.0.0.1: WSJT-X datagrams made with ``hamq.core.wsjtx``
go to the plugin's UDP listener, and two small stand-ins for Hamlib's ``rigctld`` /
``rotctld`` (replies as from the dummy devices, ``-m 1``) answer the plugin's radio and
rotator clients. The PNG files are reduced to 256 colours when
Pillow is installed, otherwise copied as QGIS wrote them; each must stay below
:data:`MAX_BYTES`. Exit status 0 when every picture was made and HamQ logged no error, 1
otherwise. ``--keep`` leaves the temporary folder (profile, GeoPackage, the PNG files as
QGIS wrote them, ``report.json``, ``qgis.log``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

STAGE_ENV = "HAMQ_SCREENSHOTS_STAGE"  # "qgis" while this file runs inside QGIS
MAX_BYTES = 300 * 1024
MY_CALL, MY_GRID = "YU1ZZZ", "KN04ft"
#: (file name, description) of every picture.
IMAGES = (
    ("qso-map.png", "QSO map, English"),
    ("settings-en.png", "settings dialog, English"),
    ("panel-statistics-sr-latn.png", "Statistics tab, Serbian Latin"),
    ("panel-live-sr-cyrl.png", "WSJT-X and Radio tabs, Serbian Cyrillic"),
    ("azimuthal-map.png", "azimuthal map"),
)


# --------------------------------------------------------------------------- host side


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _qgis_major(executable: str) -> int:
    """Major version of the QGIS executable (4 when it cannot be told)."""
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        output = subprocess.run(
            [executable, "--version"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 4
    match = re.search(r"QGIS (\d+)\.", output)
    return int(match.group(1)) if match else 4


def _write_profile(profiles: str, root: str, major: int) -> None:
    """A QGIS profile with HamQ (linked) enabled, its settings and the cty.dat excerpt."""
    profile = os.path.join(profiles, "profiles", "default")
    plugins = os.path.join(profile, "python", "plugins")
    os.makedirs(plugins)
    os.symlink(os.path.join(root, "hamq"), os.path.join(plugins, "hamq"))
    hamq_dir = os.path.join(profile, "hamq")  # settings.profile_dir()
    os.makedirs(hamq_dir)
    for suffix in ("dat", "csv"):
        shutil.copyfile(
            os.path.join(root, "tests", "fixtures", "cty", f"cty_excerpt.{suffix}"),
            os.path.join(hamq_dir, f"cty.{suffix}"),
        )
    settings = (
        "[PythonPlugins]\nhamq=true\n\n"
        "[plugin-manager]\nautomatically-check-for-updates=false\n\n"
        "[qgis]\nshowTips=false\n\n"
        "[hamq]\n"
        "language=en\n"
        f"my_call={MY_CALL}\n"
        f"my_grid={MY_GRID}\n"
        f"gpkg_path={os.path.join(hamq_dir, 'hamq.gpkg')}\n"  # the default path
        "wsjtx_addr=127.0.0.1\n"
        f"wsjtx_port={_free_port(socket.SOCK_DGRAM)}\n"
        "cty_downloaded=2026-09-15\n"
    )
    os.makedirs(os.path.join(profile, "QGIS"))
    with open(os.path.join(profile, "QGIS", f"QGIS{major}.ini"), "w", encoding="utf-8") as ini:
        ini.write(settings)


def _optimize(source: str, target: str) -> str:
    """Copy ``source`` to ``target``, with 256 colours when Pillow is available."""
    try:
        from PIL import Image
    except ImportError:
        shutil.copyfile(source, target)
        return "copied (Pillow is not installed)"
    with Image.open(source) as image:
        # the gap between two pictures put side by side is transparent
        transparent = image.mode == "RGBA" and image.getextrema()[3][0] < 255
        picture = image.convert("RGBA" if transparent else "RGB")
    # MEDIANCUT handles RGB only, FASTOCTREE also RGBA; LIBIMAGEQUANT (both) is optional
    methods = ("LIBIMAGEQUANT", "FASTOCTREE") if transparent else ("LIBIMAGEQUANT", "MEDIANCUT")
    reduced = None
    for name in methods:
        method = getattr(Image.Quantize, name)
        try:
            reduced = picture.quantize(256, method=method, dither=Image.Dither.NONE)
            break
        except (ValueError, OSError):  # Pillow built without libimagequant
            continue
    if reduced is None:
        shutil.copyfile(source, target)
        return "copied (Pillow cannot reduce the colours)"
    reduced.save(target, optimize=True)
    if os.path.getsize(target) >= os.path.getsize(source):
        shutil.copyfile(source, target)
        return "copied (256 colours were not smaller)"
    return f"256 colours ({name.lower()})"


def main(argv: list[str] | None = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--qgis", default="qgis", help="QGIS desktop executable (%(default)s)")
    parser.add_argument(
        "--output", default=os.path.join(root, "docs", "images"), help="folder for the PNG files"
    )
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder")
    parser.add_argument("--timeout", type=int, default=600, help="seconds QGIS may take")
    args = parser.parse_args(argv)

    demo = os.path.join(root, "docs", "demo", "demo_log.adi")
    if not os.path.isfile(demo):
        print(f"{demo} is missing: run scripts/make_demo_log.py first", file=sys.stderr)
        return 1
    executable = shutil.which(args.qgis)
    if executable is None:
        print(f"QGIS executable not found: {args.qgis}", file=sys.stderr)
        return 1

    major = _qgis_major(executable)
    work = tempfile.mkdtemp(prefix="hamq-screenshots-")
    # Named like the real profile folder, so the settings dialog shows a familiar path.
    profiles = os.path.join(work, ".local", "share", "QGIS", f"QGIS{major}")
    raw, scratch = os.path.join(work, "raw"), os.path.join(work, "tmp")
    os.makedirs(raw)
    os.makedirs(scratch)  # QGIS's own temporary files: removed with the folder
    _write_profile(profiles, root, major)
    report_path = os.path.join(work, "report.json")
    environment = dict(os.environ)
    environment.update(
        {
            STAGE_ENV: "qgis",
            "HAMQ_SCREENSHOTS_RAW": raw,
            "HAMQ_SCREENSHOTS_REPORT": report_path,
            "HAMQ_SCREENSHOTS_DEMO": demo,
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONDONTWRITEBYTECODE": "1",  # the plugin is this checkout: no __pycache__
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "TMPDIR": scratch,
            "TEMP": scratch,
            "TMP": scratch,
        }
    )
    command = [
        executable,
        "--profiles-path",
        profiles,
        "--nologo",
        "--noversioncheck",
        "--lang",
        "en_US",
        "--code",
        os.path.abspath(__file__),
    ]
    started = time.monotonic()
    log_path = os.path.join(work, "qgis.log")
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            status: int | str = subprocess.run(
                command,
                env=environment,
                cwd=work,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
    seconds = time.monotonic() - started
    try:
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        report = {"errors": [f"no report from QGIS (exit status {status})"]}

    print(f"QGIS {report.get('qgis', '?')} (Qt {report.get('qt', '?')}), {seconds:.0f} s")
    for step in report.get("steps", []):
        print(f"  {step}")
    for message in report.get("log", []):
        print(f"  HamQ log: {message}")
    problems = list(report.get("errors", []))
    if not report.get("ok"):
        problems.append("QGIS did not finish the screenshots")
    os.makedirs(args.output, exist_ok=True)
    for name, description in IMAGES:
        source = os.path.join(raw, name)
        if not os.path.isfile(source):
            problems.append(f"{name} ({description}) was not made")
            continue
        target = os.path.join(args.output, name)
        how = _optimize(source, target)
        size = os.path.getsize(target)
        width, height = report.get("images", {}).get(name, ["?", "?"])
        print(f"{target}: {width}x{height} px, {size / 1024:.0f} KB, {how}")
        if size >= MAX_BYTES:
            problems.append(f"{name} has {size} bytes, the limit is {MAX_BYTES}")
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if problems or args.keep:
        print(f"temporary files kept in {work} (QGIS output: {log_path})")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if problems else 0


# --------------------------------------------------------------------------- inside QGIS


def _in_qgis() -> None:
    """Take the screenshots; runs inside the QGIS desktop application (``--code``)."""
    import socketserver
    import threading
    import traceback

    import qgis.utils
    from qgis.core import (
        Qgis,
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsFillSymbol,
        QgsMapRendererParallelJob,
        QgsMapSettings,
        QgsProject,
        QgsRectangle,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QT_VERSION_STR, QCoreApplication, QSize, Qt
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    from qgis.PyQt.QtWidgets import (
        QDockWidget,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QToolBar,
        QWidget,
    )

    raw = os.environ["HAMQ_SCREENSHOTS_RAW"]
    report: dict = {
        "ok": False,
        "qgis": Qgis.version(),
        "qt": QT_VERSION_STR,
        "steps": [],
        "images": {},
        "log": [],
        "errors": [],
    }

    def step(text: str) -> None:
        report["steps"].append(text)

    def pump(seconds: float = 0.0) -> None:
        deadline = time.monotonic() + seconds
        while True:
            QCoreApplication.processEvents()
            if time.monotonic() >= deadline:
                return
            time.sleep(0.01)

    def wait_until(predicate, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def save(image: QImage, name: str) -> None:
        if image.isNull() or not image.save(os.path.join(raw, name), "PNG"):
            raise RuntimeError(f"{name} could not be saved")
        report["images"][name] = [image.width(), image.height()]
        step(f"{name}: {image.width()}x{image.height()} px")

    def side_by_side(images: list[QImage], gap: int = 12) -> QImage:
        width = sum(image.width() for image in images) + gap * (len(images) - 1)
        height = max(image.height() for image in images)
        result = QImage(width, height, QImage.Format.Format_ARGB32)
        result.fill(QColor(0, 0, 0, 0))
        painter = QPainter(result)
        left = 0
        for image in images:
            painter.drawImage(left, 0, image)
            left += image.width() + gap
        painter.end()
        return result

    class Daemon(socketserver.ThreadingTCPServer):
        """A stand-in for ``rigctld`` / ``rotctld`` on 127.0.0.1, with the replies of the
        Hamlib dummy devices. The rotator turns only when ``state["azimuth"]`` is set."""

        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, kind: str) -> None:
            super().__init__(("127.0.0.1", 0), DaemonHandler)
            self.kind = kind
            self.state: dict = {"freq": 14074000, "mode": "PKTUSB", "passband": 3000}
            self.state.update({"azimuth": 60.0, "elevation": 0.0, "target": None})
            threading.Thread(target=self.serve_forever, daemon=True).start()

        def answer(self, line: str) -> str | None:
            state, parts = self.state, line.split()
            command = parts[0] if parts else ""
            if self.kind == "rig":
                if command == "+f":
                    return f"get_freq:\nFrequency: {state['freq']}\nRPRT 0\n"
                if command == "+m":
                    mode, passband = state["mode"], state["passband"]
                    return f"get_mode:\nMode: {mode}\nPassband: {passband}\nRPRT 0\n"
                if command == "+F" and len(parts) == 2:
                    state["freq"] = int(float(parts[1]))
                    return f"set_freq: {parts[1]}\nRPRT 0\n"
                if command == "+M" and len(parts) == 3:
                    state["mode"] = parts[1]
                    return f"set_mode: {parts[1]} {parts[2]}\nRPRT 0\n"
                return None
            if command == "+p":
                azimuth, elevation = state["azimuth"], state["elevation"]
                return f"get_pos:\nAzimuth: {azimuth:.2f}\nElevation: {elevation:.2f}\nRPRT 0\n"
            if command == "+P" and len(parts) == 3:
                state["target"] = float(parts[1])
                return f"set_pos: {parts[1]} {parts[2]}\nRPRT 0\n"
            if command == "+S":
                return "stop:\nRPRT 0\n"
            return None

        def stop(self) -> None:
            self.shutdown()
            self.server_close()

    class DaemonHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for line in self.rfile:
                reply = self.server.answer(line.decode("utf-8", "replace").strip())
                if reply:
                    self.wfile.write(reply.encode("utf-8"))
                    self.wfile.flush()

    daemons: list[Daemon] = []
    disconnect_log = None
    try:
        if not wait_until(lambda: "hamq" in qgis.utils.plugins, 30):
            raise RuntimeError("the HamQ plugin did not start")
        import hamq
        from hamq.core import geo, maidenhead, wsjtx
        from hamq.events import events
        from hamq.gui.dock import HamQDock
        from hamq.gui.settings_dialog import SettingsDialog
        from hamq.plugin import LOG_TAG, plugin_metadata
        from hamq.qgis_io import compat, layers

        def on_log(message: str, tag: str, level) -> None:
            if tag == LOG_TAG and level in (compat.MSG_WARNING, compat.MSG_CRITICAL):
                kind = "critical" if level == compat.MSG_CRITICAL else "warning"
                report["log"].append(f"{kind}: {message}")
                if level == compat.MSG_CRITICAL:
                    report["errors"].append(f"HamQ logged an error: {message}")

        disconnect_log = compat.connect_message_log(on_log)
        iface = qgis.utils.iface
        plugin = qgis.utils.plugins["hamq"]
        controller = plugin.controller
        settings, language, dock = controller.settings, controller.language_manager, controller.dock
        window, canvas, project = iface.mainWindow(), iface.mapCanvas(), QgsProject.instance()
        version = plugin_metadata().get("version", "?")
        step(f"HamQ {version} from {os.path.dirname(hamq.__file__)}")

        def render(timeout: float = 60.0) -> None:
            done: list[bool] = []

            def finished() -> None:
                done.append(True)

            canvas.mapCanvasRefreshed.connect(finished)
            try:
                canvas.refreshAllLayers()
                if not wait_until(lambda: bool(done) and not canvas.isDrawing(), timeout):
                    raise RuntimeError("the map canvas did not finish rendering")
            finally:
                canvas.mapCanvasRefreshed.disconnect(finished)
            pump(0.3)

        def grab(widget) -> QImage:
            iface.messageBar().clearWidgets()  # e.g. QGIS's own notices at start
            pump(0.3)
            return widget.grab().toImage()

        def fit_dock(page_name: str, width: int, start: int, most: int = 1200) -> None:
            """Make the floating dock tall enough to show the tab without a scroll bar."""
            page = dock.findChild(QWidget, page_name)
            area = page.parent()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parent()
            for height in range(start, most + 1, 10):
                dock.resize(QSize(width, height))
                pump(0.1)
                if area is None or not area.verticalScrollBar().isVisible():
                    return

        # --- the main window: the Layers panel, the map and the HamQ panel
        window.resize(QSize(1200, 700))
        keep_toolbars = {"mFileToolBar", "mMapNavToolBar", "mAttributesToolBar", "HamQToolbar"}
        for toolbar in window.findChildren(QToolBar):
            if toolbar.parent() is window and toolbar.objectName() not in keep_toolbars:
                toolbar.hide()
        for panel in window.findChildren(QDockWidget):
            if panel.objectName() not in ("Layers", dock.objectName()):
                panel.hide()
        dock.show()
        dock.raise_()
        dock.show_tab(HamQDock.TAB_STATISTICS)
        pump(0.5)
        layers_panel = window.findChild(QDockWidget, "Layers")
        window.resizeDocks([layers_panel, dock], [180, 300], Qt.Orientation.Horizontal)

        # --- base map: QGIS's bundled world map (Natural Earth), oceans light blue
        data = os.path.join(QgsApplication.pkgDataPath(), "resources", "data", "world_map.gpkg")
        world = QgsVectorLayer(f"{data}|layername=countries", "World map", "ogr")
        if not world.isValid():
            raise RuntimeError(f"{data} cannot be read")
        fill = {"color": "#f3f0e7", "outline_color": "#b6ae9c", "outline_width": "0.15"}
        world.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(fill)))
        project.addMapLayer(world, False)
        project.layerTreeRoot().addLayer(world)
        ocean = QColor("#d7e7f3")
        project.setBackgroundColor(ocean)
        canvas.setCanvasColor(ocean)

        # --- the demo log, imported with the plugin's algorithm (Plugins > HamQ > Import ADIF)
        import processing

        result = processing.run(
            "hamq:import_adif",
            {"INPUT": os.environ["HAMQ_SCREENSHOTS_DEMO"], "GPKG": settings.gpkg_path},
        )
        imported = result["IMPORTED"]
        duplicates, skipped = result["DUPLICATES"], result["SKIPPED"]
        step(f"import: {imported} imported, {duplicates} duplicates, {skipped} skipped")
        if not wait_until(
            lambda: (
                controller.stats is not None
                and controller.stats.total == imported
                and not controller.is_refreshing()
            ),
            30,
        ):
            raise RuntimeError("the statistics were not refreshed")
        stats = controller.stats
        step(f"statistics: {stats.total} QSOs, {stats.dxcc_count} DXCC entities")
        qso_layer, path_layer = layers.find_layers(settings.gpkg_path)
        tree = project.layerTreeRoot()
        tree.findLayer(qso_layer.id()).setExpanded(False)
        tree.findLayer(path_layer.id()).setExpanded(True)

        # --- (a) the QSO map, Equal Earth projection
        world_crs = QgsCoordinateReferenceSystem("EPSG:8857")
        project.setCrs(world_crs)
        to_map = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"), world_crs, project
        )
        canvas.setExtent(to_map.transformBoundingBox(QgsRectangle(-168.0, -50.0, 180.0, 74.0)))
        render()
        save(grab(window), "qso-map.png")

        # --- (d) the settings dialog in English. The radio settings are stored without
        # events().settingsChanged, so nothing connects, and restored afterwards.
        settings.rig_enabled, settings.rot_enabled = True, True
        settings.rot_min_az, settings.rot_max_az = 0.0, 450.0
        dialog = SettingsDialog(settings, language, controller.cty_manager, window)
        dialog.show()
        pump(0.5)
        pictures = []
        for page in (dialog.station_page, dialog.radio_page):
            dialog.tabs.setCurrentWidget(page)
            pump(0.3)
            pictures.append(dialog.grab().toImage())
        dialog.reject()
        dialog.cleanup()
        dialog.deleteLater()
        settings.rig_enabled, settings.rot_enabled = False, False
        settings.rot_min_az, settings.rot_max_az = 0.0, 360.0
        save(side_by_side(pictures), "settings-en.png")

        # --- (b) the Statistics tab in Serbian, Latin script
        language.set_setting("sr_Latn")
        dock.setFloating(True)
        dock.show()
        dock.show_tab(HamQDock.TAB_STATISTICS)
        fit_dock("HamQStatsPage", 350, 900)
        save(grab(dock), "panel-statistics-sr-latn.png")

        # --- (c) live, in Serbian, Cyrillic script: WSJT-X over UDP, Hamlib over TCP
        language.set_setting("sr_Cyrl")
        if not controller.set_listening(True):
            raise RuntimeError("the WSJT-X listener did not start")
        adif = (
            "<adif_ver:5>3.1.0\n<programid:6>WSJT-X\n<EOH>\n"
            "<call:6>VK3FTZ <gridsquare:4>QF22 <mode:3>FT8 <rst_sent:3>-14 <rst_rcvd:3>-17 "
            "<qso_date:8>20260930 <time_on:6>064215 <qso_date_off:8>20260930 "
            "<time_off:6>064330 <band:3>20m <freq:9>14.075580 "
            f"<station_callsign:6>{MY_CALL} <my_gridsquare:6>{MY_GRID} <tx_pwr:3>100 <EOR>"
        )
        packets = (
            wsjtx.encode_heartbeat("WSJT-X", 3, "2.7.0", "", schema=3),
            wsjtx.encode_status(
                "WSJT-X",
                14074000,
                "FT8",
                "VK3FTZ",
                schema=3,
                report="-14",
                tx_mode="FT8",
                de_call=MY_CALL,
                de_grid=MY_GRID,
                dx_grid="QF22",
            ),
            wsjtx.encode_logged_adif("WSJT-X", adif, schema=3),
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for packet in packets:
                sender.sendto(packet, ("127.0.0.1", settings.wsjtx_port))
                pump(0.2)
        if not wait_until(lambda: controller.stats.total == imported + 1):
            raise RuntimeError("the QSO from WSJT-X did not arrive")
        step("WSJT-X: heartbeat, status and a logged QSO received over UDP")

        rig, rotator = Daemon("rig"), Daemon("rot")
        daemons += [rig, rotator]
        settings.rig_enabled, settings.rot_enabled = True, True
        settings.rig_port, settings.rot_port = rig.server_address[1], rotator.server_address[1]
        events().settingsChanged.emit()  # as when the settings dialog is saved
        if not wait_until(
            lambda: controller.rig.is_connected() and controller.rotator.is_connected()
        ):
            raise RuntimeError("the Hamlib clients did not connect")
        # Radio tab: azimuth toward the WSJT-X QSO (QF22), then Turn
        bearing = geo.bearing_deg(*maidenhead.to_latlon(MY_GRID), *maidenhead.to_latlon("QF22"))
        dock.show_tab(HamQDock.TAB_RADIO)
        dock.findChild(QSpinBox, "HamQRotatorTargetSpin").setValue(round(bearing))
        dock.findChild(QPushButton, "HamQRotatorTurnButton").click()
        if not wait_until(lambda: rotator.state["target"] is not None):
            raise RuntimeError("the rotator was not turned")
        target = rotator.state["target"]
        rotator.state["azimuth"] = target - 22.0  # still on its way
        if not wait_until(
            lambda: (
                controller.rotator.position() is not None
                and abs(controller.rotator.position()[0] - rotator.state["azimuth"]) < 0.5
            )
        ):
            raise RuntimeError("the rotator position did not arrive")
        pump(1.2)
        step(f"Hamlib: radio and rotator connected, rotator turning to {target:.0f} degrees")
        fit_dock("HamQRadioTab", 350, 500)
        pictures = []
        for tab in (HamQDock.TAB_WSJTX, HamQDock.TAB_RADIO):
            dock.show_tab(tab)
            pictures.append(grab(dock))
        save(side_by_side(pictures), "panel-live-sr-cyrl.png")

        # --- (e) the azimuthal map (Plugins > HamQ > Azimuthal map), rendered square
        language.set_setting("en")
        dock.setFloating(False)
        plugin.azimuthal_action.trigger()
        if not controller.azimuthal.is_enabled():
            raise RuntimeError("the azimuthal map was not switched on")
        render()
        map_settings = QgsMapSettings(canvas.mapSettings())
        map_settings.setOutputSize(QSize(880, 880))
        radius = 20000.0 * 1.04  # km: the whole world and the 20 000 km ring
        map_settings.setExtent(QgsRectangle(-radius, -radius, radius, radius))
        map_settings.setBackgroundColor(ocean)
        job = QgsMapRendererParallelJob(map_settings)
        job.start()
        job.waitForFinished()
        save(job.renderedImage(), "azimuthal-map.png")
        report["ok"] = True
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        if disconnect_log is not None:
            disconnect_log()
        for daemon in daemons:
            daemon.stop()
        with open(os.environ["HAMQ_SCREENSHOTS_REPORT"], "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)  # QGIS can crash in its destructors; the report tells how it went


if os.environ.get(STAGE_ENV) == "qgis":
    _in_qgis()
elif __name__ == "__main__":
    sys.exit(main())
