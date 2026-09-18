#!/usr/bin/env python3
"""flat-color art (emoji, logo, icon) -> multicolor AMS plate -> Bambu-native .3mf

    python scripts/art2plate.py samples/shrug.svg --width 70 \\
        --map '#FFDC5D=2' '#FFAC33=3' '#FA743E=4' '#662113=1' '#C1694F=4' \\
        --priority 1 4 3 2 --out out/shrug.3mf

The color->slot table IS the design. Every opaque pixel joins its nearest
listed color, so a shading tint you leave out of the table follows its parent.

Shaded colour art (a render, a painting) goes through --shaded first: its
shades are grouped and matched to your spools, anything narrower than the
nozzle is removed in the raster, and <out>_flat.png shows the result. The
report prints the table it used; edit it and pass it back as --map.

    python scripts/art2plate.py render.png --width 60 --shaded --halo 3 \
        --base-slot 1 --out out/render.3mf
Each slot becomes one raised part; all of them share one shallow layer band on
a plate printed in `--base-slot`. Which NOZZLE serves each slot is the
slicer's call -- see CLAUDE.md, "a filament slot is not a nozzle".
"""
import argparse, json, sys
from pathlib import Path

import numpy as np
from PIL import Image

from x2d import (default_capture, machine_for, Part, PX_PER_MM, extrude, rounded_plate, solid_backer,
                 printability, fits, write_3mf, project_settings, preview_layers)
from x2d.palette import separate, footprint, render_svg
from x2d.poster import ink_art, line_width_mm
from x2d.shaded import OUT_PX_PER_MM, auto_map, flatten, stand_ins, to_image
from x2d.report import plate_report

HERE = Path(__file__).resolve().parents[1]


def parse_map(pairs):
    table = {}
    for pair in pairs:
        color, _, slot = pair.partition("=")
        if not color.startswith("#") or not slot.isdigit() or int(slot) < 1:
            raise argparse.ArgumentTypeError(f"--map wants '#RRGGBB=SLOT', got {pair!r}")
        table[color.upper()] = int(slot)
    return table


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path, help="SVG (needs cairosvg) or PNG with alpha")
    ap.add_argument("--map", nargs="+", metavar="#RRGGBB=SLOT",
                    help="source color -> project filament slot. Required, except "
                         "with --shaded, which can match shades to your spools itself")
    ap.add_argument("--priority", type=int, nargs="*", default=None, metavar="SLOT",
                    help="which slot keeps a shared edge; list the small-feature "
                         "slots first. Default: ascending slot number")
    ap.add_argument("--out", type=Path, default=Path("out/art.3mf"))
    ap.add_argument("--width", type=float, default=70.0, help="artwork width, mm")
    ap.add_argument("--base", type=float, help="plate thickness, mm; default 1.6, snapped to your capture's layer grid")
    ap.add_argument("--relief", type=float, help="artwork height, mm; default 0.6, snapped to your capture's layer grid")
    ap.add_argument("--margin", type=float, default=4.0, help="plate border, mm")
    ap.add_argument("--plate", choices=["rounded", "hull"], default="rounded",
                    help="rounded: rounded rectangle around the art. hull: the "
                         "art's own outline grown by --margin")
    ap.add_argument("--radius", type=float, default=8.0, help="rounded plate corner, mm")
    ap.add_argument("--base-slot", type=int, default=1)
    ap.add_argument("--simplify", type=float, default=0.12, help="contour tolerance, mm")
    ap.add_argument("--min-area", type=float, default=0.5, metavar="MM2",
                    help="drop islands smaller than this")
    ap.add_argument("--settings", type=Path, metavar="REF.3MF",
                    help="a project saved from Bambu Studio to lift settings and "
                         "spool colours from. Defaults to profiles/default.3mf when "
                         "that exists; without one the output carries slots only.")
    ap.add_argument("--recolor", nargs="*", default=[], metavar="SLOT=#RRGGBB",
                    help="a slot whose spool changed since the capture. Fixes the "
                         "preview and the filament panel; does NOT fix the purge "
                         "volumes, which were measured against the old spool")
    ap.add_argument("--ink", nargs="?", const="110,175", metavar="STRONG,WEAK",
                    help="the image is a shaded drawing, not flat art: pull its "
                         "linework out by hysteresis and print only that, as "
                         "'#000000'. Everything else becomes plate showing "
                         "through, so this is the two-filament case. Raise WEAK "
                         "for more interior detail. See x2d/poster.py.")
    ap.add_argument("--shaded", nargs="?", type=int, const=8, metavar="CLUSTERS",
                    help="the image is shaded colour art (a render, a painting): "
                         "group its shades into CLUSTERS (default 8), match each to "
                         "a spool, and remove whatever the nozzle cannot print "
                         "before tracing. --map overrides the matching. See x2d/shaded.py.")
    ap.add_argument("--halo", type=int, metavar="SLOT",
                    help="--shaded: ring the figure in this slot, so regions that "
                         "match the plate's colour still read")
    ap.add_argument("--halo-mm", type=float, default=1.2, help="--shaded: halo width, mm")
    ap.add_argument("--stl", action="store_true", help="also write per-part STLs")
    a = ap.parse_args()

    if a.shaded is not None and a.ink is not None:
        ap.error("--ink is for linework, --shaded for colour; pick one")
    if a.halo is not None and a.shaded is None:
        ap.error("--halo applies to --shaded")
    if a.halo is not None and a.halo < 1:
        ap.error("--halo wants a filament slot, 1 or higher")
    if not a.map and a.shaded is None:
        ap.error("--map is required unless --shaded matches the shades to your spools")
    try:
        mapping = parse_map(a.map or [])
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
    try:
        recolor = {int(k): v.upper() for k, v in
                   (r.split("=", 1) for r in a.recolor)}
    except ValueError:
        ap.error("--recolor wants 'SLOT=#RRGGBB'")
    a.settings = a.settings or default_capture(HERE)
    if recolor and not a.settings:
        ap.error("--recolor patches a capture; pass --settings")
    settings = project_settings(a.settings, colors=recolor or None) if a.settings else None
    machine = machine_for(settings)
    try:
        a.base, a.relief = machine.band(a.base, a.relief, default=(1.6, 0.6))
    except ValueError as e:
        ap.error(str(e))
    swatch = (settings or {}).get("filament_colour") or []
    slots = set(mapping.values()) | {a.base_slot} | ({a.halo} if a.halo is not None else set())
    if settings and any(s > len(swatch) for s in slots):
        ap.error(f"slot beyond the {len(swatch)} in {a.settings.name}")

    # Rasterise at the pipeline's own resolution so contours are sub-nozzle.
    if a.image.suffix.lower() == ".svg":
        image = render_svg(a.image, a.width * PX_PER_MM)
    else:
        image = Image.open(a.image).convert("RGBA")

    ink = None
    if a.ink is not None:
        try:
            strong, weak = (int(v) for v in a.ink.split(","))
        except ValueError:
            ap.error("--ink wants STRONG,WEAK, e.g. --ink 110,175")
        # Reported because it is the number that decides legibility and it is
        # invisible in the source file: raw ink here is ~0.18 mm at print size.
        ink = {"strong": strong, "weak": weak,
               "raw_stroke_mm": round(line_width_mm(image, width_mm=a.width), 3),
               "min_feature_mm": round(machine.min_feature, 2)}
        image = ink_art(image, width_mm=a.width, strong=strong, weak=weak, machine=machine)

    shaded = None
    if a.shaded is not None:
        if not mapping:
            if not swatch:
                ap.error("--shaded without --map needs a capture: its spools are the palette")
            mapping = auto_map(image, {i + 1: c for i, c in enumerate(swatch)}, clusters=a.shaded)
        try:
            labels = flatten(image, mapping, width_mm=a.width, machine=machine,
                             halo_slot=a.halo, halo_mm=a.halo_mm)
        except ValueError as e:
            ap.error(str(e))
        used = sorted(set(np.unique(labels).tolist()) - {-1})
        removed = sorted(set(mapping.values()) - set(used))
        # The flat PNG in spool colours is the thing to look at and judge;
        # separate() gets stand-ins, so two slots loaded with the same colour
        # stay two regions.
        seen = {s: swatch[s - 1] if s <= len(swatch) else stand_ins(used)[s] for s in used}
        flat_png = a.out.with_name(a.out.stem + "_flat.png")
        flat_png.parent.mkdir(parents=True, exist_ok=True)
        to_image(labels, seen).save(flat_png)
        shaded = {"map": mapping, "clusters": None if a.map else a.shaded,
                  "halo": {"slot": a.halo, "mm": a.halo_mm} if a.halo is not None else None,
                  "removed_as_too_thin": removed, "flat_png": str(flat_png)}
        named = mapping
        stand = stand_ins(used)
        image, mapping = to_image(labels, stand), {stand[s]: s for s in used}
        if a.priority:
            # Which slots survive the opening is not knowable in advance: keep
            # the given order for those that did, and put the rest after it.
            a.priority = ([s for s in a.priority if s in used] +
                          [s for s in used if s not in a.priority])
        width = labels.shape[1] / OUT_PX_PER_MM          # cropped, halo included
    else:
        named, width = mapping, a.width

    regions = separate(image, mapping, width_mm=width, simplify_mm=a.simplify,
                       min_area_mm2=a.min_area, priority=a.priority)
    if not regions:
        sys.exit("no artwork found -- check --map against the image's colors")
    art = footprint(regions)
    plate = (rounded_plate(art, a.margin, a.radius) if a.plate == "rounded"
             else solid_backer(art, a.margin))

    parts = [Part(extrude(plate, a.base), "plate", extruder=a.base_slot)]
    parts += [Part(extrude(geom, a.relief, z=a.base), f"slot{slot}", extruder=slot)
              for slot, geom in regions.items()]
    whole = sum((p.mesh for p in parts[1:]), parts[0].mesh)
    write_3mf(parts, a.out, settings=settings, name=a.image.stem)
    if a.stl:
        for p in parts:
            p.mesh.export(a.out.with_name(f"{a.out.stem}_{p.name}.stl"))

    # The loaded spool's colour when there is a capture; otherwise the art's
    # own colour for that slot, and grey for a plate slot the art never names.
    art_hue = {slot: colour for colour, slot in reversed(list(named.items()))}
    hue = lambda slot: (swatch[slot - 1] if slot <= len(swatch)
                        else art_hue.get(slot, "#B0B0B0"))
    layers = [(plate, hue(a.base_slot))] + [(g, hue(s)) for s, g in regions.items()]
    preview_layers(layers, a.out.with_name(a.out.stem + "_preview.png"))

    report = plate_report(parts, settings=settings, plate=plate, artwork=regions,
                          base_slot=a.base_slot, base=a.base, relief=a.relief,
                          extra={"map": named, "ink": ink, "shaded": shaded,
                                 "recolored_since_capture": recolor or None})
    a.out.with_suffix(".json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
