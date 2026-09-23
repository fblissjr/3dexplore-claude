"""Move a project's filaments to other slots, so they match what is loaded.

A downloaded project numbers its filaments the way its designer's AMS was
loaded. Matching yours means changing three things together, or colours land
on the wrong plastic:

- `project_settings.config`: every per-filament setting, reordered.
- `model_settings.config`: each part's `extruder`, and any `*_filament`.
- every painted triangle's `paint_color` leaves (`x2d/paint.py`).

Two inputs, because they are different questions. `order[i]` is the old
filament whose settings, colour and flush volumes new slot i+1 takes.
`mapping` is where each old filament's *model* goes; it defaults to the
inverse of `order`, and must be given for any old filament that `order`
drops but the model still uses -- two old slots holding the same colour, say.
"""
import json
import re
import zipfile

from .archive import parts, rewrite
from .paint import remap, states

__all__ = ["FILAMENT_KEYS", "default_mapping", "used_filaments", "reslot_settings",
           "reslot_model_settings", "reslot_paint", "reslot_3mf"]

# Keys that hold one value per filament, or one block per filament. From the
# list-valued keys of Bambu's public fdm_filament_common.json (BambuStudio
# master, resources/profiles/BBL/filament, read 2026-09-22), plus the ones a
# Bambu Studio 02.08.02.61 project carries that the public profile lags
# behind on. Anything else named filament_* is treated as per-filament too;
# anything left over whose length is a multiple of the filament count is
# reported, not guessed at.
FILAMENT_KEYS = frozenset("""
activate_air_filtration additional_cooling_fan_speed
additional_fan_full_speed_layer chamber_temperatures circle_compensation_speed
close_additional_fan_first_x_layers close_fan_the_first_x_layers
complete_print_exhaust_fan_speed cool_plate_temp cool_plate_temp_initial_layer
cooling_perimeter_transition_distance cooling_slowdown_logic counter_coef_1
counter_coef_2 counter_coef_3 counter_limit_max counter_limit_min
default_filament_colour diameter_limit during_print_exhaust_fan_speed
enable_overhang_bridge_fan enable_pressure_advance eng_plate_temp
eng_plate_temp_initial_layer fan_cooling_layer_time fan_max_speed fan_min_speed
first_x_layer_fan_speed first_x_layer_part_fan_speed full_fan_speed_layer
hole_coef_1 hole_coef_2 hole_coef_3 hole_limit_max hole_limit_min
hot_plate_temp hot_plate_temp_initial_layer impact_strength_z ironing_fan_speed
long_retractions_when_ec no_slow_down_for_cooling_on_outwalls
nozzle_temperature nozzle_temperature_initial_layer
nozzle_temperature_range_high nozzle_temperature_range_low overhang_fan_speed
overhang_fan_threshold overhang_threshold_participating_cooling
override_process_overhang_speed pre_start_fan_time pressure_advance
reduce_fan_stop_start_freq required_nozzle_HRC retraction_distances_when_ec
slow_down_for_layer_cooling slow_down_layer_time slow_down_min_speed
supertack_plate_temp supertack_plate_temp_initial_layer temperature_vitrification
textured_plate_temp textured_plate_temp_initial_layer
volumetric_speed_coefficients
""".split())

# Named filament_* but not one entry per filament: mixed-filament definitions.
_NOT_PER_FILAMENT = re.compile(r"filament_(is_)?mixed")
_EXTRUDER = re.compile(r'(<metadata key="(?:extruder|\w+_filament)" value=")(\d+)(")')
_PLATE_MAP = re.compile(r'(<metadata key="(filament_maps|filament_volume_maps)" value=")([^"]*)(")')
_PAINT = re.compile(r'paint_color="([0-9A-Fa-f]+)"')


def default_mapping(order, extra=None):
    """old filament -> new slot: the inverse of `order`, then `extra`."""
    mapping = {old: new for new, old in enumerate(order, 1)}
    mapping.update(extra or {})
    return mapping


def _per_filament(key, value, n):
    if not isinstance(value, list) or not value or len(value) % n:
        return False
    return key in FILAMENT_KEYS or (key.startswith("filament_") and not _NOT_PER_FILAMENT.match(key))


def reslot_settings(settings, order, mapping=None):
    """(new settings, report). Reorders every per-filament array by `order`
    (1-based old slots), dropping filaments `order` leaves out, and points
    process keys like `wall_filament` through `mapping`."""
    ps = dict(settings)
    n = len(ps["filament_colour"])
    if sorted(set(order)) != sorted(order) or not all(1 <= k <= n for k in order):
        raise ValueError(f"order must name distinct slots 1-{n}, got {order}")
    idx = [k - 1 for k in order]
    moved, unsure = [], []
    for key, v in settings.items():
        if key == "flush_volumes_matrix":
            # nozzles x (n x n), row = from, column = to; permute both axes
            per = n * n
            if len(v) % per:
                raise ValueError(f"flush_volumes_matrix: {len(v)} entries is not a multiple of {per}")
            ps[key] = [v[b * per + i * n + j] for b in range(len(v) // per) for i in idx for j in idx]
        elif key == "filament_self_index":
            w = len(v) // n
            ps[key] = [str(i + 1) for i in range(len(idx)) for _ in range(w)]
        elif key == "different_settings_to_system":
            # [process, one per filament, machine]
            if len(v) != n + 2:
                raise ValueError(f"different_settings_to_system: expected {n + 2} entries, got {len(v)}")
            ps[key] = [v[0]] + [v[1 + i] for i in idx] + [v[-1]]
        elif key.endswith("_filament") and isinstance(v, str) and v.isdigit():
            # 0 is "the object's own filament" and stays 0
            ps[key] = str((mapping or default_mapping(order)).get(int(v), int(v))) if int(v) else v
        elif key == "flush_volumes_vector" or _per_filament(key, v, n):
            w = len(v) // n
            ps[key] = [x for i in idx for x in v[i * w:(i + 1) * w]]
        elif isinstance(v, list) and v and len(v) % n == 0:
            unsure.append(key)
            continue
        else:
            continue
        moved.append(key)
    return ps, {"filaments_before": n, "filaments_after": len(idx),
                "settings_reordered": len(moved), "left_as_is": sorted(unsure)}


def _check(mapping, n_new):
    bad = {k: v for k, v in mapping.items() if not 1 <= v <= n_new}
    if bad:
        raise ValueError(f"mapping points outside slots 1-{n_new}: {bad}")


def reslot_model_settings(text, mapping, order):
    """model_settings.config text with part/object filaments mapped and the
    plate's per-filament maps reordered."""
    def ext(m):
        v = int(m.group(2))
        return m.group(1) + str(mapping.get(v, v) if v else 0) + m.group(3)

    def plate(m):
        vals = m.group(3).split()
        if len(vals) < max(order):
            return m.group(0)
        return m.group(1) + " ".join(vals[k - 1] for k in order) + m.group(4)

    return _PLATE_MAP.sub(plate, _EXTRUDER.sub(ext, text))


def reslot_paint(text, mapping):
    """A .model file's text with every paint_color remapped; (text, count)."""
    cache = {}

    def sub(m):
        code = m.group(1)
        if code not in cache:
            cache[code] = remap(code, mapping)
        return f'paint_color="{cache[code]}"'

    return _PAINT.subn(sub, text)


def used_filaments(zf):
    """Old filament slots the model prints with: part extruders and paint."""
    used = {int(m["extruder"]) for m in parts(zf).values()}
    for name in zf.namelist():
        if name.startswith("3D/") and name.endswith(".model"):
            for code in set(_PAINT.findall(zf.read(name).decode())):
                used |= {s for s in states(code) if s}
    return used


def reslot_3mf(src, dst, order, extra=None):
    """Write `src` re-slotted to `dst`; returns the report."""
    mapping = default_mapping(order, extra)
    _check(mapping, len(order))
    with zipfile.ZipFile(src) as zf:
        used = used_filaments(zf)
        lost = sorted(used - set(mapping))
        if lost:
            raise ValueError(f"old filament(s) {lost} are used by the model but not placed: "
                             f"add them to order, or map them (e.g. {lost[0]}=1)")
        settings = json.loads(zf.read("Metadata/project_settings.config"))
        ps, report = reslot_settings(settings, order, mapping)
        replace = {"Metadata/project_settings.config": (json.dumps(ps, indent=4, ensure_ascii=False) + "\n").encode(),
                   "Metadata/model_settings.config": reslot_model_settings(
                       zf.read("Metadata/model_settings.config").decode(), mapping, order).encode()}
        painted = 0
        for name in zf.namelist():
            if name.startswith("3D/") and name.endswith(".model"):
                text, k = reslot_paint(zf.read(name).decode(), mapping)
                if k:
                    replace[name] = text.encode()
                    painted += k
    rewrite(src, dst, replace)
    report.update({"mapping": {str(k): v for k, v in sorted(mapping.items())},
                   "used_before": sorted(used), "painted_triangles": painted,
                   "colours_after": ps["filament_colour"]})
    return report
