"""Read and patch a Bambu Studio project someone else saved.

`write_3mf` makes projects and `inspect_3mf` checks ours, which keep one
inline mesh per part. A project saved by the Bambu Studio GUI is shaped
differently: the build object is an assembly of components pointing into
`3D/Objects/*.model` sub-files, and triangles carry per-triangle attributes
(`paint_color`, `paint_fuzzy_skin`, ...) that are the designer's work.

So this module parses with regular expressions over the raw text instead of
an XML tree, and keeps each triangle's attribute text verbatim. That is what
lets `replace_mesh` rewrite one object and leave every other byte of a 90 MB
model file as it was: nothing unrequested round-trips through a parser that
might reformat it.
"""
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

from .validation import transform

__all__ = ["Mesh", "read_meshes", "replace_mesh", "parts", "components", "world_parts", "rewrite",
           "pin_nozzles"]

_OBJECT = re.compile(r'<object id="(\d+)"[^>]*>(.*?)</object>', re.S)
_VERTEX = re.compile(r'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"\s*/>')
_TRIANGLE = re.compile(r'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"([^>]*?)\s*/>')
_PAINT = re.compile(r'paint_color="([0-9A-Fa-f]+)"')


@dataclass
class Mesh:
    """One object's mesh. `attrs[i]` is triangle i's extra attribute text,
    verbatim (' paint_color="8" paint_fuzzy_skin="4"', or '')."""
    V: np.ndarray
    F: np.ndarray
    attrs: list = field(default_factory=list)

    def paint(self, i):
        """Triangle i's `paint_color` string, or None when unpainted."""
        m = _PAINT.search(self.attrs[i])
        return m.group(1) if m else None

    def trimesh(self):
        return trimesh.Trimesh(self.V, self.F, process=False)


def _model_files(zf):
    return [n for n in zf.namelist() if n.startswith("3D/") and n.endswith(".model")]


def read_meshes(zf):
    """{(model file, object id): Mesh} for every object that has a mesh."""
    out = {}
    for name in _model_files(zf):
        text = zf.read(name).decode()
        for m in _OBJECT.finditer(text):
            body = m.group(2)
            if "<mesh>" not in body:
                continue                      # an assembly of components
            V = np.array(_VERTEX.findall(body), dtype=float).reshape(-1, 3)
            tri = _TRIANGLE.findall(body)
            F = np.array([t[:3] for t in tri], dtype=np.int64).reshape(-1, 3)
            out[(name, int(m.group(1)))] = Mesh(V, F, [t[3] for t in tri])
    return out


def _fmt(x):
    # 9 significant digits is what Bambu Studio writes; values it wrote come
    # back as the same strings.
    return "%.9g" % x


def replace_mesh(text, object_id, mesh):
    """`text` with one object's vertices and triangles replaced by `mesh`.
    Everything outside that object's two lists is untouched."""
    m = next((m for m in _OBJECT.finditer(text) if int(m.group(1)) == object_id), None)
    if m is None:
        raise KeyError(f"object {object_id} not in this model file")
    body = m.group(2)
    vs, ve = body.index("<vertices>"), body.index("</vertices>") + len("</vertices>")
    ts, te = body.index("<triangles>"), body.index("</triangles>") + len("</triangles>")
    verts = "\n".join(f'     <vertex x="{_fmt(a)}" y="{_fmt(b)}" z="{_fmt(c)}"/>' for a, b, c in mesh.V)
    tris = "\n".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"{t}/>' for (a, b, c), t in zip(mesh.F, mesh.attrs))
    body = (body[:vs] + f"<vertices>\n{verts}\n    </vertices>" + body[ve:ts]
            + f"<triangles>\n{tris}\n    </triangles>" + body[te:])
    return text[:m.start(2)] + body + text[m.end(2):]


def parts(zf):
    """{(object id, part id): metadata} from model_settings.config. Part ids
    are component object ids, numbered per object. A part without its own
    `extruder` prints in its object's, so that is filled in here."""
    if "Metadata/model_settings.config" not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read("Metadata/model_settings.config"))
    kv = lambda el: {m.get("key"): m.get("value") for m in el.findall("metadata") if m.get("key")}
    out = {}
    for obj in root.findall("object"):
        default = kv(obj).get("extruder", "1")
        for part in obj.findall("part"):
            meta = kv(part)
            meta.setdefault("extruder", default)
            out[(int(obj.get("id")), int(part.get("id")))] = meta
    return out


def components(zf):
    """[(build object id, model file, mesh object id, bed transform)] for every
    mesh the build places: build item transform @ component transform. A
    plain object with an inline mesh is its own single component."""
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    pns = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
    root = ET.fromstring(zf.read("3D/3dmodel.model"))
    objects = {int(o.get("id")): o for o in root.iter(ns + "object")}
    out = []
    for item in root.iter(ns + "item"):
        oid = int(item.get("objectid"))
        build = transform(item.get("transform"))
        comps = objects[oid].findall(f"{ns}components/{ns}component")
        if not comps:
            out.append((oid, "3D/3dmodel.model", oid, build))
        for c in comps:
            path = (c.get(pns + "path") or "/3D/3dmodel.model").lstrip("/")
            out.append((oid, path, int(c.get("objectid")), build @ transform(c.get("transform"))))
    return out


def world_parts(zf):
    """[(name, filament slot, trimesh in bed coordinates)] for every part the
    build places."""
    meshes, meta, out = read_meshes(zf), parts(zf), []
    for oid, path, cid, matrix in components(zf):
        mesh = meshes[(path, cid)].trimesh()
        mesh.apply_transform(matrix)
        info = meta.get((oid, cid), {})
        out.append((info.get("name", str(cid)), int(info.get("extruder", 1)), mesh))
    return out


def rewrite(src, dst, replace):
    """Copy the archive `src` to `dst`, entry by entry and in order, swapping
    in the bytes given in `replace` {entry name: bytes}."""
    unknown = set(replace) - set(zipfile.ZipFile(src).namelist())
    if unknown:
        raise KeyError(f"not in {Path(src).name}: {sorted(unknown)}")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = replace.get(info.filename)
            zout.writestr(info, zin.read(info.filename) if data is None else data,
                          compress_type=zipfile.ZIP_DEFLATED)


_MAP = re.compile(r'("filament_map"\s*:\s*\[)(.*?)(\])', re.S)
_MAP_MODE = re.compile(r'("filament_map_mode"\s*:\s*")[^"]*(")')
_PLATE_MODE = re.compile(r'(<metadata key="filament_map_mode" value=")[^"]*("\s*/>)')
_PLATE_MAPS = re.compile(r'(<metadata key="filament_maps" value=")([^"]*)("\s*/>)')


def pin_nozzles(project, model_settings, nozzle=1):
    """(project, model_settings, places patched): every filament pinned to
    `nozzle` -- an int, or {slot: nozzle} with the rest left as they are --
    and `filament_map_mode` set to Manual, the only mode in which the file's
    map wins over the slicer's grouping.

    Both texts, because the plate keeps its own copy of the map
    (`filament_maps`) and that copy is the one the GUI used (DESIGN.md, "What
    the X2D GUI did with it"). Text edits on the two keys only, so the rest of
    a GUI save stays byte for byte. A file without a nozzle map (one nozzle)
    comes back unchanged with nothing patched."""
    pick = (lambda slot, old: str(nozzle)) if isinstance(nozzle, int) else \
        (lambda slot, old: str(nozzle.get(slot, old)))
    done = []

    def json_list(m):
        vals = re.findall(r'"([^"]*)"', m.group(2))
        it = iter(pick(i, v) for i, v in enumerate(vals, 1))
        return m.group(1) + re.sub(r'"[^"]*"', lambda _: f'"{next(it)}"', m.group(2)) + m.group(3)

    if _MAP.search(project):
        project = _MAP.sub(json_list, project, count=1)
        project = _MAP_MODE.sub(r"\1Manual\2", project, count=1)
        done.append("project_settings.config")
    if _PLATE_MAPS.search(model_settings) or (done and _PLATE_MODE.search(model_settings)):
        model_settings = _PLATE_MAPS.sub(lambda m: m.group(1) + " ".join(
            pick(i, v) for i, v in enumerate(m.group(2).split(), 1)) + m.group(3), model_settings)
        model_settings = _PLATE_MODE.sub(r"\1Manual\2", model_settings)
        done.append("model_settings.config")
    return project, model_settings, done
