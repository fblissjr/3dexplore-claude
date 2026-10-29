"""presets/first_layer.json, as importable presets and as a patch on a project.

The fixture is the stock capture with the keys these settings touch, quoted
from Bambu's public X2D profiles, and slot 2 made PETG: the bed temperature
is a PLA number, and a PETG slot in the same project must keep its own.
"""
import json
import sys
import zipfile
from pathlib import Path

import pytest

from x2d.archive import rewrite
from x2d.presets import apply_settings, load, user_presets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import capture, write_capture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import first_layer  # noqa: E402

FIRST_LAYER = {
    # process, 0.20mm Standard @BBL X2D; two variants as a project holds them
    "brim_type": "no_brim",                 # what a download might ship
    "brim_width": "5",
    "initial_layer_speed": ["50", "50"],
    "initial_layer_infill_speed": ["105", "105"],
    # filament, fdm_filament_pla; first_x_layer_part_fan_speed left out on
    # purpose, as a project from an older Bambu Studio would
    "filament_type": ["PLA", "PETG", "PLA", "PLA"],
    "close_fan_the_first_x_layers": ["1", "1", "1", "1"],
    "textured_plate_temp": ["55", "70", "55", "55"],
    "textured_plate_temp_initial_layer": ["55", "70", "55", "55"],
    "curr_bed_type": "Cool Plate",
    "different_settings_to_system": ["enable_support", "", "", "nozzle_temperature", "", ""],
}


def test_committed_presets_are_what_the_data_makes():
    # presets/bambu/ is generated, and committed so it can be imported without
    # running anything; this keeps it from drifting from the one source.
    made = user_presets(load())
    on_disk = {p.name: json.loads(p.read_text()) for p in (ROOT / "presets" / "bambu").glob("*.json")}
    assert on_disk == made


def test_presets_inherit_and_carry_only_the_data():
    data = load()
    made = user_presets(data)
    proc = made["0.20mm Standard Safe (X2D).json"]
    assert proc["inherits"] == "0.20mm Standard @BBL X2D" and proc["from"] == "User"
    assert proc["initial_layer_infill_speed"] == ["50"] * 6 and proc["brim_type"] == "auto_brim"
    fil = made["Bambu PLA Matte Safe (X2D 0.4 nozzle).json"]
    assert fil["inherits"] == "Bambu PLA Matte @BBL X2D 0.4 nozzle"
    assert fil["textured_plate_temp"] == ["60"] and fil["close_fan_the_first_x_layers"] == ["3"]
    assert not any("@" in p["name"] for p in made.values())


def test_apply_patches_pla_slots_only_and_lists_every_key():
    cfg = capture(FIRST_LAYER)
    report = apply_settings(cfg, load())
    assert cfg["initial_layer_infill_speed"] == ["50", "50"]
    assert cfg["brim_type"] == "auto_brim" and cfg["curr_bed_type"] == "Textured PEI Plate"
    assert cfg["textured_plate_temp"] == ["60", "70", "60", "60"]
    assert cfg["close_fan_the_first_x_layers"] == ["3", "1", "3", "3"]
    diff = cfg["different_settings_to_system"]
    assert set(diff[0].split(";")) >= {"brim_type", "initial_layer_infill_speed", "enable_support"}
    assert diff[2] == ""                                   # PETG untouched
    assert "nozzle_temperature" in diff[3] and "textured_plate_temp" in diff[3]
    assert report["filament_slots"] == {1: "PLA", 3: "PLA", 4: "PLA"}
    assert report["slots_left_as_is"] == {2: "PETG"}
    assert report["not_in_file"] == ["first_x_layer_part_fan_speed"]


def test_apply_rewrites_only_the_settings_and_reports_object_overrides(tmp_path):
    src = write_capture(tmp_path / "plain.3mf", FIRST_LAYER)
    with zipfile.ZipFile(src) as zf:
        model = zf.read("Metadata/model_settings.config").decode()
    # A designer's per-object choice: kept, and named in the report.
    model = model.replace("</object>", '    <metadata key="brim_type" value="no_brim"/>\n  </object>', 1)
    dl = tmp_path / "download.3mf"
    rewrite(src, dl, {"Metadata/model_settings.config": model.encode()})

    out = tmp_path / "safe.3mf"
    report = first_layer.main(["apply", str(dl), "--out", str(out)])
    assert report["overridden_in_model"] == ["brim_type=no_brim"]
    with zipfile.ZipFile(dl) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        cfg = json.loads(b.read("Metadata/project_settings.config"))
    assert changed == ["Metadata/project_settings.config"]
    assert cfg["textured_plate_temp"] == ["60", "70", "60", "60"]


def test_apply_refuses_to_overwrite_its_input(tmp_path):
    src = write_capture(tmp_path / "p.3mf", FIRST_LAYER)
    with pytest.raises(SystemExit):
        first_layer.main(["apply", str(src), "--out", str(src)])


def test_presets_cli_writes_importable_files(tmp_path):
    files = first_layer.main(["presets", "--out", str(tmp_path)])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(files)
