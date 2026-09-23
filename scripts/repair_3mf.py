#!/usr/bin/env python3
"""Fix open and non-manifold edges in a Bambu Studio project without losing its paint.

For a project someone else made, typically a download that Bambu Studio
flags with "N open edges / N non-manifold edges" and a warning triangle on
one part. Every mesh is checked on exact vertex indices; the broken ones get
local surgery (`x2d/repair.py`) and every triangle outside the cut keeps its
vertices, order and painting. Objects that were fine are copied byte for
byte, and so is everything else in the archive.

    uv run scripts/repair_3mf.py download.3mf --out out/download_fixed.3mf

The report lists each repaired object with the defects before and after, the
cut radius, and how far the new patch sits from the surface it replaced.
Open the result in Bambu Studio and check the object info panel; this
script does not slice.
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np

from x2d.archive import Mesh, components, parts, read_meshes, replace_mesh, rewrite
from x2d.repair import defects, repair


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path, help="a .3mf saved by Bambu Studio")
    ap.add_argument("--out", type=Path, help="where to write the repaired project (default: <name>_fixed.3mf)")
    ap.add_argument("--max-radius", type=float, default=3.0,
                    help="largest cut around a defect, mm, before giving up (default 3)")
    a = ap.parse_args()
    out = a.out or a.project.with_name(a.project.stem + "_fixed.3mf")
    if out.resolve() == a.project.resolve():
        ap.error("--out would overwrite the input")

    with zipfile.ZipFile(a.project) as zf:
        meshes = read_meshes(zf)
        meta = parts(zf)
        owner = {(path, cid): oid for oid, path, cid, _ in components(zf)}
        texts, fixed = {}, []
        for (path, oid), mesh in meshes.items():
            before = defects(mesh.F)
            if not (before["open_edges"] or before["nonmanifold_edges"] or before["flipped_edges"]):
                continue
            r = repair(mesh.V, mesh.F, r_max=a.max_radius)
            attrs = [mesh.attrs[i] if i >= 0 else "" for i in r.origin]
            text = texts.get(path) or zf.read(path).decode()
            texts[path] = replace_mesh(text, oid, Mesh(r.V, r.F, attrs))
            build = owner.get((path, oid))
            fixed.append({"part": meta.get((build, oid), {}).get("name", str(oid)), "file": path,
                          "id": oid, "object_id": build,
                          "before": before, "after": defects(r.F),
                          "cut_radius_mm": r.radius_mm, "triangles_removed": r.removed,
                          "triangles_added": r.added, "patch_deviation_mm": r.deviation_mm,
                          "volume_mm3": [round(float(mesh.trimesh().volume), 3),
                                         round(float(Mesh(r.V, r.F).trimesh().volume), 3)],
                          # painted triangles inside the cut are replaced by unpainted
                          # patch; zero on a flat base, worth knowing when it is not
                          "painted_triangles_cut": int(sum("paint_color" in mesh.attrs[i]
                                                           for i in np.setdiff1d(np.arange(len(mesh.F)), r.origin)))})
        replace = {p: t.encode() for p, t in texts.items()}
        if fixed and "Metadata/model_settings.config" in zf.namelist():
            replace["Metadata/model_settings.config"] = _face_counts(
                zf.read("Metadata/model_settings.config").decode(), fixed).encode()

    if fixed:
        rewrite(a.project, out, replace)
    print(json.dumps({"project": a.project.name, "out": str(out) if fixed else None,
                      "objects_checked": len(meshes), "repaired": fixed,
                      "note": None if fixed else "nothing to repair; no file written"}, indent=2))


def _face_counts(text, fixed):
    """Keep the triangle counts in model_settings.config honest: the part's
    mesh_stat and its object's face_count. Bambu Studio recomputes them, but
    a file that disagrees with itself is one more thing to wonder about."""
    for f in fixed:
        new, old = f["after"]["triangles"], f["before"]["triangles"]
        obj = re.search(rf'<object id="{f["object_id"]}">.*?</object>', text, re.S)
        if not obj:
            continue
        block = obj.group(0)
        block = re.sub(r'<metadata face_count="(\d+)"/>',
                       lambda n: f'<metadata face_count="{int(n.group(1)) - old + new}"/>', block, count=1)
        block = re.sub(rf'(<part id="{f["id"]}"[^>]*>.*?<mesh_stat face_count=")\d+',
                       rf"\g<1>{new}", block, count=1, flags=re.S)
        text = text[:obj.start()] + block + text[obj.end():]
    return text


if __name__ == "__main__":
    main()
