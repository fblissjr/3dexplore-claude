"""Turn a photogrammetry scan (KIRI Engine OBJ) into a printable, standing solid.

The scan itself is usually fine. What kills the print is everything around it:

- KIRI writes one vertex per texture-atlas seam, so the OBJ *looks* like a few
  hundred open shells until the vertices are welded by position. Weld first,
  then judge the mesh.
- The file is in metres. Imported as millimetres it is a 3 mm figurine; scaled
  by eye, nobody checks whether the ribs are still wider than a nozzle.
- A scanned figure stands on whatever two points happened to touch the ground,
  and its arms stick out over nothing. Neither is a geometry error, so no
  mesh checker reports it, and the slicer prints it anyway.

So this script welds, keeps the one real body, scales to a stated height, cuts
a flat sole, fuses a base plate wide enough to hold the centre of mass, and
then reports what is still too thin or hanging in the air. It writes an STL,
a JSON report and, given a captured profile, a native .3mf that opens on that
printer already set up; where the supports go is still Bambu Studio's call.

    uv run scripts/scan2print.py scan.obj \
        --height 200 --out out/figure/figure_200mm
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import shapely
import trimesh
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x2d import Part, default_capture, machine_for, write_3mf, project_settings  # noqa: E402

# how far the figure sinks into the plate so the boolean union has volume to weld
PLATE_OVERLAP_MM = 0.5

# Support presets: each is a patch on the captured project_settings.config,
# nothing more. The capture says tree(manual), which means "only where
# painted", so a scan with limbs in the air needs *something* here; the rest
# is how much. Keys quoted from PrintConfig.cpp so they can be checked.
#   auto      trees wherever the slicer sees an overhang: safest, most material
#   plate     the same, but trunks may only stand on the bed: keeps trees out
#             of cavities (a ribcage), at the cost of what they then can't reach
#   slim      Tree Slim style, branches 45 degrees, and critical regions only
#             (cantilevers and sharp tails, not every bump on a scanned skin):
#             far less support, more trust in 45-degree overhangs printing bare
#   keep      whatever the capture says
#   off       none, for a figure you will paint supports onto by hand
SUPPORT_PRESETS = {
    "auto":  {"enable_support": "1", "support_type": "tree(auto)", "support_on_build_plate_only": "0"},
    "plate": {"enable_support": "1", "support_type": "tree(auto)", "support_on_build_plate_only": "1"},
    "slim":  {"enable_support": "1", "support_type": "tree(auto)", "support_on_build_plate_only": "0",
              "support_style": "tree_slim", "support_critical_regions_only": "1",
              "tree_support_branch_angle": "45"},
    "keep":  {},
    "off":   {"enable_support": "0"},
}


def load_welded(path: Path) -> trimesh.Trimesh:
    m = trimesh.load(path, force="mesh")
    # merge_tex/merge_norm: weld by position even where UV or normal differ.
    # Without it every texture chart is its own open shell.
    m.merge_vertices(merge_tex=True, merge_norm=True)
    m.remove_unreferenced_vertices()
    return m


def largest_body(m: trimesh.Trimesh):
    """Keep the biggest connected body; return it and a summary of the rest.

    Loose shards are scan noise (a bit of background, a mis-stitched patch) and
    every one of them is printed floating in mid-air.
    """
    parts = sorted(m.split(only_watertight=False), key=lambda p: -len(p.faces))
    dropped = [
        {"faces": len(p.faces), "extents_mm": p.extents.round(2).tolist(),
         "center_mm": p.centroid.round(1).tolist()}
        for p in parts[1:]
    ]
    return parts[0], dropped


def y_up_to_z_up(m: trimesh.Trimesh) -> None:
    # KIRI (and most scanners) write Y up; the printer bed is Z up.
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))


def section_polys(m: trimesh.Trimesh, z: float) -> list[Polygon]:
    s = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return []
    # project straight down (drop z) so layer polygons share the model's x/y
    planar, _ = s.to_2D(to_2D=trimesh.transformations.translation_matrix([0, 0, -z]))
    return [p for p in planar.polygons_full if p.is_valid and p.area > 0]


def islands(u):
    return list(u.geoms) if isinstance(u, MultiPolygon) else ([u] if not u.is_empty else [])


def build_base(m: trimesh.Trimesh, sole_z: float, contact: float, margin: float, thickness: float, machine):
    """A plate under the figure, plus risers under anything that almost touches.

    Two things a scanned figure never gives you for free:

    - The sole cut catches only the lowest point. Here that was one foot; the
      other stood 2 mm higher and would have printed hanging in mid-air, off
      the edge of a plate that had never heard of it. So every island that
      first appears within `contact` mm of the cut, with nothing beneath it,
      gets a riser: its own footprint extruded down into the plate.
    - The feet alone are a few mm across and the arms pull the centre of mass
      well forward of them. The plate is the convex hull of every contact
      island *and* the centre-of-mass projection, plus a margin, so the thing
      cannot tip.
    """
    step = 0.5
    plate_bottom = sole_z + PLATE_OVERLAP_MM - thickness
    riser_bottom = plate_bottom + 1.0                             # buried in the plate
    # riser tops land on the layer grid, measured from the plate's underside
    # (which becomes z=0): as a separate colour that top is a part boundary
    on_grid = lambda top: plate_bottom + machine.grid_ceil(top - plate_bottom)
    prev, contact_polys, risers, riser_info = None, [], [], []
    for z in np.arange(sole_z + step / 2, sole_z + contact, step):
        u = unary_union(section_polys(m, z))
        for isl in islands(u):
            grounded = prev is None or isl.intersects(prev)
            contact_polys.append(isl)
            if not grounded:
                foot = isl.buffer(0.5, join_style="round")
                r = trimesh.creation.extrude_polygon(foot, on_grid(z + step) - riser_bottom)
                r.apply_translation([0, 0, riser_bottom])
                risers.append(r)
                riser_info.append({"z_mm": round(float(z), 1), "area_mm2": round(isl.area, 1),
                                   "xy_mm": [round(c, 1) for c in isl.centroid.coords[0]]})
        prev = u
    com = m.center_mass
    hull = unary_union(contact_polys + [Point(com[0], com[1])]).convex_hull.buffer(margin, join_style="round")
    plate = trimesh.creation.extrude_polygon(hull, thickness)
    plate.apply_translation([0, 0, plate_bottom])
    info = {
        "contact_area_mm2": round(unary_union(contact_polys).area, 1),
        "risers": riser_info,
        "com_xy_mm": [round(com[0], 1), round(com[1], 1)],
        "com_over_contact_hull": bool(unary_union(contact_polys).convex_hull.contains(Point(com[0], com[1]))),
        "plate_area_mm2": round(hull.area, 1),
        "plate_extents_mm": [round(hull.bounds[2] - hull.bounds[0], 1), round(hull.bounds[3] - hull.bounds[1], 1)],
    }
    return [plate, *risers], info


def thin_and_floating(m: trimesh.Trimesh, step: float, min_feature: float) -> dict:
    """Per-layer look at what the slicer will actually do with this.

    at_risk: area that an opening at half the minimum feature erases, i.e.
    walls too thin to print. floating: islands with nothing beneath them in
    the previous sample, i.e. what needs support.
    """
    # half-step offset: sampling exactly on the plate's flat top returns a
    # degenerate section and everything above it reads as floating
    zs = np.arange(m.bounds[0][2] + step / 2, m.bounds[1][2], step)
    prev = None
    rows, total_area, total_risk, floating = [], 0.0, 0.0, []
    r = min_feature / 2
    for z in zs:
        polys = section_polys(m, z)
        if not polys:
            prev = None
            continue
        u = unary_union(polys)
        risk = u.area - u.buffer(-r).buffer(r).area
        total_area += u.area
        total_risk += risk
        if prev is not None:
            for p in islands(u):
                if not p.intersects(prev):
                    floating.append({"z_mm": round(float(z), 1), "area_mm2": round(p.area, 1),
                                     "xy_mm": [round(c, 1) for c in p.centroid.coords[0]]})
        rows.append((float(z), u.area, risk))
        prev = u
    worst = sorted(rows, key=lambda t: -t[2])[:5]
    return {
        "at_risk_pct": round(100 * total_risk / max(total_area, 1e-9), 2),
        "worst_bands": [{"z_mm": round(z, 1), "area_mm2": round(a, 1), "at_risk_mm2": round(k, 1)} for z, a, k in worst],
        "floating_islands": len(floating),
        "floating_examples": floating[:12],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obj", type=Path)
    ap.add_argument("--height", type=float, required=True, help="finished height incl. base, mm")
    ap.add_argument("--base", type=float, help="base plate thickness, mm; default 4.0, snapped to your capture's layer grid")
    ap.add_argument("--contact", type=float, default=6.0, help="band above the sole in which near-misses get a riser, mm")
    ap.add_argument("--margin", type=float, default=6.0, help="plate margin around sole + centre of mass, mm")
    ap.add_argument("--sole", type=float, default=1.5, help="how much of the lowest point to slice off for a flat sole, mm")
    ap.add_argument("--step", type=float, default=2.0, help="z spacing of the thin/floating report, mm")
    ap.add_argument("--settings", type=Path, help="a saved Bambu project to lift project_settings.config from "
                    "(defaults to profiles/default.3mf); with it the output is also a native .3mf")
    ap.add_argument("--slot", type=int, default=1, help="AMS filament slot for the whole figure")
    ap.add_argument("--supports", choices=tuple(SUPPORT_PRESETS), default="auto",
                    help="support preset, see SUPPORT_PRESETS in the source")
    ap.add_argument("--plate-slot", type=int, help="print the plate (and risers) in this AMS slot instead of "
                    "the figure's; two parts, one filament change at the plate top")
    ap.add_argument("--out", type=Path, required=True, help="output stem; writes .stl, .json and (with --settings) .3mf")
    a = ap.parse_args()

    # Every slot pinned, mode Manual. The capture says "Auto For
    # Flush", which hands the choice to the slicer -- and the CLI, knowing
    # nothing about the real AMS, put a single-filament print on the *aux*
    # nozzle (filament_map = 2,1,1,1 in the G-code, Bambu Studio's send
    # dialog then demanding filament in a nozzle that has none). Only Manual
    # lets the file win. The mapping it wins with is the capture's own, saved
    # from the GUI against the real AMS layout -- nozzle 1 for every slot on a
    # machine like the reference X2D, nozzle 2 for slots on a second AMS.
    # A single-nozzle printer has nothing to mis-assign.
    a.settings = a.settings or default_capture(Path(__file__).resolve().parents[1])
    cfg = project_settings(a.settings, object_slot=a.slot) if a.settings else None
    if cfg and len(cfg.get("nozzle_diameter") or []) > 1:
        pin = {slot: int(n) for slot, n in enumerate(cfg.get("filament_map") or [], start=1)}
        cfg = project_settings(a.settings, object_slot=a.slot, pin=pin)
    machine = machine_for(cfg)
    if a.base is None:
        a.base = machine.snap(4.0)
    elif a.plate_slot and a.plate_slot != a.slot and not machine.on_grid(a.base):
        # the plate top becomes a part boundary, and a boundary must be a layer's
        ap.error(f"--base {a.base:g} is off the layer grid ({machine.first_layer_h:g} mm first "
                 f"layer, then {machine.layer_h:g} mm); try {round(machine.grid_ceil(a.base), 4):g}")

    m = load_welded(a.obj)
    raw = {"vertices": len(m.vertices), "faces": len(m.faces), "watertight": bool(m.is_watertight),
           "extents_source_units": m.extents.round(4).tolist()}
    m, dropped = largest_body(m)
    y_up_to_z_up(m)

    # figure height before the cut: finished height, less the plate it stands
    # on, plus the sliver sunk into the plate and the sliver sliced off
    figure_h = a.height - a.base + PLATE_OVERLAP_MM + a.sole
    m.apply_scale(figure_h / m.extents[2])
    m.apply_translation(-m.bounds[0])       # min corner to the origin
    for d in dropped:                       # report shards in output scale
        d["extents_mm"] = (np.array(d["extents_mm"]) * figure_h / raw["extents_source_units"][1]).round(1).tolist()

    # Flat sole: keep everything above the cut. Boolean rather than slice_plane
    # because the cut runs through two bumpy feet and needs proper capping.
    sole_z = a.sole
    keep = trimesh.creation.box(extents=m.extents + 10)
    keep.apply_translation(m.bounds.mean(axis=0) - keep.bounds.mean(axis=0))
    keep.apply_translation([0, 0, sole_z - keep.bounds[0][2]])
    figure = trimesh.boolean.intersection([m, keep], engine="manifold")

    base, base_info = build_base(figure, sole_z, a.contact, a.margin, a.base, machine)
    solid = trimesh.boolean.union([figure, *base], engine="manifold")
    solid, cut_off = largest_body(solid)    # a toe tip severed by the cut would float
    shift = solid.bounds[0].copy()
    solid.apply_translation(-shift)

    report = {
        "source": raw,
        "dropped_shards": dropped,
        "severed_by_sole_cut": cut_off,
        "output": {
            "extents_mm": solid.extents.round(1).tolist(),
            "watertight": bool(solid.is_watertight),
            "winding_consistent": bool(solid.is_winding_consistent),
            "volume_cm3": round(solid.volume / 1000, 1),
            "faces": len(solid.faces),
        },
        "base": base_info,
        "print": thin_and_floating(solid, a.step, machine.min_feature),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    solid.export(a.out.with_suffix(".stl"))
    if cfg:
        # Patch the capture, never compose it: the support preset plus thin-wall
        # detection, which is what keeps a 1 mm finger from being skipped.
        cfg.update(SUPPORT_PRESETS[a.supports])
        cfg["detect_thin_wall"] = "1"
        if a.plate_slot and a.plate_slot != a.slot:
            # Two colours the cheap way: the plate is its own part, and the
            # figure is carved out of it so the two share a boundary exactly
            # (boolean-derived, the "generated by us" family) instead of
            # double-claiming the 0.5 mm overlap and the risers.
            base_part = trimesh.boolean.union(base, engine="manifold")
            base_part.apply_translation(-shift)
            figure_part = trimesh.boolean.difference([solid, base_part], engine="manifold")
            parts = [Part(figure_part, a.obj.stem, extruder=a.slot), Part(base_part, "plate", extruder=a.plate_slot)]
        else:
            parts = [Part(solid, a.obj.stem, extruder=a.slot)]
        write_3mf(parts, str(a.out.with_suffix(".3mf")), settings=cfg, name=a.out.stem)
        report["3mf"] = {"parts": {p.name: p.extruder for p in parts}, "supports": a.supports,
                         "patched": sorted(SUPPORT_PRESETS[a.supports]) + ["detect_thin_wall"],
                         "settings_from": str(a.settings)}
    a.out.with_suffix(".json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
