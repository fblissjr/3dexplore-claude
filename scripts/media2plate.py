#!/usr/bin/env python3
"""Image or selected video frame to an AMS colour mosaic, one colour per filament slot."""
import argparse
import json
from pathlib import Path
from PIL import Image
from x2d import Machine, default_capture, project_settings, write_3mf
from x2d.mosaic import make_mosaic, video_frame


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out/mosaic.3mf"))
    ap.add_argument("--settings", type=Path,
                    help="a project saved from Bambu Studio; the mosaic palette is its "
                         "loaded spool colours. Defaults to profiles/default.3mf.")
    ap.add_argument("--slots", type=int, nargs="+", help="filament slots to use; default: every slot in the capture")
    ap.add_argument("--base-slot", type=int, default=1)
    ap.add_argument("--width", type=float, default=90)
    ap.add_argument("--cell", type=float, default=1.2)
    ap.add_argument("--base", type=float, help="mm; default 1.6, snapped to your capture's layer grid")
    ap.add_argument("--relief", type=float, help="mm; default 0.6, snapped to your capture's layer grid")
    ap.add_argument("--margin", type=float, default=3)
    ap.add_argument("--frame", type=float, default=0, help="video timestamp in seconds")
    a = ap.parse_args()
    try:
        a.settings = a.settings or default_capture(Path(__file__).resolve().parents[1])
        if not a.settings:
            raise ValueError("a mosaic is matched to your loaded spool colours, so it needs "
                             "a capture: save a project from Bambu Studio as "
                             "profiles/default.3mf, or pass --settings")
        cfg = project_settings(a.settings)
        machine = Machine.from_settings(cfg)
        a.base, a.relief = machine.band(a.base, a.relief, default=(1.6, 0.6))
        colors = cfg["filament_colour"]
        a.slots = a.slots or list(range(1, len(colors) + 1))
        if any(s < 1 or s > len(colors) for s in a.slots):
            raise ValueError("selected slot is absent from the settings project")
        if a.input.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}:
            image, _ = video_frame(a.input, a.frame)
        else:
            image = Image.open(a.input)
        result = make_mosaic(image, {s: colors[s-1] for s in a.slots}, width=a.width,
                             cell=a.cell, base=a.base, relief=a.relief, margin=a.margin, base_slot=a.base_slot,
                             machine=machine)
        write_3mf(result.parts, a.out, name=a.input.stem, settings=cfg)
        result.preview.save(a.out.with_name(a.out.stem + "_preview.png"))
        a.out.with_suffix(".json").write_text(json.dumps(result.report, indent=2))
        print(json.dumps(result.report, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        ap.error(str(exc))


if __name__ == "__main__":
    main()
