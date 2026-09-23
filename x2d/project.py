"""The Bambu side: what a .3mf must contain, and what it may not guess.

We own geometry and part->filament intent. The slicer owns machine profiles and
toolpaths. That boundary is the design, not a shortcut.
"""
from dataclasses import dataclass
import zipfile, json, warnings

import trimesh
import numpy as np
from xml.sax.saxutils import escape
from pathlib import Path

from .machine import machine_for


# ---------------------------------------------------------------- settings ---
# Bambu Studio's filament_map_mode enum, from PrintConfig.cpp. "Manual" is the
# one that lets the file decide; the Auto modes let the slicer decide and are
# what the Custom/Auto radio in the Filament grouping dialog is choosing between.
MAP_MODES = ("Auto For Flush", "Auto For Match", "Auto For Quality",
             "Manual", "Nozzle Manual")


def project_settings(ref_3mf, *, pin=None, support_interface=None,
                     object_slot=1, colors=None):
    """Lift Metadata/project_settings.config out of a real Bambu project.

    Why copy rather than compose: it is ~580 interdependent keys, and a
    half-resolved one is worse than none -- Bambu Studio applies whatever it
    finds instead of falling back to your presets. So take a known-good config
    wholesale and patch only what you mean to change. Capture a fresh one with
    File > Save Project as... any time the printer or filament setup changes.

    pin: {filament_slot: nozzle}, both 1-based, as the UI numbers them. Setting
    it switches filament_map_mode to "Manual", which is the only mode where the
    file's mapping wins. Leave it None and the slicer groups filaments itself --
    usually onto the nozzle the AMS feeds, but not reliably: the GUI has put a
    one-filament project on an empty auxiliary nozzle. Pin when it matters.

    colors: {filament_slot: "#RRGGBB"} for a slot whose spool changed since the
    capture. This keeps the preview and Bambu Studio's filament panel honest
    without a re-capture, and touches nothing else -- see the note below on
    what it deliberately does not fix.

    support_interface: filament slot to print the support interface in. Only the
    interface, not the whole support body -- the body stays in the object's own
    filament, which is both cheaper and what the support materials are sold for.

    Setting it also closes the interface gap to zero. That is only safe because
    the interface is a *different* material: dissimilar plastics part cleanly
    with no gap at all, which is the entire reason to spend a second nozzle on
    this. Point it at the same material as the object and the gap would weld the
    support to the part, so that case warns.
    """
    with zipfile.ZipFile(ref_3mf) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    _check_resolved(cfg)
    if colors:
        swatch = list(cfg.get("filament_colour") or [])
        for slot, hexcode in colors.items():
            if not 1 <= slot <= len(swatch):
                raise ValueError(f"filament slot {slot}: the reference project "
                                 f"has {len(swatch)} slots")
            swatch[slot - 1] = hexcode
        cfg["filament_colour"] = swatch
        # A colour is the one key that is safe to patch by hand: nothing else in
        # the config is derived from it. flush_volumes_matrix is NOT -- it was
        # measured against the filament that was loaded when this was captured,
        # so a swap between similar-strength colours keeps sane purge volumes
        # and a swap to something much lighter or darker does not. Re-capture
        # after any filament change you care about the purge for.
    if pin:
        fmap = list(cfg.get("filament_map") or [])
        nozzles = len(cfg.get("nozzle_diameter") or [1])
        for slot, nozzle in pin.items():
            if not 1 <= slot <= len(fmap):
                raise ValueError(f"filament slot {slot}: the reference project "
                                 f"has {len(fmap)} slots")
            if not 1 <= nozzle <= nozzles:
                raise ValueError(f"nozzle {nozzle}: this printer has {nozzles}")
            fmap[slot - 1] = str(nozzle)
        cfg["filament_map"] = fmap
        cfg["filament_map_mode"] = "Manual"

    if support_interface is not None:
        kinds = cfg.get("filament_type") or []
        flags = cfg.get("filament_is_support") or []
        if not 1 <= support_interface <= len(kinds):
            raise ValueError(f"filament slot {support_interface}: the reference "
                             f"project has {len(kinds)} slots")
        mine = kinds[support_interface - 1]
        theirs = kinds[object_slot - 1] if 0 < object_slot <= len(kinds) else None
        purpose_built = (flags[support_interface - 1] == "1"
                         if support_interface <= len(flags) else False)
        if not purpose_built and mine == theirs:
            warnings.warn(
                f"slot {support_interface} is {mine}, the same material as the "
                f"object (slot {object_slot}). A zero interface gap between "
                f"identical plastics welds the support to the part. Load a "
                f"dissimilar filament or a support material in that slot.",
                stacklevel=2)
        if str(cfg.get("support_type", "")).startswith("tree"):
            warnings.warn(
                f"support_type is {cfg['support_type']!r}. Support materials are "
                f"prone to collapsing in tree supports -- Bambu says to use normal "
                f"supports with PVA. Set it in Bambu Studio and re-capture, or "
                f"expect the scaffolding to fall over.",
                stacklevel=2)
        cfg["enable_support"] = "1"
        cfg["support_interface_filament"] = str(support_interface)
        cfg["support_filament"] = "0"          # body stays the object's own
        cfg["support_top_z_distance"] = "0"    # safe only across materials
        cfg["support_bottom_z_distance"] = "0"
        cfg["support_interface_spacing"] = "0"
        cfg["support_bottom_interface_spacing"] = "0"
    return cfg


def _check_resolved(cfg):
    """Refuse a config Bambu Studio only half-resolved.

    Its command line can export a project from presets, report success, and
    leave the filaments unloaded: one colour for four filament presets, and a
    density of 0. A project saved from the GUI never has either, so they are
    the tell. See "A capture cannot be made from the command line" in
    docs/DESIGN.md.
    """
    colours = cfg.get("filament_colour") or []
    presets = cfg.get("filament_settings_id") or []
    densities = cfg.get("filament_density") or []
    problems = []
    if presets and len(presets) != len(colours):
        problems.append(f"{len(presets)} filament presets but {len(colours)} colours")
    try:
        if any(float(d) <= 0 for d in densities):
            problems.append("a filament density of 0")
    except (TypeError, ValueError):
        problems.append("a filament density that is not a number")
    if problems:
        raise ValueError("this capture looks half-resolved (" + "; ".join(problems) +
                         "). Save the project from Bambu Studio's GUI instead.")


def default_capture(root):
    """profiles/default.3mf under `root`, if one has been saved there, else None.

    No capture ships with this repo: it describes one person's printer and
    loaded spools, so profiles/ is gitignored. Save yours from Bambu Studio as
    profiles/default.3mf and every script and the workbench use it without
    --settings; pass --settings to use a different one.
    """
    path = Path(root) / "profiles" / "default.3mf"
    return path if path.is_file() else None


# ------------------------------------------------------------- 3MF writer ---
@dataclass
class Part:
    mesh: trimesh.Trimesh
    name: str
    extruder: int = 1


def write_3mf(parts, path, *, machine=None, name="model", at=None, settings=None):
    """Write a Bambu-native 3MF: one object, N parts, per-part extruder.

    Why this shape: Bambu Studio only honors Metadata/model_settings.config when
    model metadata carries BambuStudio:3mfVersion. Parts (components of a
    single object) keep the pieces locked together on the plate, which is what
    you want for a two-color plaque -- move one, move both.

    settings: a dict from project_settings(). Optional. Without it the file is
    geometry plus part->filament intent and inherits whatever presets the user
    has loaded; with it, the project opens already configured.

    machine: defaults to the printer the settings capture describes, so a
    P1S capture is checked against a P1S bed. With no capture, the X2D.
    """
    machine = machine or machine_for(settings)
    # Export must not move the caller's meshes (previews/STLs reuse them).
    parts = [Part(p.mesh.copy(), p.name, p.extruder) for p in parts]
    if not parts:
        raise ValueError("at least one part is required")
    for p in parts:
        if not isinstance(p.extruder, int) or p.extruder < 1:
            raise ValueError("filament slots must be positive integers")
        if settings and p.extruder > len(settings.get("filament_colour", [])):
            raise ValueError(f"part {p.name}: filament slot {p.extruder} is absent from settings")
        if (p.mesh.is_empty or not np.isfinite(p.mesh.vertices).all()
                or not p.mesh.is_volume):
            raise ValueError(f"part {p.name}: mesh must be a finite, oriented watertight solid")
    whole = trimesh.util.concatenate([p.mesh for p in parts])
    cx, cy, _ = whole.bounds.mean(axis=0)
    zmin = whole.bounds[0][2]
    for p in parts:                       # local frame: xy centered, z on the bed
        p.mesh.apply_translation([-cx, -cy, -zmin])
    tx, ty = at if at else (machine.bed[0] / 2, machine.bed[1] / 2)
    from .machine import reach
    for p in parts:
        bounds = p.mesh.bounds + [tx, ty, 0]
        if not np.isfinite(bounds).all() or not reach(machine, bounds, 1)["reachable"]:
            raise ValueError(f"part {p.name}: placement exceeds the build volume")
        if settings and settings.get("filament_map_mode") == "Manual":
            fmap = settings.get("filament_map") or []
            if p.extruder > len(fmap):
                raise ValueError(f"part {p.name}: slot {p.extruder} has no nozzle in filament_map")
            nozzle = int(fmap[p.extruder - 1])
            if not reach(machine, bounds, nozzle)["reachable"]:
                raise ValueError(f"part {p.name}: placement exceeds nozzle {nozzle} reach")
    xml = lambda value: escape(str(value), {'"': '&quot;', "'": '&apos;'})
    name = xml(name)

    n = len(parts)
    obj_id = n + 1                        # container object comes after the parts
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<model unit="millimeter" xml:lang="en-US"'
           ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
           ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"'
           '>',
           '<metadata name="BambuStudio:3mfVersion">1</metadata>',
           '<metadata name="Application">BambuStudio-%s</metadata>'
           % xml((settings or {}).get("version") or "02.03.00.00"),
           '<metadata name="Title">%s</metadata>' % name,
           '<resources>']
    for i, part in enumerate(parts, start=1):
        v, f = part.mesh.vertices, part.mesh.faces
        out.append(f'<object id="{i}" type="model"><mesh><vertices>')
        out += [f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v]
        out.append('</vertices><triangles>')
        out += [f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f]
        out.append('</triangles></mesh></object>')
    out.append(f'<object id="{obj_id}" type="model"><components>')
    out += [f'<component objectid="{i}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            for i in range(1, n + 1)]
    out.append('</components></object></resources>')
    out.append('<build>')
    out.append(f'<item objectid="{obj_id}" transform="1 0 0 0 1 0 0 0 1 '
               f'{tx:.4f} {ty:.4f} 0.0000" printable="1"/>')
    out.append('</build></model>')

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>',
           f'<object id="{obj_id}">',
           f'<metadata key="name" value="{name}"/>',
           f'<metadata key="extruder" value="{parts[0].extruder}"/>']
    for i, part in enumerate(parts, start=1):
        cfg += [f'<part id="{i}" subtype="normal_part">',
                f'<metadata key="name" value="{xml(part.name)}"/>',
                f'<metadata key="extruder" value="{part.extruder}"/>',
                '<metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>',
                '<mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0"'
                ' facets_reversed="0" backwards_edges="0"/>',
                '</part>']
    cfg += ['</object>',
            '<plate>',
            '<metadata key="plater_id" value="1"/>',
            '<metadata key="plater_name" value=""/>',
            '<metadata key="locked" value="false"/>',
           '<metadata key="filament_map_mode" value="%s"/>'
           % xml((settings or {}).get("filament_map_mode", "Auto For Flush")),
            '<metadata key="gcode_file" value=""/>',
            f'<model_instance><metadata key="object_id" value="{obj_id}"/>',
            '<metadata key="instance_id" value="0"/>',
            '<metadata key="identify_id" value="463"/></model_instance>',
            '</plate>',
            '<assemble>',
            f'<assemble_item object_id="{obj_id}" instance_id="0" transform="1 0 0 0 1 0 0 0 1 '
            f'{tx:.4f} {ty:.4f} 0.0000" offset="0 0 0"/>',
            '</assemble>', '</config>']

    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1"'
            ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
              '<Default Extension="config" ContentType="application/xml"/>'
              '<Default Extension="png" ContentType="image/png"/></Types>')

    if isinstance(path, (str, Path)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", "\n".join(out))
        z.writestr("Metadata/model_settings.config", "\n".join(cfg))
        if settings:
            z.writestr("Metadata/project_settings.config",
                       json.dumps(settings, indent=4, ensure_ascii=False))
    return path
