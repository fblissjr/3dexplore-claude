# 3dexplore-claude

Turns text, images, drawings, renders and 3D scans into Bambu Studio projects
(`.3mf`) with each part already assigned to a filament slot. Bambu Studio does
the slicing; nothing here talks to a printer.

Optimized for a Bambu Lab X2D with the AMS Combo, the only printer it has been
verified on. It adapts to your printer and spools through a capture you save
from Bambu Studio.

## Setup

```bash
uv sync
uv run pytest
```

SVG input needs the cairo library (`brew install cairo`; on Apple Silicon also
`export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`).

## Capture your printer

In the Bambu Studio app, pick your printer, process and filaments, add any
object, then `File → Save Project as...` to `profiles/default.3mf`. Everything
uses it automatically; `--settings` picks another. `profiles/` is gitignored.
See [profiles/README.md](profiles/README.md).

## Use

```bash
uv run scripts/text2plate.py ABC --width 90 --filaments 1 4 --out out/abc.3mf
uv run scripts/img2plate.py logo.png --width 70 --out out/logo.3mf
uv run scripts/art2plate.py samples/worried_guy.png --width 72 --ink \
       --map '#000000=1' --base-slot 3 --out out/guy.3mf
uv run scripts/art2plate.py render.png --width 60 --shaded --halo 3 \
       --out out/render.3mf
uv run scripts/scan2print.py scan.obj --height 200 --out out/figure
uv run scripts/verify.py out/abc.3mf
uv run app.py                     # local workbench, http://127.0.0.1:8765
```

For a project someone else made (a download with errors, the wrong colours,
or slots that do not match your AMS):

```bash
uv run scripts/repair_3mf.py download.3mf --out out/fixed.3mf     # open/non-manifold edges, paint kept
uv run scripts/reslot_3mf.py out/fixed.3mf --order 4 1 3 2 --out out/ams.3mf
uv run scripts/overhangs.py out/ams.3mf    # what "floating cantilever" is pointing at
```

Every script has `--help`.

## More

- [docs/DESIGN.md](docs/DESIGN.md): how it works, why, and what is proven
- [CLAUDE.md](CLAUDE.md): rules for coding agents
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT. The shrug dude emoji in `samples/` is from Twemoji (CC-BY 4.0); see
[NOTICE](NOTICE). Not affiliated with Bambu Lab.
