# Working in 3dexplore-claude

Instructions for coding agents (and people) changing this repo. The reasoning
behind every rule here is in [`docs/DESIGN.md`](docs/DESIGN.md), imported at
the bottom so an agent reads it automatically. Read it before changing
anything; most of it was expensive to learn.

## Boundaries

- **Do not add a slicer.** This repo owns geometry and part->filament intent.
  Bambu Studio owns machine profiles and toolpaths.
- **Never send, queue or control a print.** `scripts/slice_local.py` slices
  locally with an isolated data directory, and that is as far as it goes.
- **A filament slot is not a nozzle.** A `.3mf` assigns slots; Bambu Studio
  assigns nozzles unless `filament_map_mode` is `Manual`.
- **Printer facts come from the capture** (`Machine.from_settings`), with the
  `X2D` table in `x2d/machine.py` as the default. A printer constant anywhere
  else is a bug. Only the X2D has been verified (opened and sliced in Bambu
  Studio); other printers are tested against their public profile keys.
- **Never compose `project_settings.config` by hand.** Patch a capture saved
  from the Bambu Studio app; that is what `project_settings(...)` does. Its
  command line exports half-resolved configs that still report success, so
  it is not a capture source.
- **Heights sit on the layer grid, which starts at the first layer.** Use
  `Machine.on_grid` / `check_band`; "a multiple of the layer height" is wrong
  whenever the first layer differs.
- **Remove what the nozzle cannot print in the raster, before tracing.**
  `ink_art()` and `shaded.flatten()` both do; growing or shrinking polygons
  after `separate()` reopens the seams it closed.

## Conventions

- `uv sync`, `uv run` for everything. Never edit `uv.lock` by hand.
- Comments explain why. The what is in the code.
- Machine facts are quoted from the profile with the key name, so the next
  person can check them.
- Keep what is verified separate from what is not. An unperformed step (a
  slice, a physical print) is never reported as successful.
- Personal data stays gitignored: your own inputs in `inputs/`, printer
  captures in `profiles/`, generated files in `out/`. What a personal input
  teaches is what gets committed -- as code, tests with synthetic fixtures
  (`tests/stock_capture.py` stands in for a capture), and `docs/DESIGN.md`.
  Only cleared example inputs go in `samples/`.
- A new test counts once it has failed: put the bug back and watch it go
  red. Three tests written in one session passed with their bug still in --
  one letter too slanted to reach, one stroke the denoise removed first, one
  halo checked at its edge but not its width.
- When you change a script, give it a test (`tests/test_scripts.py` has the
  harness); scripts are where bugs have escaped. `review_layers` and
  `slice_local` are tested only up to the Bambu Studio call; `compare_slices`
  and `make_sample` have no tests yet.

@docs/DESIGN.md
