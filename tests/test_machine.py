"""The printer comes from the capture, and the capture can be any Bambu printer.

The fixtures below patch the stock X2D stand-in (tests/stock_capture.py) with
the machine keys of other printers, quoted from Bambu's public profiles
(resources/profiles/BBL/machine in the BambuStudio repo): "Bambu Lab A1 mini
0.4 nozzle.json" and its parent fdm_bbl_3dp_001_common for a single nozzle,
"Bambu Lab H2D 0.4 nozzle.json" for two nozzles with different reach. They are
test inputs, not profiles to print with -- only a project saved from Bambu
Studio is that.
"""
import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from x2d import X2D, Machine, Part, default_capture, machine_for, write_3mf
from x2d.validation import inspect_3mf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import capture, write_capture  # noqa: E402

A1_MINI = {
    'printer_model': 'Bambu Lab A1 mini',
    'printable_area': ['0x0', '180x0', '180x180', '0x180'],
    'printable_height': '180',
    'extruder_printable_area': [],          # fdm_bbl_3dp_001_common
    'extruder_printable_height': [],
    'extruder_type': ['Direct Drive'],
    'nozzle_diameter': ['0.4'],
    'filament_map': ['1'],
}
H2D = {
    'printer_model': 'Bambu Lab H2D',
    'printable_area': ['0x0', '350x0', '350x320', '0x320'],
    'extruder_printable_area': ['0x0,325x0,325x320,0x320', '25x0,350x0,350x320,25x320'],
    'nozzle_diameter': ['0.4', '0.4'],
}


def cube(size=20.0):
    return Part(trimesh.creation.box((size, size, size)), 'cube', 1)


class MachineFromCaptureTests(unittest.TestCase):
    def test_the_public_x2d_profile_reproduces_the_x2d_table(self):
        # The hand-written table and the parser check each other: if either
        # misreads a key, one of them is wrong about a real machine.
        self.assertEqual(Machine.from_settings(capture()), X2D)

    def test_a_single_nozzle_reaches_the_whole_bed(self):
        m = Machine.from_settings(capture(A1_MINI))
        self.assertEqual(m.name, 'Bambu Lab A1 mini')
        self.assertEqual(m.bed, (180.0, 180.0, 180.0))
        self.assertEqual(m.nozzles, 1)
        e = m.extruder(1)
        self.assertEqual((e.x, e.y, e.z_max, e.feed), ((0.0, 180.0), (0.0, 180.0), 180.0, 'direct_drive'))

    def test_two_nozzles_keep_their_own_reach(self):
        m = Machine.from_settings(capture(H2D))
        self.assertEqual(m.bed[:2], (350.0, 320.0))
        self.assertEqual(m.extruder(1).x, (0.0, 325.0))
        self.assertEqual(m.extruder(2).x, (25.0, 350.0))

    def test_a_percentage_line_width_scales_by_the_nozzle(self):
        m = Machine.from_settings(capture({'outer_wall_line_width': '105%'}))
        self.assertAlmostEqual(m.line_width, 0.42)

    def test_an_incomplete_capture_is_refused_not_guessed(self):
        for key in ('printable_area', 'printable_height', 'nozzle_diameter',
                    'layer_height', 'outer_wall_line_width'):
            cfg = capture()
            cfg.pop(key)
            if key == 'outer_wall_line_width':
                cfg.pop('line_width')
            with self.subTest(key=key), self.assertRaises(ValueError):
                Machine.from_settings(cfg)

    def test_no_capture_means_the_x2d(self):
        self.assertIs(machine_for(None), X2D)


class CaptureDrivesTheChecksTests(unittest.TestCase):
    def test_the_writer_places_and_bounds_on_the_captured_bed(self):
        cfg = capture(A1_MINI)
        out = io.BytesIO()
        write_3mf([cube()], out, settings=cfg)
        report = inspect_3mf(io.BytesIO(out.getvalue()))
        self.assertTrue(report['valid'], report['errors'])
        # centred on the A1 mini's 180 mm bed, not the X2D's 256
        self.assertEqual(np.mean(report['parts'][0]['bounds_mm'], axis=0)[:2].tolist(), [90.0, 90.0])
        with self.assertRaisesRegex(ValueError, 'build volume'):
            write_3mf([cube(190)], io.BytesIO(), settings=cfg)

    def test_preflight_judges_a_file_by_the_printer_it_carries(self):
        # Written with the X2D's reach overridden, at a spot the X2D can print
        # and an A1 mini cannot. verify.py must read the capture and say no.
        out = io.BytesIO()
        write_3mf([cube()], out, settings=capture(A1_MINI), machine=X2D, at=(220, 128))
        report = inspect_3mf(io.BytesIO(out.getvalue()))
        self.assertFalse(report['valid'])
        self.assertIn('outside build volume', ' '.join(report['errors']))

    def test_the_workbench_accepts_a_capture_from_another_printer(self):
        import app
        from PIL import Image
        project = io.BytesIO()
        write_3mf([cube()], project, settings=capture(A1_MINI))
        settings = base64.b64encode(project.getvalue()).decode()
        self.assertEqual(app.machine_info(app.settings_for({'settings': settings}))['name'],
                         'Bambu Lab A1 mini')
        img = Image.new('RGBA', (120, 90), (222, 67, 67, 255))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        result = app.build({'file': base64.b64encode(buf.getvalue()).decode(),
                            'settings': settings, 'width': 60, 'slots': [1, 2]})
        self.assertTrue(inspect_3mf(io.BytesIO(base64.b64decode(result['model'])))['valid'])


class ScriptsOnAnotherPrinterTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.a1 = self.tmp / 'a1mini.3mf'
        write_3mf([cube()], self.a1, settings=capture(A1_MINI))

    def test_scan2print_on_a_single_nozzle_capture(self):
        # It used to pin slots 1-4 to nozzle 1 unconditionally, which raises
        # on a capture with fewer slots in filament_map than that.
        from test_scripts import run_cli
        scan = self.tmp / 'scan.obj'
        trimesh.creation.box((0.03, 0.08, 0.03)).export(scan)     # metres, Y up
        r = run_cli('scan2print.py', [scan, '--height', 40, '--settings', self.a1,
                                      '--out', self.tmp / 'fig'])
        self.assertTrue(r['output']['watertight'])
        report = inspect_3mf(self.tmp / 'fig.3mf')
        self.assertTrue(report['valid'], report['errors'])

    def test_media2plate_on_a_single_nozzle_capture(self):
        from PIL import Image
        from test_scripts import run_cli
        img = self.tmp / 'art.png'
        Image.new('RGBA', (80, 60), (0, 0, 0, 255)).save(img)
        out = self.tmp / 'mosaic.3mf'
        run_cli('media2plate.py', [img, '--width', 40, '--slots', 1, 2,
                                   '--settings', self.a1, '--out', out])
        self.assertTrue(inspect_3mf(out)['valid'])


# A 0.12 mm process over a 0.2 mm first layer: boundaries at 0.2, 0.32, 0.44...
FINE = {'layer_height': '0.12', 'initial_layer_print_height': '0.2'}


class LayerGridTests(unittest.TestCase):
    """The grid starts at the first layer, not at zero."""

    def setUp(self):
        self.fine = Machine.from_settings(capture(FINE))

    def test_boundaries_are_counted_from_the_first_layer(self):
        m = self.fine
        for z in (0.0, 0.2, 0.32, 1.52):
            self.assertTrue(m.on_grid(z), z)
        for z in (0.12, 0.24, 1.6):                  # multiples of 0.12 from zero
            self.assertFalse(m.on_grid(z), z)
        self.assertAlmostEqual(m.grid_ceil(1.45), 1.52)
        self.assertEqual(m.layers(1.52), 12)
        self.assertTrue(X2D.on_grid(1.6) and X2D.layers(1.6) == 8)

    def test_an_off_grid_band_names_the_nearest_heights(self):
        with self.assertRaisesRegex(ValueError, r'base 1\.6 mm is off the layer grid.*1\.52 or 1\.64'):
            self.fine.check_band(1.6, 0.6)
        with self.assertRaisesRegex(ValueError, 'relief 0.5 mm'):
            self.fine.check_band(1.52, 0.5)
        self.fine.check_band(1.52, 0.48)
        X2D.check_band(1.6, 0.6)

    def test_a_mosaic_on_the_fine_grid(self):
        from PIL import Image
        from x2d.mosaic import make_mosaic
        img = Image.new('RGB', (20, 20), '#000000')
        with self.assertRaisesRegex(ValueError, 'layer grid'):
            make_mosaic(img, {1: '#000000'}, width=20, cell=2, base=1.6, relief=0.6, machine=self.fine)
        r = make_mosaic(img, {1: '#000000'}, width=20, cell=2, base=1.52, relief=0.48, machine=self.fine)
        self.assertEqual((r.report['base_layers'], r.report['color_layers']), (12, 4))

    def test_the_workbench_refuses_a_download_verify_would_refuse(self):
        import app
        from PIL import Image
        project = io.BytesIO()
        write_3mf([cube()], project, settings=capture(FINE))
        settings = base64.b64encode(project.getvalue()).decode()
        buf = io.BytesIO()
        Image.new('RGBA', (60, 40), (0, 0, 0, 255)).save(buf, 'PNG')
        req = {'file': base64.b64encode(buf.getvalue()).decode(), 'settings': settings,
               'width': 40, 'slots': [1, 2], 'relief': 0.48}
        with self.assertRaisesRegex(ValueError, 'layer grid'):
            app.build({**req, 'base': 1.6})
        result = app.build({**req, 'base': 1.52})
        self.assertTrue(inspect_3mf(io.BytesIO(base64.b64decode(result['model'])))['valid'])


class CaptureReadingTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_a_zero_outer_wall_means_line_width_as_in_bambu_studio(self):
        self.assertAlmostEqual(Machine.from_settings(capture({'outer_wall_line_width': '0'})).line_width, 0.42)
        with self.assertRaises(ValueError):
            Machine.from_settings(capture({'outer_wall_line_width': '0', 'line_width': '0'}))

    def test_a_half_resolved_capture_is_refused(self):
        # The shape the Bambu Studio CLI exports from unflattened presets.
        from x2d import project_settings
        for patch in ({'filament_density': ['0', '1.26', '1.26', '1.26']},
                      {'filament_settings_id': ['a', 'b', 'c', 'd'], 'filament_colour': ['#00AE42']}):
            path = write_capture(self.tmp / 'half.3mf', patch)
            with self.subTest(patch=patch), self.assertRaisesRegex(ValueError, 'half-resolved'):
                project_settings(path)

    def test_scan2print_keeps_the_captures_own_nozzle_map(self):
        # Slots 3-4 on a second AMS on nozzle 2. Pinning everything to nozzle 1
        # would send slot 3 to a nozzle that has no such filament.
        from test_scripts import run_cli
        cap = write_capture(self.tmp / 'two-ams.3mf', {'filament_map': ['1', '1', '2', '2']})
        scan = self.tmp / 'scan.obj'
        trimesh.creation.box((0.03, 0.08, 0.03)).export(scan)
        run_cli('scan2print.py', [scan, '--height', 40, '--settings', cap, '--slot', 3,
                                  '--out', self.tmp / 'fig'])
        from x2d import project_settings
        out = project_settings(self.tmp / 'fig.3mf')
        self.assertEqual(out['filament_map'], ['1', '1', '2', '2'])
        self.assertEqual(out['filament_map_mode'], 'Manual')

    def test_scan2print_plate_top_sits_on_the_grid(self):
        from contextlib import redirect_stderr
        from test_scripts import run_cli
        cap = write_capture(self.tmp / 'fine.3mf', FINE)
        scan = self.tmp / 'scan.obj'
        trimesh.creation.box((0.03, 0.08, 0.03)).export(scan)
        argv = [scan, '--height', 40, '--settings', cap, '--slot', 1, '--plate-slot', 2]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_cli('scan2print.py', argv + ['--base', 4.0, '--out', self.tmp / 'a'])
        run_cli('scan2print.py', argv + ['--base', 3.8, '--out', self.tmp / 'b'])
        plate = [p for p in inspect_3mf(self.tmp / 'b.3mf')['parts'] if p['name'] == 'plate']
        self.assertTrue(plate and plate[0]['layer_aligned'])


class ReviewFixTests(unittest.TestCase):
    """One test per defect the pre-release review found."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.cap = write_capture(self.tmp / 'x2d.3mf')

    def letters_area(self, *extra):
        from test_scripts import run_cli
        # 'H' has a straight left stroke at mid-height, where the hole sits;
        # an 'A' slants away from it and would hide the bite.
        r = run_cli('text2plate.py', ['HIH', '--width', 60, '--filaments', 1, 4, '--settings', self.cap,
                                      '--out', self.tmp / 'k.3mf', *extra])
        return next(c['area_mm2'] for c in r['checks'] if c['label'] == 'slot4')

    def test_the_keychain_hole_leaves_the_letters_whole(self):
        self.assertEqual(self.letters_area('--hole', 5), self.letters_area())

    def test_an_out_path_without_3mf_keeps_the_project(self):
        from test_scripts import run_cli
        out = self.tmp / 'name'
        run_cli('text2plate.py', ['ABC', '--width', 60, '--settings', self.cap, '--out', out])
        self.assertTrue(inspect_3mf(out)['valid'])              # still the project, not a PNG
        self.assertTrue((self.tmp / 'name_preview.png').is_file())

    def test_font_given_means_no_font_lookup(self):
        from unittest import mock
        from test_scripts import run_cli
        from x2d.fonts import bold_font
        font = bold_font()
        with mock.patch('x2d.fonts.bold_font', side_effect=FileNotFoundError('no fonts here')):
            run_cli('text2plate.py', ['ABC', '--width', 60, '--font', font, '--settings', self.cap,
                                      '--out', self.tmp / 'f.3mf'])
        self.assertTrue(inspect_3mf(self.tmp / 'f.3mf')['valid'])

    def test_defaults_snap_to_the_captures_grid(self):
        from test_scripts import run_cli
        fine = write_capture(self.tmp / 'fine.3mf', FINE)
        r = run_cli('text2plate.py', ['ABC', '--width', 60, '--settings', fine, '--out', self.tmp / 't.3mf'])
        self.assertTrue(inspect_3mf(self.tmp / 't.3mf')['valid'])
        self.assertEqual(r['layers'], {'plate': 16, 'relief': 8})   # 2.0 -> 2.0, 1.0 -> 0.96
        from PIL import Image
        img = self.tmp / 'm.png'
        Image.new('RGBA', (40, 30), (0, 0, 0, 255)).save(img)
        run_cli('media2plate.py', [img, '--width', 30, '--settings', fine, '--out', self.tmp / 'm.3mf'])
        self.assertTrue(inspect_3mf(self.tmp / 'm.3mf')['valid'])
        with self.assertRaises(ValueError):                      # an explicit height is checked, not moved
            Machine.from_settings(capture(FINE)).band(1.6, None, default=(2.0, 1.0))

    def test_a_short_filament_map_is_a_finding_not_a_crash(self):
        import json, zipfile
        src = io.BytesIO()
        write_3mf([Part(trimesh.creation.box((20, 20, 2)), 'p', 3)], src, settings=capture())
        out = self.tmp / 'short.3mf'
        with zipfile.ZipFile(io.BytesIO(src.getvalue())) as zin, zipfile.ZipFile(out, 'w') as zout:
            for n in zin.namelist():
                data = zin.read(n)
                if n == 'Metadata/project_settings.config':
                    data = json.dumps(capture({'filament_map_mode': 'Manual', 'filament_map': ['1']}))
                zout.writestr(n, data)
        report = inspect_3mf(out)
        self.assertFalse(report['valid'])
        self.assertIn('no nozzle in filament_map', ' '.join(report['errors']))
        with self.assertRaisesRegex(ValueError, 'no nozzle in filament_map'):
            write_3mf([Part(trimesh.creation.box((20, 20, 2)), 'p', 3)], io.BytesIO(),
                      settings=capture({'filament_map_mode': 'Manual', 'filament_map': ['1']}))

    def test_an_unreadable_project_is_a_value_error(self):
        import zipfile
        bad = self.tmp / 'bad.3mf'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('3D/3dmodel.model', '<model><unclosed>')
            z.writestr('Metadata/model_settings.config', '<config/>')
        with self.assertRaisesRegex(ValueError, 'not a readable project'):
            inspect_3mf(bad)

    def test_the_workbench_answers_every_failure_with_json(self):
        import json, threading, urllib.error, urllib.request
        from http.server import ThreadingHTTPServer
        from unittest import mock
        import app
        srv = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_port
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/build', data=b'{}',
                                     headers={'Content-Type': 'application/json', 'Host': f'127.0.0.1:{port}'})
        with mock.patch('app.build', side_effect=RuntimeError('cv2 fell over')), \
                self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 500)
        self.assertIn('cv2 fell over', json.loads(caught.exception.read())['error'])


class NoCaptureTests(unittest.TestCase):
    """No profile ships, so a fresh clone has no capture. Each entry point
    either works without one and says what it assumed, or says how to make one.

    default_capture is patched out rather than trusted to be absent: whoever
    runs the suite may well have saved their own profiles/default.3mf.
    """

    def setUp(self):
        from unittest import mock
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.none = mock.patch('x2d.default_capture', return_value=None)

    def test_the_default_is_found_only_where_documented(self):
        self.assertIsNone(default_capture(self.tmp))
        (self.tmp / 'profiles').mkdir()
        (self.tmp / 'profiles/other.3mf').write_bytes(b'')
        self.assertIsNone(default_capture(self.tmp))
        write_3mf([cube()], self.tmp / 'profiles/default.3mf', settings=capture())
        self.assertEqual(default_capture(self.tmp), self.tmp / 'profiles/default.3mf')

    def test_art2plate_without_a_capture_writes_slots_and_says_so(self):
        from PIL import Image
        from test_scripts import run_cli
        img = self.tmp / 'art.png'
        a = np.zeros((80, 80, 4), np.uint8)
        a[10:70, 10:70] = (255, 0, 0, 255)
        Image.fromarray(a, 'RGBA').save(img)
        out = self.tmp / 'art.3mf'
        with self.none:
            r = run_cli('art2plate.py', [img, '--width', 40, '--map', '#FF0000=2',
                                         '--out', out])
        self.assertEqual(r['machine_from'], 'default (no capture)')
        self.assertTrue(inspect_3mf(out)['valid'])

    def test_media2plate_without_a_capture_says_how_to_make_one(self):
        from PIL import Image
        from test_scripts import run_cli
        img = self.tmp / 'art.png'
        Image.new('RGBA', (40, 30), (0, 0, 0, 255)).save(img)
        err = io.StringIO()
        from contextlib import redirect_stderr
        with self.none, redirect_stderr(err), self.assertRaises(SystemExit):
            run_cli('media2plate.py', [img, '--out', self.tmp / 'm.3mf'])
        self.assertIn('profiles/default.3mf', err.getvalue())

    def test_the_workbench_without_a_capture_says_how_to_make_one(self):
        from unittest import mock
        import app
        with mock.patch('app.default_capture', return_value=None):
            with self.assertRaisesRegex(ValueError, 'profiles/default.3mf'):
                app.settings_for({})


if __name__ == '__main__':
    unittest.main()
