"""Printable palette artwork: disjoint pixel regions over a continuous backer.

Quantization happens at physical feature size, not photograph resolution. Row
runs share exact grid coordinates, avoiding contour gaps between colors.
"""
from dataclasses import dataclass
import math

import cv2
import numpy as np
import manifold3d
import trimesh
from PIL import Image, ImageColor, ImageOps
from shapely.geometry import box
from shapely.ops import unary_union

from .geometry import extrude
from .machine import X2D
from .project import Part


@dataclass
class Mosaic:
    parts: list
    preview: Image.Image
    report: dict


def make_mosaic(image, palette, *, width=90.0, cell=1.2, base=1.6,
                relief=0.6, margin=3.0, base_slot=1, machine=X2D):
    """Map an image to {project_slot: '#RRGGBB'}; return unsliced solids.

    Alpha is composited against the backer color. The rectangular backer keeps
    every color island supported. All art shares one shallow layer band, so
    same-layer color changes are confined to that band.
    """
    for name, value in dict(width=width, cell=cell, base=base, relief=relief).items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("margin must be finite and nonnegative")
    if not palette or base_slot not in palette:
        raise ValueError("choose at least one color, including the backer slot")
    if any(not isinstance(s, int) or s < 1 for s in palette):
        raise ValueError("palette keys must be positive project filament slots")
    if cell < machine.min_feature or width < cell:
        raise ValueError(f"cell must be at least {machine.min_feature:g} mm and no wider than the image")
    machine.check_band(base, relief)
    image = ImageOps.exif_transpose(image).convert("RGBA")
    height = width * image.height / image.width
    if width + 2 * margin > machine.bed[0] - 10 or height + 2 * margin > machine.bed[1] - 10:
        raise ValueError("image plus border exceeds the bed margin; reduce width or crop the image")
    if base + relief > machine.bed[2]:
        raise ValueError("model exceeds build height")
    cols, rows = int(width / cell), int(height / cell)
    if rows < 1:
        raise ValueError("image is too short for the chosen feature size")
    if cols * rows > 100_000:
        raise ValueError("too many cells; increase feature size")
    dx, dy = width / cols, height / rows
    slots = list(palette)
    rgb = np.array([ImageColor.getrgb(palette[s]) for s in slots], dtype=np.uint8)
    background = Image.new("RGBA", image.size, palette[base_slot])
    small = Image.alpha_composite(background, image).convert("RGB").resize((cols, rows), Image.Resampling.LANCZOS)
    # Lab distance respects lightness as well as hue; no dithering that creates
    # tiny alternating islands and extra travels.
    lab = cv2.cvtColor(np.asarray(small).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    swatches = cv2.cvtColor(rgb[None].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
    labels = ((lab[:, :, None] - swatches) ** 2).sum(axis=3).argmin(axis=2)
    backer = box(-margin, -margin, width + margin, height + margin)
    parts = [Part(extrude(backer, base), "backer", base_slot)]
    areas = {}
    for index, slot in enumerate(slots):
        runs = []
        for row in range(rows):
            padded = np.pad((labels[row] == index).astype(np.int8), (1, 1))
            edges = np.diff(padded)
            for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                runs.append(box(start * dx, (rows - row - 1) * dy,
                                end * dx, (rows - row) * dy))
        if runs:
            region = unary_union(runs)
            # Earcut + vertex merging can join diagonal pixel corners into a
            # nonmanifold edge. Manifold preserves separate vertex topology.
            section = manifold3d.CrossSection([np.asarray(r.exterior.coords)[:-1] for r in runs])
            solid = section.extrude(relief).to_mesh()
            mesh = trimesh.Trimesh(vertices=solid.vert_properties[:, :3],
                                   faces=solid.tri_verts, process=False)
            mesh.apply_translation([0, 0, base])
            parts.append(Part(mesh, f"artwork_slot_{slot}", slot))
            areas[str(slot)] = round(region.area, 2)
    picture = Image.fromarray(rgb[labels]).resize((cols * 6, rows * 6), Image.Resampling.NEAREST)
    used = [p.extruder for p in parts[1:]]
    report = {
        "mode": "palette mosaic (2.5D)", "size_mm": [width + 2 * margin, height + 2 * margin, base + relief],
        "cell_mm": [round(dx, 3), round(dy, 3)], "grid": [cols, rows],
        "base_layers": machine.layers(base),
        "color_layers": machine.layers(base + relief) - machine.layers(base),
        "used_artwork_slots": used, "area_by_slot_mm2": areas,
        "parts": [{"name": p.name, "filament_slot": p.extruder,
                   "watertight": bool(p.mesh.is_volume), "triangles": len(p.mesh.faces)} for p in parts],
        "note": "Multiple colors share each artwork layer. Purge, prime tower, nozzle grouping and time must be checked after slicing in Bambu Studio.",
    }
    return Mosaic(parts, picture, report)


def video_frame(path, seconds=0.0):
    """Decode a selected timestamp; release the decoder even on invalid input."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("timestamp must be finite and nonnegative")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("video could not be opened; try an H.264 MP4")
        fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = count / fps if fps > 0 and count > 0 else None
        if duration is not None and seconds >= duration:
            raise ValueError(f"timestamp is outside the video ({duration:.2f} seconds)")
        if seconds and not cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000):
            raise ValueError("this video decoder cannot seek to the selected timestamp")
        ok, frame = cap.read()
        if not ok:
            raise ValueError("no decodable frame at this timestamp")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), duration
    finally:
        cap.release()
