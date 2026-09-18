"""Shaded colour art -> flat regions a nozzle can print. A sixth way in.

A render or a painting spreads each material across dozens of shades, and
separate()'s nearest-colour join is exact per pixel: pointed straight at one,
it returns thousands of slivers thinner than the nozzle -- 11-22% of the area
at risk on the first attempt at a character render. So this step decides the
regions first and only then hands them to separate(): send each shade to a
filament slot, remove whatever is narrower than the printer's minimum feature,
and give the pixels that frees to the nearest region that survived. By the
time a contour is traced, every region is already printable.

The flat image is the design. Save it, look at it, and change the table: the
automatic grouping is a starting point, not a verdict (see auto_map).
"""
import colorsys

import cv2
import numpy as np
from PIL import Image, ImageColor

from .machine import X2D
from .palette import _lab, label_map

# Output pitch. Finer carries nothing a nozzle can use and makes contour
# simplification fight separate()'s seam growth; about 5 px/mm keeps every
# region the opening left and traces clean, watertight contours.
OUT_PX_PER_MM = 5.0
# Render noise is per pixel, so this one is fixed in pixels rather than scaled
# by the figure ("Pixel constants" in docs/DESIGN.md).
DENOISE_PX = 7


def _denoised(image):
    px = np.asarray(image.convert("RGBA"))
    rgb = cv2.medianBlur(np.ascontiguousarray(px[:, :, :3]), DENOISE_PX)
    return Image.fromarray(np.dstack([rgb, px[:, :, 3]]), "RGBA")


def auto_map(image, spools, *, clusters=8, seed=0):
    """Group an image's shades into `clusters` and send each to its nearest spool.

    spools is {slot: "#RRGGBB"}, normally the capture's filament colours. The
    result has the shape of art2plate's --map, {"#RRGGBB": slot} keyed by
    group centre, so a printed table can be edited and passed back.

    More groups than spools on purpose: a lit surface is several shades of one
    material, and each shade needs its own chance to land on the right spool.
    The match is still only a first guess -- a dark, desaturated maroon can
    land on black where a person would call it red. That is what the --map
    override is for, and why the table is reported.
    """
    px = np.asarray(_denoised(image))
    rgb = px[px[:, :, 3] >= 128][:, :3].astype(np.float32)
    if not len(rgb):
        raise ValueError("the image has no opaque pixels")
    sample = rgb[np.random.default_rng(seed).permutation(len(rgb))[:20000]]
    k = max(1, min(clusters, len(np.unique(sample, axis=0))))
    cv2.setRNGSeed(seed)
    _, _, centres = cv2.kmeans(sample, k, None,
                               (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5),
                               4, cv2.KMEANS_PP_CENTERS)
    hexes = ["#%02X%02X%02X" % tuple(int(round(v)) for v in c) for c in centres]
    slots = list(spools)
    shades = _lab(hexes).astype(float)
    inks = _lab([spools[s] for s in slots]).astype(float)
    # A render is a lit version of its materials, usually darker or paler than
    # any spool, so compare the two on one scale: stretch the shades' lightness
    # and colourfulness ranges onto the spools', then give lightness half
    # weight, because shading moves lightness far more than it moves hue.
    # Measured against hand-made tables: 6 of 8 groups right on a character
    # render (plain Lab: 3) and 7 of 8 on tests/test_shaded.py's fixture (plain
    # Lab: 8, where a deep red shadow now goes to black). 13 of 16 against 11,
    # on two images -- a better first guess, not a solved problem.
    span = np.ptp(shades[:, 0])
    if span > 1e-6:
        shades[:, 0] = inks[:, 0].min() + (shades[:, 0] - shades[:, 0].min()) / span * np.ptp(inks[:, 0])
    chroma = np.hypot(shades[:, 1], shades[:, 2]).max()
    if chroma > 1e-6:
        shades[:, 1:] *= np.hypot(inks[:, 1], inks[:, 2]).max() / chroma
    distance = ((shades[:, None] - inks[None]) ** 2 * np.array([0.25, 1, 1])).sum(-1)
    return {h: slots[i] for h, i in zip(hexes, distance.argmin(1))}


def _refill(labels, where):
    """Give each pixel in `where` the slot of the nearest labelled pixel."""
    if not where.any() or not (labels >= 0).any():
        return
    _, nearest = cv2.distanceTransformWithLabels((labels < 0).astype(np.uint8), cv2.DIST_L2, 5,
                                                 labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(labels >= 0)
    lut = np.zeros(nearest.max() + 1, int)
    lut[nearest[ys, xs]] = labels[ys, xs]
    labels[where] = lut[nearest[where]]


def flatten(image, mapping, *, width_mm, machine=X2D, halo_slot=None, halo_mm=1.2):
    """-> int array of filament slots at OUT_PX_PER_MM, -1 where there is no art.

    mapping is {"#RRGGBB": slot}: every opaque pixel goes to the nearest listed
    colour in Lab, as in separate(). Then, in the raster, before any contour:

    - the figure as a whole is opened at the printer's minimum feature, and
      what that removes is dropped. Only the regions inside it are opened per
      slot, with the freed pixels going to the nearest surviving region. Refill
      the outline too and a hair strand comes straight back, just as thin, in
      its neighbour's colour;
    - with `halo_slot`, a ring of that slot `halo_mm` wide goes round the
      figure, so a region that shares the plate's colour (black boots on a
      black plate) still reads as a shape. The canvas is padded first, so a
      tight crop or a fully opaque image still gets a ring on every side;
    - notches in the outline narrower than the nozzle are closed, into the halo
      slot when there is one. Left open they print as bridged blobs, and
      verify.py counts them as seam gaps.

    The result is cropped to the art, halo included. Its width in mm is
    `labels.shape[1] / OUT_PX_PER_MM`, a little more than `width_mm` when
    there is a halo; pass that, not `width_mm`, to separate().
    """
    image = image.convert("RGBA")
    px_per_mm = image.width / float(width_mm)
    feature = machine.min_feature * px_per_mm
    halo = int(round(halo_mm * px_per_mm)) if halo_slot is not None and halo_mm > 0 else 0
    pad = halo + int(round(1.5 * feature)) + 2
    padded = Image.new("RGBA", (image.width + 2 * pad, image.height + 2 * pad), (0, 0, 0, 0))
    padded.paste(image, (pad, pad))

    alpha = np.asarray(padded)[:, :, 3] >= 128
    labels = label_map(_denoised(padded), mapping)
    k = int(round(feature)) | 1
    kernel = np.ones((k, k), np.uint8)
    body = cv2.morphologyEx(alpha.astype(np.uint8), cv2.MORPH_OPEN, kernel) > 0
    opened = np.full(labels.shape, -1, int)
    for slot in sorted(set(mapping.values())):
        mask = ((labels == slot) & body).astype(np.uint8) * 255
        opened[cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel) > 0] = slot
    if not (opened >= 0).any():
        raise ValueError(f"nothing in the image is wider than the {machine.min_feature:.2f} mm "
                         f"minimum feature at {width_mm:g} mm wide; print it larger")
    _refill(opened, body & (opened < 0))

    if halo:
        # A centred kernel grows a shape by half its size on each side, so a
        # ring `halo` px wide needs one 2*halo+1 across. Sized as `halo` alone,
        # --halo-mm 1.2 made 0.6 mm -- under the nozzle.
        disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * halo + 1,) * 2)
        opened[(cv2.dilate(body.astype(np.uint8), disc) > 0) & ~body] = halo_slot

    # 1.5x the feature: margin over verify.py's own closing radius, so the fill
    # survives the resample below.
    ck = int(round(1.5 * feature)) | 1
    closed = cv2.morphologyEx((opened >= 0).astype(np.uint8), cv2.MORPH_CLOSE,
                              np.ones((ck, ck), np.uint8)) > 0
    notches = closed & (opened < 0)
    if halo_slot is not None:
        opened[notches] = halo_slot
    else:
        _refill(opened, notches)

    scale = OUT_PX_PER_MM / px_per_mm
    size = (max(1, round(padded.width * scale)), max(1, round(padded.height * scale)))
    small = cv2.resize(opened.astype(np.int16), size, interpolation=cv2.INTER_NEAREST).astype(int)
    ys, xs = np.nonzero(small >= 0)
    return small[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def to_image(labels, colours):
    """Slot labels -> RGBA painted in {slot: "#RRGGBB"}, clear where there is no art."""
    out = np.zeros(labels.shape + (4,), np.uint8)
    for slot, colour in colours.items():
        out[labels == slot] = (*ImageColor.getrgb(colour)[:3], 255)
    return Image.fromarray(out, "RGBA")


def stand_ins(slots):
    """One far-apart colour per slot, for handing labels to separate().

    Spool colours cannot do this job: two slots loaded with the same white
    would be indistinguishable, and separate() matches by colour.
    """
    slots = sorted(slots)
    return {s: "#%02X%02X%02X" % tuple(int(255 * v) for v in colorsys.hsv_to_rgb(i / len(slots), 1, 1))
            for i, s in enumerate(slots)}
