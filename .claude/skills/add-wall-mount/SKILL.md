---
name: add-wall-mount
description: Turn a downloaded Bambu Studio .3mf (a desk stand, holder or organiser) into a wall-mounted version for the X2D, with countersunk screw holes and/or a measured patch for adhesive strips, then show where it goes on the wall. Use when someone wants to hang, wall-mount, screw or 3M-strip a printed object, or asks where on a wall to put one.
---

# Add a wall mount

The face a part prints on is flat, and on many desk objects it is a side that
already stands upright on the desk. Put that face on the wall and the object
works exactly as it did on the desk. The reasons and the numbers are in
`docs/DESIGN.md`, section "Wall mounts".

Run everything from the 3dexplore-claude repo root with `uv run`. Write to
`out/` (gitignored), never over the user's file, and never send, queue or
control a print.

## 1. Ask what you cannot see

- Where it goes: photos of the wall, and which way the person faces when
  they use it (for a bed, where the pillow is).
- Screws or adhesive strips (renters, tile, a kid pulling on it).
- Which filament and AMS slot (see "Filaments" in `docs/DESIGN.md`).

## 2. Survey

```bash
uv run scripts/wallmount_3mf.py download.3mf --survey --up 135 --out out/thing/thing_wall.3mf
```

Look at `out/thing/thing_wall_survey.png`. It is drawn in the wall frame: X
along the wall, U up it, mm, origin at the centre of the footprint.

- `--up` is the bed direction that points up the wall, in degrees
  counter-clockwise from the bed's +X. Try values until the object stands
  the way it does on the desk (the foot, if any, is a bar along the bottom).
- `painted_triangles` must be 0: the edit is a boolean and would drop paint.
- Panel "above the plate" is everything that stands on the plate. What sits
  in it blocks a screw, including a lip high above.

## 3. Build

```bash
uv run scripts/wallmount_3mf.py download.3mf --up 135 \
    --trim "-40,-35 40,-35 40,-20 -40,-20" --mirror --out out/thing/thing_wall.3mf
```

- `--trim` (repeatable): a wall-frame polygon. Whatever stands on the plate
  inside it is cut away; the plate stays. Put the line a couple of mm past
  any fillet: a 0.3 mm sliver left along a trim line blocks every screw
  spot (it happened on the watch stand).
- Holes are placed automatically: the widest level pair that a screwdriver
  can reach. `--holes "X,U X,U"` places them by hand, `--holes none` gives
  strips only. Defaults fit a #6 / M3.5-M4 flat head; change with `--shaft`,
  `--head`, `--boss-r`, `--boss-h`.
- `--mirror` makes it face the other way along the wall. Pick the side that
  faces the person using it.
- `--no-pin` leaves nozzle grouping to the slicer. Default pins every
  filament to the main nozzle in both places the file keeps the map.

## 4. Check the report

- `watertight: true`, `through_holes_added` equal to the number of holes.
- `overhangs.before` and `.after` agree, unless the trim removed or exposed
  something that needs support. Say which.
- `longest_strip_mm`: the longest strip of each width that fits on the flat
  back. A back that looks big can be mostly hole. Recommend a strip only
  if one fits; otherwise say screws.
- Open `<out>_wall.png`: holes, spacing, strip patch, size on the wall.

## 5. Show where it goes

Draw the front view into the person's wall photo at true size: take the scale
from something of known size in the photo (a puck light, an outlet plate),
warp the front view with an affine transform that follows the wall's
perspective, and add a zoomed inset, because a part the size of a hand is
tiny in a photo of a wall. Say the scale is estimated. Mark the cable path,
the clearance above and below, and what it faces.

## 6. Hand off

The human does these; say them in order.

- Open in Bambu Studio, slice, and check the Filament Grouping panel shows
  every filament under **Main nozzle**. If not, Regroup filament → Custom.
- In the send dialog, map each filament to its AMS slot.
- After printing, clean support residue off anything that seats a part
  (a charger puck, a case).
- Mounting: tape it up first and check it from where it will be used. Then
  mark through the holes, drywall anchors, flat-head screws snug, not tight.
  Strips: isopropyl on the back, press 30 s, wait an hour before loading.

## 7. Record

What the slicer reported and how the print and mount held up go in
`docs/DESIGN.md` under "Wall mounts", marked observed or inferred.
