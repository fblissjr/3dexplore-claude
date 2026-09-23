---
name: fix-downloaded-3mf
description: Repair, recolour or re-slot a Bambu Studio .3mf someone else made (a MakerWorld download) without losing its painting, then get it printing on the X2D. Use when Bambu Studio reports open or non-manifold edges, a painted feature needs another colour, the file's filament slots do not match the AMS, or it warns about a floating cantilever.
---

# Fix a downloaded project

A downloaded project's value is the designer's painting, part layout and
tuned settings. **Edit it in place; never rebuild it.** Anything that
round-trips it through a general tool (pymeshfix, MeshLab, re-export) loses
some of that. The reasons and the numbers are in `docs/DESIGN.md`, section
"Someone else's project".

The scripts live in the 3dexplore-claude repo; run them from its root with
`uv run` (in Cowork, connect that folder first). Never overwrite the user's
file: write `<name>_fixed.3mf` beside it (or in `out/`), and never send,
queue or control a print.

## 1. Diagnose

Read what Bambu Studio's object info panel says (open / non-manifold edges,
and which part has the warning triangle). Then:

```bash
uv run scripts/repair_3mf.py download.3mf --out out/download_fixed.3mf
```

It checks every mesh on exact vertex indices, which is how Bambu counts; its
`before` numbers should match the panel. Merged-vertex counts do not, and
will send you after parts that are fine.

## 2. Repair

The same command repairs what it found. Check the report:

- `after`: open, non-manifold and flipped edges all 0.
- `patch_deviation_mm`: max a few hundredths of a mm. Tenths means the hole
  wrapped a sharp edge and came back chamfered; say so.
- `painted_triangles_cut`: painted triangles inside the cut come back
  unpainted. Zero is usual (damage sits on flat bases); if not, repaint them
  (step 3).
- `volume_mm3`: before and after should agree to well under 0.1%.

If it raises "not a local repair", the damage is not local; stop and tell the
user rather than reaching for a global fixer.

## 3. Recolour a painted feature

`paint_color` strings are trees (`x2d/paint.py`): whole-triangle codes are
`solid(k)` = `4`, `8`, `0C`, `1C`, `2C` for slots 1-5; longer strings are
subdivided triangles. Never string-replace them.

1. `read_meshes()` the part; collect triangles whose `states()` include the
   feature's colour.
2. Connected components of *whole-triangle* paint, subdivided triangles as
   borders. Rank by area, centroid and mean normal to find the feature (the
   catbus's eyes: two front-facing components of ~25 mm², each enclosing a
   small pupil component inside a ring of subdivided triangles).
3. Render the options (pyvista off-screen works in a headless container)
   and let the user choose. Offer a colour already in the print first: a new
   colour costs purges on every layer it appears in.
4. Set `solid(slot)` on the chosen triangles, `replace_mesh()` that object,
   `rewrite()` the archive.

## 4. Re-slot to the AMS

Ask what is in each slot. Then map the file onto it:

```bash
uv run scripts/reslot_3mf.py out/download_fixed.3mf --order 5 1 3 2 --map 4=1 --out out/download_ams.3mf
```

`--order`: for new slot 1, 2, ..., the old filament whose colour, settings
and purge volumes it takes. `--map OLD=NEW`: send an old filament's parts and
paint elsewhere, e.g. two old slots of the same colour into one. Check
`used_before` (every one must be placed; it refuses otherwise) and
`left_as_is` (lists it could not classify; look at them).

A slot nothing uses is free: a colour can be swapped into it without
touching anything else.

## 5. Explain the warnings

"It seems object ... has floating cantilever" with supports off:

```bash
uv run scripts/overhangs.py out/download_ams.3mf
```

Floating islands fail without support. Bridges (held on two or more sides)
of a few mm and cantilevers of a mm or two are fine in PLA. On flexi
print-in-place models, supports get trapped inside hollow bodies and can
fuse the joints: recommend against them unless something actually floats.

## 6. Hand off to Bambu Studio

The human does these; say them in order.

- **Sync info.** The AMS sync dialog maps by the *old* slot layout; check
  its dropdowns and preview before accepting, and untick "merge the same
  colours" (irreversible). Skipping the sync is fine: the send dialog's
  mapping is what decides.
- **Purging volumes → auto-calculate** after the colours are synced.
- **Filament grouping (X2D).** Filament-Saving mode may put one colour on
  the auxiliary nozzle, which has no AMS, via an external spool. On the
  catbus that print smeared yellow everywhere; the cause is not isolated
  yet. Slice with **Custom** grouping, every filament on the main nozzle.
  Not Convenience: it groups by what the printer reports loaded, so a spool
  left in the auxiliary could pull a colour back there (inferred). The orange "not
  optimal / Set to Optimal" tip after slicing is the auxiliary suggestion
  again; ignore it. To ship a file already pinned, set `filament_map_mode`
  `Manual` and every filament to `1` in **both** places: the plate in
  `model_settings.config` (`filament_maps`) carries its own copy, and that
  copy is the one the GUI used.
- **"Switch diameter" with a blank main nozzle**: not a real mismatch. Close
  it; sync again once the printer is idle.
- **Send dialog.** Check each filament's slot. The thumbnail uses the
  printer's record of each spool, so an external spool whose colour was
  never entered makes the thumbnail wrong while the print is right; fix it
  under Device.
- **Plate.** Textured PEI with PLA: wash with dish soap and warm water (not
  alcohol), no glue. Let it cool before removing; flex each joint gently.
- **Waste.** A small four-colour figure is mostly purge. Sliced, one
  nozzle: the catbus is 14 g of plastic and 109 g of purge and tower per
  plate (123 g for one copy, 137.5 g for two), because yellow, brown and
  white change on nearly every layer. Printing several copies at once is the
  big lever: the changes per layer stay the same, so the purge is shared.
  Dropping a colour saves only what its layers cost (black pupils: 18
  layers, ~7 g). Do not coarsen a flexi's layer height to save purge: its
  clearances are sized for the designer's layers (0.12 mm on the catbus;
  0.2 mm stacks legs directly on the chassis). Flush into infill can absorb
  only the infill, ~1 g on a figure this size.

## 7. Record

Anything new the GUI did, and how the print came out, goes in
`docs/DESIGN.md` under "What the X2D GUI did with it", marked observed or
inferred.
