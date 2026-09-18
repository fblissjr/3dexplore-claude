"""3dexplore-claude: text, images, drawings and scans -> Bambu-native .3mf projects."""
from .machine import Extruder, Machine, X2D, machine_for, reach, printability, fits
from .geometry import (silhouette, detail, mask_to_polygons, extrude, each,
                       solid_backer, rounded_plate, render_text, thicken, PX_PER_MM)
from .project import Part, write_3mf, project_settings, default_capture, MAP_MODES
from .preview import preview, preview_layers
from .palette import separate, label_map, footprint, render_svg
from .fonts import bold_font

__all__ = [
    "Extruder", "Machine", "X2D", "machine_for", "reach", "printability", "fits",
    "silhouette", "detail", "mask_to_polygons", "extrude", "each",
    "solid_backer", "rounded_plate", "render_text", "thicken", "PX_PER_MM",
    "Part", "write_3mf", "project_settings", "default_capture", "MAP_MODES", "preview", "preview_layers",
    "separate", "label_map", "footprint", "render_svg",
    "bold_font",
]
