#!/usr/bin/env python3
"""Safer first layers: a brim where it is needed, a slower first layer, no
part fan for three layers, and a 60 C textured plate for PLA.

The settings live in presets/first_layer.json; edit that, not this. Two ways
to use them:

    # once: write the presets to import into Bambu Studio
    # (File > Import > Import Configs), then pick them for new projects
    uv run scripts/first_layer.py presets

    # per project: a download carries its own settings and overrides yours
    uv run scripts/first_layer.py apply download.3mf --out out/download_safe.3mf

`apply` changes only Metadata/project_settings.config. It reports keys the
file does not have (an older Bambu Studio) and settings an object or plate
overrides in model_settings.config; both are left as they are. Nothing is
sliced or sent.
"""
import argparse
import json
from pathlib import Path

from x2d.presets import DEFAULT, apply_3mf, load, user_presets

REPO = Path(__file__).resolve().parents[1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT, help="the settings, default %(default)s")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("presets", help="write importable Bambu Studio presets")
    pr.add_argument("--out", type=Path, default=REPO / "presets" / "bambu")
    ar = sub.add_parser("apply", help="patch the settings into a .3mf")
    ar.add_argument("project", type=Path)
    ar.add_argument("--out", type=Path, help="default: <name>_safe.3mf beside it")
    a = ap.parse_args(argv)
    data = load(a.data)

    if a.cmd == "presets":
        a.out.mkdir(parents=True, exist_ok=True)
        files = user_presets(data)
        for name, preset in files.items():
            (a.out / name).write_text(json.dumps(preset, indent=4, ensure_ascii=False) + "\n")
        print(json.dumps({"out": str(a.out), "presets": sorted(files)}, indent=2))
        return files

    out = a.out or a.project.with_name(a.project.stem + "_safe.3mf")
    if out.resolve() == a.project.resolve():
        ap.error("--out would overwrite the input")
    try:
        report = apply_3mf(a.project, out, data)
    except (KeyError, ValueError) as e:
        ap.error(str(e))
    print(json.dumps({"project": a.project.name, "out": str(out), **report}, indent=2))
    return report


if __name__ == "__main__":
    main()
