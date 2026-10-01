#!/usr/bin/env python3
"""Generate ``hamq/resources/styles/<kind>.qml`` from the styles HamQ builds in code.

``hamq.qgis_io.styles.apply_default_style`` loads these files when they exist. They are
made from ``styles.build_default_style`` so that file and code always agree, and they
must be generated on the OLDEST supported QGIS (3.34): QGIS reads styles written by
older versions, not always those of newer ones. Run it in the QGIS 3.34 Docker image as
your own user, so the files are not owned by root::

    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \\
        -v "$PWD":/app:ro -v "$PWD/hamq/resources/styles":/out -w /app \\
        camptocamp/qgis-server:3.34 python3 scripts/make_styles.py --output /out

Only symbology and labels are written. The font family of the labels is removed from the
file, so every installation uses its own default font instead of looking for the font of
the machine that generated the file. The output is reproducible: Qt's hash seed is fixed
(attribute order) and the random UUIDs QGIS gives symbol layers and categories are
replaced by UUIDs derived from the kind and their position. ``--check`` compares the
generated files with those in the output folder and exits with status 1 when they differ
(the QGIS version aside).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT = os.path.join(ROOT, "hamq", "resources", "styles")

# Memory layer URI of a template layer per style kind: geometry and the fields a style uses.
TEMPLATES = {
    "qso": "Point?crs=EPSG:4326&field=band:string",
    "qso_path": "MultiLineString?crs=EPSG:4326&field=band:string",
    "grid": "Polygon?crs=EPSG:4326&field=locator:string",
}
_FONT_ATTRIBUTES = re.compile(r'\s(?:fontFamily|namedStyle)="[^"]*"')
_VERSION = re.compile(r'\sversion="[^"]*"')
_UUID = re.compile(
    r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}"
)
_UUID_NAMESPACE = uuid.UUID("9b0f5a52-6f3c-4c55-9a8e-3f2d1c7b4a10")  # HamQ styles


def _normalized(kind: str, text: str) -> str:
    """Drop font names and replace random UUIDs by stable ones (see the module docstring)."""
    text = _FONT_ATTRIBUTES.sub("", text)
    numbers: dict[str, str] = {}

    def stable(match: re.Match[str]) -> str:
        found = match.group(0).lower()
        if found not in numbers:
            numbers[found] = "{" + str(uuid.uuid5(_UUID_NAMESPACE, f"{kind}:{len(numbers)}")) + "}"
        return numbers[found]

    return _UUID.sub(stable, text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="folder for the .qml files")
    parser.add_argument(
        "--check", action="store_true", help="only compare with the files in --output"
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_HASH_SEED"] = "0"  # read by Qt at start-up: stable XML attribute order
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from qgis.core import Qgis, QgsApplication, QgsVectorLayer

    app = QgsApplication([], False)
    with contextlib.redirect_stdout(io.StringIO()):
        app.initQgis()
    from hamq.core.i18n import LANG_EN, set_language
    from hamq.qgis_io import styles

    set_language(LANG_EN)  # the legend label is translated again when a style is applied
    print(f"QGIS {Qgis.version()}")
    changed = []
    with tempfile.TemporaryDirectory() as scratch:
        for kind, uri in TEMPLATES.items():
            layer = QgsVectorLayer(uri, kind, "memory")
            if not layer.isValid():
                print(f"{kind}: template layer is not valid", file=sys.stderr)
                return 2
            styles.build_default_style(layer, kind)
            raw = os.path.join(scratch, f"{kind}.qml")
            message, ok = layer.saveNamedStyle(raw, categories=styles.style_categories())
            if not ok:
                print(f"{kind}: {message}", file=sys.stderr)
                return 2
            with open(raw, encoding="utf-8") as handle:
                text = _normalized(kind, handle.read())
            target = os.path.join(args.output, f"{kind}.qml")
            if args.check:
                try:
                    with open(target, encoding="utf-8") as handle:
                        current = handle.read()
                except OSError:
                    current = ""
                if _VERSION.sub("", current) != _VERSION.sub("", text):
                    changed.append(target)
                continue
            os.makedirs(args.output, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            print(f"wrote {target}")
    if changed:
        print("out of date: " + ", ".join(changed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)  # QGIS may crash in its destructors at interpreter exit
