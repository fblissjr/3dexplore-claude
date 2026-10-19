"""Wall mounts (`x2d/wallmount.py`, `scripts/wallmount_3mf.py`) and pinning a
downloaded project's nozzles (`archive.pin_nozzles`).

The fixture is shaped like the case that taught this, a desk stand printed on
its side, without shipping anyone's model: a plate, a ring standing on it, a
desk foot along the bottom edge, and a lip high above the plate that leaves
the plate clear near the bed but blocks a screwdriver from the room.
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh
from shapely import Point, box

from x2d import Part, write_3mf
from x2d.archive import pin_nozzles, read_meshes, world_parts
from x2d.wallmount import (Frame, Screw, bed_face, build, footprint_above, plate_top,
                           screw_spots, strip_fit)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import capture  # noqa: E402
from test_scripts import run_cli  # noqa: E402

# ring top is at 33, so the footprint is centred on the origin, which is
# where Frame.for_mesh puts the wall frame
FOOT = box(-30, -33, 30, -29)            # wall frame with up = +y and origin at the centre
LIP = box(-30, -27, -10, 4)


def cube(x0, y0, z0, x1, y1, z1):
    return mf.Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def stand():
    ring = (mf.Manifold.cylinder(30, 18, 18, 96) - mf.Manifold.cylinder(32, 11, 11, 96).translate([0, 0, -1])).translate([0, 15, 0])
    # the plate is the ring's own back plus a band below it, like the real
    # stand: no free corners beside the ring
    back = mf.Manifold.cylinder(3, 18, 18, 96).translate([0, 15, 0])
    s = (cube(-30, -33, 0, 30, 0, 3) + back + ring + cube(-30, -33, 0, 30, -29, 30)
         + cube(-30, 0, 0, -26, 4, 30) + cube(-30, -27, 26, -10, 4, 30))     # pillar + lip
    m = s.to_mesh()
    return trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=False)


TRIM = [[(-40, -50), (40, -50), (40, -28), (-40, -28)]]


class FrameTests(unittest.TestCase):
    def test_round_trip_and_handedness(self):
        f = Frame(135, (128.0, 128.0))
        p = np.array([[140.0, 120.0], [100.0, 150.0]])
        np.testing.assert_allclose(f.to_bed(f.to_wall(p)), p)
        # seen from the room, X is up turned clockwise: up = +y gives X = +x
        np.testing.assert_allclose(Frame(90).to_wall([[1.0, 0.0], [0.0, 1.0]]), [[1, 0], [0, 1]], atol=1e-12)

    def test_plate_is_the_lowest_real_flat(self):
        self.assertAlmostEqual(plate_top(stand()), 3.0)


class SpotTests(unittest.TestCase):
    def setUp(self):
        self.mesh = stand()
        self.frame = Frame.for_mesh(self.mesh, 90)
        cut, _ = build(self.mesh, self.frame, trim=TRIM, holes=[])
        self.spots = screw_spots(cut, self.frame)

    def test_level_and_on_the_plate(self):
        (_, ua), (_, ub) = self.spots
        self.assertEqual(ua, ub)
        face = bed_face(self.mesh).buffer(-(Screw.boss_r + 0.5) + 1e-6)
        for X, U in self.spots:
            self.assertTrue(face.contains(Point(X, U)))

    def test_a_lip_high_above_still_blocks_the_screwdriver(self):
        # the lip sits 23 mm above the plate: a check that looks only near the
        # plate puts a screw under it, where no driver can reach
        for X, U in self.spots:
            self.assertGreaterEqual(LIP.distance(Point(X, U)), Screw.boss_r + 1.0 - 1e-6)

    def test_widest_level_pair(self):
        (a, _), (b, _) = sorted(self.spots)
        # right of the lip's clearance, left of the plate's edge clearance
        self.assertAlmostEqual(a, -10 + 7, delta=0.3)
        self.assertAlmostEqual(b, 30 - 6.5, delta=0.3)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.mesh = stand()
        self.frame = Frame.for_mesh(self.mesh, 90)

    def test_trim_takes_the_foot_and_leaves_the_plate(self):
        out, rep = build(self.mesh, self.frame, trim=TRIM, holes=[])
        foot_above_plate = 60 * 4 * 27
        self.assertAlmostEqual(rep["volume_mm3"][0] - rep["volume_mm3"][1], foot_above_plate, delta=1)
        self.assertFalse(footprint_above(out, 3.0).intersection(FOOT).area > 1e-6)
        self.assertAlmostEqual(bed_face(out).area, bed_face(self.mesh).area, delta=1)
        self.assertTrue(out.is_watertight)

    def test_holes_go_through_and_are_reachable(self):
        out, rep = build(self.mesh, self.frame, trim=TRIM)
        self.assertEqual(rep["through_holes_added"], 2)
        self.assertTrue(out.is_watertight)
        for X, U in rep["holes_wall_mm"]:
            x, y = self.frame.to_bed([X, U])
            hits, _, _ = out.ray.intersects_location([[x, y, 50]], [[0, 0, -1]])
            self.assertEqual(len(hits), 0, "a vertical line through the hole should touch nothing")
            # the head is seated: a ring at the countersink's rim meets the boss top
            rim, _, _ = out.ray.intersects_location([[x + Screw.head_d / 2 + 0.5, y, 50]], [[0, 0, -1]])
            self.assertAlmostEqual(rim[:, 2].max(), 3.0 + Screw.boss_h, places=3)

    def test_mirror_moves_the_lip_to_the_other_side(self):
        out, rep = build(self.mesh, self.frame, trim=TRIM, mirror=True)
        self.assertGreater(out.volume, 0)                  # a reflection turns a solid inside out unless the faces flip too
        above = self.frame.polygon(footprint_above(out, 20.0))       # over the bosses, at the lip
        self.assertGreater(above.intersection(box(10, -27, 30, 4)).area, 100)
        self.assertLess(above.intersection(box(-30, -27, -10, 0)).area, 1e-6)
        np.testing.assert_allclose(sorted(X for X, _ in rep["holes_wall_mm"]), [-23.5, 3.0], atol=0.3)

    def test_paint_is_refused(self):
        with self.assertRaises(ValueError):
            build(self.mesh, self.frame, trim=TRIM, painted=3)


class StripTests(unittest.TestCase):
    def test_measures_what_fits_not_what_looks_big(self):
        tile = trimesh.creation.box((50, 20, 3))
        fits = strip_fit(tile, Frame.for_mesh(tile, 90), widths=(16, 19))
        self.assertAlmostEqual(fits[16][0], 48, delta=0.6)      # 1 mm off each edge
        self.assertEqual(fits[19][0], 0.0)                       # 18 mm of face, 19 mm strip


GUI_PROJECT = """{
    "filament_colour": [
        "#FFFFFF",
        "#000000"
    ],
    "filament_map": [
        "2",
        "1"
    ],
    "filament_map_mode": "Auto For Flush",
    "layer_height": "0.2"
}"""
GUI_MODEL = """<config>
  <plate>
    <metadata key="filament_map_mode" value="Auto For Flush"/>
    <metadata key="filament_maps" value="2 1"/>
  </plate>
</config>"""


class PinTests(unittest.TestCase):
    def test_both_places_and_nothing_else(self):
        p, m, done = pin_nozzles(GUI_PROJECT, GUI_MODEL, 1)
        self.assertEqual(done, ["project_settings.config", "model_settings.config"])
        self.assertEqual(json.loads(p)["filament_map"], ["1", "1"])
        self.assertEqual(json.loads(p)["filament_map_mode"], "Manual")
        self.assertIn('value="1 1"', m)
        self.assertIn('key="filament_map_mode" value="Manual"', m)
        changed = [a for a, b in zip(GUI_PROJECT.splitlines(), p.splitlines()) if a != b]
        self.assertEqual(changed, ['        "2",', '    "filament_map_mode": "Auto For Flush",'])

    def test_one_slot_by_dict(self):
        p, m, _ = pin_nozzles(GUI_PROJECT, GUI_MODEL, {2: 2})
        self.assertEqual(json.loads(p)["filament_map"], ["2", "2"])
        self.assertIn('value="2 2"', m)

    def test_single_nozzle_file_is_left_alone(self):
        p, m, done = pin_nozzles('{"layer_height": "0.2"}', "<config/>", 1)
        self.assertEqual((p, m, done), ('{"layer_height": "0.2"}', "<config/>", []))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "stand.3mf"
        write_3mf([Part(stand(), "stand", 1)], self.src, settings=capture())

    def tearDown(self):
        self.tmp.cleanup()

    def test_survey_then_build(self):
        out = self.dir / "stand_wall.3mf"
        rep = run_cli("wallmount_3mf.py", [self.src, "--survey", "--up", "90", "--out", out])
        self.assertEqual(rep["plate_top_mm"], 3.0)
        self.assertTrue(Path(rep["survey_png"]).exists())
        self.assertFalse(out.exists())

        rep = run_cli("wallmount_3mf.py", [self.src, "--up", "90", "--trim", "-40,-50 40,-50 40,-28 -40,-28",
                                            "--out", out])
        self.assertEqual(rep["through_holes_added"], 2)
        self.assertTrue(rep["watertight"])
        self.assertEqual(rep["pinned_to_main_nozzle"], ["project_settings.config", "model_settings.config"])
        self.assertEqual(rep["overhangs"]["before"]["floating_islands"], rep["overhangs"]["after"]["floating_islands"])
        self.assertTrue(Path(rep["front_view_png"]).exists())
        with zipfile.ZipFile(out) as zf:
            (_, _, mesh), = world_parts(zf)
            self.assertTrue(mesh.is_watertight)
            self.assertAlmostEqual(mesh.bounds[0, 2], 0.0, places=4)       # still on the bed
            settings = json.loads(zf.read("Metadata/project_settings.config"))
            self.assertEqual(settings["filament_map_mode"], "Manual")
            self.assertIn('value="Manual"', zf.read("Metadata/model_settings.config").decode())
            names = zf.namelist()
        with zipfile.ZipFile(self.src) as zf:
            self.assertEqual(zf.namelist(), names)

    def test_refuses_to_overwrite_its_input(self):
        with self.assertRaises(SystemExit):
            run_cli("wallmount_3mf.py", [self.src, "--out", self.src])


if __name__ == "__main__":
    unittest.main()
