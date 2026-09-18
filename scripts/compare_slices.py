#!/usr/bin/env python3
"""Put several local slices of the same model side by side.

Time comes from the slicer's own estimate; filament share per feature is
summed from the G-code's E moves, which is the only place the support
fraction actually lives. Same model, different settings: the numbers are the
argument, the layer plot is the sanity check.

    uv run scripts/compare_slices.py out/figure/figure_200mm_{auto,plate,slim}-slice --z 121 150
"""
import argparse
import collections
import json
import re
from pathlib import Path


def feature_shares(gcode: Path) -> dict:
    feat, total = '', collections.Counter()
    for line in gcode.open():
        if line.startswith('; FEATURE:'):
            feat = line.split(':')[1].strip()
        elif line[:3] in ('G1 ', 'G2 ', 'G3 '):
            m = re.search(r'E(\d*\.?\d+)', line)
            if m:
                total[feat] += float(m.group(1))
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('slices', type=Path, nargs='+', help='slice_local.py output directories')
    ap.add_argument('--z', type=float, nargs='*', help='also plot these heights for each slice')
    a = ap.parse_args()
    rows = []
    for d in a.slices:
        s = json.loads((d / 'slice.json').read_text())
        plate = s['bambu_result']['sliced_plates'][0]
        shares = feature_shares(d / 'plate_1.gcode')
        tot = sum(shares.values())
        sup = sum(v for k, v in shares.items() if k.startswith('Support'))
        grams = plate['filaments'][0]['total_used_g']
        rows.append({'slice': d.name.replace('-slice', ''), 'time': s['estimated_time'].split(';')[0],
                     'g': round(grams, 1), 'support_g': round(grams * sup / tot, 1),
                     'support_pct': round(100 * sup / tot, 1),
                     'support_time_pct': round(100 * plate['feature_type_times'].get('Support', 0) / plate['main_predication'], 1),
                     'layers': s['layer_count']})
    w = max(len(r['slice']) for r in rows)
    print(f"{'slice':{w}}  {'time':>10}  {'g':>6}  {'support g':>9}  {'support %':>9}  {'support time %':>14}  layers")
    for r in rows:
        print(f"{r['slice']:{w}}  {r['time']:>10}  {r['g']:6.1f}  {r['support_g']:9.1f}  {r['support_pct']:9.1f}  {r['support_time_pct']:14.1f}  {r['layers']}")
    if a.z:
        import subprocess, sys
        for d in a.slices:
            subprocess.run([sys.executable, str(Path(__file__).with_name('review_layers.py')),
                            str(d / 'plate_1.gcode'), '--z', *map(str, a.z)], check=True)


if __name__ == '__main__':
    main()
