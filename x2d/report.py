"""One report shape for every generator, and the filament facts behind it.

Five generators grew five different reports with **no key in common** -- not
"mostly aligned", literally zero. img2plate never said what colour anything
was, text2plate never named the machine, scan2print never said whether it fit
the bed. Worse, three of them each re-derived the filament swatch in their own
four lines of `swatch = settings.get(...)` / `hue = lambda slot: ...`, and one
copy was missing, so every name plaque ever previewed came out gold-on-grey
whatever was loaded. A thing copied four times is wrong in at least one of
them; this is the one place it lives now.

The numbers here come from the captured project, never from a constant. That
is the same rule as `project_settings` and it is not a formality: an older
viewer written against a different printer hardcoded 1.24 g/cm3, 45 degrees
and 20% infill, and the capture says 1.26 (1.32 for PLA Matte), 30
degrees, and 15%. Three out of four wrong, silently, in the direction of
under-reporting what a print costs.
"""
from dataclasses import dataclass, asdict

from .machine import X2D, fits, machine_for, printability


@dataclass(frozen=True)
class Filament:
    """One AMS slot as the capture describes it."""
    slot: int
    colour: str
    kind: str                 # filament_type, e.g. "PLA"
    density: float            # g/cm3, per slot: matte is denser than basic
    cost_per_kg: float


def filaments(settings):
    """-> {slot: Filament} from a captured project_settings dict.

    Every per-slot list in the capture is indexed 0-based while Bambu Studio's
    panel numbers slots from 1. That off-by-one is the single easiest mistake
    to make against this file, so it is made exactly here.
    """
    if not settings:
        return {}
    colours = settings.get("filament_colour") or []
    kinds = settings.get("filament_type") or []
    density = settings.get("filament_density") or []
    cost = settings.get("filament_cost") or []
    at = lambda seq, i, default: (seq[i] if i < len(seq) else default)
    return {
        i + 1: Filament(
            slot=i + 1,
            colour=colours[i],
            kind=at(kinds, i, "?"),
            density=float(at(density, i, 1.24)),
            cost_per_kg=float(at(cost, i, 0.0)),
        )
        for i in range(len(colours))
    }


def mass_g(volume_mm3, filament, *, infill=1.0):
    """Filament mass for a solid volume, at a given infill fraction.

    An UPPER BOUND, and measured as one rather than assumed. Real mass is
    walls plus top and bottom solid layers plus sparse infill plus support
    plus purge, and only the slicer knows those -- this repo does not have one
    and is not getting one.

    Calibrated against the only slice on record, a three-letter 90 mm name
    plaque that Bambu Studio put at 13.40 g: solid volume x density gives **15.17 g, 13% over**.
    The gap is the 15% sparse infill in the core of the plate, which the bound
    counts as solid. So it is honest as a ceiling and as a comparison between
    two designs, and it is not a quote. Anything that needs a real number has
    to go through slice_local.py.
    """
    return volume_mm3 / 1000.0 * filament.density * infill


def plate_report(parts, *, settings=None, machine=None, plate=None, artwork=None,
                 base_slot=None, base=None, relief=None, extra=None):
    """The shape every generator emits. `parts` is the list handed to write_3mf.

    `plate` is the backer geometry and `artwork` is {slot: geometry} in the
    relief band above it. They are separate arguments because they are separate
    layer bands, and collapsing them is not cosmetic: the first version of this
    took one dict of everything, so a two-colour plaque counted its plate as a
    second colour in the relief band and reported **10 filament changes for a
    print that needs 1**. Both get a printability check; only `artwork` decides
    what the band costs.

    A scan has neither and passes None, which is why `checks` can be empty
    rather than absent.
    """
    machine = machine or machine_for(settings)
    spools = filaments(settings)
    whole = parts[0].mesh
    for p in parts[1:]:
        whole = whole + p.mesh

    def one(p):
        spool = spools.get(p.extruder)
        volume = float(p.mesh.volume)
        row = {"name": p.name, "slot": p.extruder,
               "colour": spool.colour if spool else None,
               "watertight": bool(p.mesh.is_watertight),
               "triangles": int(len(p.mesh.faces)),
               "volume_mm3": round(volume, 1)}
        if spool:
            row["solid_g"] = round(mass_g(volume, spool), 2)
        return row

    rows = [one(p) for p in parts]
    report = {
        "machine": machine.name,
        # Without a capture every check here ran against the X2D default,
        # which is only right if that is the printer. Say which it was.
        "machine_from": "capture" if settings else "default (no capture)",
        "size_mm": [round(v, 2) for v in whole.extents],
        "fits_bed": fits(whole, machine),
        "parts": rows,
        "filaments": {s: asdict(f) for s, f in spools.items()
                      if s in {p.extruder for p in parts}},
        "solid_g_total": round(sum(r.get("solid_g", 0.0) for r in rows), 2),
        "checks": ([printability(plate, machine, label="plate")] if plate is not None else [])
                  + [printability(g, machine, label=f"slot{s}")
                     for s, g in (artwork or {}).items()],
    }
    if base is not None and relief is not None:
        report["layers"] = {"plate": machine.layers(base),
                            "relief": machine.layers(base + relief) - machine.layers(base)}
        report["filament_changes"] = changes(artwork or {}, base_slot, relief, machine)
    report.update(extra or {})
    return report


def changes(artwork, base_slot, relief, machine=X2D):
    """How many filament swaps the relief band actually costs.

    Colours sharing one band cost a change per colour per layer of it. ONE
    colour in the band does not: the band is a single slab above the plate,
    which is the two-filament plaque case and one swap for the whole print.
    Counting layers x colours regardless said 3 for a drawing that needs 1,
    and that is the sort of number that quietly becomes "so it needs a prime
    tower" when it does not.
    """
    slots = set(artwork)
    if not slots or slots == {base_slot}:
        return 0
    if len(slots) == 1:
        return 1
    return round(relief / machine.layer_h) * len(slots)
