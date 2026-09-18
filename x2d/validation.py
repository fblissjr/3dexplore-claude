"""Preflight the geometry inside our native 3MF projects, without sidecar STLs."""
import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .machine import X2D, machine_for, reach

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}

# Fraction of a layer band that may be bare plate showing between parts.
SEAM_GAP_LIMIT = 0.001
# Two parts may not claim the same volume at all; this is float noise only.
OVERLAP_LIMIT_MM2 = 0.01


def transform(value):
    values = np.array([float(v) for v in value.split()]) if value else np.array([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    if values.size != 12 or not np.isfinite(values).all():
        raise ValueError("invalid 3MF transform")
    matrix = np.eye(4)
    matrix[:3, :] = values.reshape(4, 3).T
    return matrix


def _footprint(mesh, tol=1e-9):
    """The XY area a prism occupies, as 2D polygons in mm, or None.

    Taken from the upward-facing triangles rather than from a cross-section,
    and that choice is load-bearing. extrude() concatenates one solid per
    polygon, so a part made of touching pieces -- every mosaic run, every
    multi-island colour -- carries coincident internal walls. Sectioning cuts
    those walls too, and the extra rings do not assemble into the right region:
    an earlier even-odd version of this silently reported 33 mm2 of overlap on
    a mosaic whose meshes provably intersect in 0 mm3.

    Top caps have none of that problem. The pieces have disjoint interiors, so
    their caps tile the footprint exactly, and interior walls are vertical and
    project to nothing. It also drops trimesh's path assembly, which wants
    scipy, networkx and rtree to turn a cut into loops.

    Returns None when the part is not a prism, rather than guessing: caller
    treats that as "cannot compare", which is honest about the scope in this
    module's docstring.
    """
    normals, areas = mesh.face_normals, mesh.area_faces
    up = normals[:, 2] > tol
    if not up.any():
        return None
    faces = [Polygon(t[:, :2]) for t in mesh.triangles[up]]
    top = unary_union([f for f in faces if f.is_valid and f.area > tol])
    if top.is_empty:
        return None
    height = mesh.bounds[1][2] - mesh.bounds[0][2]
    # A prism's volume is its footprint times its height. Anything else here is
    # a shape this function cannot describe with one polygon.
    if height <= 0 or abs(top.area * height - mesh.volume) > 1e-3 * max(mesh.volume, 1.0):
        return None
    return top


def cross_check(parts, machine=X2D):
    """How the parts of one object relate to EACH OTHER, which is the thing
    per-part validity cannot see.

    Every check above this one asks whether a part is a good solid in a legal
    place. All of them pass on a plate whose colours overlap into the same
    space, or stand a hairline apart with bare plate showing between them --
    both of which slice happily and print wrong. `parts` is [(name, mesh)].

    Two measures, per layer band:

      overlap   two parts claiming the same volume. Should be exactly zero;
                what the slicer does with it is undefined.
      seam gap  bare plate between parts, found by morphological closing:
                dilate the union by half the minimum printable feature and
                erode it back. Anything the closing swallows was a void
                narrower than one extrusion pair, which is a seam nobody
                designed; anything wider survives untouched, which is why a
                keychain hole and the outer margin do not register.

    Closing is what this needs rather than a hole hunt, because a seam is not
    always enclosed: two colours meeting at the artwork's edge leave a channel
    that is open at both ends and is not a hole in the union at all. It is also
    not a pairwise distance -- two parts that touch along most of their border
    and part company at one end have a minimum separation of exactly zero.
    """
    bands, report = {}, []
    for name, mesh in parts:
        lo, hi = mesh.bounds[0][2], mesh.bounds[1][2]
        bands.setdefault((round(lo, 4), round(hi, 4)), []).append((name, mesh))
    # A band is only interesting where more than one part shares the height.
    for (lo, hi), members in sorted(bands.items()):
        occupants = [(n, m) for b, ms in bands.items() if b[0] < hi and b[1] > lo
                     for n, m in ms]
        if len(occupants) < 2:
            continue
        z = (lo + hi) / 2
        slices = [(n, g) for n, m in occupants if (g := _footprint(m)) is not None]
        if len(slices) < 2:
            continue
        union = unary_union([g for _, g in slices])
        overlap = sum(g.area for _, g in slices) - union.area
        r = machine.min_feature / 2
        closed = union.buffer(r, join_style=2).buffer(-r, join_style=2)
        gaps = closed.difference(union)
        pieces = [g for g in getattr(gaps, "geoms", [gaps]) if g.area > 1e-3]
        report.append({
            "z_mm": round(z, 4),
            "parts": [n for n, _ in slices],
            "area_mm2": round(union.area, 3),
            "overlap_mm2": round(max(overlap, 0.0), 4),
            "seam_gap_mm2": round(sum(g.area for g in pieces), 4),
            "seam_gaps": len(pieces),
        })
    return report


def inspect_3mf(path, machine=None):
    with zipfile.ZipFile(path) as archive:
        try:
            model = ET.fromstring(archive.read("3D/3dmodel.model"))
            metadata = ET.fromstring(archive.read("Metadata/model_settings.config"))
        except ET.ParseError as e:
            raise ValueError(f"not a readable project: {e}") from None
        settings = (json.loads(archive.read("Metadata/project_settings.config"))
                    if "Metadata/project_settings.config" in archive.namelist() else {})
    # A file that carries a capture is checked against the printer it was
    # captured on, not against whatever this repo happened to be built for.
    machine = machine or machine_for(settings)
    if model.get("unit", "millimeter") != "millimeter":
        raise ValueError("preflight expects millimeter units")
    objects = {o.get("id"): o for o in model.findall("m:resources/m:object", NS)}
    config_objects = metadata.findall("object")
    errors, reports, solids = [], [], []
    attrs_name = lambda part: next(
        (m.get("value") for m in part.findall("metadata") if m.get("key") == "name"), "?")
    if model.find('m:metadata[@name="BambuStudio:3mfVersion"]', NS) is None:
        errors.append("missing Bambu Studio project marker")
    if not config_objects:
        errors.append("no object metadata")
    for obj in config_objects:
        resource = objects.get(obj.get("id"))
        if resource is None:
            errors.append("object metadata points to missing resource")
            continue
        components = {c.get("objectid"): c for c in resource.findall("m:components/m:component", NS)}
        if sorted(components) != sorted(p.get("id") for p in obj.findall("part")):
            errors.append("part metadata does not match components")
        builds = [b for b in model.findall("m:build/m:item", NS) if b.get("objectid") == obj.get("id")]
        if not builds:
            errors.append("object is absent from build")
        for build in builds:
            for part in obj.findall("part"):
                pid = part.get("id")
                if pid not in objects or pid not in components:
                    errors.append(f"missing part {pid}")
                    continue
                mesh_xml = objects[pid].find("m:mesh", NS)
                if mesh_xml is None:
                    errors.append(f"part {pid} has no inline mesh; external projects are not supported by this preflight")
                    continue
                vertices = [[float(v.get(k)) for k in ("x", "y", "z")] for v in mesh_xml.findall("m:vertices/m:vertex", NS)]
                faces = [[int(f.get(k)) for k in ("v1", "v2", "v3")] for f in mesh_xml.findall("m:triangles/m:triangle", NS)]
                mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
                mesh.apply_transform(transform(build.get("transform")) @ transform(components[pid].get("transform")))
                solids.append((attrs_name(part), mesh))
                attrs = {m.get("key"): m.get("value") for m in part.findall("metadata")}
                slot = int(attrs.get("extruder", "0"))
                if slot < 1 or (settings and slot > len(settings.get("filament_colour", []))):
                    errors.append(f"part {pid}: invalid filament slot {slot}")
                valid = bool(not mesh.is_empty and np.isfinite(mesh.vertices).all() and mesh.is_volume)
                if not valid:
                    errors.append(f"part {pid}: invalid solid")
                    continue
                bounds = mesh.bounds
                bed_ok = reach(machine, bounds, 1)["reachable"]
                if not bed_ok:
                    errors.append(f"part {pid}: outside build volume")
                nozzle = None
                fmap = settings.get("filament_map") or []
                if settings.get("filament_map_mode") == "Manual" and slot > 0:
                    if slot > len(fmap):
                        errors.append(f"part {pid}: slot {slot} has no nozzle in filament_map")
                    else:
                        nozzle = int(fmap[slot - 1])
                        if not reach(machine, bounds, nozzle)["reachable"]:
                            errors.append(f"part {pid}: outside nozzle {nozzle} reach")
                layer_ok = all(machine.on_grid(z) for z in bounds[:, 2])
                if not layer_ok:
                    errors.append(f"part {pid}: boundary is off the captured layer grid")
                reports.append({"name": attrs.get("name"), "filament_slot": slot,
                                "manual_nozzle": nozzle, "watertight": valid,
                                "bounds_mm": bounds.round(4).tolist(), "layer_aligned": layer_ok})
    if not reports:
        errors.append("no valid printable parts")

    bands = cross_check(solids, machine) if len(solids) > 1 else []
    for band in bands:
        # Overlap is zero by construction in every generator here, so anything
        # above float noise means a generator is wrong, not a tolerance issue.
        if band["overlap_mm2"] > OVERLAP_LIMIT_MM2:
            errors.append(f"z={band['z_mm']}: parts overlap by "
                          f"{band['overlap_mm2']} mm2")
        # A gap is a measure, not a verdict. Contour rounding on a correctly
        # seamed plate measures ~0.01% of the band; the fixed-seam bug this
        # check exists for measured 0.48%. SEAM_GAP_LIMIT sits between them,
        # an order of magnitude clear of the noise.
        if band["area_mm2"] and band["seam_gap_mm2"] / band["area_mm2"] > SEAM_GAP_LIMIT:
            errors.append(f"z={band['z_mm']}: {band['seam_gaps']} seam gaps "
                          f"totalling {band['seam_gap_mm2']} mm2 between parts")
    return {"valid": not errors, "errors": errors, "parts": reports,
            "bands": bands,
            "scope": "Native 3dexplore-claude geometry, placement and part-to-part fit; "
                     "slice in Bambu Studio to validate toolpaths and purge."}
