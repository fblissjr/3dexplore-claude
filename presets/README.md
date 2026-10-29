# Presets

Your own default settings, kept as data. `first_layer.json` is the source of
truth; edit it, then run `uv run scripts/first_layer.py presets` to
regenerate `bambu/`. Why each value is what it is: `docs/DESIGN.md`, "Your
own defaults".

`first_layer.json` fixes weak first layers on the X2D with PLA:

- a brim where Bambu Studio's Auto brim thinks a part needs one (5 mm when
  set to "Outer brim only")
- 50 mm/s for all of layer one, infill included
- part cooling fan off for the first 3 layers
- a 60 C textured PEI plate for PLA, and the textured plate selected

## New projects: import the presets once

1. Bambu Studio: `File → Import → Import Configs...`
2. Select every file in `presets/bambu/`.
3. Pick **0.20mm Standard Safe (X2D)** as the process, and the
   **... Safe (X2D 0.4 nozzle)** filament for each PLA spool in the AMS.
   Select the Textured PEI Plate.

They inherit Bambu's own presets and change only the keys above, so they keep
following Bambu's updates.

## Downloaded projects: apply to the .3mf

A `.3mf` carries its own settings, which replace your presets when it opens.
Patch the file instead:

```bash
uv run scripts/first_layer.py apply download.3mf --out out/download_safe.3mf
```

Only PLA filaments get the PLA settings; other types keep theirs. The report
lists:

- `not_in_file`: keys an older Bambu Studio did not save; nothing is added.
- `overridden_in_model`: settings the designer set on one object or plate,
  e.g. `brim_type=no_brim`. Bambu Studio applies those over the project's.
  They are left as they are; change them in the object list if you want yours.

Orientation and brim ears are per model: use Auto-orient (or lay on face)
and the brim-ear tool in Bambu Studio.
