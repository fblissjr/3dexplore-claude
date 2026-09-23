"""What Bambu Studio means by "floating cantilever", measured.

With supports off, Bambu Studio warns when a model has unsupported regions,
and names the object but not the place. This slices the parts on the
capture's own layer grid and finds them:

- floating: an island in layer k with nothing at all under it in layer k-1.
- overhang: area beyond what the layer below holds up, where "holds up" is
  `support_threshold_angle` read from horizontal, as Bambu Studio does -- the
  layer below reaches out h / tan(angle).

Each overhang is then classified by how many separate stretches of its
outline sit on the layer below: two or more is a bridge (a ceiling across a
cavity; PLA spans these), one is a cantilever (a ledge held on one side).
`reach_mm` is how far it gets from anything beneath it.

On the catbus this was written for, the warning came from internal bridges
under the roof, the longest reaching 5.6 mm from its walls, and ledges of at
most 2.2 mm: nothing that wanted supports. The report is a way to decide
that, not the decision.
"""
import math

import numpy as np
import shapely
from shapely.ops import unary_union

__all__ = ["layer_z", "sections", "overhang_report"]


def layer_z(machine, top):
    """Mid-heights of every layer up to `top`: the first layer, then whole layers."""
    z = [machine.first_layer_h / 2]
    edge = machine.first_layer_h
    while edge + machine.layer_h <= top + 1e-9:
        z.append(edge + machine.layer_h / 2)
        edge += machine.layer_h
    return np.array(z)


def sections(meshes, zs):
    """{layer index: [(part index, polygon)]} for every part the layer cuts."""
    out = {}
    for i, m in enumerate(meshes):
        lo, hi = m.bounds[:, 2]
        rows = np.flatnonzero((zs > lo) & (zs < hi))
        if not len(rows):
            continue
        for k, s in zip(rows, m.section_multiplane([0, 0, 0], [0, 0, 1], zs[rows])):
            polys = [p for p in (s.polygons_full if s is not None else []) if p.area > 1e-6]
            if polys:
                out.setdefault(int(k), []).append((i, unary_union(polys).buffer(0)))
    return out


def _islands(g):
    return list(g.geoms) if hasattr(g, "geoms") else ([] if g.is_empty else [g])


def overhang_report(parts, machine, angle_deg, *, min_area=0.3, top=12):
    """parts: [(name, trimesh in bed coordinates)]. Returns a JSON-able dict."""
    names = [n for n, _ in parts]
    meshes = [m for _, m in parts]
    z0 = min(m.bounds[0, 2] for m in meshes)
    zs = layer_z(machine, max(m.bounds[1, 2] for m in meshes) - z0) + z0
    per = sections(meshes, zs)
    layers = {k: unary_union([g for _, g in v]) for k, v in per.items()}
    held = machine.layer_h / math.tan(math.radians(angle_deg))
    floating, rows = [], []
    for k in sorted(per):
        if k == 0:
            continue
        below = layers.get(k - 1)
        support = below.buffer(held) if below is not None else None
        near = below.buffer(0.05) if below is not None else None
        rest = below.buffer(held + 0.05) if below is not None else None
        for i, g in per[k]:
            afloat = [isl for isl in _islands(g) if support is None or not isl.intersects(near)]
            floating += [_row(zs[k] - z0, names[i], f, area_mm2=f.area) for f in afloat if f.area >= 0.01]
            if support is None:
                continue
            for oh in _islands(g.difference(unary_union([support] + afloat))):
                if oh.area < min_area:
                    continue
                pts = shapely.points(shapely.get_coordinates(oh.segmentize(0.2)))
                reach = float(shapely.distance(pts, below).max())
                # separate stretches of the outline resting on the layer below
                touch = _islands(oh.boundary.intersection(rest))
                anchors = len(_islands(unary_union([t.buffer(0.3) for t in touch]))) if touch else 0
                rows.append(_row(zs[k] - z0, names[i], oh, area_mm2=oh.area, reach_mm=reach,
                                 kind="bridge" if anchors >= 2 else "cantilever"))
    by_part = {}
    for r in rows:
        b = by_part.setdefault(r["part"], {"overhang_mm2": 0.0, "max_reach_mm": 0.0,
                                           "bridge_layers": 0, "cantilever_layers": 0})
        b["overhang_mm2"] = round(b["overhang_mm2"] + r["area_mm2"], 1)
        b["max_reach_mm"] = max(b["max_reach_mm"], r["reach_mm"])
        b[f"{r['kind']}_layers"] += 1
    worst = lambda kind: max((r["reach_mm"] for r in rows if r["kind"] == kind), default=0.0)
    return {
        "layers": len(zs), "support_threshold_angle": angle_deg,
        "held_per_layer_mm": round(held, 3),
        "floating_islands": len(floating), "floating": floating[:top],
        "worst_bridge_reach_mm": worst("bridge"), "worst_cantilever_reach_mm": worst("cantilever"),
        "overhangs": sorted(rows, key=lambda r: -r["reach_mm"])[:top],
        "by_part": dict(sorted(by_part.items(), key=lambda kv: -kv[1]["max_reach_mm"])),
    }


def _row(z, part, geom, **extra):
    c = geom.centroid
    row = {"z_mm": round(float(z), 2), "part": part, "xy_mm": [round(c.x, 1), round(c.y, 1)]}
    row.update({k: (round(v, 2) if isinstance(v, float) else v) for k, v in extra.items()})
    return row
