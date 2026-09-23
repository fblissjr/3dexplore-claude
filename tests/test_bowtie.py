"""scripts/bowtie.py: the glue-on bowtie."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bowtie as bt
from x2d.machine import X2D


def test_solid_flat_backed_and_on_the_layer_grid():
    # Off-grid asks on purpose: 2.07 and 2.55 must land on layer boundaries.
    m = bt.bowtie(16, X2D, wing=2.07, knot=2.55, chamfer=0.55)
    assert abs(m.bounds[1][2] - 2.6) < 1e-6
    assert m.is_volume
    assert abs(m.bounds[0][2]) < 1e-6                 # the glue face is the bed
    # Only the flat faces: where the knot's chamfer crosses a wing's, the union
    # makes mid-layer vertices that are no plane of their own.
    flat = np.abs(m.face_normals[:, 2]) > 0.999
    for z in np.unique(np.round(m.triangles[flat][:, :, 2], 4)):
        assert X2D.on_grid(z), f"z={z} is mid-layer"
    assert abs(m.extents[0] - 16) < 1e-6


def test_two_triangles_joined_by_a_printable_neck():
    m = bt.bowtie(16, X2D)
    assert m.is_volume and m.body_count == 1          # one piece, not two wings
    assert abs(m.bounds[1][2] - 2.0) < 1e-6           # no knot unless asked
    # Width of the back face across the centre line: the neck.
    neck = m.section(plane_origin=[0, 0, 0.1], plane_normal=[1, 0, 0]).extents[1]
    assert neck >= 2 * X2D.min_feature - 1e-6


def test_writes_one_part_per_width_in_the_asked_slot(tmp_path):
    out = tmp_path / "b.3mf"
    parts = bt.main(["--widths", "14", "18", "--slot", "3", "--out", str(out)])
    assert [p.extruder for p in parts] == [3, 3] and out.stat().st_size > 0
    # Laid out apart, not stacked: parts that touch slice as one blob.
    assert parts[1].mesh.bounds[0][1] - parts[0].mesh.bounds[1][1] > 1


def test_a_patched_setting_is_listed_so_bambu_studio_applies_it():
    cfg = {"ironing_type": "no ironing", "brim_type": "auto_brim", "filament_colour": ["#000"],
           "different_settings_to_system": ["enable_support;sparse_infill_pattern", "", ""]}
    bt.patch_process(cfg, {"ironing_type": "top", "brim_type": "no_brim"})
    assert cfg["ironing_type"] == "top"
    assert cfg["different_settings_to_system"] == [
        "brim_type;enable_support;ironing_type;sparse_infill_pattern", "", ""]
    with pytest.raises(ValueError, match="not a key"):
        bt.patch_process(cfg, {"ironing": "top"})
    with pytest.raises(ValueError, match="per-filament"):
        bt.patch_process(cfg, {"filament_colour": "#fff"})


def test_nozzle_and_color_reach_the_written_project(tmp_path):
    import json, zipfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from stock_capture import write_capture
    out = tmp_path / "b.3mf"
    bt.main(["--widths", "16", "--slot", "1", "--nozzle", "1", "--color", "#161616",
             "--settings", str(write_capture(tmp_path / "cap.3mf", {"brim_type": "auto_brim"})), "--out", str(out)])
    cfg = json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config"))
    assert cfg["filament_map_mode"] == "Manual" and cfg["filament_map"][0] == "1"
    assert cfg["filament_colour"][0] == "#161616"
    assert cfg["brim_type"] == "no_brim" and "ironing_type" not in cfg
