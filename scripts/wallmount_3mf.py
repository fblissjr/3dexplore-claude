#!/usr/bin/env python3
"""Make a wall-mount version of a downloaded project: the face it prints on
goes against the wall (`x2d/wallmount.py`).

Two steps, because the second needs numbers only the first shows.

    # 1. look: sections through the part in the wall frame, plate height, size
    uv run scripts/wallmount_3mf.py stand.3mf --survey --up 135

    # 2. build: cut the desk foot, add two level countersunk screw bosses,
    #    face the other way along the wall, pin to the main nozzle
    uv run scripts/wallmount_3mf.py stand.3mf --up 135 \\
        --trim "-50,-40 50,-40 50,-22.5 -50,-22.5" --mirror --out out/stand_wall.3mf

--up is the bed direction that points up the wall, in degrees counter-clockwise
from the bed's +X: in the slicer's top view, the direction the object's top
should face once it hangs. Coordinates for --trim and --holes are wall-frame
mm (X along the wall, U up it, origin at the centre of the footprint); the
survey image is drawn in exactly that frame.

Writes <out>.3mf, <out>_wall.png (as it hangs, holes and strip patch marked)
and prints a report: hole spacing, how far up they sit, the longest adhesive
strip that fits, and the overhang reading before and after, which should
match unless the trim or bosses changed what needs support.
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np

from x2d.archive import Mesh, components, pin_nozzles, read_meshes, replace_mesh, rewrite
from x2d.machine import machine_for
from x2d.overhang import overhang_report
from x2d.wallmount import (Frame, Screw, bed_face, build, footprint_above, front_view,
                           plate_top, strip_fit)


def points(text):
    return [tuple(float(v) for v in p.split(",")) for p in text.split()]


def overhangs(mesh, settings):
    angle = settings.get("support_threshold_angle")
    if angle is None:
        return None
    r = overhang_report([("part", mesh)], machine_for(settings), float(angle))
    return {k: r[k] for k in ("floating_islands", "worst_bridge_reach_mm", "worst_cantilever_reach_mm")}


def survey(mesh, frame, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    z0, z1 = mesh.bounds[:, 2]
    zp = plate_top(mesh)
    shapes = [("bed face", bed_face(mesh)), (f"above the plate (z>{zp - z0:.1f})", footprint_above(mesh, zp))]
    zs = np.linspace(zp + 0.2, z1 - 0.2, 4)
    from x2d.wallmount import _section
    shapes += [(f"section z={z - z0:.1f}", _section(mesh, z)) for z in zs]
    fig, axs = plt.subplots(2, 3, figsize=(16, 10))
    for ax, (title, poly) in zip(axs.ravel(), shapes):
        poly = frame.polygon(poly)
        for g in getattr(poly, "geoms", [poly]):
            if g.is_empty:
                continue
            ax.fill(*g.exterior.xy, color="#bbb")
            for r in g.interiors:
                ax.fill(*r.xy, color="white")
        ax.set_title(title); ax.set_aspect("equal"); ax.grid(True, alpha=.4)
        ax.set_xlabel("X along the wall, mm"); ax.set_ylabel("U up the wall, mm")
    plt.tight_layout(); plt.savefig(path, dpi=70)
    return {"plate_top_mm": round(zp - z0, 2), "bed_face_mm2": round(bed_face(mesh).area, 1),
            "out_from_wall_mm": round(z1 - z0, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path, help="a .3mf saved by Bambu Studio")
    ap.add_argument("--up", type=float, default=90.0, help="bed direction that is up the wall, degrees from +X")
    ap.add_argument("--survey", action="store_true", help="only draw sections and measure; write nothing else")
    ap.add_argument("--trim", action="append", default=[], metavar='"X,U X,U ..."',
                    help="wall-frame polygon: what stands on the plate inside it is cut away (repeatable)")
    ap.add_argument("--holes", metavar='"X,U X,U"', help='screw centres by hand, or "none" for strips only')
    ap.add_argument("--mirror", action="store_true", help="face the other way along the wall")
    ap.add_argument("--shaft", type=float, default=Screw.shaft_d, help="screw clearance hole, mm")
    ap.add_argument("--head", type=float, default=Screw.head_d, help="countersink diameter, mm (90 degrees)")
    ap.add_argument("--boss-r", type=float, default=Screw.boss_r, help="boss radius, mm")
    ap.add_argument("--boss-h", type=float, default=Screw.boss_h, help="boss height above the plate, mm")
    ap.add_argument("--no-pin", action="store_true", help="leave nozzle grouping to the slicer")
    ap.add_argument("--object", type=int, help="build object id, when the project has more than one mesh")
    ap.add_argument("--out", type=Path, help="default: <name>_wall.3mf")
    a = ap.parse_args()
    out = a.out or a.project.with_name(a.project.stem + "_wall.3mf")
    if out.resolve() == a.project.resolve():
        ap.error("--out would overwrite the input")

    with zipfile.ZipFile(a.project) as zf:
        comps = [c for c in components(zf) if a.object in (None, c[0])]
        if len(comps) != 1:
            ap.error(f"{len(comps)} meshes on the plate: pick one with --object "
                     f"({sorted({c[0] for c in comps})})")
        oid, path, cid, matrix = comps[0]
        src = read_meshes(zf)[(path, cid)]
        text = zf.read(path).decode()
        names = zf.namelist()
        settings_text = zf.read("Metadata/project_settings.config").decode() \
            if "Metadata/project_settings.config" in names else ""
        model_settings = zf.read("Metadata/model_settings.config").decode() \
            if "Metadata/model_settings.config" in names else ""
    settings = json.loads(settings_text) if settings_text else {}
    mesh = src.trimesh()
    mesh.apply_transform(matrix)                    # bed coordinates: the bed is z = 0
    frame = Frame.for_mesh(mesh, a.up)

    if a.survey:
        png = out.with_name(out.stem + "_survey.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        report = survey(mesh, frame, png)
        lo, hi = frame.to_wall(mesh.vertices[:, :2]).min(0), frame.to_wall(mesh.vertices[:, :2]).max(0)
        report.update({"wall_frame_bounds_mm": [np.round(lo, 1).tolist(), np.round(hi, 1).tolist()],
                       "painted_triangles": sum(1 for t in src.attrs if "paint_color" in t),
                       "survey_png": str(png)})
        print(json.dumps(report, indent=2))
        return

    holes = None if a.holes is None else [] if a.holes == "none" else points(a.holes)
    screw = Screw(a.shaft, a.head, a.boss_r, a.boss_h)
    wall, report = build(mesh, frame, trim=[points(t) for t in a.trim], holes=holes, screw=screw,
                         mirror=a.mirror, painted=sum(1 for t in src.attrs if "paint_color" in t))
    report["overhangs"] = {"before": overhangs(mesh, settings), "after": overhangs(wall, settings)}
    view = wall.copy()
    strips = strip_fit(view, frame)
    report["longest_strip_mm"] = {f"{w} wide": L for w, (L, _) in strips.items()}

    back = wall.copy()
    back.apply_transform(np.linalg.inv(matrix))     # back into the object's own coordinates
    text = replace_mesh(text, cid, Mesh(back.vertices, back.faces, [""] * len(back.faces)))
    replace = {path: text.encode()}
    if model_settings:
        # face counts are the GUI's mesh statistics for this object; stale ones are harmless but wrong
        block = re.compile(rf'(<object id="{oid}">.*?</object>)', re.S)
        model_settings = block.sub(lambda m: re.sub(r'face_count="\d+"', f'face_count="{len(back.faces)}"', m.group(1)),
                                   model_settings)
    if not a.no_pin:
        settings_text, model_settings, report["pinned_to_main_nozzle"] = pin_nozzles(settings_text, model_settings, 1)
    if settings_text:
        replace["Metadata/project_settings.config"] = settings_text.encode()
    if model_settings:
        replace["Metadata/model_settings.config"] = model_settings.encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    rewrite(a.project, out, replace)
    report["front_view_png"] = str(front_view(wall, frame, out.with_name(out.stem + "_wall.png"),
                                              holes=report["holes_wall_mm"], strips=strips))
    report["out"] = str(out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
