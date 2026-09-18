import io
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

import cv2
import numpy as np
from PIL import Image
import trimesh
from shapely.geometry import box as sbox
from shapely.ops import unary_union

from x2d import Part, write_3mf, project_settings, extrude, X2D
from x2d.mosaic import make_mosaic, video_frame
from x2d.palette import separate, footprint, label_map
from x2d.poster import ink_art, ink_mask, line_width_mm
from x2d.validation import inspect_3mf, cross_check

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import PROFILE  # noqa: E402


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = project_settings(PROFILE)

    def test_palette_partition_volume_and_native_export(self):
        colors = {1: '#000000', 2: '#0A2989', 3: '#FFFFFF', 4: '#DE4343'}
        image = Image.new('RGB', (40, 40))
        for i, color in enumerate(colors.values()):
            image.paste(color, (i % 2 * 20, i // 2 * 20, i % 2 * 20 + 20, i // 2 * 20 + 20))
        result = make_mosaic(image, colors, width=40, cell=2)
        self.assertEqual(result.report['used_artwork_slots'], [1, 2, 3, 4])
        self.assertAlmostEqual(sum(p.mesh.volume for p in result.parts[1:]), 40 * 40 * .6, delta=1e-3)
        self.assertTrue(all(p.mesh.is_volume for p in result.parts))
        # No volume overlaps: each top region exactly partitions the image.
        for i, a in enumerate(result.parts[1:]):
            for b in result.parts[1:][i+1:]:
                common = trimesh.boolean.intersection([a.mesh, b.mesh], engine='manifold')
                self.assertLess(abs(common.volume), 1e-5)
        output = io.BytesIO()
        write_3mf(result.parts, output, settings=self.cfg)
        output.seek(0)
        self.assertTrue(inspect_3mf(output)['valid'])

    def test_writer_escapes_names_and_preserves_coordinates(self):
        mesh = trimesh.creation.box([10, 10, 2])
        mesh.apply_translation([20, 30, 1])
        before = mesh.vertices.copy()
        output = io.BytesIO()
        write_3mf([Part(mesh, 'red & "white"', 4)], output, name='A&B <test>', settings=self.cfg)
        np.testing.assert_array_equal(before, mesh.vertices)
        with zipfile.ZipFile(output) as z:
            cfg = ET.fromstring(z.read('Metadata/model_settings.config'))
            ET.fromstring(z.read('3D/3dmodel.model'))
        self.assertEqual(cfg.find('object/metadata').get('value'), 'A&B <test>')

    def test_bad_slots_and_placement_are_rejected(self):
        for slot in (0, -1, 5):
            with self.assertRaises(ValueError):
                write_3mf([Part(trimesh.creation.box([10, 10, 2]), 'bad', slot)], io.BytesIO(), settings=self.cfg)
        with self.assertRaises(ValueError):
            write_3mf([Part(trimesh.creation.box([300, 10, 2]), 'oversize')], io.BytesIO())
        cfg = project_settings(PROFILE, pin={1: 2})
        with self.assertRaises(ValueError):
            write_3mf([Part(trimesh.creation.box([240, 10, 2]), 'aux')], io.BytesIO(), settings=cfg)

    def test_a_mosaic_uses_as_many_slots_as_the_capture_has(self):
        # Two AMS units is eight slots; the palette is the capture, not a
        # constant four.
        colors = {i + 1: c for i, c in enumerate(
            ['#000000', '#FFFFFF', '#DE4343', '#0A2989', '#2E8B57', '#F4D03F'])}
        image = Image.new('RGB', (60, 40))
        for i, color in enumerate(colors.values()):
            image.paste(color, (i % 3 * 20, i // 3 * 20, i % 3 * 20 + 20, i // 3 * 20 + 20))
        result = make_mosaic(image, colors, width=60, cell=2)
        self.assertEqual(result.report['used_artwork_slots'], list(colors))

    def test_invalid_mosaic_dimensions(self):
        for kw in ({'width': -1}, {'cell': .1}, {'base': 1.55}, {'width': 250}, {'relief': float('nan')}):
            with self.assertRaises(ValueError):
                make_mosaic(Image.new('RGB', (10, 10)), {1: '#000000'}, **kw)

    def test_transparency_and_diagonal_islands(self):
        transparent = Image.new('RGBA', (10, 10), (255, 0, 0, 0))
        result = make_mosaic(transparent, {1: '#000000', 4: '#FF0000'}, width=10, cell=1)
        self.assertEqual(result.report['used_artwork_slots'], [1])
        checker = Image.fromarray(np.array([[0, 255], [255, 0]], dtype=np.uint8)).convert('RGB')
        result = make_mosaic(checker, {1: '#000000', 3: '#FFFFFF'}, width=4, cell=2)
        self.assertTrue(all(p.mesh.is_volume for p in result.parts))

    def test_video_seek_and_out_of_range(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'test.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (32, 32))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                writer.write(np.full((32, 32, 3), i * 20, dtype=np.uint8))
            writer.release()
            image, duration = video_frame(path, .5)
            self.assertAlmostEqual(duration, 1)
            self.assertAlmostEqual(np.asarray(image).mean(), 100, delta=5)
            with self.assertRaises(ValueError):
                video_frame(path, 2)

    def test_preflight_checks_zip_geometry_without_stls(self):
        output = io.BytesIO()
        write_3mf([Part(trimesh.creation.box([10, 10, 1.55]), 'off grid')], output, settings=self.cfg)
        output.seek(0)
        report = inspect_3mf(output)
        self.assertFalse(report['valid'])
        self.assertIn('layer grid', report['errors'][0])


class PaletteTests(unittest.TestCase):
    """Flat-color separation: the emoji path, where edges come from the artist."""

    def test_missing_cairo_library_says_how_to_fix_it(self):
        # cairosvg imports cairocffi, which raises OSError -- not ImportError --
        # when the native library is absent. That is the first thing a fresh
        # macOS clone hits, and it must end in an instruction, not dlopen noise.
        import builtins
        from unittest import mock
        from x2d.palette import render_svg
        real = builtins.__import__
        def no_cairo(name, *a, **k):
            if name == 'cairosvg':
                raise OSError("no library called \"cairo\" was found")
            return real(name, *a, **k)
        with mock.patch('builtins.__import__', no_cairo):
            with self.assertRaisesRegex(ImportError, 'cairo library.*PNG'):
                render_svg('unused.svg', 100)

    def art(self):
        # Two touching bars plus a small square inside one of them, on alpha.
        image = Image.new('RGBA', (200, 200), (0, 0, 0, 0))
        image.paste((0, 0, 0, 255), (0, 0, 200, 100))
        image.paste((255, 255, 255, 255), (0, 100, 200, 200))
        image.paste((222, 67, 67, 255), (80, 20, 120, 60))
        return image

    def test_regions_tile_the_artwork_with_no_gap_or_overlap(self):
        mapping = {'#000000': 1, '#FFFFFF': 3, '#DE4343': 4}
        regions = separate(self.art(), mapping, width_mm=40, priority=[4, 1, 3])
        self.assertEqual(sorted(regions), [1, 3, 4])
        total = sum(g.area for g in regions.values())
        union = unary_union(list(regions.values()))
        # Grown seams are differenced away, so summed area IS the union area:
        # no double-printed overlap, and no hairline gap left between parts.
        self.assertAlmostEqual(total, union.area, places=6)
        # And no hairline gaps: footprint() is the hole-filled hull of the lot,
        # so it exceeding the union would mean bare plate showing between parts.
        self.assertAlmostEqual(footprint(regions).area, union.area, delta=0.05)
        for slot, geom in regions.items():
            self.assertTrue(extrude(geom, 0.6).is_volume, f'slot {slot}')

    def test_priority_decides_who_keeps_a_shared_edge(self):
        mapping = {'#000000': 1, '#FFFFFF': 3, '#DE4343': 4}
        first = separate(self.art(), mapping, width_mm=40, priority=[4, 1, 3])
        last = separate(self.art(), mapping, width_mm=40, priority=[1, 3, 4])
        # The square is the detail, wholly inside the black bar. Going first it
        # keeps the grown seam all round; going last the bar has taken it.
        self.assertGreater(first[4].area, last[4].area)
        # Either way the plate is still exactly covered -- priority moves the
        # boundary, it never opens a gap or double-prints.
        for regions in (first, last):
            self.assertAlmostEqual(sum(g.area for g in regions.values()),
                                   footprint(regions).area, delta=0.05)

    def test_unlisted_colors_join_their_nearest_listed_neighbour(self):
        image = Image.new('RGBA', (10, 10), (250, 250, 250, 255))   # not #FFFFFF
        labels = label_map(image, {'#000000': 1, '#FFFFFF': 3})
        self.assertTrue((labels == 3).all())

    def test_transparent_pixels_belong_to_no_slot(self):
        image = Image.new('RGBA', (10, 10), (0, 0, 0, 0))
        self.assertTrue((label_map(image, {'#000000': 1}) == -1).all())
        self.assertEqual(separate(image, {'#000000': 1}, width_mm=10), {})

    def test_priority_must_cover_the_mapping(self):
        with self.assertRaises(ValueError):
            separate(self.art(), {'#000000': 1, '#FFFFFF': 3}, width_mm=40, priority=[1])

    def test_recoloring_a_slot_leaves_every_other_key_alone(self):
        before = project_settings(PROFILE)
        after = project_settings(PROFILE, colors={2: '#8E9089'})
        self.assertEqual(after['filament_colour'][1], '#8E9089')
        self.assertEqual({k: v for k, v in after.items() if k != 'filament_colour'},
                         {k: v for k, v in before.items() if k != 'filament_colour'})
        with self.assertRaises(ValueError):
            project_settings(PROFILE, colors={9: '#8E9089'})


class CrossCheckTests(unittest.TestCase):
    """Part-to-part fit -- the thing per-part validity cannot see.

    Every one of these files passes every other check: watertight solids, legal
    slots, on the bed, on the layer grid. They are still wrong.
    """

    def setUp(self):
        self.cfg = project_settings(PROFILE)

    def plate(self, regions, outline):
        parts = [Part(extrude(outline, 3.0), 'plate', 2)]
        parts += [Part(extrude(g, .6, z=3.0), f'slot{s}', s) for s, g in regions.items()]
        output = io.BytesIO()
        write_3mf(parts, output, settings=self.cfg)
        output.seek(0)
        return inspect_3mf(output)

    def test_overlapping_parts_are_rejected(self):
        a, b = sbox(0, 0, 20, 20), sbox(8, 0, 28, 20)
        report = self.plate({1: a, 3: b}, sbox(-5, -5, 33, 25))
        self.assertFalse(report['valid'])
        self.assertIn('overlap', report['errors'][0])
        self.assertAlmostEqual(report['bands'][0]['overlap_mm2'], 12 * 20, delta=.5)

    def test_a_hairline_between_parts_is_rejected(self):
        # 0.1 mm apart: both solids valid, nothing else notices, and it prints
        # with a line of bare plate down the middle. Open at both ends, so it
        # is not a hole in the union -- the case a hole hunt would miss.
        gap = self.plate({1: sbox(0, 0, 20, 20), 3: sbox(20.1, 0, 40, 20)},
                         sbox(-5, -5, 45, 25))
        self.assertFalse(gap['valid'])
        self.assertIn('seam gap', gap['errors'][0])
        self.assertAlmostEqual(gap['bands'][0]['seam_gap_mm2'], 0.1 * 20, delta=.1)
        shared = self.plate({1: sbox(0, 0, 20, 20), 3: sbox(20, 0, 40, 20)},
                            sbox(-5, -5, 45, 25))
        self.assertTrue(shared['valid'], shared['errors'])

    def test_a_printable_hole_survives_the_closing(self):
        # Closing is what tells a seam from a hole somebody meant, so a hole
        # wide enough to print must come through it -- otherwise every
        # keychain trips the check.
        report = self.plate({1: sbox(-20, -20, 20, 20).difference(sbox(-6, -6, 6, 6)),
                             3: sbox(20, -20, 30, 20)}, sbox(-25, -25, 35, 25))
        self.assertTrue(report['valid'], report['errors'])
        self.assertEqual(report['bands'][0]['seam_gaps'], 0)

    def test_parts_in_different_layer_bands_are_not_compared(self):
        # A plate and the artwork stacked on it overlap in XY by design.
        report = self.plate({1: sbox(0, 0, 20, 20)}, sbox(-5, -5, 25, 25))
        self.assertTrue(report['valid'], report['errors'])
        self.assertEqual(report['bands'], [])

    def test_cross_check_needs_two_parts_to_say_anything(self):
        self.assertEqual(cross_check([]), [])


class BlurScalingTests(unittest.TestCase):
    def test_blur_floor_holds_and_the_fraction_takes_over_when_larger(self):
        from x2d.geometry import BLUR_FRAC, BLUR_MIN_PX
        pick = lambda w: max(BLUR_MIN_PX, int(round(BLUR_FRAC * w)))
        self.assertEqual(pick(300), BLUR_MIN_PX)      # small: floor, as before
        self.assertEqual(pick(1899), BLUR_MIN_PX)     # a 1899 px photo: unchanged
        self.assertGreater(pick(8000), BLUR_MIN_PX)   # large: scales up


class InkTests(unittest.TestCase):
    """Pulling a drawing's linework out of a shaded illustration."""

    def drawing(self):
        # A dark stroke that fades to mid-grey at one end, plus a detached
        # mid-grey smudge. A single threshold cannot tell them apart.
        a = np.full((120, 200), 250, np.uint8)
        a[58:62, 20:100] = 40            # the stroke's dark core
        a[58:62, 100:160] = 160          # its fade, connected to the core
        a[20:24, 20:60] = 160            # a smudge, connected to nothing dark
        return Image.fromarray(a).convert('RGBA')

    def test_hysteresis_follows_a_stroke_and_leaves_an_unseeded_smudge(self):
        mask = ink_mask(self.drawing(), strong=110, weak=200) > 0
        self.assertTrue(mask[60, 50])    # core
        self.assertTrue(mask[60, 140])   # fade: same stroke, so it is kept
        self.assertFalse(mask[22, 40])   # smudge: no seed, so it is not
        # A plain threshold at the same level cannot make that distinction,
        # which is the whole reason this is not a threshold.
        self.assertTrue(np.asarray(self.drawing().convert('L'))[22, 40] < 200)

    def test_thickening_lifts_a_sub_nozzle_stroke_over_the_minimum(self):
        # 4 px of stroke across 200 px at 40 mm is 0.8 mm... of image, but the
        # measure that matters is what separate() then hands the nozzle.
        art = ink_art(self.drawing(), width_mm=40, strong=110, weak=200)
        thin = ink_art(self.drawing(), width_mm=40, strong=110, weak=200, thicken=False)
        rows = lambda im: (np.asarray(im)[:, 50, 3] > 0).sum()
        self.assertGreater(rows(art), rows(thin))
        grown = (rows(art) - rows(thin)) / 2 * 40 / 200
        self.assertAlmostEqual(grown, X2D.min_feature / 2, delta=0.15)

    def test_everything_that_is_not_ink_is_transparent_not_white(self):
        # Paper has to be the plate showing through. A part printed in white
        # would cost a filament change and a layer band for 90% of a drawing.
        art = np.asarray(ink_art(self.drawing(), width_mm=40))
        self.assertEqual(art[5, 5, 3], 0)
        self.assertGreater(art[60, 50, 3], 0)

    def test_line_width_is_a_physical_measure_of_the_print(self):
        # It is a property of the raster read at a print size, so it has to
        # scale with the print size and not with the file. Both halves matter:
        # the reference drawing measures 0.182 mm at 72 mm, which is the number
        # that says the linework needs thickening at all.
        narrow = line_width_mm(self.drawing(), width_mm=40)
        self.assertAlmostEqual(line_width_mm(self.drawing(), width_mm=80), 2 * narrow, places=5)
        self.assertLess(narrow, X2D.min_feature)

    def test_bad_thresholds_are_rejected(self):
        for kw in ({'strong': 0}, {'strong': 200, 'weak': 100}, {'weak': 300}):
            with self.assertRaises(ValueError):
                ink_mask(self.drawing(), **kw)


if __name__ == '__main__':
    unittest.main()


class ReportTests(unittest.TestCase):
    """The filament facts, which come from the capture and not from constants."""

    def setUp(self):
        from x2d.report import filaments
        self.spools = filaments(project_settings(PROFILE))

    def test_slots_are_one_based_like_the_filament_panel(self):
        # Every per-slot list in the capture is 0-based; Bambu Studio numbers
        # them from 1. The conversion happens in exactly one place now.
        self.assertEqual(min(self.spools), 1)
        self.assertEqual(self.spools[1].colour, '#000000')
        self.assertEqual(self.spools[4].colour, '#DE4343')

    def test_density_is_read_per_slot_not_assumed(self):
        # The matte in slot 4 is denser than the basic PLA in 1-3. An older
        # viewer assumed a single 1.24 for everything and was wrong for all
        # four. Anything that hardcodes one number here is that bug again.
        self.assertEqual({s.density for s in list(self.spools.values())[:3]}, {1.26})
        self.assertEqual(self.spools[4].density, 1.32)
        self.assertNotEqual(self.spools[4].density, self.spools[1].density)

    def test_mass_is_an_upper_bound_and_scales_with_density(self):
        from x2d.report import mass_g
        # 1 cm3 of the matte weighs more than 1 cm3 of the basic.
        self.assertAlmostEqual(mass_g(1000, self.spools[1]), 1.26, places=6)
        self.assertGreater(mass_g(1000, self.spools[4]), mass_g(1000, self.spools[1]))

    def test_the_relief_band_decides_the_change_count_not_the_plate(self):
        from x2d.report import changes
        box = sbox(0, 0, 10, 10)
        # One colour above the plate is one swap for the whole print.
        self.assertEqual(changes({4: box}, 1, relief=1.0), 1)
        # Same slot as the plate is no swap at all.
        self.assertEqual(changes({1: box}, 1, relief=1.0), 0)
        # Two colours share the band, so they cost a swap each per layer.
        self.assertEqual(changes({3: box, 4: box}, 1, relief=1.0), 10)
        self.assertEqual(changes({}, 1, relief=1.0), 0)
