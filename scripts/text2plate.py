#!/usr/bin/env python3
"""Name plaque / keychain. Text in one color, plate in another.

    python text2plate.py ABC --width 90 --out abc.3mf
    python text2plate.py ABC --hole 5 --out abc_keychain.3mf
"""
import argparse, json
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import box

from x2d import (Part, default_capture, machine_for, mask_to_polygons, extrude, printability, fits,
                 write_3mf, project_settings, preview,
                 solid_backer, rounded_plate, render_text)
from x2d.fonts import bold_font
from x2d.report import plate_report

REPO = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--out", default="name.3mf")
    ap.add_argument("--width", type=float, default=90.0, help="letters' total width, mm")
    ap.add_argument("--base", type=float, help="mm; default 2, snapped to your capture's layer grid")
    ap.add_argument("--relief", type=float, help="mm; default 1, snapped to your capture's layer grid")
    ap.add_argument("--margin", type=float, default=7.0)
    ap.add_argument("--radius", type=float, default=6.0, help="corner radius, mm")
    ap.add_argument("--hole", type=float, default=0.0, help="keychain hole dia, mm")
    ap.add_argument("--shape", choices=["rect", "hug"], default="rect")
    ap.add_argument("--font", help="a bold TrueType font; default: the first one found on this system")
    ap.add_argument("--filaments", type=int, nargs=2, default=[1, 2],
                    metavar=("PLATE", "LETTERS"),
                    help="project filament slots, in plate-then-letters order. "
                         "These are slot numbers as Bambu Studio shows them, "
                         "not nozzle numbers -- the slicer maps slots to nozzles.")
    ap.add_argument("--settings", metavar="REF.3MF",
                    help="a project saved from Bambu Studio (File > Save Project as...) to lift Metadata/project_settings.config from, so the output opens already configured. Defaults to profiles/default.3mf when that exists.")
    ap.add_argument("--support-interface", type=int, default=None, metavar="SLOT",
                    help="print the support INTERFACE from this filament slot, and "
                         "close the interface gap to zero. Only safe across "
                         "dissimilar materials, which is the point -- they part "
                         "cleanly with no gap. The support body stays in the "
                         "object's own filament. Needs --settings.")
    ap.add_argument("--pin", metavar="SLOT:NOZZLE", nargs="*", default=None,
                    help="pin filament slots to nozzles, e.g. --pin 1:1 4:2. Needs "
                         "--settings. Without it the slicer groups filaments itself.")
    a = ap.parse_args()
    a.settings = a.settings or default_capture(REPO)
    plate_fil, letter_fil = a.filaments

    settings = None
    if a.settings:
        pin = {int(k): int(v) for k, v in (p.split(":") for p in (a.pin or []))}
        settings = project_settings(a.settings, pin=pin or None,
                                    support_interface=a.support_interface,
                                    object_slot=plate_fil)
    elif a.pin or a.support_interface:
        ap.error("--pin and --support-interface need --settings: "
                 "there is no config to patch")
    try:
        a.base, a.relief = machine_for(settings).band(a.base, a.relief, default=(2.0, 1.0))
    except ValueError as e:
        ap.error(str(e))

    # looked up only when not given, so --font works where no system font does
    img = np.array(render_text(a.text, a.font or bold_font(), cap_mm=20))
    _, mask = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY_INV)
    art = mask_to_polygons(mask, width_mm=a.width, simplify_mm=0.08)

    if a.shape == "rect":
        base = rounded_plate(art, a.margin, a.radius)
    else:
        base = solid_backer(art, a.margin)

    if a.hole:
        x0, y0, x1, y1 = base.bounds
        cx = x0 + a.margin / 2                       # centred in the margin, clear of the letters
        cy = (y0 + y1) / 2
        base = base.difference(
            box(cx - a.hole / 2, cy - a.hole / 2,
                cx + a.hole / 2, cy + a.hole / 2).centroid.buffer(a.hole / 2, quad_segs=24))
        art = art.difference(base.envelope.difference(base))   # keep letters clear

    parts = [Part(extrude(base, a.base), "plate", extruder=plate_fil),
             Part(extrude(art, a.relief, z=a.base), "letters", extruder=letter_fil)]
    write_3mf(parts, a.out, settings=settings, name=a.text)
    # Sidecars named from the stem, never by editing the path: --out without
    # a lowercase .3mf used to write them over the project itself.
    out = Path(a.out)
    for p in parts:
        p.mesh.export(out.with_name(f"{out.stem}_{p.name}.stl"))
    # The real filament colours, not preview()'s defaults. This is the only
    # picture anyone looks at before committing an hour of print time, and it
    # spent a while quietly showing gold-on-grey for a black-and-red plate.
    swatch = (settings or {}).get("filament_colour") or []
    hue = lambda slot: swatch[slot - 1] if 0 < slot <= len(swatch) else None
    colors = (hue(plate_fil) or "#2b2b2b", hue(letter_fil) or "#e6b422")
    preview(base, art, out.with_name(f"{out.stem}_preview.png"), colors=colors)

    report = plate_report(parts, settings=settings,
                          plate=base, artwork={letter_fil: art},
                          base_slot=plate_fil, base=a.base, relief=a.relief,
                          extra={"text": a.text})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
