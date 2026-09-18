"""A stand-in capture for the tests, so the suite never needs anyone's printer.

No printer profile ships with this repo: a capture describes one person's
machine and spools, so it lives in the gitignored profiles/ folder. The tests
still need something shaped like one, and this is it.

Only the keys this repo reads, with values quoted from Bambu's public profiles
(resources/profiles/BBL in the BambuStudio repo), resolved through their
inherits chains:

    machine   Bambu Lab X2D 0.4 nozzle
              <- fdm_bbl_3dp_002_common <- fdm_machine_common
    process   0.20mm Standard @BBL X2D
              <- fdm_process_dual_0.20_nozzle_0.4 <- fdm_process_dual_common
              <- fdm_process_common
    filament  Bambu PLA Basic / Bambu PLA Matte @BBL X2D 0.4 nozzle
              <- Bambu PLA Basic / Matte @base <- fdm_filament_pla
              <- fdm_filament_common

Slots 1-3 are PLA Basic and slot 4 is PLA Matte, which is what makes the
per-slot density test mean something (1.26 against 1.32). filament_colour and
filament_map are project-level and in no preset; the colours are arbitrary.

This is not a resolved project_settings.config -- a real one has ~580 keys --
and nothing should ever be printed with it.
"""
import tempfile
from pathlib import Path

import trimesh

from x2d import Part, write_3mf

STOCK_X2D = {
    # machine
    "printer_model": "Bambu Lab X2D",
    "printable_area": ["0x0", "256x0", "256x256", "0x256"],
    "printable_height": "261",
    "extruder_printable_area": ["0x0,256x0,256x256,0x256",
                                "20.5x0,256x0,256x256,20.5x256"],
    "extruder_printable_height": ["261", "256"],
    "extruder_type": ["Direct Drive", "Bowden"],
    "nozzle_diameter": ["0.4", "0.4"],
    # process
    "layer_height": "0.2",
    "initial_layer_print_height": "0.2",
    "line_width": "0.42",
    "outer_wall_line_width": "0.42",
    "support_threshold_angle": "30",
    "sparse_infill_density": "15%",
    "enable_support": "0",
    "support_type": "tree(auto)",
    # filament, per slot
    "filament_type": ["PLA", "PLA", "PLA", "PLA"],
    "filament_density": ["1.26", "1.26", "1.26", "1.32"],
    "filament_cost": ["19.99", "19.99", "19.99", "19.99"],
    "filament_is_support": ["0", "0", "0", "0"],
    # project
    "filament_colour": ["#000000", "#0A2989", "#FFFFFF", "#DE4343"],
    "filament_map": ["1", "1", "1", "1"],
    "filament_map_mode": "Auto For Flush",
}


def capture(patch=None):
    """A fresh copy of the stand-in, with any keys replaced."""
    cfg = {k: list(v) if isinstance(v, list) else v for k, v in STOCK_X2D.items()}
    cfg.update(patch or {})
    return cfg


def write_capture(path, patch=None):
    """Write the stand-in as a .3mf that project_settings() can read."""
    part = Part(trimesh.creation.box((20, 20, 20)), "cube", 1)
    write_3mf([part], path, name="capture", settings=capture(patch))
    return Path(path)


_DIR = tempfile.TemporaryDirectory(prefix="x2d-capture-")
PROFILE = str(write_capture(Path(_DIR.name) / "stock-x2d.3mf"))
