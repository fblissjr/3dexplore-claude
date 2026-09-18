#!/usr/bin/env python3
"""image -> two-color plaque -> Bambu-native .3mf

    python img2plate.py logo.png --width 70 --out badge.3mf

The backer takes one project filament slot, the raised artwork another. Which
NOZZLE serves each slot is the slicer's call, not ours -- see README.
"""
import argparse, json, sys
from pathlib import Path

from x2d import (Part, default_capture, machine_for, silhouette, detail, mask_to_polygons, extrude,
                 solid_backer, thicken, printability, fits, write_3mf,
                 project_settings, preview)
from x2d.report import plate_report


REPO = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default="plate.3mf")
    ap.add_argument("--width", type=float, default=70.0, help="artwork width, mm")
    ap.add_argument("--base", type=float, help="backer thickness, mm; default 1.6, snapped to your capture's layer grid")
    ap.add_argument("--relief", type=float, help="artwork height, mm; default 0.8, snapped to your capture's layer grid")
    ap.add_argument("--margin", type=float, default=3.0, help="backer border, mm")
    ap.add_argument("--simplify", type=float, default=0.12, help="contour tolerance, mm")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--artwork", choices=["silhouette", "detail"], default="silhouette",
                    help="silhouette: the outline is the raised artwork, on a grown "
                         "backer -- right for logos and line art. detail: the shape "
                         "is the backer and interior shading (creases, folds, pen "
                         "lines) is the raised artwork -- right for photographs of "
                         "objects, where a silhouette would give you a blob.")
    ap.add_argument("--feature", choices=["creases", "ridges"], default="creases",
                    help="--artwork detail: which side of the local average is the "
                         "artwork. creases (darker) for drawings and ink, where the "
                         "mark is the dark thing. ridges (lighter) for photographed "
                         "objects, where the part catching the light is the part "
                         "that physically stands proud.")
    ap.add_argument("--inset", type=float, default=None, metavar="FRAC",
                    help="--artwork detail: how much of the rim to exclude, as a "
                         "fraction of the figure. Defaults per --feature; raise it "
                         "if a bright arc traces the outline.")
    ap.add_argument("--depth", type=int, default=8, metavar="LEVELS",
                    help="--artwork detail: how much darker than its neighbourhood a "
                         "pixel must be to count. Lower finds more, and more noise.")
    ap.add_argument("--thicken", type=float, default=0.0, metavar="MM",
                    help="grow the artwork outward by this much. Detail from shading "
                         "is often thinner than the nozzle can print; half the minimum "
                         "printable feature (0.42 mm on a 0.4 nozzle) is the usual dose. "
                         "The report's min_printable_feature_mm is the number for "
                         "your printer.")
    ap.add_argument("--min-area", type=float, default=1.0, metavar="MM2",
                    help="drop artwork specks smaller than this")
    ap.add_argument("--filaments", type=int, nargs=2, default=[1, 2],
                    metavar=("BASE", "ARTWORK"),
                    help="project filament slots, in base-then-artwork order. "
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
    ap.add_argument("--stl", action="store_true", help="also write per-part STLs")
    a = ap.parse_args()
    a.settings = a.settings or default_capture(REPO)
    base_fil, art_fil = a.filaments

    settings = None
    if a.settings:
        pin = {int(k): int(v) for k, v in (p.split(":") for p in (a.pin or []))}
        settings = project_settings(a.settings, pin=pin or None,
                                    support_interface=a.support_interface,
                                    object_slot=base_fil)
    elif a.pin or a.support_interface:
        ap.error("--pin and --support-interface need --settings: "
                 "there is no config to patch")
    try:
        a.base, a.relief = machine_for(settings).band(a.base, a.relief, default=(1.6, 0.8))
    except ValueError as e:
        ap.error(str(e))

    shape = mask_to_polygons(silhouette(a.image, invert=a.invert),
                             width_mm=a.width, simplify_mm=a.simplify)
    if shape.is_empty:
        sys.exit("no shapes found -- try --invert or a higher-contrast image")

    if a.artwork == "detail":
        art = thicken(mask_to_polygons(detail(a.image, feature=a.feature, depth=a.depth, inset=a.inset),
                                       width_mm=a.width, simplify_mm=a.simplify,
                                       min_area_mm2=a.min_area), a.thicken)
        if art.is_empty:
            sys.exit("no interior detail found -- lower --depth, or use "
                     "--artwork silhouette if the shape is the point")
        base = solid_backer(shape, a.margin)      # the object itself, plus a rim
    else:
        art = shape
        base = solid_backer(art, a.margin)

    parts = [Part(extrude(base, a.base), "base", extruder=base_fil),
             Part(extrude(art, a.relief, z=a.base), "artwork", extruder=art_fil)]

    report = plate_report(parts, settings=settings,
                          plate=base, artwork={art_fil: art},
                          base_slot=base_fil, base=a.base, relief=a.relief,
                          extra={"artwork_from": a.artwork if a.artwork != "detail"
                                 else f"detail:{a.feature}"})
    write_3mf(parts, a.out, settings=settings, name=a.image.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    out = Path(a.out)                  # sidecars from the stem, never over the project
    if a.stl:
        for p in parts:
            p.mesh.export(out.with_name(f"{out.stem}_{p.name}.stl"))
    swatch = (settings or {}).get("filament_colour") or []
    hue = lambda slot: swatch[slot - 1] if 0 < slot <= len(swatch) else None
    colors = (hue(base_fil) or "#2b2b2b", hue(art_fil) or "#e6b422")
    preview(base, art, out.with_name(f"{out.stem}_preview.png"), colors=colors)
    report["colors"] = {"backer": colors[0], "artwork": colors[1]}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
