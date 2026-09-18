"""Image and text to polygons to solids. No printer knowledge lives here.

Geometry is data: an image becomes a mask, a mask becomes polygons in mm,
polygons become solids. Every stage is inspectable and swappable.
"""
import cv2
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from PIL import Image, ImageDraw, ImageFont

PX_PER_MM = 50            # raster resolution: 0.02 mm/px, well under a nozzle


# ------------------------------------------------------------ image -> 2D ---
# Denoising before Otsu is the one knob here that is NOT purely a fraction of
# the picture, and the distinction is worth keeping straight. Sensor noise is a
# per-pixel phenomenon, so a fixed kernel is the right shape for it -- scaling
# it down on a small image just lets the speckle through (measured: a 300 px
# noisy logo goes from 2 blobs to 9). What a fixed kernel gets wrong is the
# other end: on a very large scan the grain is several pixels across and 3 is
# under-smoothing. So: a floor that preserves the tuned behaviour, and a
# fraction that only ever takes over above it.
BLUR_MIN_PX = 3
BLUR_FRAC = 0.0016


def silhouette(path, *, invert=False, threshold=None, blur=None):
    """Image -> binary mask of the figure.

    `blur` is a Gaussian kernel in PIXELS, defaulting to BLUR_MIN_PX or
    BLUR_FRAC of the image width, whichever is larger. Pass an int to override,
    0 to skip. See the note on those constants for why this one is a floor
    rather than a pure fraction like everything in detail().
    """
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    # An alpha channel is a better silhouette than any luminance guess.
    if img.ndim == 3 and img.shape[2] == 4 and img[:, :, 3].min() < 250:
        mask = (img[:, :, 3] > 127).astype(np.uint8) * 255
    else:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        if blur is None:
            blur = max(BLUR_MIN_PX, int(round(BLUR_FRAC * gray.shape[1])))
        if blur:
            gray = cv2.GaussianBlur(gray, (blur | 1, blur | 1), 0)
        if threshold is None:
            _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    if invert:
        mask = 255 - mask
    return mask


def detail(path, *, feature="creases", sigma=0.035, depth=8, inset=None,
           open_frac=0.004, close_frac=0.013):
    """Interior detail pulled out of shading, as a mask.

    A silhouette throws away everything inside the outline. That is the right
    answer for a logo and the wrong one for a photographed object whose whole
    interest is in creases and folds -- a pleated bun, a carved surface, a
    drawing with interior lines. There the shape is the backer and the shading
    is the artwork, so we need a second mask.

    The method is local contrast: a pixel is detail when it sits `depth` grey
    levels away from its own neighbourhood. Blurring at `sigma` defines what a
    neighbourhood means, which is what makes this ignore the broad lighting
    falloff across a curved object and keep only the features riding on it.

    `feature` picks which side of the neighbourhood counts, and the honest
    answer depends on what the picture is of:

      "creases"  darker than surroundings -- ink on paper, engraved lines, the
                 shadow side of a fold. Raising these is right for a drawing,
                 where the mark *is* the dark thing.
      "ridges"   lighter than surroundings -- the lit crest of a fold, an
                 embossed edge. Raising these is right for a photographed
                 object, because the part catching the light is the part that
                 physically stands proud.

    Get this backwards and the relief is inside out: you raise the valleys and
    sink the peaks. It still looks like a pattern, which is why it is easy to
    miss.

    `inset` is how much of the rim to exclude, and it defaults per feature for
    a physical reason: the curved edge of a lit object is itself a highlight,
    so ridge detection finds a bright arc all round the silhouette that is
    lighting, not shape. Creases have no such twin -- the rim is not dark --
    so they can be read much closer to the edge.

    Every knob is a fraction of the figure's own size, so feeding in a larger
    photograph of the same object gives the same result rather than a finer,
    noisier one.
    """
    if feature not in ("creases", "ridges"):
        raise ValueError(f"feature must be 'creases' or 'ridges', not {feature!r}")
    if inset is None:
        inset = 0.085 if feature == "ridges" else 0.025

    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    mask = silhouette(path)
    bgr = img[:, :, :3] if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Scale every kernel off the figure, not the canvas: a mask that fills a
    # quarter of the frame should not get quarter-sized creases.
    span = max(np.sqrt((mask > 0).sum()), 1.0)
    px = lambda frac: max(int(round(frac * span)), 1)
    odd = lambda n: n | 1

    # The rim is a silhouette edge, not a crease. Keep detail off it.
    inner = cv2.erode(mask, np.ones((odd(px(inset)),) * 2, np.uint8))

    bg = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), px(sigma))
    contrast = gray.astype(np.float32) - bg
    hit = contrast > depth if feature == "ridges" else contrast < -depth
    lit = cv2.bitwise_and(hit.astype(np.uint8) * 255, inner)
    lit = cv2.morphologyEx(lit, cv2.MORPH_OPEN, np.ones((odd(px(open_frac)),) * 2, np.uint8))
    lit = cv2.morphologyEx(lit, cv2.MORPH_CLOSE, np.ones((odd(px(close_frac)),) * 2, np.uint8))
    return lit


def mask_to_polygons(mask, *, width_mm, simplify_mm=0.15, min_area_mm2=1.0):
    """Binary mask -> shapely polygons in mm, holes preserved, y flipped."""
    cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return MultiPolygon()
    hier = hier[0]
    h, w = mask.shape[:2]
    s = width_mm / w                      # px -> mm
    to_mm = lambda c: [(x * s, (h - y) * s) for x, y in c[:, 0, :]]

    polys = []
    for i, cnt in enumerate(cnts):
        if hier[i][3] != -1 or len(cnt) < 3:
            continue                       # holes are collected via children
        holes = []
        child = hier[i][2]
        while child != -1:
            if len(cnts[child]) >= 3:
                holes.append(to_mm(cnts[child]))
            child = hier[child][0]
        p = Polygon(to_mm(cnt), holes).buffer(0)   # buffer(0) repairs self-touch
        if not p.is_empty:
            polys.append(p)

    geom = unary_union(polys).simplify(simplify_mm)
    geom = MultiPolygon([geom]) if isinstance(geom, Polygon) else geom
    keep = [p for p in geom.geoms if p.area >= min_area_mm2]
    return MultiPolygon(keep)


# ------------------------------------------------------------ 2D -> solid ---
def extrude(geom, height, z=0.0):
    meshes = [_extrude_one(p, height) for p in each(geom)]
    m = trimesh.util.concatenate(meshes)
    m.apply_translation([0, 0, z])
    return m


def _extrude_one(p, height):
    """One polygon -> watertight prism.

    trimesh's earcut path is fast and right nearly always, but on a traced
    raster outline it occasionally emits an open mesh for a polygon shapely
    calls valid (near-collinear runs along a pixel edge, a hole a hair from
    the rim). write_3mf refuses those, correctly. manifold3d is already a
    dependency and its extrusion is manifold by construction, so use it as
    the fallback rather than nudging the polygon until earcut behaves --
    a nudge moves the seam that separate() just lined up.
    """
    m = trimesh.creation.extrude_polygon(p, height)
    if m.is_watertight:
        return m
    import manifold3d as m3d
    rings = [np.asarray(p.exterior.coords)[:-1]] + [np.asarray(h.coords)[:-1] for h in p.interiors]
    cs = m3d.CrossSection([r.tolist() for r in rings], m3d.FillRule.EvenOdd)
    mesh = cs.extrude(height).to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3],
                           np.asarray(mesh.tri_verts), process=False)


def each(geom):
    return geom.geoms if isinstance(geom, MultiPolygon) else [geom]


# ------------------------------------------------------------ plate shapes ---
def thicken(geom, mm):
    """Grow every piece outward, so features survive the nozzle.

    Detail pulled from shading has no idea how wide an extrusion is: a crease
    two pixels across is real in the photograph and gone in the print. Growing
    by half the minimum printable feature is the cheapest honest fix -- the
    lines get slightly fatter and they all actually appear.
    """
    if not mm:
        return geom
    grown = unary_union([p.buffer(mm, join_style=1, quad_segs=8) for p in each(geom)])
    return MultiPolygon([grown]) if isinstance(grown, Polygon) else grown


def solid_backer(geom, margin):
    """Outer hull of the artwork, grown by margin, holes filled."""
    grown = unary_union([Polygon(p.exterior) for p in each(geom)]).buffer(
        margin, join_style=1, quad_segs=16)
    return unary_union([Polygon(p.exterior) for p in each(grown)])


def rounded_plate(geom, margin, radius):
    x0, y0, x1, y1 = geom.bounds
    r = min(radius, margin + min(x1 - x0, y1 - y0) / 4)
    return box(x0 - margin + r, y0 - margin + r,
               x1 + margin - r, y1 + margin - r).buffer(r, join_style=1, quad_segs=24)


def render_text(text, font_path, cap_mm):
    """Text -> black-on-white mask, tight-cropped, at PX_PER_MM."""
    size = int(cap_mm * PX_PER_MM * 1.45)          # em box vs cap height
    f = ImageFont.truetype(font_path, size)
    pad = size
    tmp = Image.new("L", (size * len(text) * 2 + pad * 2, size * 3), 255)
    ImageDraw.Draw(tmp).text((pad, pad), text, font=f, fill=0)
    return tmp.crop(tmp.point(lambda v: 255 - v).getbbox())
