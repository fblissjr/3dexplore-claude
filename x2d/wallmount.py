"""Turn a printed object into a wall-mounted one: the face it prints on goes
against the wall.

The bed face is the one flat face every FDM part has, and on many desk
objects it is a side that already stands upright on the desk (a stand printed
lying down), so putting it on the wall keeps the object's use unchanged. What
changes is what the wall makes useless (a desk foot), and how it holds: screws
through the plate the object prints on, or adhesive strips on its back.

Everything here works in one **wall frame**: the bed plane seen from the room,
`X` along the wall and `U` up it, origin at the centre of the part's
footprint. `up_deg` says which bed direction is up on the wall, measured
counter-clockwise from the bed's +X, so a slicer's top view of the plate is the
wall seen from the room, turned by that angle.

Edits are booleans (manifold3d), which renumber triangles. That is fine on an
unpainted part and loses the designer's work on a painted one, so `build`
refuses paint (DESIGN.md, "Someone else's project").
"""
from dataclasses import dataclass

import manifold3d as mf
import numpy as np
import trimesh
from shapely import Point, Polygon, affinity, box
from shapely.ops import unary_union

__all__ = ["Frame", "Screw", "bed_face", "plate_top", "footprint_above", "screw_spots",
           "build", "strip_fit", "front_view"]


@dataclass(frozen=True)
class Frame:
    """Bed xy <-> wall (X, U)."""
    up_deg: float
    origin: tuple = (0.0, 0.0)

    @property
    def axes(self):
        a = np.radians(self.up_deg)
        up = np.array([np.cos(a), np.sin(a)])
        return np.array([up[1], -up[0]]), up            # wall X is up turned clockwise

    def to_wall(self, xy):
        x_ax, u_ax = self.axes
        d = np.asarray(xy, float) - self.origin
        return np.stack([d @ x_ax, d @ u_ax], axis=-1)

    def to_bed(self, XU):
        x_ax, u_ax = self.axes
        XU = np.asarray(XU, float)
        return XU[..., :1] * x_ax + XU[..., 1:2] * u_ax + self.origin

    def polygon(self, poly):
        """A bed-plane polygon in wall coordinates."""
        x_ax, u_ax = self.axes
        ox, oy = self.origin
        return affinity.affine_transform(
            poly, [x_ax[0], x_ax[1], u_ax[0], u_ax[1], -(ox * x_ax[0] + oy * x_ax[1]), -(ox * u_ax[0] + oy * u_ax[1])])

    @classmethod
    def for_mesh(cls, mesh, up_deg):
        lo, hi = mesh.bounds[:, :2]
        return cls(up_deg, tuple((lo + hi) / 2))


@dataclass(frozen=True)
class Screw:
    """A countersunk screw and the boss that seats it. Defaults fit a #6 (or
    M3.5-M4) flat head: 4.2 mm clearance, 8.5 mm head at 90 degrees."""
    shaft_d: float = 4.2
    head_d: float = 8.5
    boss_r: float = 6.0          # leaves 1.75 mm of wall round the head
    boss_h: float = 3.0          # on top of the plate; head depth is (head - shaft) / 2


def _section(mesh, z):
    path = mesh.section([0, 0, 1], [0, 0, z])
    if path is None:
        return Polygon()
    p, M = path.to_2D()
    return unary_union([affinity.affine_transform(q, [*M[0, :2], *M[1, :2], M[0, 3], M[1, 3]])
                        for q in p.polygons_full])


def bed_face(mesh, depth=0.05):
    """What touches the bed, taken just above it so a first-layer chamfer is
    already counted out: that is the flat area a wall or a strip actually meets."""
    return _section(mesh, mesh.bounds[0, 2] + depth)


def plate_top(mesh, min_frac=0.02):
    """Height of the plate the part prints on: the lowest upward-facing flat
    with real area (2% of the bed face). Screw bosses stand on it, and
    trimming starts there, so the plate itself is never cut."""
    n, c, area = mesh.face_normals, mesh.triangles_center, mesh.area_faces
    z0 = mesh.bounds[0, 2]
    flat = (n[:, 2] > 0.999) & (c[:, 2] > z0 + 1e-3)
    if not flat.any():
        raise ValueError("no upward-facing flat above the bed: the part has no plate to mount by")
    zs = np.round(c[flat, 2], 3)
    need = min_frac * bed_face(mesh).area
    for z in np.unique(zs):
        if area[flat][zs == z].sum() >= need:
            return float(z)
    raise ValueError("no upward-facing flat above the bed is big enough to be a plate")


def footprint_above(mesh, z):
    """Everything above height z, projected onto the bed. Exact (a projection
    of the cut mesh, not a stack of sections), because a screw needs a straight
    line for the driver from its head to the room, and a lip high up blocks it
    as surely as a wall beside the plate."""
    top = mesh.slice_plane([0, 0, z + 1e-3], [0, 0, 1], cap=False)
    tris = [Polygon(t[:, :2]) for t in top.triangles]
    return unary_union([t for t in tris if t.area > 1e-9])


def screw_spots(mesh, frame, screw=Screw(), *, z_plate=None, edge=0.5, clear=1.0, step=0.25):
    """The level pair of screw centres (wall X, U) furthest apart, each boss
    `edge` mm inside the bed face and `clear` mm from anything above the plate.

    Level, because then the holes are marked with a spirit level rather than a
    template. Widest, because spacing is what resists the object twisting off
    the wall when someone pulls the thing it holds."""
    z_plate = plate_top(mesh) if z_plate is None else z_plate
    ok = frame.polygon(bed_face(mesh)).buffer(-(screw.boss_r + edge))
    ok = ok.difference(frame.polygon(footprint_above(mesh, z_plate)).buffer(screw.boss_r + clear))
    if ok.is_empty:
        raise ValueError("no room for a screw boss on the plate. Anything standing on it blocks, even a "
                         "fillet sliver left along a trim line: move the trim up past it (the survey's "
                         "'above the plate' panel shows what blocks), shrink --boss-r, or use strips")
    best = None
    x0, u0, x1, u1 = ok.bounds
    for u in np.arange(np.floor(u0 / step) * step, u1 + step, step):
        line = ok.intersection(box(x0 - 1, u - 1e-6, x1 + 1, u + 1e-6))
        if line.is_empty:
            continue
        a, _, b, _ = line.bounds
        if best is None or b - a > best[1] - best[0] + 1e-9:
            best = (a + 1e-3, b - 1e-3, u)
    a, b, u = best
    if b - a < 2 * screw.boss_r:
        raise ValueError("room for one screw boss only (see the survey's 'above the plate' panel): "
                         "pass --holes to place them by hand, or use strips")
    return [(a, u), (b, u)]


def _solid(mesh):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                               tri_verts=np.asarray(mesh.faces, np.uint32)))


def _prism(poly, z0, z1):
    """A wall-frame-independent prism: `poly` is already in bed coordinates."""
    rings = [np.asarray(poly.exterior.coords[:-1])] + [np.asarray(r.coords[:-1]) for r in poly.interiors]
    return mf.Manifold.extrude(mf.CrossSection(rings), z1 - z0).translate([0, 0, z0])


def build(mesh, frame, *, trim=(), holes=None, screw=Screw(), mirror=False, painted=0):
    """The wall-mount version of `mesh` (bed coordinates). Returns
    (trimesh, report).

    trim: wall-frame polygons [(X, U), ...]; whatever stands on the plate
    inside them is cut away, the plate stays. holes: wall-frame screw centres,
    or None to place a level pair with `screw_spots`, or [] for none (strips
    only). mirror: reflect across the wall's vertical axis, so a part that
    faces one way along the wall faces the other."""
    if painted:
        raise ValueError(f"{painted} painted triangles: a boolean renumbers triangles and would "
                         "drop the paint. Repaint after, or edit a copy with the paint removed.")
    z_bed, z_top = mesh.bounds[0, 2], mesh.bounds[1, 2]
    z_plate = plate_top(mesh)
    solid = _solid(mesh)
    v0 = solid.volume()
    for poly in trim:
        bed = Polygon(frame.to_bed(np.asarray(poly, float)))
        solid = solid - _prism(bed if bed.exterior.is_ccw else bed.reverse(), z_plate, z_top + 1)
    cut = trimesh.Trimesh(*_arrays(solid), process=False)
    if holes is None:
        holes = screw_spots(cut, frame, screw, z_plate=z_plate)
    sink = (screw.head_d - screw.shaft_d) / 2
    z_head = z_plate + screw.boss_h
    for X, U in holes:
        x, y = frame.to_bed([X, U])
        solid = solid + mf.Manifold.cylinder(screw.boss_h + 0.5, screw.boss_r, screw.boss_r, 64).translate([x, y, z_plate - 0.5])
        solid = solid - (mf.Manifold.cylinder(z_head - z_bed + 2, screw.shaft_d / 2, screw.shaft_d / 2, 48).translate([x, y, z_bed - 1])
                         + mf.Manifold.cylinder(sink, screw.shaft_d / 2, screw.head_d / 2, 48).translate([x, y, z_head - sink])
                         + mf.Manifold.cylinder(1, screw.head_d / 2, screw.head_d / 2, 48).translate([x, y, z_head]))
    out = trimesh.Trimesh(*_arrays(solid), process=False)
    if mirror:
        x_ax, _ = frame.axes
        V = out.vertices.copy()
        d = (V[:, :2] - frame.origin) @ x_ax
        V[:, :2] -= 2 * d[:, None] * x_ax
        out = trimesh.Trimesh(V, out.faces[:, ::-1], process=False)
        holes = [(-X, U) for X, U in holes]
    XU = frame.to_wall(out.vertices[:, :2])
    report = {
        "plate_top_mm": round(z_plate - z_bed, 2),
        "size_on_wall_mm": {"wide": round(float(np.ptp(XU[:, 0])), 1), "tall": round(float(np.ptp(XU[:, 1])), 1),
                            "out_from_wall": round(float(z_top - z_bed), 1)},
        "holes_wall_mm": [[round(X, 2), round(U, 2)] for X, U in holes],
        "hole_spacing_mm": round(float(np.hypot(*np.subtract(holes[1], holes[0]))), 1) if len(holes) == 2 else None,
        "holes_above_bottom_mm": [round(U - float(XU[:, 1].min()), 1) for _, U in holes],
        "volume_mm3": [round(v0, 1), round(solid.volume(), 1)],
        "triangles": [len(mesh.faces), len(out.faces)],
        "watertight": bool(out.is_watertight),
        "through_holes_added": int((mesh.euler_number - out.euler_number) // 2),
    }
    return out, report


def _arrays(solid):
    m = solid.to_mesh()
    return np.asarray(m.vert_properties, float)[:, :3], np.asarray(m.tri_verts, np.int64)


def strip_fit(mesh, frame, widths=(16, 19), *, edge=1.0, step=1.0, angles=range(-90, 91, 5)):
    """{strip width: (longest length, (X, U, angle))} that fits on the bed face.

    Adhesive strips need flat back, and a back that looks big can be mostly
    hole: the watch stand's ring is open in the middle and its rim is 9 mm
    wide, narrower than any picture strip. Measure before recommending one."""
    face = frame.polygon(bed_face(mesh)).buffer(-edge)
    x0, u0, x1, u1 = face.bounds
    out = {}
    for w in widths:
        best, where = 0.0, None
        for ang in angles:
            for X in np.arange(x0, x1, step):
                for U in np.arange(u0, u1, step):
                    if not face.contains(Point(X, U)):
                        continue
                    rect = lambda L: affinity.rotate(box(X - L / 2, U - w / 2, X + L / 2, U + w / 2), ang, origin=(X, U))
                    if not face.contains(rect(best + 0.5)):
                        continue
                    lo, hi = best + 0.5, max(x1 - x0, u1 - u0) * 1.5
                    while hi - lo > 0.25:
                        mid = (lo + hi) / 2
                        lo, hi = (mid, hi) if face.contains(rect(mid)) else (lo, mid)
                    best, where = lo, (round(float(X), 1), round(float(U), 1), ang)
        # turned 90 degrees, a strip's short side can lie along the patch; a
        # strip shorter than it is wide is not a strip
        out[w] = (round(best, 1), where) if best >= w else (0.0, None)
    return out


def front_view(mesh, frame, path, *, holes=(), strips=None, title=None, ppmm=5):
    """The part as it hangs, seen from the room, on a 10 mm grid: holes,
    their spacing, and where a strip fits. Painter's algorithm, no GPU."""
    from PIL import Image, ImageDraw, ImageFont
    from .fonts import bold_font
    font = lambda s: ImageFont.truetype(bold_font(), s)
    z0 = mesh.bounds[0, 2]
    W = np.c_[frame.to_wall(mesh.vertices[:, :2]), mesh.vertices[:, 2] - z0]
    lo, hi = W.min(0), W.max(0)
    pad = 14
    size = (max(560, int((hi[0] - lo[0] + 2 * pad) * ppmm)), int((hi[1] - lo[1] + 2 * pad) * ppmm) + 40)
    im = Image.new("RGB", size, (250, 249, 252))
    d = ImageDraw.Draw(im)
    P = lambda X, U: ((X - lo[0] + pad) * ppmm, (hi[1] - U + pad) * ppmm + 40)
    for g in np.arange(np.floor(lo[0] / 10) * 10, hi[0] + 10, 10):
        d.line([P(g, lo[1] - pad), P(g, hi[1] + pad)], fill=(232, 228, 240))
    for g in np.arange(np.floor(lo[1] / 10) * 10, hi[1] + 10, 10):
        d.line([P(lo[0] - pad, g), P(hi[0] + pad, g)], fill=(232, 228, 240))
    tri = W[mesh.faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None] + 1e-12
    light = np.array([-0.4, 0.6, 0.7]) / np.linalg.norm([-0.4, 0.6, 0.7])
    for i in np.argsort(tri[:, :, 2].mean(1)):
        if n[i, 2] < -0.05:
            continue
        g = int(150 + 100 * max(0.0, n[i] @ light))
        d.polygon([P(*p[:2]) for p in tri[i]], fill=(g, g, g - 4))
    blue = (25, 118, 210)
    for X, U in holes:
        x, y = P(X, U)
        d.ellipse((x - 9, y - 9, x + 9, y + 9), outline=blue, width=3)
    if len(holes) == 2:
        (a, ua), (b, ub) = sorted(holes)
        y = P(0, lo[1])[1] + 22
        d.line([(P(a, 0)[0], y), (P(b, 0)[0], y)], fill=blue, width=2)
        d.text(((P(a, 0)[0] + P(b, 0)[0]) / 2, y + 4), f"{np.hypot(b - a, ub - ua):.0f} mm" + (", level" if abs(ua - ub) < 0.01 else ""),
               font=font(16), fill=blue, anchor="ma")
    if strips:
        w, (L, (X, U, ang)) = max(strips.items(), key=lambda kv: kv[1][0] * kv[0])
        r = affinity.rotate(box(X - L / 2, U - w / 2, X + L / 2, U + w / 2), ang, origin=(X, U))
        d.polygon([P(*c) for c in list(r.exterior.coords)[:-1]], outline=blue, width=2)
    d.text((10, 8), title or f"{hi[0] - lo[0]:.0f} mm wide x {hi[1] - lo[1]:.0f} mm tall, "
           f"{hi[2] - lo[2]:.0f} mm out from the wall; 1 square = 10 mm", font=font(13), fill=(33, 33, 33))
    im.save(path)
    return path
