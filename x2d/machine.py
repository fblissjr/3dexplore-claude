"""The printer as data.

"X2D-optimized" is not a vibe, it is this table plus the checks run against it.
Every constant is verbatim from the profile Bambu ships
(`Bambu Lab X2D 0.4 nozzle.json`).

Any other Bambu printer comes from its own capture: Machine.from_settings()
reads the same keys out of a saved project's project_settings.config, so the
printer is whatever the user actually saved, never a table somebody typed in.
X2D stays as the machine this was built and verified against, and as the
default when there is no capture to read.
"""
from dataclasses import dataclass
import math


# ---------------------------------------------------------------- machine ---
@dataclass(frozen=True)
class Extruder:
    """One nozzle plus the box it can actually reach.

    Why this exists: on the X2D the two nozzles are NOT equals. Extruder 2 is
    Bowden-fed and gives up the left 20.5 mm of the bed and the top 5 mm of Z.
    Straight from extruder_printable_area / extruder_printable_height in
    "Bambu Lab X2D 0.4 nozzle.json". Anything second-color inherits that limit.
    """
    id: int               # 1-based, matching how the slicer and UI number nozzles
    feed: str             # "direct_drive" | "bowden"
    dia: float
    x: tuple              # min, max in mm
    y: tuple
    z_max: float


@dataclass(frozen=True)
class Machine:
    name: str
    bed: tuple            # x, y, z in mm -- the union of what any nozzle reaches
    extruders: tuple
    line_width: float     # typical outer wall width for that nozzle
    layer_h: float
    first_layer_h: float

    def extruder(self, n):
        """1-based lookup. m.extruder(2).x[0] -> 20.5 for the X2D."""
        if not 1 <= n <= self.nozzles:
            raise ValueError(f"nozzle {n}: this machine has {self.nozzles}")
        return self.extruders[n - 1]

    @property
    def nozzles(self):
        return len(self.extruders)

    @property
    def nozzle_dia(self):
        return self.extruders[0].dia

    @property
    def min_feature(self):
        """Two extrusion widths. Thinner than this and the slicer drops it."""
        return 2 * self.line_width

    # The layer grid is measured from the first layer, not from zero: a
    # 0.12 mm process over a 0.2 mm first layer puts its boundaries at 0.2,
    # 0.32, 0.44 ... and a colour change anywhere else lands mid-layer.
    # Checking "a multiple of layer_height" is only right when the two match.
    def on_grid(self, z, tol=1e-3):
        """Is z a layer boundary: the bed, or the first layer plus whole layers?"""
        if abs(z) < 1e-4:
            return True
        steps = (z - self.first_layer_h) / self.layer_h
        return steps > -tol and abs(steps - round(steps)) < tol

    def grid_ceil(self, z):
        """The lowest layer boundary at or above z."""
        if z <= 1e-4:
            return 0.0
        if z <= self.first_layer_h:
            return self.first_layer_h
        return self.first_layer_h + math.ceil((z - self.first_layer_h) / self.layer_h - 1e-6) * self.layer_h

    def snap(self, z):
        """The layer boundary nearest z, never below the first layer."""
        steps = max(0, round((z - self.first_layer_h) / self.layer_h))
        return round(self.first_layer_h + steps * self.layer_h, 4)

    def band(self, base, relief, default):
        """Plate `base` and artwork `relief` heights, checked against the grid.

        None means "not asked for": that one comes from `default`, snapped onto
        this printer's grid. A default like 1.6/0.6 is tuned to the X2D's
        0.2 mm layers, and on a 0.12 mm process it would fail a check the user
        never typed a number for. An explicit height is checked, never moved.
        """
        if base is None:
            base = self.snap(default[0])
        if relief is None:
            relief = round(max(1, round(default[1] / self.layer_h)) * self.layer_h, 4)
        self.check_band(base, relief)
        return base, relief

    def layers(self, z):
        """How many layers print up to the boundary at z."""
        return 0 if z < 1e-4 else 1 + round((z - self.first_layer_h) / self.layer_h)

    def check_band(self, base, relief):
        """Raise unless a plate of `base` and artwork `relief` above it both end
        on layer boundaries, naming the nearest heights that would."""
        grid = (f"{self.first_layer_h:g} mm first layer, then {self.layer_h:g} mm")
        if not self.on_grid(base):
            hi = self.grid_ceil(base)
            lo = max(self.first_layer_h, hi - self.layer_h)
            raise ValueError(f"base {base:g} mm is off the layer grid ({grid}); "
                             f"nearest: {round(lo, 4):g} or {round(hi, 4):g} mm")
        if not self.on_grid(base + relief):
            raise ValueError(f"relief {relief:g} mm is not a whole number of "
                             f"{self.layer_h:g} mm layers ({grid})")

    @classmethod
    def from_settings(cls, cfg):
        """The printer a captured project_settings.config describes.

        A capture is fully resolved, so these keys are present in any project
        Bambu Studio saves. Single-nozzle profiles leave the extruder_* lists
        empty (fdm_bbl_3dp_001_common: extruder_printable_area = []), which
        means the nozzle reaches the whole printable_area. The X2D and H2D fill
        them in because their two nozzles do not reach the same box -- the
        H2D's nozzle 1 stops at x=325 of 350, the X2D's nozzle 2 starts at 20.5.
        """
        def number(key):
            try:
                return float(cfg[key])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"settings capture has no usable {key}") from None

        def window(points):
            # ["0x0", "256x0", "256x256", "0x256"] -> ((x0, x1), (y0, y1))
            xy = [tuple(float(v) for v in p.split("x")) for p in points if p]
            if not xy:
                raise ValueError("settings capture has no printable_area")
            xs, ys = zip(*xy)
            return (min(xs), max(xs)), (min(ys), max(ys))

        dias = [float(d) for d in cfg.get("nozzle_diameter") or []]
        if not dias:
            raise ValueError("settings capture has no nozzle_diameter")
        bed_x, bed_y = window(cfg.get("printable_area") or [])
        bed_z = number("printable_height")
        areas = cfg.get("extruder_printable_area") or []
        heights = cfg.get("extruder_printable_height") or []
        feeds = cfg.get("extruder_type") or []
        extruders = []
        for i, dia in enumerate(dias):
            x, y = window(areas[i].split(",")) if i < len(areas) else (bed_x, bed_y)
            z = float(heights[i]) if i < len(heights) else bed_z
            feed = feeds[i] if i < len(feeds) else "Direct Drive"
            extruders.append(Extruder(i + 1, feed.lower().replace(" ", "_"), dia,
                                      x=x, y=y, z_max=z))

        # The outer wall is the width that decides whether a thin feature
        # survives, so it is the one min_feature is built on. Bambu Studio
        # reads 0 as "use line_width", so fall back exactly as it does; with
        # both at 0 there is nothing left to read, and guessing a width from
        # the nozzle would be inference.
        def width(key):
            value = str(cfg.get(key) or "").strip()
            try:
                w = float(value[:-1]) / 100 * dias[0] if value.endswith("%") else float(value)
            except ValueError:
                return 0.0
            return w if w > 0 else 0.0
        line_width = width("outer_wall_line_width") or width("line_width")
        if line_width <= 0:
            raise ValueError("settings capture has no outer_wall_line_width or line_width")

        return cls(name=cfg.get("printer_model") or "unknown printer",
                   bed=(bed_x[1], bed_y[1], bed_z),
                   extruders=tuple(extruders),
                   line_width=line_width,
                   layer_h=number("layer_height"),
                   first_layer_h=number("initial_layer_print_height"))


X2D = Machine(
    name="Bambu Lab X2D",
    bed=(256.0, 256.0, 261.0),
    extruders=(
        Extruder(1, "direct_drive", 0.4, x=(0.0, 256.0), y=(0.0, 256.0), z_max=261.0),
        Extruder(2, "bowden", 0.4, x=(20.5, 256.0), y=(0.0, 256.0), z_max=256.0),
    ),
    line_width=0.42,
    layer_h=0.2,
    first_layer_h=0.2,
)


def machine_for(settings):
    """The capture's printer when there is a capture, else the X2D."""
    return Machine.from_settings(settings) if settings else X2D


def reach(machine, bounds, n):
    """Can extruder n reach this axis-aligned box? bounds is ((x0,y0,z0),(x1,y1,z1))."""
    e = machine.extruder(n)
    (x0, y0, z0), (x1, y1, z1) = bounds
    return {
        "extruder": n,
        "feed": e.feed,
        "x_window_mm": list(e.x),
        "z_max_mm": e.z_max,
        "reachable": bool(x0 >= e.x[0] and x1 <= e.x[1]
                          and y0 >= e.y[0] and y1 <= e.y[1] and z0 >= 0 and z1 <= e.z_max),
    }


# ------------------------------------------------------------- the checks ---
def printability(geom, machine, *, label=""):
    """Flag features thinner than two extrusion widths -- the real limit."""
    min_feature = machine.min_feature
    thinned = geom.buffer(-min_feature / 2)
    lost = geom.area - (thinned.buffer(min_feature / 2).area if not thinned.is_empty else 0.0)
    return {
        "label": label,
        "min_printable_feature_mm": round(min_feature, 2),
        "area_mm2": round(geom.area, 1),
        "area_at_risk_mm2": round(max(lost, 0.0), 1),
        "at_risk_pct": round(100 * max(lost, 0.0) / geom.area, 1) if geom.area else 0.0,
        "fully_lost": thinned.is_empty,
    }


def fits(mesh, machine):
    e = mesh.extents
    return bool(e[0] <= machine.bed[0] - 10 and e[1] <= machine.bed[1] - 10 and e[2] <= machine.bed[2])
