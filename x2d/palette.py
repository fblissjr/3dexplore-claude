"""Flat-color artwork -> one polygon set per filament slot.

For emoji, logos, icons: art that is already a handful of solid colors. The
mosaic module quantises a photograph onto a grid; this module keeps the
artist's edges and only decides which slot each region prints in.

Geometry is data: image -> label map (one slot per pixel) -> {slot: polygons}.
The color->slot table is the whole design decision, and it is an input.
"""
import cv2
import numpy as np
from PIL import Image, ImageColor
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .geometry import mask_to_polygons, each


def _lab(hexes):
    rgb = np.array([ImageColor.getrgb(h) for h in hexes], dtype=np.float32) / 255
    return cv2.cvtColor(rgb[None], cv2.COLOR_RGB2LAB)[0]


def label_map(image, mapping, *, alpha_cutoff=128):
    """RGBA image + {"#RRGGBB": slot} -> int array of slots, -1 = transparent.

    Every opaque pixel goes to the nearest listed color in Lab, so anti-aliased
    edge pixels fall to one side or the other instead of becoming a third,
    unlisted "color". Source colors you leave out of the table still land on
    their nearest listed neighbour -- a shading tint joins its parent color
    without being named.
    """
    image = image.convert("RGBA")
    px = np.asarray(image)
    lab = cv2.cvtColor(px[:, :, :3].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    keys = list(mapping)
    nearest = ((lab[:, :, None] - _lab(keys)) ** 2).sum(axis=3).argmin(axis=2)
    slots = np.array([mapping[k] for k in keys])[nearest]
    slots[px[:, :, 3] < alpha_cutoff] = -1
    return slots


def separate(image, mapping, *, width_mm, simplify_mm=0.12, min_area_mm2=0.5,
             seam_mm=None, priority=None):
    """-> {slot: MultiPolygon} in mm, tiled with no gaps and no overlaps.

    Contours traced per slot leave a hairline between neighbours: each region's
    outline runs through its own edge pixels, so two touching colors end up
    about one pixel apart. Adjacent parts of one object must share a boundary,
    so each slot is grown by `seam_mm` and the slots already claimed are then
    cut out of it, in `priority` order. Put the small features first -- eyes, a
    mouth -- so the big fill around them is what yields, not the detail.

    `seam_mm` defaults to one and a half pixels of the image you actually
    passed, because that hairline is a property of the raster and nothing else.
    A fixed constant would be silently too small on a coarse image, which does
    not fail -- it prints a plate with gaps between the colors. The cost of the
    growth is that the artwork's outer edge also moves out by that much, which
    is why the default is tied to the pitch rather than made generous.
    """
    if seam_mm is None:
        seam_mm = 1.5 * width_mm / image.width
    labels = label_map(image, mapping)
    order = list(priority) if priority else sorted(set(mapping.values()))
    if set(order) != set(mapping.values()):
        raise ValueError("priority must list every slot in the mapping exactly once")
    claimed, out = None, {}
    for slot in order:
        mask = (labels == slot).astype(np.uint8) * 255
        geom = mask_to_polygons(mask, width_mm=width_mm, simplify_mm=simplify_mm,
                                min_area_mm2=min_area_mm2)
        if geom.is_empty:
            continue
        grown = unary_union([p.buffer(seam_mm, join_style=1, quad_segs=4) for p in each(geom)])
        if claimed is not None:
            grown = grown.difference(claimed)
        grown = unary_union([p for p in each(grown) if p.area >= min_area_mm2])
        if grown.is_empty:
            continue
        out[slot] = MultiPolygon([grown]) if isinstance(grown, Polygon) else grown
        claimed = grown if claimed is None else unary_union([claimed, grown])
    return out


def footprint(regions):
    """Outer hull of every slot together -- what the backer has to carry."""
    return unary_union([Polygon(p.exterior) for g in regions.values() for p in each(g)])


def render_svg(path, width_px):
    """SVG -> RGBA PIL image, `width_px` wide. Needs cairosvg; PNG input does not."""
    import io
    try:
        import cairosvg
    except ImportError as e:
        raise ImportError("rendering SVG needs cairosvg; pass a PNG instead") from e
    except OSError as e:
        # cairosvg installs fine without the native cairo library and then
        # fails to dlopen it at import, with a wall of search paths that never
        # names the fix. On Apple Silicon Homebrew's lib dir is not searched
        # by a uv-managed Python even when cairo is installed.
        raise ImportError("rendering SVG needs the cairo library (brew install cairo, "
                          "apt install libcairo2); with Homebrew on Apple Silicon also set "
                          "DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib. Or pass a PNG instead") from e
    png = cairosvg.svg2png(url=str(path), output_width=int(width_px))
    return Image.open(io.BytesIO(png)).convert("RGBA")
