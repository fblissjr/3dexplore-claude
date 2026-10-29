"""Your own settings, kept as data and applied two ways.

`presets/first_layer.json` is the source of truth: process keys, filament
keys per `filament_type`, and project keys, named as Bambu Studio names them.
From it come

- `user_presets()`: process and filament presets to import into Bambu Studio
  (File > Import > Import Configs), each inheriting a system preset and
  carrying only the keys set here, so everything else still tracks Bambu's
  updates; and
- `apply_3mf()`: the same keys patched into any project, because a project
  carries its own settings and a downloaded one overrides your presets.

Filament keys go only to filaments of the listed type: 60 C is a PLA bed
temperature, and a PETG slot in the same project must keep its own.
"""
import json
import re
import zipfile
from pathlib import Path

from .archive import rewrite
from .project import patch_process
from .reslot import FILAMENT_KEYS

__all__ = ["DEFAULT", "load", "user_presets", "apply_settings", "apply_3mf"]

DEFAULT = Path(__file__).resolve().parents[1] / "presets" / "first_layer.json"
_META = re.compile(r'<metadata key="([^"]+)" value="([^"]*)"\s*/>')


def load(path=DEFAULT):
    data = json.loads(Path(path).read_text())
    for kind, keys in data["filament"].items():
        stray = [k for k in keys if k not in FILAMENT_KEYS and not k.startswith("filament_")]
        if stray:
            raise ValueError(f"filament {kind}: {stray} are not per-filament keys")
    return data


def _named(parent, tag):
    """"0.20mm Standard @BBL X2D" -> "0.20mm Standard Safe (X2D)": sorts beside
    its parent, and keeps "@BBL", the mark of a system preset, out of a user
    preset's name."""
    base, _, rest = parent.partition(" @")
    rest = rest.removeprefix("BBL ")
    return f"{base} {tag} ({rest})" if rest else f"{base} {tag}"


def user_presets(data):
    """{file name: preset dict} for Bambu Studio's Import Configs."""
    imp, tag = data["import"], data["name"]
    out = {}
    for parent, shape in imp["process"].items():
        name = _named(parent, tag)
        keys = {k: [v] * shape["variants"] if k in shape["per_variant"] else v
                for k, v in data["process"].items()}
        out[name + ".json"] = {"type": "process", "name": name, "inherits": parent,
                               "from": "User", "is_custom_defined": "0",
                               "print_settings_id": name, "version": imp["version"], **keys}
    for kind, parents in imp["filament"].items():
        for parent in parents:
            name = _named(parent, tag)
            # Every filament key is a list, one entry per filament.
            keys = {k: [v] for k, v in data["filament"][kind].items()}
            out[name + ".json"] = {"type": "filament", "name": name, "inherits": parent,
                                   "from": "User", "is_custom_defined": "0",
                                   "filament_settings_id": [name], "version": imp["version"], **keys}
    return out


def apply_settings(cfg, data):
    """Patch a project_settings.config dict in place; returns what was done.

    Keys the file does not have are reported, never added: a project from an
    older Bambu Studio may predate a key, and a key it does not know is not
    one it will apply."""
    missing = sorted(k for section in ("process", "project") for k in data[section] if k not in cfg)
    patch_process(cfg, {k: v for k, v in data["process"].items() if k in cfg})
    for k, v in data["project"].items():
        if k in cfg:
            cfg[k] = v

    types = cfg.get("filament_type") or []
    n = len(types)
    diff = list(cfg.get("different_settings_to_system") or [])
    if n and len(diff) != n + 2:      # [process, one per filament, machine]
        raise ValueError(f"different_settings_to_system: expected {n + 2} entries, got {len(diff)}")
    patched, skipped = {}, {}
    for i, kind in enumerate(types):
        keys = data["filament"].get(kind)
        if keys is None:
            skipped[i + 1] = kind
            continue
        for k, v in keys.items():
            vals = cfg.get(k)
            if not isinstance(vals, list) or not vals or len(vals) % n:
                missing.append(k)
                continue
            # One block per filament, as x2d/reslot.py reads them.
            w = len(vals) // n
            vals[i * w:(i + 1) * w] = [v] * w
        listed = set(filter(None, diff[1 + i].split(";"))) | {k for k in keys if k in cfg}
        diff[1 + i] = ";".join(sorted(listed))
        patched[i + 1] = kind
    if n:
        cfg["different_settings_to_system"] = diff
    return {"process": {k: v for k, v in data["process"].items() if k in cfg},
            "project": {k: v for k, v in data["project"].items() if k in cfg},
            "filament_slots": patched, "slots_left_as_is": skipped,
            "not_in_file": sorted(set(missing))}


def apply_3mf(src, dst, data):
    """Write `src` with the settings applied to `dst`; returns the report.

    Only project_settings.config changes. Per-object and per-plate overrides
    in model_settings.config are the designer's choice for that part, so
    they are listed, not removed: Bambu Studio applies them over the
    project's, and the report says which of yours they beat."""
    with zipfile.ZipFile(src) as zf:
        cfg = json.loads(zf.read("Metadata/project_settings.config"))
        names = set(zf.namelist())
        model = zf.read("Metadata/model_settings.config").decode() \
            if "Metadata/model_settings.config" in names else ""
    report = apply_settings(cfg, data)
    ours = set(data["process"]) | set(data["project"])
    report["overridden_in_model"] = sorted({f"{k}={v}" for k, v in _META.findall(model) if k in ours})
    rewrite(src, dst, {"Metadata/project_settings.config":
                       (json.dumps(cfg, indent=4, ensure_ascii=False) + "\n").encode()})
    return report
