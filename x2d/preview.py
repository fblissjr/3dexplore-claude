"""A flat PNG of what the plate will look like. Sanity check, not a render."""
from .geometry import each


def preview(base, art, path, colors=("#2b2b2b", "#e6b422"), bg="#f4f4f4"):
    """Flat top-down render. `colors` is (backer, artwork) as hex.

    Pass the real filament colours. A preview in invented colours is worse than
    no preview: it is the only thing anyone looks at before committing an hour
    of print time, and it will happily sell you a plate you would never choose.
    """
    return preview_layers([(base, colors[0]), (art, colors[1])], path, bg=bg)


def preview_layers(layers, path, bg="#f4f4f4"):
    """Same render for any number of (geometry, hex) layers, painted in order."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Path, PathPatch
    import numpy as np

    def patch(poly, **kw):
        verts, codes = [], []
        for ring in [poly.exterior, *poly.interiors]:
            xy = np.asarray(ring.coords)
            verts += list(xy)
            codes += [Path.MOVETO] + [Path.LINETO] * (len(xy) - 2) + [Path.CLOSEPOLY]
        return PathPatch(Path(verts, codes), **kw)

    fig, ax = plt.subplots(figsize=(5, 5), dpi=160)
    for geom, color in layers:
        for p in each(geom):
            ax.add_patch(patch(p, fc=color, ec="#00000033", lw=0.5))
    ax.autoscale_view(); ax.set_aspect("equal"); ax.axis("off")
    ax.relim(); ax.autoscale()
    fig.savefig(path, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    return path
