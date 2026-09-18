"""Smoke tests for the CLIs, which is empirically where the bugs escape.

Every bug this repo caught before a human saw it lived in `x2d/`: the palette
seam, the cross_check false positive, the undeclared scipy and networkx. Every
bug that reached the user lived in `scripts/`: the preview that rendered
gold-on-grey whatever was loaded, the filament count that claimed 3 swaps for a
one-swap plate, and rtree -- found by a clean `uv run`, not by this suite.

The scripts are not thin. They hold argument validation, the filament lookup,
the report assembly and the preview call, and none of it was executed by a test
until this file. Each test here runs a real CLI end to end on a tiny fixture and
checks the two things the user actually relies on: the project opens, and the
report does not lie.
"""
import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
from PIL import Image

from x2d.validation import inspect_3mf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import PROFILE  # noqa: E402
SHARED = {'machine', 'size_mm', 'fits_bed', 'parts', 'filaments',
          'solid_g_total', 'checks', 'layers', 'filament_changes'}


def run_cli(script, argv):
    """Run a CLI in-process and return (parsed report, output path)."""
    out = io.StringIO()
    saved = sys.argv
    sys.argv = [script] + [str(a) for a in argv]
    try:
        with redirect_stdout(out):
            runpy.run_path(str(ROOT / 'scripts' / script), run_name='__main__')
    finally:
        sys.argv = saved
    text = out.getvalue()
    start = text.index('{')
    return json.loads(text[start:])


class ScriptTests(unittest.TestCase):
    def setUp(self):
        # Outside the repo on purpose: a test that writes into a connected
        # folder cannot always clean up after itself, and out/ is for things
        # the user asked for.
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def logo(self):
        """A blob big enough to survive the nozzle at the sizes used here."""
        a = np.full((120, 120, 4), 0, np.uint8)
        a[30:90, 30:90] = (0, 0, 0, 255)
        path = self.tmp / 'logo.png'
        Image.fromarray(a, 'RGBA').save(path)
        return path

    def drawing(self):
        a = np.full((160, 160), 250, np.uint8)
        a[40:44, 20:140] = 40                      # a stroke, 4 px -> sub-nozzle
        a[70:74, 20:140] = 150                     # a fainter one
        a[70:74, 20:30] = 40                       # ...seeded, so it is kept
        path = self.tmp / 'draw.png'
        Image.fromarray(a).convert('RGB').save(path)
        return path

    def assert_sane(self, report, out):
        self.assertTrue(SHARED <= set(report), SHARED - set(report))
        self.assertTrue(report['fits_bed'])
        self.assertTrue(all(p['watertight'] for p in report['parts']))
        # Every part names a colour, because a report that cannot say what it
        # will print in is how the preview shipped in invented colours.
        self.assertTrue(all(p['colour'] for p in report['parts']))
        self.assertGreater(report['solid_g_total'], 0)
        verdict = inspect_3mf(out)
        self.assertTrue(verdict['valid'], verdict['errors'])

    def test_text2plate_writes_a_project_that_verifies(self):
        out = self.tmp / 'abc.3mf'
        r = run_cli('text2plate.py', ['ABC', '--width', 60, '--filaments', 1, 4,
                                      '--settings', PROFILE, '--out', out])
        self.assert_sane(r, out)
        self.assertEqual(r['text'], 'ABC')

    def test_img2plate_writes_a_project_that_verifies(self):
        out = self.tmp / 'badge.3mf'
        r = run_cli('img2plate.py', [self.logo(), '--width', 40, '--filaments', 1, 4,
                                     '--settings', PROFILE, '--out', out])
        self.assert_sane(r, out)

    def test_art2plate_writes_a_project_that_verifies(self):
        out = self.tmp / 'art.3mf'
        r = run_cli('art2plate.py', [self.logo(), '--width', 40, '--base', 1.6,
                                     '--map', '#000000=4', '--base-slot', 1,
                                     '--settings', PROFILE, '--out', out])
        self.assert_sane(r, out)

    def test_art2plate_ink_flag_survives_a_sub_nozzle_stroke(self):
        out = self.tmp / 'ink.3mf'
        r = run_cli('art2plate.py', [self.drawing(), '--width', 40, '--base', 1.6,
                                     '--ink', '110,200', '--map', '#000000=1',
                                     '--base-slot', 3, '--settings', PROFILE,
                                     '--out', out])
        self.assert_sane(r, out)
        self.assertLess(r['ink']['raw_stroke_mm'], r['ink']['min_feature_mm'])
        ink = next(c for c in r['checks'] if c['label'] != 'plate')
        self.assertLess(ink['at_risk_pct'], 5.0)

    def test_a_two_colour_plate_costs_exactly_one_filament_change(self):
        # The plate and the artwork are different layer bands, so the plate is
        # not a second colour in the relief band. Collapsing the two into one
        # dict made this report 10 for a print that needs 1.
        for script, argv, out in [
            ('text2plate.py', ['ABC', '--width', 60, '--filaments', 1, 4], 'a.3mf'),
            ('img2plate.py', [self.logo(), '--width', 40, '--filaments', 1, 4], 'b.3mf'),
        ]:
            path = self.tmp / out
            r = run_cli(script, argv + ['--settings', PROFILE, '--out', path])
            self.assertEqual(r['filament_changes'], 1, script)

    def test_every_generator_emits_the_same_report_shape(self):
        # Five reports with no key in common is how one of them lost its
        # colours without anyone noticing.
        shapes = []
        shapes.append(set(run_cli('text2plate.py', [
            'Z', '--width', 40, '--filaments', 1, 4, '--settings', PROFILE,
            '--out', self.tmp / 'x.3mf'])))
        shapes.append(set(run_cli('img2plate.py', [
            self.logo(), '--width', 40, '--filaments', 1, 4, '--settings', PROFILE,
            '--out', self.tmp / 'y.3mf'])))
        shapes.append(set(run_cli('art2plate.py', [
            self.logo(), '--width', 40, '--base', 1.6, '--map', '#000000=4',
            '--settings', PROFILE, '--out', self.tmp / 'z.3mf'])))
        self.assertTrue(SHARED <= set.intersection(*shapes))


if __name__ == '__main__':
    unittest.main()


class AppTests(unittest.TestCase):
    """The endpoints the viewer reads. Same reason as the CLIs: app.py was
    untested, and it is now the thing deciding what a browser believes."""

    def setUp(self):
        import app
        self.app = app
        import base64
        self.settings = app.settings_for(
            {'settings': base64.b64encode(Path(PROFILE).read_bytes()).decode()})

    def test_machine_facts_come_from_the_capture_not_from_constants(self):
        m = self.app.machine_info(self.settings)
        # The three the old viewer hardcoded, and got wrong for this machine.
        self.assertEqual(m['overhang_deg'], 30.0)        # not 45
        self.assertAlmostEqual(m['infill'], 0.15)        # not 0.20
        self.assertEqual(m['filaments'][4]['density'], 1.32)   # not 1.24
        self.assertEqual(m['filaments'][1]['density'], 1.26)
        self.assertEqual(m['bed_mm'][0], 256.0)          # not 220
        self.assertEqual(len(m['nozzles']), 2)
        self.assertEqual(m['nozzles'][1]['feed'], 'bowden')

    def test_inspect_returns_the_same_verdict_as_verify(self):
        import base64
        out = self.tmp = Path(tempfile.mkdtemp()) / 'p.3mf'
        r = run_cli('text2plate.py', ['ABC', '--width', 60, '--filaments', 1, 4,
                                      '--settings', PROFILE, '--out', out])
        self.assertEqual(r['filament_changes'], 1)
        got = self.app.inspect_upload({'name': 'p.3mf',
                                       'file': base64.b64encode(out.read_bytes()).decode()})
        self.assertTrue(got['report']['valid'], got['report']['errors'])
        self.assertEqual([p['filament_slot'] for p in got['report']['parts']], [1, 4])

    def test_inspect_refuses_an_stl_and_says_why(self):
        # An STL has no part or slot metadata, so "valid" would be meaningless.
        import base64
        with self.assertRaises(ValueError) as e:
            self.app.inspect_upload({'name': 'x.stl',
                                     'file': base64.b64encode(b'solid x').decode()})
        self.assertIn('.3mf', str(e.exception))


class ScriptHelperTests(unittest.TestCase):
    """The parts of the slicing scripts that do not need Bambu Studio itself."""

    def load(self, script):
        return runpy.run_path(str(ROOT / 'scripts' / script))

    def test_bambu_studio_is_found_not_assumed(self):
        from unittest import mock
        find = self.load('slice_local.py')['find_studio']
        with tempfile.TemporaryDirectory() as d:
            given, env = Path(d) / 'given', Path(d) / 'env'
            given.write_text(''); env.write_text('')
            with self.assertRaisesRegex(FileNotFoundError, '--studio'):
                find(Path(d) / 'typo')                          # asked for, missing: no fallback
            with mock.patch.dict('os.environ', {'BAMBU_STUDIO': str(env)}):
                self.assertEqual(find(given), given)          # --studio wins
                self.assertEqual(find(None), env)              # then the env var
            with mock.patch.dict('os.environ', {}, clear=True), \
                    mock.patch('shutil.which', return_value=None), \
                    mock.patch('sys.platform', 'linux'):
                with self.assertRaisesRegex(FileNotFoundError, 'BAMBU_STUDIO'):
                    find(None)

    def test_default_layer_heights_come_from_the_gcode(self):
        spread = self.load('review_layers.py')['spread_heights']
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / 'plate_1.gcode'
            g.write_text(''.join(f'; Z_HEIGHT: {0.2 * i:.1f}\n' for i in range(1, 101)))
            zs = spread(g)
        self.assertEqual(len(zs), 4)
        self.assertTrue(0 < zs[0] < zs[-1] <= 20.0)
        self.assertGreater(zs[-1], 15.0)                        # reaches near the top

