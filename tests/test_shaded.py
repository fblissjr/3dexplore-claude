"""Shaded colour art: x2d/shaded.py and art2plate --shaded.

The fixture is drawn here, not shipped: a red disc and a blue square, each lit
from one side so it spans many shades, render noise on top, and a yellow stroke
0.5 mm wide -- wide enough to survive denoising (10 px at 20 px/mm), narrower
than the 0.84 mm a 0.4 nozzle can print. Only the opening step removes it,
which is what makes it a test of that step.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import write_capture  # noqa: E402
from test_scripts import run_cli  # noqa: E402

from x2d import X2D, printability  # noqa: E402
from x2d.palette import separate  # noqa: E402
from x2d.shaded import OUT_PX_PER_MM, auto_map, flatten, stand_ins, to_image  # noqa: E402
from x2d.validation import inspect_3mf  # noqa: E402

SPOOLS = {1: '#000000', 2: '#0A2989', 3: '#FFFFFF', 4: '#DE4343'}
WIDTH_MM = 30                                            # 600 px -> 20 px/mm
TABLE = {'#E04646': 4, '#9A3030': 4, '#2846BE': 2, '#1A2E80': 2, '#FAE628': 1}


def render(size=600, seed=1):
    """A shaded red disc and blue square, noisy, with a sub-nozzle stroke."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[:size, :size]
    u = size / 300
    light = 0.45 + 0.55 * (x / size)                      # lit from the right
    img = np.zeros((size, size, 4), np.uint8)
    disc = (x - 90 * u) ** 2 + (y - 150 * u) ** 2 < (70 * u) ** 2
    square = (x > 170 * u) & (x < 280 * u) & (y > 80 * u) & (y < 220 * u)
    for mask, rgb in ((disc, (224, 70, 70)), (square, (40, 70, 190))):
        shade = np.clip(np.array(rgb)[None] * light[mask][:, None], 0, 255)
        img[mask, :3] = shade
        img[mask, 3] = 255
    body = img[..., 3] > 0
    img[body, :3] = np.clip(img[body, :3] + rng.normal(0, 18, (body.sum(), 3)), 0, 255)
    stroke = (slice(int(145 * u), int(145 * u) + 10), slice(int(20 * u), int(290 * u)))
    img[stroke + (slice(0, 3),)] = (250, 230, 40)        # 10 px = 0.5 mm
    img[stroke + (3,)] = 255
    return Image.fromarray(img, 'RGBA')


class ShadedTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_shades_never_cross_to_another_material(self):
        # The promise is not "every shade on its own spool" -- a deep shadow may
        # fairly go to black -- but that red never becomes blue or white, and
        # each material keeps at least one shade on its own spool.
        table = auto_map(render(), SPOOLS)
        rgb = {c: (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) for c in table}
        red = [table[c] for c, (r, g, b) in rgb.items() if r > 1.5 * g and r > 1.5 * b]
        blue = [table[c] for c, (r, g, b) in rgb.items() if b > 1.5 * r and b > 1.5 * g]
        self.assertTrue(red and set(red) <= {4, 1} and 4 in red, table)
        self.assertTrue(blue and set(blue) <= {2, 1} and 2 in blue, table)

    def regions(self, labels):
        stand = stand_ins(sorted(set(np.unique(labels).tolist()) - {-1}))
        return separate(to_image(labels, stand), {c: s for s, c in stand.items()},
                        width_mm=labels.shape[1] / OUT_PX_PER_MM)

    def test_nothing_narrower_than_the_nozzle_survives(self):
        labels = flatten(render(), TABLE, width_mm=WIDTH_MM, machine=X2D)
        self.assertNotIn(1, np.unique(labels))            # the 0.5 mm stroke is gone
        self.assertTrue({2, 4} <= set(np.unique(labels)))

    def test_a_thin_outline_part_is_dropped_not_refilled(self):
        # The stroke runs 1 mm past the blue square. Refilled from its
        # neighbour it came back as a 0.5 mm blue sliver: 0.4% of blue at
        # risk. Dropped, what remains is contour rounding.
        for slot, geom in self.regions(flatten(render(), TABLE, width_mm=WIDTH_MM)).items():
            self.assertLessEqual(printability(geom, X2D)['at_risk_pct'], 0.2, slot)

    def test_the_halo_is_as_wide_as_asked(self):
        labels = flatten(render(), {'#E04646': 4, '#2846BE': 2}, width_mm=WIDTH_MM,
                         halo_slot=3, halo_mm=1.2)
        row = labels[labels.shape[0] // 2]
        start = np.nonzero(row >= 0)[0][0]
        ring = next(i for i, v in enumerate(row[start:]) if v != 3)
        self.assertGreaterEqual(ring, round(1.2 * OUT_PX_PER_MM) - 1)   # was half that

    def test_a_tight_crop_still_gets_a_halo_all_round(self):
        opaque = Image.new('RGBA', (400, 300), (224, 70, 70, 255))
        labels = flatten(opaque, {'#E04646': 4}, width_mm=40, halo_slot=3)
        for edge in (labels[0], labels[-1], labels[:, 0], labels[:, -1]):
            self.assertTrue(((edge == 3) | (edge == -1)).all() and (edge == 3).any())

    def test_art_too_small_for_the_nozzle_says_so(self):
        with self.assertRaisesRegex(ValueError, 'print it larger'):
            flatten(render(), TABLE, width_mm=1)

    def test_two_slots_with_the_same_spool_stay_two_regions(self):
        labels = flatten(render(), {'#E04646': 3, '#2846BE': 2}, width_mm=WIDTH_MM)
        stand = stand_ins([2, 3])
        regions = separate(to_image(labels, stand), {stand[2]: 2, stand[3]: 3}, width_mm=WIDTH_MM)
        self.assertEqual(set(regions), {2, 3})           # though both might be loaded white

    def test_art2plate_shaded_prints_where_plain_separation_does_not(self):
        src = self.tmp / 'render.png'
        render().save(src)
        cap = write_capture(self.tmp / 'x2d.3mf', {'filament_colour': list(SPOOLS.values())})
        table = [f'{c}={s}' for c, s in TABLE.items()]
        plain = run_cli('art2plate.py', [src, '--width', WIDTH_MM, '--map', *table, '--settings', cap,
                                         '--out', self.tmp / 'plain.3mf'])
        shaded = run_cli('art2plate.py', [src, '--width', WIDTH_MM, '--shaded', '--map', *table,
                                          '--settings', cap, '--out', self.tmp / 'shaded.3mf'])
        worst = lambda r: max(c['at_risk_pct'] for c in r['checks'])
        self.assertGreater(worst(plain), 50)            # the stroke, traced as a part
        self.assertLessEqual(worst(shaded), 1.0)
        self.assertNotIn('slot1', {c['label'] for c in shaded['checks']})
        self.assertTrue(inspect_3mf(self.tmp / 'shaded.3mf')['valid'])
        self.assertTrue((self.tmp / 'shaded_flat.png').is_file())

    def test_automatic_table_is_reported_for_editing(self):
        src = self.tmp / 'render.png'
        render().save(src)
        cap = write_capture(self.tmp / 'x2d.3mf', {'filament_colour': list(SPOOLS.values())})
        r = run_cli('art2plate.py', [src, '--width', WIDTH_MM, '--shaded', '--halo', 3,
                                     '--settings', cap, '--out', self.tmp / 'auto.3mf'])
        self.assertTrue(all(k.startswith('#') for k in r['shaded']['map']))
        self.assertEqual(r['shaded']['halo'], {'slot': 3, 'mm': 1.2})
        self.assertTrue(inspect_3mf(self.tmp / 'auto.3mf')['valid'])

    def test_a_priority_naming_a_removed_slot_is_not_a_crash(self):
        src = self.tmp / 'render.png'
        render().save(src)
        cap = write_capture(self.tmp / 'x2d.3mf', {'filament_colour': list(SPOOLS.values())})
        r = run_cli('art2plate.py', [src, '--width', WIDTH_MM, '--shaded', '--priority', 1, 4, 2,
                                     '--map', *[f'{c}={s}' for c, s in TABLE.items()],
                                     '--settings', cap, '--out', self.tmp / 'p.3mf'])
        self.assertEqual(r['shaded']['removed_as_too_thin'], [1])
        self.assertTrue(inspect_3mf(self.tmp / 'p.3mf')['valid'])

    def test_argument_mistakes_are_named(self):
        from unittest import mock
        src = self.tmp / 'render.png'
        render().save(src)
        cases = [
            (['--shaded', '--ink'], '--ink'),
            (['--halo', '3', '--map', '#FF0000=1'], '--halo'),
            ([], '--map is required'),
            (['--shaded'], 'needs a capture'),
            (['--shaded', '--halo', '0', '--map', '#E04646=4'], '--halo wants'),
            (['--shaded', '--map', '#E04646=4', '--width', '1'], 'print it larger'),
        ]
        for extra, message in cases:
            err = io.StringIO()
            with self.subTest(extra=extra), mock.patch('x2d.default_capture', return_value=None), \
                    redirect_stderr(err), self.assertRaises(SystemExit):
                run_cli('art2plate.py', [src, '--width', WIDTH_MM, *extra, '--out', self.tmp / 'e.3mf'])
            self.assertIn(message, err.getvalue())


if __name__ == '__main__':
    unittest.main()
