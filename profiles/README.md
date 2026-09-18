# Printer captures

Your printer's settings, as a project saved from Bambu Studio. Everything in
this folder except this file is gitignored: a capture describes one printer and
the spools loaded in it that day, so none ships with the repo.

## Make one

1. In Bambu Studio, select your printer, process preset and the filament in
   each AMS slot.
2. Add any object to the plate. A cube is fine; only the settings are read.
3. `File → Save Project as...` and save it here as `default.3mf`.

Every script and the workbench use `profiles/default.3mf` when it exists. Pass
`--settings profiles/<name>.3mf` to use a different one, e.g. one per printer
or per filament setup.

It has to come from the GUI. Bambu Studio's command line can export a project
from presets too, but it produces a half-resolved config that still reports
success -- generic start G-code included. `docs/DESIGN.md` has the details.

Save a new one whenever you change printer, nozzle, process or spools. The
colours can be patched without a re-save (`--recolor SLOT=#RRGGBB`), but the
purge volumes cannot.

## What is read from it

Only `Metadata/project_settings.config`, and it is copied into each output
whole, never composed by hand: it has ~580 interdependent keys, and a
half-resolved one is worse than none. From it this repo reads the bed, each
nozzle's reach and feed, nozzle diameter, outer wall line width, layer
heights, and each slot's filament colour, type, density and cost. See
`docs/DESIGN.md`.

## Without one

`text2plate`, `img2plate`, `art2plate` and `scan2print` still work: the output
carries parts and filament slots, opens with whatever presets Bambu Studio has
loaded, and geometry is checked against the X2D. Their reports say
`"machine_from": "default (no capture)"` when that happened. Mosaics
(`media2plate` and the workbench) need one, because their palette is your
loaded spools.
