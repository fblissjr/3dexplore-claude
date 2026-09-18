#!/usr/bin/env python3
"""Plot what Bambu Studio actually sliced at chosen heights; no printer access.

Reads the G-code, not the model: these are the real extrusion moves, arcs
included, so a wall that is too thin to print simply is not there. That is
the check a geometry report cannot make.

    uv run scripts/review_layers.py out/figure/figure_200mm-slice/plate_1.gcode --z 3 60 121 190
    uv run scripts/review_layers.py out/figure/figure_200mm-slice   # a slice_local.py dir, four heights spread over the print
"""
import argparse
import os
import tempfile
os.environ.setdefault('MPLCONFIGDIR', os.path.join(tempfile.gettempdir(), 'mpl-config'))
import re
import math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

# feature name in the G-code comments -> colour. Support is drawn so you can
# see what the slicer decided to hold up, and where the interface touches.
FEATURES = {'Outer wall': '#406f45', 'Inner wall': '#9bbf9f', 'Support': '#c9c9c9', 'Support interface': '#8a8a8a'}


def moves_by_layer(gcode: Path, wanted: list[float], tol: float = 0.16) -> dict:
    """{z: {feature: [segments]}} merging every layer within tol of each height.

    Merged, not nearest: Tree Slim supports print on their own layer heights,
    so support and model moves at "the same" height sit on different
    Z_HEIGHT values, and picking one layer shows a figure with no support.
    """
    layers = {}                                    # z -> {feature: segments}
    z, feature, x, y = 0., '', 0., 0.
    for line in gcode.read_text().splitlines():
        if line.startswith('; Z_HEIGHT:'):
            z = float(line.split(':')[1])
        elif line.startswith('; FEATURE:'):
            feature = line.split(':')[1].strip()
        elif line[:3] in ('G0 ', 'G1 ', 'G2 ', 'G3 '):
            coords = {k: float(v) for k, v in re.findall(r'([XYEIJ])(-?(?:\d+(?:\.\d*)?|\.\d+))', line)}
            nx, ny = coords.get('X', x), coords.get('Y', y)
            near = [w for w in wanted if abs(w - z) <= tol]
            if near and feature in FEATURES and coords.get('E', 0) > 0 and ('X' in coords or 'Y' in coords):
                segs = layers.setdefault(z, {}).setdefault(feature, [])
                if line.startswith(('G2 ', 'G3 ')):
                    cx, cy = x + coords.get('I', 0), y + coords.get('J', 0)
                    start, end = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                    sweep = (end - start) % (2 * math.pi)
                    if line.startswith('G2 '):
                        sweep -= 2 * math.pi
                    r = math.hypot(x - cx, y - cy)
                    n = max(2, math.ceil(abs(sweep) * r / .2))
                    pts = [(cx + r * math.cos(start + sweep * i / n), cy + r * math.sin(start + sweep * i / n)) for i in range(n + 1)]
                    segs.extend(zip(pts[:-1], pts[1:]))
                else:
                    segs.append([(x, y), (nx, ny)])
            x, y = nx, ny
    out = {}
    for w in wanted:
        merged = {}
        for zz in (zz for zz in layers if abs(zz - w) <= tol):
            for feat, segs in layers[zz].items():
                merged.setdefault(feat, []).extend(segs)
        out[w] = merged
    return out


def spread_heights(gcode: Path, n: int = 4) -> list[float]:
    """n layer heights spread over what was actually sliced, low to near the top."""
    zs = sorted({float(line.split(':')[1]) for line in gcode.read_text().splitlines()
                 if line.startswith('; Z_HEIGHT:')})
    if not zs:
        raise ValueError(f'{gcode}: no Z_HEIGHT markers; pass --z')
    return sorted({zs[round(f * (len(zs) - 1))] for f in (0.2, 0.45, 0.7, 0.95)[:n]})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', type=Path, help='a plate_1.gcode, or the slice_local.py directory holding one')
    ap.add_argument('--z', type=float, nargs='+', help='heights to plot, mm (layer nearest each)')
    ap.add_argument('--out', type=Path, help='png path; default next to the gcode')
    a = ap.parse_args()
    gcode = a.target / 'plate_1.gcode' if a.target.is_dir() else a.target
    out = a.out or gcode.with_name('sliced-layers.png')
    wanted = a.z or spread_heights(gcode)

    layers = moves_by_layer(gcode, wanted)
    fig, axes = plt.subplots(1, len(layers), figsize=(3.6 * len(layers), 4), squeeze=False)
    counts = {}
    for ax, (z, feats) in zip(axes[0], layers.items()):
        if not feats:
            raise ValueError(f'no extrusion at z={z}')
        for name, colour in FEATURES.items():          # support first, walls on top
            if name in feats:
                ax.add_collection(LineCollection(feats[name], colors=colour, linewidths=1.0 if 'wall' in name else 0.6))
        ax.autoscale(); ax.set_aspect('equal'); ax.set_title(f'Z = {z:g} mm'); ax.set_xlabel('X (mm)')
        counts[z] = {k: len(v) for k, v in feats.items()}
    axes[0][0].set_ylabel('Y (mm)')
    fig.suptitle('Bambu Studio sliced moves: walls (green) and support (grey), actual G-code')
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(counts)


if __name__ == '__main__':
    main()
