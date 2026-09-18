#!/usr/bin/env python3
"""Slice a .3mf locally with Bambu Studio's CLI. Never connects to a printer.

Why this exists: everything else in this repo checks geometry, and a file can
pass every geometry check and still print garbage. The slicer is the only
thing that knows what the toolpaths will be, so the last preflight is to ask
it -- headlessly, into an isolated data dir, with nothing queued.

    uv run scripts/slice_local.py out/figure/figure_200mm.3mf

Writes <outdir>/plate_1.gcode, <outdir>/sliced.3mf and <outdir>/slice.json.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def find_studio(given=None) -> Path:
    """The Bambu Studio executable: --studio, then $BAMBU_STUDIO, then PATH,
    then the macOS app bundle, which is the one install location verified here."""
    if given is not None:
        # Asked for one install by name: never slice with a different one.
        if not Path(given).is_file():
            raise FileNotFoundError(f'--studio {given}: no such file')
        return Path(given)
    candidates = [os.environ.get('BAMBU_STUDIO'),
                  shutil.which('bambu-studio'), shutil.which('BambuStudio')]
    if sys.platform == 'darwin':
        candidates.append('/Applications/BambuStudio.app/Contents/MacOS/BambuStudio')
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise FileNotFoundError('Bambu Studio not found: pass --studio PATH or set BAMBU_STUDIO')


def slice_3mf(model: Path, outdir: Path, studio: Path | None = None, datadir: Path | None = None) -> dict:
    studio = find_studio(studio)
    # Absolute everywhere: the slicer runs with cwd=outdir, and a relative
    # outdir handed to it resolves inside itself and fails to create.
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    # --datadir keeps the CLI's config away from the GUI's; an empty dir is
    # fine because the .3mf carries its whole project_settings.config.
    datadir = (datadir or outdir / 'datadir').resolve()
    command = [str(studio), '--datadir', str(datadir), '--slice', '1',
               '--outputdir', str(outdir), '--export-3mf', 'sliced.3mf', str(model.resolve())]
    result = {'status': 'failed', 'command': command, 'printer_access': False}
    log = outdir / 'slice.log'
    try:
        with log.open('w') as fh:
            run = subprocess.run(command, stdout=fh, stderr=subprocess.STDOUT, timeout=600, cwd=outdir)
        result['exit_code'] = run.returncode
        if (outdir / 'result.json').exists():
            result['bambu_result'] = json.loads((outdir / 'result.json').read_text())
        gcode = outdir / 'plate_1.gcode'
        if run.returncode == 0 and (outdir / 'sliced.3mf').is_file() and gcode.is_file():
            result['status'] = 'sliced_locally'
            result.update(gcode_summary(gcode))
        result['log_messages'] = [l for l in log.read_text().splitlines() if '[error]' in l or '[warning]' in l]
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result['error'] = str(exc)
    (outdir / 'slice.json').write_text(json.dumps(result, indent=2))
    return result


def gcode_summary(gcode: Path) -> dict:
    """The header block is the slicer's own summary; lift the numbers out."""
    head = gcode.read_text().split('; HEADER_BLOCK_END')[0]
    grab = lambda key: (re.search(rf'; {re.escape(key)}: (.+)', head) or [None, None])[1]
    return {
        'layer_count': int(grab('total layer number') or 0),
        'filament_used_g': grab('total filament weight [g]'),
        'filament_used_m': grab('filament used [mm]'),
        'estimated_time': grab('model printing time') or grab('estimated printing time (normal mode)'),
        'header': head,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('model', type=Path, help='the .3mf to slice')
    ap.add_argument('--outdir', type=Path, help='default: <model dir>/<stem>-slice')
    ap.add_argument('--studio', type=Path, help='Bambu Studio executable; default: $BAMBU_STUDIO, PATH, '
                    'then the macOS app bundle')
    a = ap.parse_args()

    outdir = a.outdir or a.model.parent / f'{a.model.stem}-slice'
    result = slice_3mf(a.model, outdir, a.studio)
    print(json.dumps({k: v for k, v in result.items() if k != 'header'}, indent=2))
    return 0 if result['status'] == 'sliced_locally' else 1


if __name__ == '__main__':
    raise SystemExit(main())
