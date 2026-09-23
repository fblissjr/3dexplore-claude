#!/usr/bin/env python3
"""Renumber a project's filaments to match the spools you have loaded.

A downloaded project numbers its filaments the way its designer's AMS was
loaded. This moves them: every per-filament setting, the purge matrix, each
part's filament and every painted triangle, together (`x2d/reslot.py`).

    # the file has 1 yellow, 2 brown, 3 white, 4 black, 5 black (unused);
    # the AMS has 1 black, 2 yellow, 3 white, 4 brown:
    uv run scripts/reslot_3mf.py catbus.3mf --order 5 1 3 2 --map 4=1 --out out/catbus_ams.3mf

--order lists, for each new slot in turn, the old filament it takes over:
its colour, settings and purge volumes. Old filaments left out are dropped.
--map sends an old filament's *model* somewhere other than its own new slot,
which is how two old slots of the same colour become one; any old filament
the model uses must end up somewhere, or this refuses.

Colours and settings come from the file, not from the printer. Sync the AMS
in Bambu Studio afterwards, and check its mapping dialog before accepting it:
it matches by the old slot layout.
"""
import argparse
import json
from pathlib import Path

from x2d.reslot import reslot_3mf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path, help="a .3mf saved by Bambu Studio")
    ap.add_argument("--order", type=int, nargs="+", required=True, metavar="OLD",
                    help="old filament for new slot 1, 2, ...")
    ap.add_argument("--map", nargs="*", default=[], metavar="OLD=NEW",
                    help="send an old filament's parts and paint to another new slot")
    ap.add_argument("--out", type=Path, help="default: <name>_reslot.3mf")
    a = ap.parse_args()
    out = a.out or a.project.with_name(a.project.stem + "_reslot.3mf")
    if out.resolve() == a.project.resolve():
        ap.error("--out would overwrite the input")
    try:
        extra = {int(k): int(v) for k, v in (m.split("=") for m in a.map)}
    except ValueError:
        ap.error(f"--map takes OLD=NEW pairs, got {a.map}")
    try:
        report = reslot_3mf(a.project, out, a.order, extra)
    except ValueError as e:
        ap.error(str(e))
    print(json.dumps({"project": a.project.name, "out": str(out), **report}, indent=2))


if __name__ == "__main__":
    main()
