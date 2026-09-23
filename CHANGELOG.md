# Changelog

All notable changes to this project. Versions follow semver.

## Unreleased

Projects someone else made, from a MakerWorld download that Bambu Studio
flagged with 273 open and 34 non-manifold edges.

- `scripts/repair_3mf.py` and `x2d/repair.py`: local mesh repair that cuts
  out only the damage and refills it from the surface it replaced, keeping
  every other triangle, its order and its paint. Objects that were fine, and
  the rest of the archive, are copied byte for byte.
- `scripts/reslot_3mf.py` and `x2d/reslot.py`: move a project's filaments to
  the slots your AMS has them in -- per-filament settings, the purge matrix,
  part filaments and painted triangles together. Keys it cannot place are
  reported, not guessed.
- `scripts/overhangs.py` and `x2d/overhang.py`: explain a "floating
  cantilever" warning -- floating islands, bridges and cantilevers on the
  project's own layer grid, with how far each reaches.
- `x2d/archive.py`: read and patch a GUI-saved project (meshes in
  `3D/Objects/`, components, per-triangle attributes) without reformatting
  it. `x2d/paint.py`: parse and remap `paint_color` strings.
- `.claude/skills/fix-downloaded-3mf`: the whole workflow, including what the
  X2D GUI does with a four-colour download.

Also:

- `scripts/bowtie.py`: a glue-on bowtie in several sizes, to choose the fit
  against the real print before gluing.
- Removed `docs/AGENT_WORKFLOW.md`; what still held is in `docs/DESIGN.md`,
  "Direction". Removed `scripts/make_sample.py`, which nothing used.

## 0.3.0

First public release, as 3dexplore-claude.

- Nothing printer-specific is hardcoded outside the X2D default in
  `x2d/machine.py`. `slice_local.py` finds Bambu Studio from `--studio`,
  `BAMBU_STUDIO`, `PATH`, then the macOS app bundle. `review_layers.py` picks
  its default heights from the G-code. Mosaics and the workbench use every slot
  in the capture instead of four, and the workbench reads the printer name,
  layer height, bed and minimum feature from `/api/machine`.
- The layer grid is counted from the first layer height, so captures whose
  first layer differs from the rest (0.12 mm over 0.2 mm) get colour
  boundaries on real layer boundaries. Every generator checks base and relief
  against it and names the nearest valid heights; the workbench runs the
  `verify.py` preflight before offering a download.
- An outer wall line width of 0 falls back to `line_width`, as in Bambu Studio.
  Half-resolved captures (a filament density of 0, or fewer colours than
  filament presets) are refused with an instruction to save from the GUI.
- `scan2print.py` keeps the capture's own slot-to-nozzle map when it switches
  to Manual, instead of pinning every slot to nozzle 1.
- `slice_local.py --studio` with a path that does not exist is an error, not a
  fallback to another install.
- `art2plate --shaded`: shaded colour art (renders, paintings) is flattened
  into printable regions first (`x2d/shaded.py`): shades matched to spools,
  anything narrower than the nozzle removed in the raster, an optional halo
  (`--halo SLOT`), and `<out>_flat.png` to review. Without `--map` the table is
  matched to the capture's spools automatically and reported for editing.
- Default base and relief heights snap to the capture's layer grid; heights
  given explicitly are checked, never moved.
- The viewer's overhang shading reads `support_threshold_angle` from
  horizontal, as Bambu Studio does; it was only right at 45 degrees. Part
  names and preflight errors from an uploaded file are inserted as text.
- The keychain hole is centred in the margin instead of biting the first
  letter. `--out` without a `.3mf` suffix no longer gets its preview and STLs
  written over the project. `--font` works on a system with no fonts found.
- The workbench answers every failure with a JSON error, and an unreadable
  project or a filament map shorter than a part's slot is reported, not raised.
- The procedural bun (`x2d/bun.py`, `model_bun.py`, its viewer and `/models/`
  routes) is removed; `slice_local.py` and `review_layers.py` take any `.3mf`
  and slice output.
- No printer profile ships. Save a project from Bambu Studio as
  `profiles/default.3mf` (gitignored) and every script and the workbench use
  it; `--settings` picks another. Without a capture the generators still write
  parts and slots, checked against the X2D, and their reports say
  `"machine_from": "default (no capture)"`; mosaics ask for one.
- Tests use a stand-in capture quoted from Bambu's public X2D profiles
  (`tests/stock_capture.py`) instead of any real machine's.
- Any Bambu printer: `Machine.from_settings()` reads bed, per-nozzle reach and
  feed, nozzle diameter, outer wall line width and layer heights from the
  captured project. `write_3mf`, `plate_report` and `inspect_3mf` take the
  printer from the settings they are given, so `--settings` selects it. The
  X2D table stays as the default and is checked against Bambu's public X2D
  profile keys.
- The workbench and `media2plate.py` accept a capture from any printer instead
  of refusing everything but an X2D with 0.20 mm layers.
- `verify.py` checks a file against the printer its own capture describes.
- `scan2print.py` takes its minimum feature and layer height from the capture,
  and pins slots to nozzle 1 only on a two-nozzle printer; on a single-nozzle
  capture it no longer raises.
- Tests for single-nozzle and two-nozzle captures, and for `scan2print.py` and
  `media2plate.py`.

## 0.2.0

- Five ways in, all ending in a native Bambu Studio `.3mf` with part->filament
  intent: `silhouette()` and `detail()` for images, `separate()` for flat-colour
  art, `ink_art()` for shaded drawings, and `scripts/scan2print.py` for
  photogrammetry scans.
- `write_3mf` writes one object with N parts, each on a filament slot, and can
  carry a captured `project_settings.config` so the project opens configured.
- `scripts/verify.py` preflight: solids, placement, layer grid, and
  `cross_check` for overlaps and seam gaps between parts.
- One report shape (`x2d/report.py`) with filament colour, type, density and
  cost read from the capture.
- Local upload workbench (`app.py`) with colour mosaics and a model viewer that
  reads filament intent from Bambu projects.
- Local slicing with Bambu Studio's CLI (`scripts/slice_local.py`); nothing in
  the repo sends or queues a print.
- Extrusion falls back to manifold3d when earcut leaves a traced polygon's prism
  open.
- A missing system cairo library now produces an instruction instead of dlopen
  errors when rendering SVG input.
