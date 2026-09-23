#!/usr/bin/env python3
"""Where a "floating cantilever" warning comes from, and whether it matters.

Bambu Studio, with supports off, warns that an object "has floating
cantilever" and names the object, not the place. This slices every part on
the project's own layer grid (first layer, then layer height, from its
settings) and reports what has nothing under it (`x2d/overhang.py`):

- floating islands: start in mid-air. These fail without support.
- bridges: held on two or more sides; PLA spans short ones.
- cantilevers: held on one side; `reach_mm` is how far they stick out.

    uv run scripts/overhangs.py catbus.3mf
    uv run scripts/overhangs.py catbus.3mf --angle 30 --top 20

A reading, not a slice: the slicer's own support preview is the final word.
"""
import argparse
import json
import zipfile
from pathlib import Path

from x2d.archive import world_parts
from x2d.machine import machine_for
from x2d.overhang import overhang_report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path, help="a .3mf project")
    ap.add_argument("--angle", type=float,
                    help="overhang threshold from horizontal, degrees (default: the file's support_threshold_angle)")
    ap.add_argument("--min-area", type=float, default=0.3, help="ignore overhangs smaller than this, mm^2")
    ap.add_argument("--top", type=int, default=12, help="how many of the worst to list")
    a = ap.parse_args()
    with zipfile.ZipFile(a.project) as zf:
        settings = (json.loads(zf.read("Metadata/project_settings.config"))
                    if "Metadata/project_settings.config" in zf.namelist() else {})
        parts = [(name, mesh) for name, _, mesh in world_parts(zf)]
    machine = machine_for(settings)
    angle = a.angle if a.angle is not None else settings.get("support_threshold_angle")
    if angle is None:
        ap.error("the file carries no support_threshold_angle; pass --angle")
    angle = float(angle)
    report = overhang_report(parts, machine, angle, min_area=a.min_area, top=a.top)
    print(json.dumps({"project": a.project.name, "machine": machine.name,
                      "layer_mm": [machine.first_layer_h, machine.layer_h],
                      "supports_enabled": settings.get("enable_support") == "1", **report}, indent=2))


if __name__ == "__main__":
    main()
