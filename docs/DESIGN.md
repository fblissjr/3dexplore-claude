# Design notes

Why the code is shaped the way it is. Read this before changing anything:
most of what follows was expensive to learn and is not obvious from the code.

## What this is, and what it refuses to be

A harness that turns an image or a string into a two-color plate for a Bambu
Lab X2D, as a native `.3mf`. It owns **geometry and part→filament intent**.
It does **not** slice. Bambu Studio owns machine profiles and toolpaths.

That boundary is the design. Do not add a slicer.

```
image/text ─▶ mask ─▶ polygons (mm) ─▶ solids ─▶ .3mf ─▶ Bambu Studio ─▶ printer
             cv2     shapely         trimesh    ours     (profiles, slicing)
```

## Layout

```
x2d/machine.py    the printer as data: Extruder, Machine, X2D, reach, printability, fits
x2d/geometry.py   image/text -> mask -> polygons -> solids. No printer knowledge.
                  silhouette() for outlines, detail() for shading
x2d/palette.py    separate(): flat-colour art -> one region per filament slot
x2d/poster.py     ink_art(): linework out of a shaded drawing
x2d/shaded.py     flatten(): shaded colour art -> printable flat regions
x2d/mosaic.py     coarse colour grid for the upload workbench
x2d/project.py    the Bambu side: Part, write_3mf, project_settings
x2d/validation.py archive preflight and cross_check between parts
x2d/report.py     one report shape for every generator
x2d/preview.py    flat PNG sanity check
x2d/fonts.py      cross-platform bold font lookup
app.py, web/      local upload workbench and model viewer
scripts/          CLI generators, preflight, local slicing
profiles/         your printer captures; gitignored, see profiles/README.md
samples/          input images for the worked examples; committed, they are inputs
out/              generated, gitignored
```

Printer facts come from exactly two places. `Machine.from_settings()` reads
them out of a capture -- `printable_area`, `printable_height`,
`extruder_printable_area`/`_height`, `extruder_type`, `nozzle_diameter`,
`outer_wall_line_width`, `layer_height`, `initial_layer_print_height` -- and
the `X2D` table in `x2d/machine.py` is the default when there is no capture.
A stand-in quoted key by key from Bambu's public X2D profiles
(`tests/stock_capture.py`) parses to exactly that table, and
`tests/test_machine.py` holds the two to each other. `write_3mf`, `plate_report` and `inspect_3mf`
take the machine from the settings they are handed, so a script that passes
`--settings` through gets the right printer without naming one. If you find
yourself adding a printer constant anywhere else, that is the bug.

Single-nozzle profiles leave the `extruder_*` lists empty
(`fdm_bbl_3dp_001_common`), meaning the one nozzle reaches the whole
`printable_area`. A missing key is refused with a `ValueError`, never filled
in with an X2D number: a check against the wrong bed is worse than no check.
`outer_wall_line_width` of 0 means "use `line_width`" in Bambu Studio, and is
read that way.

The layer grid starts at the first layer, not at zero: a 0.12 mm process over a
0.2 mm first layer has boundaries at 0.2, 0.32, 0.44, and "a multiple of the
layer height" puts a colour change mid-layer. `Machine.on_grid` is the one
definition; every generator's base and relief check, the scan risers and
`verify.py` use it.

## The one thing to get right

**A filament slot is not a nozzle.**

A `.3mf` can say "this part uses project filament slot N" — the numbers 1–4 in
Bambu Studio's filament panel. It cannot assign a **nozzle**. Bambu Studio
decides that at slice time, in a step it calls *filament grouping*
(Preview → Slicing Result → "Regroup filament").

`filament_map_mode` enum, from `PrintConfig.cpp`:
`Auto For Flush` (default), `Auto For Match`, `Auto For Quality`, `Manual`,
`Nozzle Manual`. **Only `Manual` lets the file's `filament_map` win.** Every
Auto mode hands the decision back to the slicer.

On the machine this was built against — one 4-slot AMS on the main nozzle,
nothing on the auxiliary — grouping puts *every* slot on nozzle 1 and builds a
prime tower. Any claim that this project gets "two colors with no purge tower"
is wrong; an earlier README said exactly that and it had to be retracted.

It matters less than it sounds when the artwork occupies its own layer band
above the backer, which is how these plates are built: no layer holds two
colors, so the whole print is **one** filament change.

## Six ways in

The table is the index; each way has its own section further down.

| way | input | module | right for |
|---|---|---|---|
| `silhouette()` | image | `geometry.py` | logos, lettering, line art with a clean edge |
| `detail()` | photo | `geometry.py` | photographed objects, where a silhouette gives a blob |
| `separate()` | flat art | `palette.py` | emoji, icons -- the artist already drew the regions |
| `scan2print` | photogrammetry OBJ | `scripts/` | real objects, in metres, standing on one foot |
| `ink_art()` | shaded drawing | `poster.py` | illustrations, where the linework is the subject |
| `flatten()` | shaded colour art | `shaded.py` | renders and paintings, where colour is the subject |

All six end in the same place: polygons in mm -> solids -> `write_3mf`, with
one report shape (`report.py`) and one preflight (`verify.py`).

## Two ways to read an image

`silhouette()` takes the outline, preferring the alpha channel over any
brightness guess. Right for logos, lettering, line art.

`detail(feature=...)` takes interior shading by local contrast, for photos of
objects where a silhouette gives a blob. The reference case was a photograph
of a pleated bun (a bao, not shipped with the repo): silhouette mode produces
an oval on an oval, detail mode produces the pleats.

Two things make it work and are worth preserving:

- **Every knob is a fraction of the figure's size**, measured from the mask's
  own area, not the canvas. A bigger photograph of the same object must give
  the same plate, not a finer and noisier one.
- **The rim is eroded away before detection.** A silhouette edge is a huge
  local-contrast event and would otherwise swamp the real features. How much
  gets eroded defaults per feature and that asymmetry is physical, not a
  fudge: the curved edge of a lit object is itself a highlight, so `ridges`
  needs a much wider exclusion (0.085) than `creases` (0.025), which have no
  bright twin at the rim.
- **`feature` decides whether the relief is right way up.** `creases` raises
  what is darker than its surroundings, which is correct for a drawing, where
  the mark is the dark thing. `ridges` raises what is lighter, which is correct
  for a photographed object, because the part catching the light is the part
  that physically stands proud. Choose wrong and you raise the valleys and sink
  the peaks -- it still looks like a pattern, which is why the bun came out that
  way at first and had to be corrected by eye.

Shading detail has no idea how wide an extrusion is. Always pair it with
`thicken()` at roughly half the minimum printable feature, or the thin lines
are real in the photograph and absent in the print. `at_risk_pct` in the CLI
report is the check; it should read 0.

## Settings come from the machine, never from inference

`--settings` (or `profiles/default.3mf`, used when present) copies
`Metadata/project_settings.config` out of a project saved from Bambu Studio
into the output, wholesale. No capture ships: it describes one printer and
its loaded spools, so `profiles/` is gitignored.

Do not compose that config by hand. It is ~580 interdependent keys and a
half-resolved one is **worse than none**, because Bambu Studio applies
whatever it finds instead of falling back to the user's presets. Patch a
known-good capture; that is what `project_settings(..., pin=...)` does.

Re-capture when the printer or filament setup changes:
Bambu Studio → set up a plate → `File → Save Project as...`

What the reference capture settled -- the one from the machine this was built
against, which is not shipped -- read straight from it:

```
filament_map        ["1","1","1","1"]      all four slots -> nozzle 1
filament_map_mode   "Auto For Flush"       the slicer decided, not the file
extruder_ams_count  ["1#0|4#1","1#0|4#0"]  4-slot AMS on nozzle 1, none on nozzle 2
flush_volumes_matrix  measured per colour pair, 90 to 900 mm3 -- dark to
                      light is the expensive direction
```

### A capture cannot be made from the command line

Tried with Bambu Studio 02.08.02.61's CLI (`--load-settings`,
`--load-filaments`, `--export-3mf`, no slicing), and it fails in the worst
way: it reports success.

- **Bundled presets passed as they are** (`resources/profiles/BBL/...`): the
  CLI reads only the leaf file and ignores `inherits`. Filaments do not load
  (one slot, density 0) and the process falls back to generic defaults --
  `outer_wall_line_width` 0, `sparse_infill_density` 20% instead of 15%. A
  `--datadir` holding the system presets changes nothing.
  `project_settings()` now refuses exactly this shape -- a filament density of
  0, or fewer colours than filament presets, neither of which a GUI save ever
  has.
- **Inherits chains flattened first**, so no value is invented: the machine now
  parses to exactly the `X2D` table and every placement check here passes --
  while the start, end, filament-change, layer-change and timelapse G-code are
  generic templates (one dated 2023), `flush_volumes_matrix` is a flat 280,
  the per-nozzle-variant filament lists are not expanded, and the colours still
  do not load. The G-code is what homes and primes the printer.

The GUI does preset work the CLI does not: G-code templates per machine,
variant expansion, the device's AMS and nozzle state. So the only capture is a
project saved from Bambu Studio. Nothing in this repo can tell a flattened CLI
config from a real one by its machine keys, which is exactly why not to build on
one.

## Supports

`--support-interface SLOT` patches the capture: interface filament, supports
on, and every interface gap to zero.

The zero gap is the interesting part and it is conditional. Dissimilar
plastics part cleanly with no gap, which is exactly why a second nozzle is
worth spending on supports; identical plastics weld. So the flag compares
`filament_type` between the interface slot and the object's slot and warns
when they match and the slot is not flagged `filament_is_support`. Do not
remove that check to silence a warning -- it is the difference between a
support that snaps off and a part that is ruined.

Body stays in the object's filament (`support_filament = "0"`). Bambu's
support materials are sold for the interface only, and the body is where all
the volume is.

**Untested on hardware.** No support material has been loaded on the reference
machine, so this writes plausible settings that nobody has printed. Treat the first
print with it as an experiment.

## Proven, and by what

| Claim | Evidence |
|---|---|
| Valid 3MF | `lib3mf` parses it; meshes `manifold_and_oriented` |
| Parts fit each other | `cross_check` per layer band: zero overlap, seam gaps under 0.1% |
| ID graph sound | every id in `model_settings.config` resolves; parts match components 1:1 |
| Geometry printable | watertight, layer-aligned, fits bed, clear of extruder-2 dead zone |
| Bambu reads it as a project | one object, two parts, correct stacking and slots, on a real X2D |
| Opens with zero setup | `--settings` output lands on X2D + `0.20mm Standard @BBL X2D` + 4 slots |
| Slices | 4.38 m / 13.40 g / 29m21s for a three-letter 90 mm name plaque |
| Prints | every worked example in this file, printed on the reference X2D |

**Not proven:** that `--pin 4:2` actually moves a part to the auxiliary nozzle
*on the printer*. The file is written correctly and range-checked, but the
machine has no filament on nozzle 2, so nothing has printed that way. Load the
aux nozzle before trusting it.

The optional `lib3mf` check reports Bambu's unqualified `printable="1"`
build-item attribute as a namespace warning. It is kept because Bambu Studio
expects it; the meshes still pass lib3mf's manifold and orientation checks.

## What actually goes in the aux nozzle

The aux nozzle exists for support material -- the X2D is marketed on "easy
support removal" and that is the second head's job. The materials named for
it are a short list:

| material | role | note |
|---|---|---|
| PVA, BVOH | dissolvable support | the headline case; washes away, so interface adhesion stops mattering |
| Support for PLA/PETG | breakaway | TPU+PET+PCT; reported unreliable on dual-nozzle machines (see below) |
| official PLA | second colour, or support **for PETG parts** | PLA cannot support PLA -- identical plastics weld |

Two traps found the hard way:

- **PETG is not on the list.** It is the obvious choice (dissimilar to PLA,
  cheap, and Bambu's own "Support for PLA (New Edition)" is itself PETG/PET),
  and the usual PETG-support horror stories are single-nozzle purge
  contamination that a second nozzle removes. But the firmware flags it as not
  recommended in this nozzle and warns that quality will drop -- the aux is
  **Bowden**, and Bowden plus stringy PETG is the bad combination. Reasoned
  support for using it anyway is not the same as it being recommended.
- **Support for PLA/PETG works better on single-nozzle machines**, because PLA
  residue in the shared nozzle helps it bond. A dual nozzle removes exactly the
  contamination that was propping it up, so the interface under-adheres. Its
  strength depended on a flaw the X2D does not have.

Also worth knowing:

- **Matte PLA fails as a support material** where regular PLA works.
- **PVA collapses in tree supports.** Bambu says to use normal supports with
  it. The captured profile has `support_type = "tree(manual)"`, so
  `--support-interface` warns about this rather than silently overriding a
  setting the user chose.
- PVA wants drying at 80C for 12h and storage under 20% RH, prints at
  220-250C, and needs a 0.4 mm nozzle or larger.

Reported failure mode with support materials on the X2D is warping -- the part
lifting off its support as it cools. The fixes people land on are aggressive
cooling reduction, long drying, and a zero Z-gap, which is what
`--support-interface` writes.

Sources are manufacturer docs and owner reports, not a print done here. The
authoritative page is `wiki.bambulab.com/en/x2d/manual/auxiliary-extruder-intro`.

## Facts worth not rediscovering

- **The X2D's nozzles are not equals.** Verbatim from the machine profile:
  `extruder_type ["Direct Drive", "Bowden"]`, `extruder_printable_area
  ["0x0,256x0,256x256,0x256", "20.5x0,256x0,256x256,20.5x256"]`,
  `extruder_printable_height ["261","256"]`. Extruder 2 gives up the left
  20.5 mm of the bed and the top 5 mm of Z. The bed shows it as the "Left
  nozzle only area" stripe.
- **`BambuStudio:3mfVersion` is the project gate**, per `bbs_3mf.cpp`. Without
  `<metadata name="BambuStudio:3mfVersion">1</metadata>` inside `<model>`, the file is geometry-only and every piece of
  part metadata is silently discarded.
- **PrusaSlicer is not a proxy for Bambu.** It cannot read
  `model_settings.config`, so it splits components into separate objects and
  drops each to the bed. That looks exactly like a broken file and isn't. It
  cost an hour once. The current `verify.py` reads native archive geometry directly; it no
  longer invokes PrusaSlicer.
- Min printable feature = 2 × line width = **0.84 mm** on a 0.4 nozzle. The
  check reports *area at risk* rather than pass/fail, because a few mm² of
  letter tips at risk is fine and a whole thin ring vanishing is not.
- The public profiles are fetchable and were used to build the machine table:
  `raw.githubusercontent.com/bambulab/BambuStudio/master/resources/profiles/BBL/`
  — `machine/Bambu Lab X2D 0.4 nozzle.json`,
  `process/0.20mm Standard @BBL X2D.json`. Neither contains `filament_map`;
  it is a project-level setting, so a saved project is the only source.

## Gotchas when an agent drives Bambu Studio

From driving the GUI through computer use (Claude Cowork, in this case).

- **macOS file dialogs are a different process.** A computer-use grant for
  BambuStudio does not cover its Open/Save panel. Opening and saving files
  needs the human: press the menu item, then ask.
- `File → Save Project as...` is **disabled until a project is open** and the
  window is frontmost. Looks like a permissions problem; isn't.
- Bambu Studio's object tree is under **Process → Objects** in the left panel.
  Expand the object to see parts and their `Fila.` numbers.
- **Git in an agent's connected folder needs delete permission**, or `git` cannot
  unlink its own `.lock` files and every commit poisons the next one. Request
  it once, up front.
- The app's UI is custom-drawn. Radio buttons in its dialogs often will not
  toggle via background clicks. Don't burn turns on it.

## Conventions

- Comments explain **why**, not what. The what is in the code.
- Machine facts are quoted from the profile, with the key name, so the next
  person can check them.
- `verify.py` skips the optional `lib3mf` check when absent; core geometry
  and metadata checks always run.
- Anything generated goes in `out/`. Anything captured from hardware goes in
  `profiles/` and is gitignored: a capture describes one printer and its
  spools. The tests use `tests/stock_capture.py`, quoted from Bambu's public
  profiles, so they never depend on anyone's capture.

## The upload workbench and mosaics

`app.py` serves a plain HTML/CSS/JavaScript upload workbench from `web/index.html`
using Python’s standard library HTTP server. Run `python app.py`, then open
`http://127.0.0.1:8765`. No UI framework, deliberately; Streamlit was ruled out.
`x2d/mosaic.py` partitions an image into a coarse physical grid mapped to up to
four captured filament colors. Exact shared cell boundaries avoid gaps and
volume overlaps. A continuous rectangular backer supports all color islands.
No image-generation service or reconstruction model is involved. Videos are
selectable timestamp frames; multiple uploads produce individual projects.

Do not apply the old "one filament change" claim to mosaics: all palette colors
share the shallow artwork layer band. The app leaves profile colors, purge,
nozzle grouping and prime tower settings intact. Layer height, minimum cell
and bed come from the chosen capture; base and base + relief must end on its
layer grid, and the workbench runs the same preflight as `verify.py` before it
offers a download. `scripts/media2plate.py` is the CLI.

How a mosaic behaves, so nobody has to rediscover it: `--cell` is a physical
pixel size, never below the capture's minimum feature. Colours are matched to
the capture's spools in Lab, without dithering, so photographs come out
coarse and limited-palette and bold images and logos come out best.
Transparent pixels composite against the backer colour. The palette is every
slot in the capture, not a fixed four. A 0.6 mm relief is three colour layers
at 0.2 mm, and a thin light colour may show the backer through it.

`write_3mf` now copies meshes before moving them, escapes XML names, checks
positive/captured slots, solid validity, bed placement and manual nozzle reach.
`scripts/verify.py` now reads archive geometry via `x2d/validation.py` rather
than assuming per-part STL sidecars exist. It returns a failure exit code on
invalid geometry or metadata. It does not invoke PrusaSlicer. Its scope is our
inline-mesh, one-level native files. `tests/test_pipeline.py` exercises palette
coverage, mesh volume/nonoverlap, transparency, video seeking, XML names,
nonmutation, placement and invalid inputs.

## Direction

The next step is an agent-assisted modeling workflow; see
[`AGENT_WORKFLOW.md`](AGENT_WORKFLOW.md). The current mosaic app is a
prototype, not completion of the image-to-3D objective. Keep future
capabilities separate from verified behavior.

## Local slicing and layer review

`scripts/slice_local.py` invokes Bambu Studio's CLI on any `.3mf`, with an
isolated data directory; it never sends or queues prints.
`scripts/review_layers.py` plots the actual outer-wall and support G-code,
including XY arcs, and `scripts/compare_slices.py` puts several slices of one
model side by side.

That loop -- model, preview, slice locally, plot the sliced layers -- was
proved on the first 3D candidate, a procedural pleated bun. The bun code is
gone; the loop is what it left. Nothing in this repo submits, queues or
controls a print, and that boundary is deliberate: keep it. Bun-v2 sliced successfully with the captured profile
(Bambu Studio 02.08.02.61): 220 layers, about 37.43 g, 1h 54m 27s estimated,
zero mid-print filament changes, and sliced outer walls at 10, 24, 36 and
42 mm that keep the upper folds. Its log carries `Invalid T command`
diagnostics for T65279 and T65535, preserved in the report and unexplained. Do not equate this with a physical print test or with
arbitrary image reconstruction.

## Flat-color art: `x2d/palette.py`, `scripts/art2plate.py`

A third way in, alongside `silhouette()` and `detail()`. Those two ask "what is
the figure?" of a photograph. Emoji, logos and icons have already answered it:
the artist drew flat regions with exact edges, and the only open question is
which filament each region prints in. So `separate()` keeps the artist's edges
and throws away nothing.

`--map '#RRGGBB=SLOT'` is the whole design decision, and it is an input, not an
inference. Every opaque pixel goes to the **nearest listed color in Lab**, so a
shading tint you leave out of the table follows its parent color instead of
becoming a fifth region -- Twemoji's `#FCAB40` skin shadow lands on the skin
without being named. Transparent pixels belong to no slot.

Two things earn their keep:

- **Adjacent parts of one object must share a boundary.** Contours traced per
  color run through each region's own edge pixels, so two touching colors come
  out about a pixel apart -- a hairline of bare plate between them. Each slot
  is grown by `seam_mm` and the slots already claimed are cut out of it, which
  closes the gap without ever double-printing. Proven by
  `sum(areas) == union.area == footprint.area`, in `PaletteTests`.
- **`seam_mm` defaults to 1.5 px of the image you actually passed**, not to a
  constant. The hairline is a property of the raster and nothing else, and a
  fixed default is silently too small on a coarse image -- which does not throw,
  it just prints a plate with gaps between the colors. A constant `0.05` was the
  first version and it was wrong at 5 px/mm while looking right at 50.

`--priority` decides who keeps the shared edge. Put the small features first --
eyes, a mouth -- so the big fill around them yields, not the detail. It moves
the boundary; it can never open a gap or an overlap, which is asserted.

**Every color shares one shallow relief band above the plate**, so the "one
filament change" claim from the two-color plaques does NOT apply, exactly as it
does not for mosaics. The report prints `filament_changes_upper_bound` =
relief layers x colors. Expect a prime tower.

`project_settings(..., colors={slot: "#RRGGBB"})` and the CLI's `--recolor`
patch the captured swatch for a spool that changed since the capture. It fixes
the preview and the filament panel and **nothing else** -- `flush_volumes_matrix`
was measured against the filament that was loaded that day. Re-capture when the
purge matters.

The rasteriser for SVG input is `cairosvg`, kept out of the hard dependencies:
`render_svg()` raises with an instruction to pass a PNG instead if it is absent.

Worked example, the shrug dude emoji on the captured 4-slot AMS:

```bash
uv run scripts/art2plate.py samples/shrug.svg --width 72 --base 3.0 \
    --base-slot 2 --priority 4 1 3 \
    --map '#FFDC5D=3' '#FCAB40=3' '#FFAC33=4' '#FA743E=1' '#662113=1' '#C1694F=4' \
    --recolor '2=#8E9089' --out out/shrug.3mf
```

White face, red hair, black shirt and eyes, on a grey plate. The plate has to
differ from the shirt or the shirt stops existing -- the first attempt used a
black plate and lost it. 80 x 78 x 3.6 mm, `verify.py` valid, `lib3mf`
manifold, 0.1% area at risk. **Printed.**

### Emo shrug: when the palette is right but the artwork is not

`samples/shrug_emo.svg` and `samples/shrug_emo_streak.svg` are the shrug dude
emoji from Twemoji (CC-BY 4.0, see `NOTICE`) with one path appended: a side-swept bang in the hair's own fill
colour, so it needs no new entry in `--map` and flows through the existing
separation unchanged. The streak variant adds a second path in `#FF0000`,
a colour absent from the original, purely so the table can send it somewhere
the rest of the hair does not go.

That is the pattern worth remembering: **to add a region, add a path in a
colour, not a special case in the pipeline.** `separate()` already partitions
by colour, so new artwork costs an SVG path and a `--map` entry.

The reason the bang exists at all is that recolouring alone could not get
there. Black hair, white face, black shirt and a red mouth is the emo palette
exactly, and on the stock rounded bob it still reads as a person with dark
hair. Emo is the fringe over one eye, which is geometry.

The streak also earns its filament change. Red is otherwise only the mouth,
16 mm²; the streak takes it to 107 mm². A colour that appears in a few mm²
still costs a full change and a purge on every layer of the relief band, so it
is worth asking whether it is buying anything.

```bash
uv run scripts/art2plate.py samples/shrug_emo_streak.svg --width 72 --base 3.0 \
    --base-slot 2 --priority 4 1 3 --recolor '2=#8E9089' \
    --map '#FFAC33=1' '#FFDC5D=3' '#FCAB40=3' '#FA743E=1' '#DD551F=1' \
          '#662113=1' '#C1694F=4' '#FF0000=4' --out out/shrug_emo_streak.3mf
```

80 x 78 x 3.6 mm, valid, manifold, 0.5% area at risk. **Printed.**

## Adjacent parts must share a boundary, and there are two ways

Whenever one object has parts that touch, they have to touch *exactly*. Too far
apart and bare plate shows through in a hairline; overlapping and two parts
claim the same volume, which the slicer resolves however it likes. Both slice
without complaint. This repo now contains two correct answers, and **which one
is right is decided by the input, not by taste**:

| the boundaries are | do this | example |
|---|---|---|
| generated by us | share the coordinates outright | `mosaic.py` builds every cell from the same `dx`/`dy`, so neighbours are equal by construction |
| recovered from a raster | grow and reconcile | `palette.py` grows each slot by the pixel pitch, then subtracts what is already claimed |

Contour tracing cannot use the first method: each region's outline runs through
its own edge pixels, so two touching colours come out about a pixel apart and
there is no shared coordinate to reuse. A grid cannot need the second: there is
nothing to reconcile when both sides came from the same number.

Do not invent a third. If a new generator needs one, it belongs in whichever of
these two families its input puts it in.

## The check that makes the above enforceable

`x2d/validation.py:cross_check` is the only thing here that looks at how parts
relate to **each other**. Everything else asks whether a part is a good solid in
a legal place, and a plate with hairline gaps between its colours passes all of
it. That is not hypothetical: the first version of `palette.py` shipped exactly
that bug and `verify.py` said `valid: true`.

It works per layer band, on the footprint of each part -- taken from its
upward-facing triangles, not from a cross-section -- and measures two things:

- **overlap** — summed part area minus the union. Zero by construction in every
  generator here, so anything above float noise is a generator bug.
- **seam gap** — morphological closing of the union at half the minimum
  printable feature, minus the union. Whatever the closing swallows was a void
  too narrow to print, i.e. a seam nobody designed; anything wider survives, so
  a keychain hole and the outer margin do not register.

**Footprints from top caps, not sections, and that distinction cost a round
too.** `extrude()` concatenates one solid per polygon, so any part made of
touching pieces -- every mosaic run, every multi-island colour -- carries
coincident internal walls. A section cuts those walls as well, and the extra
rings do not assemble back into the right region: the first version reported
33 mm2 of overlap on a mosaic whose meshes provably intersect in 0 mm3, and it
was `tests/test_upload.py` that caught it, not anything written for the check.
Top caps sidestep it entirely -- the pieces have disjoint interiors so their
caps tile the footprint exactly, and vertical interior walls project to
nothing. It also drops trimesh's path assembly, which wants scipy, networkx
and rtree to turn a cut into loops.

`_footprint` returns None for anything that is not a prism (footprint x height
must equal volume) rather than guessing, and the caller reads that as "cannot
compare". That keeps the check inside the scope this module claims.

Closing rather than hunting for holes, and that distinction cost a round:

- A seam is **not always enclosed**. Two colours meeting at the artwork's outer
  edge leave a channel open at both ends, which is not a hole in the union at
  all. Hole-hunting misses it.
- A seam is **not a pairwise distance** either. Two parts that touch along most
  of their border and part company at one end have a minimum separation of
  exactly zero.

Calibration, so the thresholds are not vibes: a correctly seamed plate measures
0.006-0.015% of the band; the fixed-seam bug measured 0.48% at 50 px/mm and
0.75% at 5. `SEAM_GAP_LIMIT` is 0.1%, an order of magnitude clear of both.

Note that a two-colour plaque reports no bands at all, correctly: the backer and
the artwork live in different layer bands, so no band holds two parts and there
is nothing to cross-check.

## Pixel constants: a fraction, but not always

The seam bug generalises to "a constant in pixels is a bug waiting for a
different input size", and `detail()` already follows that rule properly --
every knob is a fraction of the figure's own span.

`silhouette()`'s blur is the exception that keeps the rule honest, and it was
measured rather than assumed. Making it a pure fraction **made it worse**: a
300 px noisy logo went from 2 blobs to 9, because sensor noise is a per-pixel
phenomenon and scaling the kernel down on a small image just lets speckle
through. What a fixed kernel does get wrong is the other end, where a large scan
has grain several pixels across. So it is `max(BLUR_MIN_PX, BLUR_FRAC * width)`:
a floor that preserves the tuned behaviour, and a fraction that only takes over
above it. The 1899 px bun photograph came out byte-identical either way,
which is how the change was checked.

The rule, restated: **scale by the figure when you are reconstructing the
figure's geometry; scale by the pixel when you are fighting the sensor.**

## uv is the environment, and the lock is a test

`uv sync` from `uv.lock`, `uv run` for everything. `.python-version` pins
development to 3.14; `requires-python` is 3.11+ because 3.9 and 3.10 are past
end of life and holding the floor there drags the whole resolution backwards.

The dependency ranges stay loose on purpose -- the code really does only use
these libraries' dullest corners -- and `uv.lock` is what makes a run
reproducible. Do not tighten a range to pin a version; that is the lock's job.
Tighten one only when a version genuinely breaks the code, and say which.

**Locking earned its keep immediately.** The first clean environment it built
failed four tests with `ModuleNotFoundError: No module named 'scipy'`, then
`networkx`. Both are trimesh's optional graph stack, both are needed by
`section()` in `validation.py`, and neither was declared -- every machine this
was written on already had them from something else. The failure mode is the
bad kind: trimesh imports fine and raises from inside the call, so nothing goes
wrong until the moment you use it.

The general lesson, which is the same one as the seam and the blur: **a thing
that works everywhere you happen to have run it is not the same as a thing that
works.** A lockfile is not paperwork here, it is the only way an undeclared
dependency ever shows up.

Two more things fell out of the same switch, both worth keeping:

- `tests/test_upload.py` imports `app` from the repo root. `python -m unittest
  discover` got that from the cwd for free; pytest against an installed package
  does not, hence `pythonpath = ["."]`. The tests were not passing before, they
  were only being invoked in the one way that happened to work.
- Running the whole suite under the lock is what surfaced the mosaic false
  positive above. Two tests short of the full suite and it would have shipped.

The third one arrived with `scan2print.py`: `rtree`, which `Path2D.polygons_full`
uses to nest layer loops into polygons with holes. Same symptom, same cause, found
by a first clean `uv run` on a fresh machine, not by the suite.

`trimesh[easy]` would have covered it bluntly at the cost of 21 extra packages.
Named explicitly instead, while the list is short enough to name.

## Scans: `scripts/scan2print.py`

A fourth way in, for photogrammetry output (KIRI Engine OBJ) rather than
images. The first scan tried, a skeleton figure, "printed filament in random spots" from a file
Bambu Studio sliced without complaint. None of it was a mesh defect:

- **KIRI's OBJ is closed once the texture seams are welded.** It writes a
  vertex per UV chart, so with plain loading it reads as 387 open shells and
  every hole check screams. `merge_vertices(merge_tex=True, merge_norm=True)`
  first, then judge it: 124,910 verts, watertight, genus 46, volume positive.
- **It is in metres.** 2.78 units tall. Imported as mm it is a 3 mm figurine
  and gets scaled by eye, and nobody re-checks whether the ribs still clear
  0.84 mm. `--height` is the only scale input; everything derives from it.
- **A scan stands on whatever touched the ground.** Here one foot; the other
  was 2 mm higher at print scale and off the edge of any plate hugging the
  sole. Not a geometry error, so no checker reports it. The script cuts a flat
  sole, then gives any island that first appears within `--contact` mm of the
  cut, with nothing beneath it, a riser into the plate.
- **The plate is sized by the centre of mass, not the feet.** Outstretched
  arms put the COM 25 mm forward of the sole hull. Plate = convex hull of
  every contact island plus the COM projection, plus `--margin`.
- **Loose shards print in mid-air.** One 624-face fragment floated by the
  left elbow. Largest body wins; the rest is listed in the report.

The report's `print` block is the honest part: `at_risk_pct` (area an opening
at half the min feature erases) and `floating_islands` (layers with nothing
beneath them, i.e. what needs support). For the skeleton at 200 mm: 0.4% at
risk, all in the fingers around z=120, and ~46 floating islands, all arms and
ribs. **The 200 mm output is printed.**

With `--settings` it also writes a native `.3mf` through `write_3mf`, one part
on `--slot`, patching exactly two keys of the capture: `support_type` to
`tree(auto)` (the capture's `tree(manual)` means "only where painted", which
is nowhere) and `detect_thin_wall` to `1`. `verify.py` passes it; Bambu Studio
opens it on the X2D profile with supports already on.

**Sliced locally first.** `slice_local.py` (now takes any `.3mf`) on the
200 mm skeleton: 1000 layers, 6h30, 68 g, of which **49% is support** (33 g) --
the arms, hands and every rib are horizontal bars over nothing. Heaviest
support layers are the hands at z=118-123. `review_layers.py` (now takes a
G-code and `--z` list) shows the finger bones survive as walls at z=121, tree
trunks land on the bed beside the plate for the arms, and support grows
*between the ribs* at z=150, which is the removal job to expect. The raised
foot's sloped sole also picks up a little support on the plate: the riser fills
the first island only, and the slicer covers the slope. Both CLI logs carry two
`Invalid T command (T65279/T65535)` errors; the bun's slice had the same two and
they are harmless.

### Support presets, and what the slicer did with each

`--supports` is a name in `SUPPORT_PRESETS`, a dict of patches on the capture.
Three of them were sliced locally on the same geometry (`compare_slices.py`):

| preset | time | g | support g | what changed |
|---|---|---|---|---|
| auto  | 6h25 | 68.1 | 33.2 | cocoon: every bump on the scanned skin gets a branch |
| plate | 6h18 | 66.3 | 31.4 | almost nothing: the ribcage trees already stood on the bed |
| slim  | 5h44 | 58.4 | 25.3 | hands and arms still held; ribcage support down ~75%; skull nearly bare |

`support_on_build_plate_only` was the intuitive fix for trees inside the
ribcage and it did not work, because trunks on the bed can route up into the
cavity anyway. What worked was `support_critical_regions_only` plus
`tree_slim`: supporting cantilevers and sharp tails, not the skin. The skin of
a scan is bumps all the way down, and every bump leans out past the threshold.

Tree Slim prints support on its own layer heights (1444 distinct Z values for
1000 model layers), so any per-layer analysis has to merge layers within a
tolerance or it will show a figure with no support under it -- the first
`review_layers.py` did exactly that.

`--plate-slot` makes the plate its own part in another colour, carved out of
the figure with a boolean so they share the boundary; riser tops are snapped
to the layer grid because a part boundary must sit on it (`verify.py` checks).
One filament change, at the plate top. **Printed.**

### The CLI slices for the wrong nozzle unless the file says otherwise

The first slim slice's G-code header read `filament_map = 2,1,1,1`: the one
filament in use was assigned to the **aux** nozzle, and Bambu Studio's send
dialog then showed the main nozzle empty and demanded PLA in a nozzle that has
no AMS. Cause: the capture is `filament_map_mode = "Auto For Flush"`, which
hands nozzle grouping to the slicer, and the headless CLI -- with an empty
`--datadir` and no printer to sync against -- rewrote `extruder_ams_count` to
claim an AMS on both nozzles and then picked the second one. Everything the
"filament slot is not a nozzle" section warns about, reproduced by accident.

`scan2print.py` now pins every slot to the nozzle the capture already maps it
to (`project_settings(..., pin=...)`, which flips the mode to Manual). The GUI
saved that map against the real AMS layout, so it is nozzle 1 for every slot on
the reference X2D and nozzle 2 for slots on a second AMS; Manual stops the CLI
from regrouping. A single-nozzle printer has nothing to mis-assign. Check any CLI slice's header for
`filament_map` before sending it; the GUI dialog's empty "Main Nozzle" box is
the symptom.

```bash
uv run scripts/scan2print.py scan.obj \
    --height 200 --out out/figure/figure_200mm
```

## Drawings: `x2d/poster.py`, `art2plate.py --ink`

A fifth way in, for a shaded illustration -- the worried-man reference in
`samples/worried_guy.png`. `silhouette()` and `detail()` ask "what is the
figure?" of a photograph; `separate()` trusts an artist who already drew flat
regions. A drawing is neither: the answer IS the drawing, but it was spread
across 144,815 distinct RGB values, and `separate()` pointed straight at it
gave 117 islands with **61% of the linework below the nozzle**. Unusable, and
it would have sliced without complaint.

Two things had to happen first.

**A stroke is continuous; a threshold is not.** Soft pencil edges mean any
single luminance cut catches the dark cores and drops the fades, so outlines
come out as dotted lines -- measured at four thresholds, all dashes. Hysteresis
encodes what is true about a drawing instead: a moderately dark pixel connected
to a definitely-dark one is the same stroke. Seed on `strong`, grow through
`weak`, keep components containing a seed. `--ink 110,175` is the default;
raising WEAK pulls in interior detail, and at 215 the jacket's roses arrive as
blobs.

**Ink is about two pixels wide whatever the file's resolution.** 0.18 mm at
72 mm here against a 0.84 mm minimum. The mask is dilated by half the minimum,
in pixels derived from the physical width. At-risk area goes 61% -> 0.3%.

That dilation happens **in the raster, before any region is traced**. Growing
polygons afterwards would push the ink into its neighbours and reopen the
overlap `separate()`'s seam logic exists to prevent. Growing the mask first
means the ink simply owns those pixels -- the raster form of listing small
features first in `--priority`.

**Non-ink is transparent, not white.** Paper is the plate showing through. A
white part would cost a filament change and a layer band for the 90% of a
drawing that is paper.

**`detail(feature="creases")` does not work here**, despite being the tool this
file points at for ink. It returned 0.34% coverage, essentially blank. The
reason is worth keeping: `detail()` takes `silhouette()` as the figure and
erodes its rim, but on a drawing sitting on pale paper `silhouette()` picks out
the *strokes* as the figure, so the rim erosion eats the very thing being
looked for. `detail()` wants a photographed object against a background it can
separate. A line drawing is not that.

One filament, one colour in the relief band, so **one change and no prime
tower** -- the two-colour plaque case, and the report now says so. It used to
print `layers x colours` regardless, which read as 3 for these plates; that is
the sort of number that quietly becomes "it needs a prime tower" when it does
not.

```bash
uv run scripts/art2plate.py samples/worried_guy.png --width 72 --base 3.0 \
    --ink 110,175 --map '#000000=1' --base-slot 3 --out out/guy_white_black.3mf
```

80 x 106 x 3.6 mm, valid, 0.3% at risk, 1 filament change. `verify.py` reports
no bands, correctly: plate and artwork are in different layer bands, so there
is no part-to-part comparison to make. **Printed.**

## Shaded colour art: `x2d/shaded.py`, `art2plate.py --shaded`

A sixth way in, for a render or a painting: colour is the subject, but each
material is spread across dozens of shades. Pointed straight at a character
render, `separate()` put 27-32% of every colour at risk, and `verify.py`
rejected the plate for 480 seam gaps. The same table through `--shaded`:
valid, and at most 1.6% at risk.

The fix is the drawing lesson again, for colour: decide the regions in the
raster, before any contour is traced.

- **Denoise, then send each shade to a slot** (nearest in Lab, as in
  `separate()`). The median is 7 px because render noise is per pixel.
- **Open the figure, then each slot, at the minimum feature** from the
  capture. What the figure's own opening removes is dropped; what a slot's
  removes inside the figure goes to the nearest surviving region -- a hole
  left there would be a seam gap. Refilling the outline as well was the first
  version, and it handed every thin strand straight back, in a neighbour's
  colour.
- **Halo** (`--halo SLOT`): a ring round the figure, so a region that matches
  the plate (black boots on a black plate) still reads. The canvas is padded
  first, or a tight crop gets no ring on the side that touches the edge. The
  ring's kernel is 2r+1 across: sized r, `--halo-mm 1.2` made 0.6 mm, under
  the nozzle -- and a measurement taken on that narrow ring once argued for
  putting the halo first in `--priority`. At the right width, first put 0.4%
  of the halo but 3.5% of the hair at risk; default order, 1.6% at worst.
- **Close notches** narrower than the nozzle, into the halo slot or the
  nearest region, or `verify.py` counts them as seam gaps.
- **Resample to 5 px/mm.** Finer carries nothing a nozzle can use and makes
  contour simplification fight the seam growth.

`<out>_flat.png` is the design, in spool colours: look at it before printing.
`separate()` itself gets one stand-in colour per slot, so two slots loaded
with the same white stay two regions.

**The automatic table is a first guess.** Without `--map`, the shades are
grouped (k-means, 8 by default) and each group goes to the nearest spool --
after stretching the groups' lightness and colourfulness ranges onto the
spools' and halving lightness's weight, because a render is a lit version of
its materials and shading moves lightness more than hue. Against hand-made
tables that scored 13 of 16 groups right on two images, against 11 for plain
Lab. The misses are deep, desaturated shadows going to black. The report
prints the table; edit it and pass it back as `--map`.

The test fixture draws its own stroke 0.5 mm wide on purpose: a 2 px stroke is
removed by the denoise alone, and a test built on one passed with the opening
switched off.

## One report, one place: `x2d/report.py`

Five generators had grown five reports with **no key in common** -- not mostly
aligned, literally zero. `img2plate` never said what colour anything was,
`text2plate` never named the machine, `scan2print` never said whether it fit
the bed. Three of them each re-derived the filament swatch in their own copy
of `swatch = settings.get(...)` / `hue = lambda slot: ...`, and one copy was
missing, which is exactly how every name plaque came out gold-on-grey whatever
was loaded. A thing copied four times is wrong in at least one of them.

`plate_report()` is now that one place. It also fixed a real error on the way
in: `plate` and `artwork` are separate arguments because they are separate
layer bands, and the first version took one dict of everything -- so a
two-colour plaque counted its own plate as a second colour in the relief band
and reported **10 filament changes for a print that needs 1**.

### The numbers come from the capture, including the ones that look universal

`filaments()` reads colour, type, **density** and **cost** per slot out of the
captured project. That is the same rule as everything else here and it is not
a formality. The STL viewer written for the pre-X2D printer hardcoded what
everyone hardcodes, and the capture disagrees with three of four:

| the viewer assumed | the capture measures |
|---|---|
| 1.24 g/cm3 | `filament_density` 1.26, 1.26, 1.26, **1.32** -- per slot; matte is denser |
| 45 degrees overhang | `support_threshold_angle` **30** |
| 20% infill | `sparse_infill_density` **15%** |
| -- | `filament_cost` 19.99/kg, so cost is computable honestly |

Per-slot lists in the capture are 0-based and Bambu Studio's panel numbers
slots from 1. That conversion happens in `filaments()` and nowhere else.

### Mass is an upper bound, and it was measured as one

`mass_g()` is solid volume x density. Against the only slice on record -- the
three-letter name plaque, 13.40 g from Bambu Studio -- it reads **15.17 g, 13% over**. The
gap is the 15% sparse infill in the core, which the bound counts as solid.

So it is honest as a ceiling and for comparing two designs, and it is not a
quote. Doing better means modelling walls, top and bottom shells and infill,
which is writing a slicer, which this repo does not do. Anything needing a
real number goes through `slice_local.py`.

## Scripts are where the bugs escape

Worth stating plainly because the evidence is one-sided. Every bug this repo
caught before a human saw it lived in `x2d/`: the palette seam, the
`cross_check` false positive, the undeclared scipy and networkx. Every bug that
reached a human lived in `scripts/`: the invented preview colours, the
filament count claiming 3 swaps for a one-swap plate, and `rtree` -- found by a
clean `uv run`, not by the suite.

The reason is not that scripts are riskier code. It is that **no script had a
test**, all eleven of them, while every module did. And the scripts are not
thin: argument validation, the filament lookup, the report assembly and the
preview call all live there.

`tests/test_scripts.py` runs the generator CLIs in-process on a tiny fixture
(`tests/test_machine.py` adds `scan2print` and `media2plate` on a
single-nozzle capture) and checks the two things a user actually relies on -- the project opens, and the report
does not lie. It writes to a real temp dir, never into the repo: a test that
writes into a connected folder cannot always clean up after itself, and `out/`
is for things someone asked for.

## The viewer, and machine facts as an API: `web/viewer.html`, `/api/machine`

An STL viewer written before this printer existed, folded in. It was already
good at the things a browser is good at -- orbit, overhang shading, orientation
scoring, cross-section, bed contact, connected components -- and those are
capabilities this repo did not have anywhere.

**What it had to stop doing is carry its own printer.** It hardcoded a 220 mm
bed, PLA at 1.24 g/cm3, a 45 degree overhang line and 20% infill. Against the
capture, three of the four are wrong:

| the viewer assumed | the capture says | key |
|---|---|---|
| 1.24 g/cm3 for everything | 1.26, and **1.32** for PLA Matte | `filament_density` |
| 45 degrees | **30** | `support_threshold_angle` |
| 20% infill | **15%** | `sparse_infill_density` |
| 220 mm bed | 256 mm | `machine.py` |

So `/api/machine` serves them and the page asks. When it cannot reach the
server it still runs, but it shows a banner saying the numbers are generic --
same rule as the preview: **a viewer reporting confident numbers for the wrong
machine is worse than one that admits it does not know.**

Serving the right number was not enough on its own. `support_threshold_angle`
is a slope measured from horizontal, so a downward face at slope t needs
support when t is below it (normal z < -cos(threshold)). The viewer's first
conversion measured from vertical, `-cos(90 - deg)`, which agrees only at 45
degrees -- exactly the old hardcoded value, which is why nobody saw it. At the
captured 30 it flagged every face under 60 degrees and skewed the orientation
scores with it. Anything the page puts into its HTML from an uploaded file --
part names, preflight errors -- goes in as text, never markup.

### The weight number was worse than no number

Its "realistic" weight was solid x 0.35, a multiplier for 20% infill plus
walls. On the name plaque that reads **5.3 g against a real 13.40 g slice, 60%
under**. The plain solid bound reads 15.17 g, +13%. The multiplier was tuned on
chunky parts; a 3 mm plate is nearly all wall and solid skin, so scaling the
whole volume by infill is simply the wrong model for what this repo makes.

The panel now shows the bound as the headline, labelled, with the infill figure
as an explicit floor beneath it. The 50 g cap became a user input -- it was a
library's rule, not a fact about anything here.

### A geometry viewer of a Bambu .3mf is showing you half the file

It parsed `3D/3dmodel.model` and stopped, which is every part merged into one
grey lump. `Metadata/model_settings.config` is where part -> filament slot
lives, and that intent is the only thing this repo actually owns. The viewer
now reads it, lists the parts with their slot and real filament colour, and
says "plain 3MF: no filament intent" when there is none -- which is also what
distinguishes our output from anything else's.

This is the same fact that makes PrusaSlicer look like it is showing a broken
file. Reading geometry alone is not reading the project.

### Where the line sits

The browser does geometry: orbit, overhang, orientation, section. The server
does the checks: `inspect_3mf`, overlap, seam gaps, layer-grid alignment. The
Preflight button posts to `/api/inspect` rather than reimplementing any of it
in JavaScript, and `/api/inspect` refuses an STL with a reason, because "valid"
means nothing for a file with no parts and no slots.

`app.py` and the viewer are now covered by `tests/test_scripts.py` for the same
reason the CLIs are.

**Not yet verified in a browser.** The module parses clean under `node --check`,
every `getElementById` has a matching element, there is no top-level
use-before-declaration, and all four endpoints answer correctly over HTTP. The
rendering itself wants one human look.
