"""Repair a mostly-good mesh without touching the triangles that are fine.

The general tools are wrong for someone else's model. On a 127k-triangle
sculpted head with 273 open and 34 non-manifold edges, all inside one square
millimetre of its base, pymeshfix dropped 43k triangles and changed the volume
by 5% (it resolves self-intersections, which a sculpt has on purpose), and
MeshLab's filters flipped about 1200. Both also renumber the triangles, which
throws away the designer's painting.

So this is surgery. Cut a ball around every defective triangle, grow it until
what is left has clean holes, and refill each hole as a height field sampled
from the surface that was cut out. Every triangle outside the cut keeps its
vertices, its order and, through `Repair.origin`, its paint.

Everything here counts edges on exact vertex indices. Merging coincident
vertices first reports "non-manifold" edges wherever two separate shells of a
part touch, which is normal in a multi-shell part and not what Bambu Studio
means by the word.
"""
from dataclasses import dataclass

import mapbox_earcut as earcut
import numpy as np
import trimesh
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import Polygon

__all__ = ["Repair", "defects", "bad_faces", "boundary_loops", "fill", "repair"]

# Largest patch triangle, mm^2. About a quarter of the 0.84 mm minimum
# feature on a 0.4 nozzle, squared: finer than anything the patch could
# print, coarse enough that a 20 mm^2 hole stays a few hundred triangles.
MAX_PATCH_AREA = 0.02


def _edges(F):
    """Each triangle edge (3 per triangle, in F's column order), with how many
    triangles share it undirected and how many walk it in the same direction."""
    D = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    _, inv, count = np.unique(np.sort(D, axis=1), axis=0, return_inverse=True, return_counts=True)
    _, dinv, dcount = np.unique(D, axis=0, return_inverse=True, return_counts=True)
    return D, count[inv.ravel()], dcount[dinv.ravel()]


def defects(F):
    """Counts as Bambu Studio's object info reports them -- open edges (one
    triangle) and non-manifold edges (three or more) -- plus edges two
    neighbours walk the same way (inconsistent winding)."""
    D, per_edge, per_dir = _edges(F)
    edge = lambda mask: len(np.unique(np.sort(D[mask], axis=1), axis=0))
    return {"triangles": len(F), "open_edges": edge(per_edge == 1),
            "nonmanifold_edges": edge(per_edge > 2), "flipped_edges": edge(per_dir > 1)}


def bad_faces(F):
    """Triangles touching an open or non-manifold edge, or wound against a neighbour."""
    _, per_edge, per_dir = _edges(F)
    bad = (per_edge != 2) | (per_dir > 1)
    return bad.reshape(3, -1).any(axis=0)


def boundary_loops(F):
    """The holes in F as closed vertex loops, following the triangles' own
    edge direction; None unless every hole is a simple loop (no pinched
    vertex, no non-manifold edge anywhere)."""
    D, per_edge, per_dir = _edges(F)
    if (per_edge > 2).any() or (per_dir > 1).any():
        return None
    nxt = {}
    for a, b in D[per_edge == 1]:
        if a in nxt:
            return None
        nxt[a] = b
    loops, seen = [], set()
    for start in nxt:
        if start in seen:
            continue
        loop, v = [], start
        while v not in seen:
            seen.add(v)
            loop.append(v)
            v = nxt.get(v)
            if v is None:
                return None
        if v != start:
            return None
        loops.append(loop)
    return loops


def fill(V, loop, surface=None, max_area=MAX_PATCH_AREA):
    """Close one hole: (new vertices, triangles), triangles indexing V then the
    new vertices. None when the hole is not a height field in its own plane.

    The patch runs against the loop, since the loop follows the neighbouring
    triangles' edges. Its plane is the loop's Newell normal; the outline is
    triangulated there with interior points about `max_area` apart and no new
    point on the outline, so the seam stays watertight. Each new point takes
    its height from `surface` -- what was cut out -- by a ray cast from
    outside along the normal.
    """
    L = np.asarray(loop[::-1])
    P = V[L]
    Q = np.roll(P, -1, axis=0)
    n = np.array([np.sum((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2])),
                  np.sum((P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0])),
                  np.sum((P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1]))])
    if not np.linalg.norm(n):
        return None
    n /= np.linalg.norm(n)
    a = np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    c = P.mean(axis=0)
    uv = np.c_[(P - c) @ a, (P - c) @ b]
    if not Polygon(uv).is_valid:
        return None
    tris, pts = _mesh_polygon(uv, max_area)
    if tris is None:
        return None
    extra = pts[len(L):]
    h = np.zeros(len(extra))
    if len(extra) and surface is not None and len(surface.faces):
        base = c + extra[:, :1] * a + extra[:, 1:] * b
        reach = np.ptp(surface.vertices, axis=0).max() + 1.0
        hits, ray, _ = surface.ray.intersects_location(base + n * reach, np.tile(-n, (len(base), 1)))
        # the outermost hit along each ray: the skin, not whatever is behind it
        h.fill(np.nan)
        np.fmax.at(h, ray, (hits - c) @ n)
        h = np.nan_to_num(h, nan=0.0)
    new = c + extra[:, :1] * a + extra[:, 1:] * b + h[:, None] * n
    ids = np.r_[L, len(V) + np.arange(len(extra))]
    return new, ids[tris]


def _mesh_polygon(uv, max_area):
    """Triangles over the polygon `uv` whose only outline vertices are its
    own, plus interior points so no triangle is much over `max_area`:
    (triangles, points), or (None, None).

    The outline cannot gain a vertex -- its edges are shared with the triangles
    that stay -- and some of its edges are long (a fan of thin triangles on a
    flat base leaves 7 mm edges behind). So: ear-clip the outline, flip to a
    constrained Delaunay triangulation, insert a grid of interior points one at
    a time, and re-flip after each. Splitting ear-clipped triangles at their
    centroids instead is simpler and cannot fail, but leaves slivers along the
    long edges that cut the corner off a curved surface: 0.24 mm from the old
    skin against 0.04 on the head this was written for. It is kept as the
    fallback for when the flipping does not settle.
    """
    n = len(uv)
    tri = earcut.triangulate_float64(uv, np.array([n], dtype=np.uint32)).reshape(-1, 3)
    if len(tri) != n - 2:
        return None, None
    tri = [list(t) if _area2(uv[t]) > 0 else list(t[::-1]) for t in tri]       # CCW faces +n
    poly = Polygon(uv)
    step = np.sqrt(2 * max_area)
    lo, hi = uv.min(axis=0), uv.max(axis=0)
    # staggered rows: a square grid is four points on every circle, which is
    # the degenerate case for the Delaunay test
    rows = np.arange(lo[1] + step / 2, hi[1], step * np.sqrt(3) / 2)
    grid = np.array([(x + (step / 2 if r % 2 else 0), y) for r, y in enumerate(rows)
                     for x in np.arange(lo[0] + step / 2, hi[0], step)]).reshape(-1, 2)
    if len(grid):
        grid = grid[shapely.contains_xy(poly, grid[:, 0], grid[:, 1])]
        grid = grid[shapely.distance(poly.exterior, shapely.points(grid)) > step / 2]
    out = _cdt(np.vstack([uv, grid]), n, tri)
    if out is not None:
        return out
    pts, todo, tris = list(uv), tri, []
    while todo:
        t = todo.pop()
        tp = np.array([pts[i] for i in t])
        if _area2(tp) / 2 <= max_area:
            tris.append(t)
            continue
        pts.append(tp.mean(axis=0))
        k = len(pts) - 1
        todo += [[t[0], t[1], k], [t[1], t[2], k], [t[2], t[0], k]]
    return np.array(tris), np.array(pts)


def _cdt(pts, n, tri, max_flips=200000):
    """Insert pts[n:] into the triangulation `tri` of the outline pts[:n],
    keeping it constrained-Delaunay; None if it does not settle cleanly."""
    fixed = {frozenset((i, (i + 1) % n)) for i in range(n)}
    tris, owner = [], {}                       # directed edge -> triangle index

    def put(t, k=None):
        if k is None:
            k = len(tris)
            tris.append(t)
        else:
            tris[k] = t
        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            owner[e] = k
        return k

    def incircle(a, b, c, d):
        m = np.array([pts[a] - pts[d], pts[b] - pts[d], pts[c] - pts[d]])
        m = np.c_[m, (m ** 2).sum(axis=1)]
        return np.linalg.det(m)

    flips = [0]

    def legalize(stack):
        while stack:
            a, b = stack.pop()
            if frozenset((a, b)) in fixed or (a, b) not in owner or (b, a) not in owner:
                continue
            t, u = tris[owner[(a, b)]], tris[owner[(b, a)]]
            p = next(v for v in t if v not in (a, b))
            q = next(v for v in u if v not in (a, b))
            scale = max(np.ptp(pts[[a, b, p, q]], axis=0)) ** 4
            if incircle(a, b, p, q) <= 1e-12 * scale:
                continue
            flips[0] += 1
            if flips[0] > max_flips:
                raise RuntimeError
            kt, ku = owner.pop((a, b)), owner.pop((b, a))
            put([a, q, p], kt)
            put([q, b, p], ku)
            stack += [(a, q), (q, b)]

    for t in tri:
        put(t)
    try:
        legalize([(t[i], t[(i + 1) % 3]) for t in tri for i in range(3)])
        for k in range(n, len(pts)):
            T = np.array(tris)
            A, B, C = pts[T[:, 0]], pts[T[:, 1]], pts[T[:, 2]]
            x = pts[k]
            side = lambda P, Q: (Q[:, 0] - P[:, 0]) * (x[1] - P[:, 1]) - (Q[:, 1] - P[:, 1]) * (x[0] - P[:, 0])
            s = np.c_[side(A, B), side(B, C), side(C, A)]
            eps = 1e-12 * max(np.ptp(pts, axis=0)) ** 2
            inside = np.flatnonzero((s > eps).all(axis=1))
            if len(inside) == 1:
                a, b, c = tris[inside[0]]
                for e in ((a, b), (b, c), (c, a)):
                    owner.pop(e)
                put([a, b, k], inside[0])
                put([b, c, k])
                put([c, a, k])
                legalize([(a, b), (b, c), (c, a)])
                continue
            # on an edge shared by two triangles: split both
            on = np.flatnonzero((s > -eps).all(axis=1))
            if len(on) != 2:
                return None
            t1 = tris[on[0]]
            e = int(np.argmin(np.abs(s[on[0]])))
            a, b, c = t1[e], t1[(e + 1) % 3], t1[(e + 2) % 3]
            if frozenset((a, b)) in fixed or owner.get((b, a)) != on[1]:
                return None
            d = next(v for v in tris[on[1]] if v not in (a, b))
            for t in (tris[on[0]], tris[on[1]]):
                for f in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                    owner.pop(f)
            put([a, k, c], on[0])
            put([b, k, d], on[1])
            put([k, b, c])
            put([k, a, d])
            legalize([(b, c), (c, a), (a, d), (d, b)])
    except RuntimeError:
        return None
    T = np.array(tris)
    if len(T) != n + 2 * (len(pts) - n) - 2 or min(_area2(pts[t]) for t in T) <= 0:
        return None
    return T, pts


def _area2(t):
    return (t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) - (t[2, 0] - t[0, 0]) * (t[1, 1] - t[0, 1])


@dataclass
class Repair:
    V: np.ndarray          # vertices in use, renumbered
    F: np.ndarray
    origin: np.ndarray     # per output triangle: the input triangle it is, or -1 if new
    radius_mm: float       # cut radius that isolated the defects
    removed: int
    added: int
    deviation_mm: dict     # how far the cut-out surface is from the patch: max, mean


def repair(V, F, *, r0=0.05, dr=0.05, r_max=3.0, max_area=MAX_PATCH_AREA, samples=20000):
    """Cut and refill until no defect is left; raises if r_max is not enough.
    A mesh with no defects comes back as it went in.

    The patch is a height field, so a hole that wraps a sharp edge comes back
    chamfered. That is the limit of "local": on a curved or flat skin, which
    is where download damage sits, the patch is within a few hundredths of a
    millimetre of what it replaced.
    """
    bad = bad_faces(F)
    if not bad.any():
        return Repair(V, F, np.arange(len(F)), 0.0, 0, 0, {"max": 0.0, "mean": 0.0})
    dist = cKDTree(V[F[bad]].mean(axis=1)).query(V)[0]
    r = r0
    while r <= r_max + 1e-9:
        cut = bad | (dist < r)[F].any(axis=1)
        loops = boundary_loops(F[~cut])
        if loops is not None:
            # heights come from everything cut out, junk included: the junk
            # is where the skin was, and leaving it out lets rays through
            surface = trimesh.Trimesh(V, F[cut], process=False)
            fills = [fill(V, lp, surface, max_area) for lp in loops]
            if all(f is not None for f in fills):
                out = _assemble(V, F, cut, fills)
                if not bad_faces(out[1]).any():
                    return _finish(V, F, cut, bad, out, r, samples)
        r += dr
    raise ValueError(f"defects not isolated within {r_max} mm; this is not a local repair")


def _assemble(V, F, cut, fills):
    newV, patches, offset = [V], [], len(V)
    for pts, tris in fills:
        # fill() numbers its new vertices from len(V); shift past earlier patches
        tris = np.where(tris >= len(V), tris - len(V) + offset, tris)
        newV.append(pts)
        patches.append(tris)
        offset += len(pts)
    F2 = np.vstack([F[~cut]] + patches)
    origin = np.r_[np.flatnonzero(~cut), np.full(sum(len(p) for p in patches), -1)]
    return np.vstack(newV), F2, origin


def _finish(V, F, cut, bad, out, r, samples):
    V2, F2, origin = out
    used = np.unique(F2)
    renum = np.full(len(V2), -1)
    renum[used] = np.arange(len(used))
    V3, F3 = V2[used], renum[F2]
    dev = {"max": 0.0, "mean": 0.0}
    old = F[cut & ~bad]
    patch = F2[origin < 0]
    if len(old) and len(patch):
        pts, _ = trimesh.sample.sample_surface(trimesh.Trimesh(V, old, process=False), samples, seed=0)
        _, d, _ = trimesh.proximity.closest_point(trimesh.Trimesh(V2, patch, process=False), pts)
        dev = {"max": round(float(d.max()), 4), "mean": round(float(d.mean()), 4)}
    return Repair(V3, F3, origin, round(r, 3), int(cut.sum()), int((origin < 0).sum()), dev)
