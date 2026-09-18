"""A drawing's linework -> flat art that separate() can print. A fifth way in.

silhouette() and detail() ask "what is the figure?" of a photograph.
palette.separate() trusts an artist who already drew flat regions. A shaded
illustration is neither: the drawing IS the answer, but it is spread across
144,815 distinct RGB values in the reference image, and pointing separate()
straight at one gives speckle -- 117 islands with 61% of the linework below
the nozzle -- rather than a plate.

Two things have to happen first, and both are the kind of thing that looks
like a detail and decides whether the print is legible.

**A stroke is continuous; a threshold is not.** Ink in a drawing like this is
soft at the edges, so any single luminance cut catches the dark cores and
drops the fades, and an outline comes out as a dotted line. Hysteresis is the
fix and it encodes what is actually true about a drawing: a moderately dark
pixel connected to a definitely-dark one belongs to the same stroke. Seed on
`strong`, grow through `weak`, keep the components that contain a seed.

**Ink is about two pixels wide whatever the file's resolution** -- 0.18 mm at
72 mm for the reference image, against a 0.84 mm minimum feature. The mask is
dilated by half the minimum in pixels derived from the physical width, so a
line lands just over the limit. This happens HERE, in the raster, before any
region is traced. Growing polygons afterwards would push the ink into its
neighbours and reopen exactly the overlap separate()'s seam logic exists to
prevent; growing the mask first means the ink simply owns those pixels, which
is the raster form of listing small features first in --priority.

`detail(feature="creases")` is the tool CLAUDE.md points at for ink, and it
does not work on this input -- it returned 0.34% coverage, essentially blank.
The reason is worth keeping: detail() takes silhouette() as the figure and
erodes its rim, but on a drawing that sits on a pale background silhouette()
picks out the *strokes* as the figure, so the rim erosion then eats the very
thing being looked for. detail() wants a photographed object on a background
it can separate. A line drawing is not that.
"""
import cv2
import numpy as np
from PIL import Image

from .machine import X2D


def ink_mask(image, *, strong=110, weak=175):
    """Linework as a binary mask, via hysteresis on luminance.

    `strong` seeds and `weak` grows. Raising `weak` pulls in more of the
    lighter interior detail -- on the reference image it is the difference
    between a clean outline and every rose on the jacket.
    """
    if not 0 < strong <= weak <= 255:
        raise ValueError("need 0 < strong <= weak <= 255")
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    grown = (gray < weak).astype(np.uint8)
    count, labels = cv2.connectedComponents(grown, connectivity=8)
    seeded = np.zeros(count, bool)
    seeded[np.unique(labels[(gray < strong) & (grown > 0)])] = True
    seeded[0] = False                      # component 0 is the background
    return seeded[labels].astype(np.uint8) * 255


def ink_art(image, *, width_mm, strong=110, weak=175, machine=X2D,
            thicken=True):
    """-> RGBA where the linework is opaque black and everything else is clear.

    Transparent, not white, and the difference is the whole print: the paper is
    the plate showing through, not a part printed in white. A part would cost a
    filament change and a layer band for the 90% of a drawing that is paper.
    Feed the result to palette.separate() with a single `#000000=SLOT` entry.
    """
    image = image.convert("RGBA")
    mask = ink_mask(image, strong=strong, weak=weak)
    if thicken:
        grow = max(int(round(machine.min_feature / 2 * image.width / float(width_mm))), 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2)
        mask = cv2.dilate(mask, kernel)
    out = np.zeros((image.height, image.width, 4), np.uint8)
    out[:, :, 3] = np.minimum(mask, np.asarray(image)[:, :, 3])
    return Image.fromarray(out, "RGBA")


def line_width_mm(image, *, width_mm, dark=130):
    """How wide the strokes actually are, in mm at this print size.

    Worth reporting: it is the number that decides whether the linework
    survives, and it is invisible in the source file.
    """
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    ink = (gray < dark).astype(np.uint8)
    if not ink.any():
        return 0.0
    half = cv2.distanceTransform(ink, cv2.DIST_L2, 5)[ink > 0]
    return float(2 * np.median(half) * width_mm / image.width)
