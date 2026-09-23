#!/usr/bin/env python3
"""Glue-on bowtie -> Bambu-native .3mf

A flat-backed bowtie to superglue onto an already-printed figure. Writes a few
sizes side by side so the fit is chosen against the real print, dry, before
any glue: a 16 mm bowtie is seconds of plastic, a wrong guess is a redo.

Usage:
    uv run scripts/bowtie.py --out out/bowtie.3mf
    uv run scripts/bowtie.py --widths 14 16 18 --slot 3 --settings inputs/figure.3mf
"""
import argparse
import sys
from pathlib import Path

import manifold3d as m3d
import numpy as np
import trimesh
from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from x2d import Part, default_capture, machine_for, project_settings, write_3mf

# Proportions as fractions of the overall width, so one number resizes it.
HEIGHT = 0.50        # outer edge of a wing
KNOT_W, KNOT_H = 0.20, 0.27   # only if a knot is asked for
CORNER = 0.04        # outer corner radius: a sharp corner prints as a blob


# What a small flat glue-on part wants from the process, as profile keys and
# the values Bambu Studio itself writes for them. The part has nothing to
# support, a brim would have to be trimmed off the only edges anyone sees, and
# the front face is a top surface, which ironing smooths.
FLAT_PART = {"enable_support": "0", "brim_type": "no_brim", "ironing_type": "top"}


def patch_process(cfg, changes):
    """Set process keys in a capture, and tell Bambu Studio they were set.

    Why the second half: on load Bambu Studio starts from the named system
    preset and re-applies only the keys listed in different_settings_to_system
    (entry 0 is the process). A value changed without being listed there is
    in the file and silently ignored.
    """
    for key, value in changes.items():
        if key not in cfg:            # a typo must not become a new key
            raise ValueError(f"{key}: not a key in this capture")
        if isinstance(cfg[key], list):
            raise ValueError(f"{key}: per-filament key, not a process setting")
        cfg[key] = str(value)
    diff = list(cfg.get("different_settings_to_system") or [""])
    listed = set(filter(None, diff[0].split(";"))) | set(changes)
    diff[0] = ";".join(sorted(listed))
    cfg["different_settings_to_system"] = diff
    return cfg


def _prism(poly, z0, z1, chamfer):
    """Convex polygon -> solid from z0 to z1, its top edge chamfered at 45 deg.

    Why a hull: every piece here is convex, so the hull of the outline low and
    the inset outline at the top *is* the chamfered solid -- no boolean, no
    offset bookkeeping, always watertight.
    """
    top = poly.buffer(-chamfer, join_style="mitre") if chamfer else poly
    ring = lambda p, z: [(x, y, z) for x, y in p.exterior.coords[:-1]]
    pts = ring(poly, z0) + ring(poly, z1 - chamfer) + ring(top, z1)
    return m3d.Manifold.hull_points(np.array(pts, dtype=np.float64))


def bowtie(width, machine, *, wing=2.0, knot=0.0, chamfer=0.6):
    """One bowtie, flat back on z=0, `width` mm across.

    The back is flat on purpose. It is the bed face (flat and keyed by the
    textured plate, which glue likes), and a flat back bridges whatever relief
    is on the figure instead of having to match it.
    """
    wing, knot = machine.snap(wing), machine.snap(knot) if knot else 0.0
    # The chamfer starts on a layer boundary. snap() is a height, not a
    # difference, so snap where the chamfer starts and take what is left.
    cut = lambda top: top - machine.snap(top - chamfer) if chamfer else 0.0
    w, h = width / 2, width * HEIGHT / 2
    # Two triangles meeting at a point is the drawing; a point is not
    # printable, and would come off the bed as two loose wings. So the point
    # is a neck of the least the nozzle can lay down twice over (four lines),
    # the same on every size -- it reads as a point from arm's length.
    pin, r = 2 * machine.min_feature / 2, width * CORNER
    if pin >= width * HEIGHT / 2:
        raise ValueError(f"bowtie {width} mm: no taller than its {2 * pin:.2f} mm neck")
    solids = []
    for s in (-1, 1):
        # The wing runs past the centre line so the two overlap under the knot:
        # a shared edge is a seam, an overlap is a union.
        trap = Polygon([(s * w, -h), (s * w, h), (-s * 0.5, pin), (-s * 0.5, -pin)])
        trap = trap.buffer(-r).buffer(r)          # round the corners, stay convex
        solids.append(_prism(trap, 0, wing, cut(wing)))
    out = solids[0] + solids[1]
    if knot:
        kw, kh = width * KNOT_W / 2, width * KNOT_H / 2
        kn = box(-kw, -kh, kw, kh).buffer(-r).buffer(r)
        out += _prism(kn, 0, knot, cut(knot))
    m = out.to_mesh()
    mesh = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts))
    if not mesh.is_volume:
        raise ValueError(f"bowtie {width} mm did not come out watertight")
    return mesh


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--widths", type=float, nargs="+", default=[14.0, 16.0, 18.0])
    ap.add_argument("--slot", type=int, default=1, help="filament slot (1-based)")
    ap.add_argument("--wing", type=float, default=2.0, help="wing thickness, mm")
    ap.add_argument("--knot", type=float, default=0.0,
                    help="centre knot thickness, mm; 0 (default) is two plain triangles")
    ap.add_argument("--chamfer", type=float, default=0.6)
    ap.add_argument("--gap", type=float, default=6.0, help="spacing on the plate")
    ap.add_argument("--nozzle", type=int, default=None,
                    help="pin the slot to this nozzle (1 main, 2 auxiliary). Unset, "
                         "Bambu Studio groups filaments itself and may pick either")
    ap.add_argument("--color", default=None, metavar="#RRGGBB",
                    help="what is loaded in the slot now, if not what the capture had")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="process setting to patch in the capture; repeatable. "
                         f"Defaults: {FLAT_PART}")
    ap.add_argument("--settings", type=Path, default=None,
                    help="a .3mf saved from Bambu Studio; default profiles/default.3mf")
    ap.add_argument("--out", type=Path, default=REPO / "out" / "bowtie.3mf")
    a = ap.parse_args(argv)

    capture = a.settings or default_capture(REPO)
    settings = project_settings(
        capture, object_slot=a.slot,
        pin={a.slot: a.nozzle} if a.nozzle else None,
        colors={a.slot: a.color} if a.color else None) if capture else None
    if settings:
        asked = dict(kv.split("=", 1) for kv in a.sets)
        # The defaults only where the capture has the key (a real one always
        # does); what was asked for by name stays strict, so a typo is loud.
        wanted = {k: v for k, v in FLAT_PART.items() if k in settings}
        patch_process(settings, {**wanted, **asked})
    machine = machine_for(settings)

    parts, y = [], 0.0
    for wd in a.widths:
        mesh = bowtie(wd, machine, wing=a.wing, knot=a.knot, chamfer=a.chamfer)
        mesh.apply_translation([0, y + wd * HEIGHT / 2, 0])
        y += wd * HEIGHT + a.gap
        parts.append(Part(mesh, f"bowtie {wd:g} mm", a.slot))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    write_3mf(parts, a.out, machine=machine, name="bowtie", settings=settings)
    for p in parts:
        ex = p.mesh.extents
        print(f"{p.name}: {ex[0]:.1f} x {ex[1]:.1f} x {ex[2]:.1f} mm, slot {p.extruder}")
    print(f"wrote {a.out}  (not sliced)")
    return parts


if __name__ == "__main__":
    main()
