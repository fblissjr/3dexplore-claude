"""Projects someone else made: paint codec, archive round trip, repair,
re-slotting and the overhang report, each on a synthetic fixture.

The fixture is built the way the real case looked (a Bambu Studio download:
painted triangles, one part broken on its flat base) without shipping anyone's
model: a write_3mf project, then painted and broken through x2d/archive.py.
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from x2d import Part, write_3mf
from x2d.archive import Mesh, parts, read_meshes, replace_mesh, rewrite, world_parts
from x2d.machine import machine_for
from x2d.overhang import overhang_report
from x2d.paint import leaves, remap, solid, states
from x2d.repair import _cdt, bad_faces, defects, fill, repair
from x2d.reslot import reslot_3mf, reslot_settings

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import capture  # noqa: E402
from test_scripts import run_cli  # noqa: E402

MODEL = "3D/3dmodel.model"


def settings_with_structure():
    """The stand-in capture plus the per-filament structure a GUI save has,
    with every value distinct so a wrong permutation cannot pass by luck."""
    n = 4
    return capture({
        "filament_colour": ["#000000", "#FEC600", "#FFFFFF", "#6F5034"],
        "nozzle_temperature": [str(200 + 10 * f + v) for f in range(n) for v in range(6)],
        "filament_self_index": [str(f + 1) for f in range(n) for _ in range(6)],
        "flush_volumes_matrix": [str(100 * b + 10 * i + j) for b in range(2) for i in range(n) for j in range(n)],
        "flush_volumes_vector": [str(10 * f + k) for f in range(n) for k in range(2)],
        "different_settings_to_system": ["process", "f1", "f2", "f3", "f4", "machine"],
        "wall_filament": "2",
        "support_filament": "0",
        "mystery_per_something": ["a", "b", "c", "d"],
    })


class Fixture:
    """A two-part project: a subdivided box on slot 2 and a cylinder on slot 1,
    with paint on the box, optionally broken on the box's base."""

    def __init__(self, tmp, broken=False):
        box = trimesh.creation.box((20, 20, 20)).subdivide().subdivide().subdivide()
        cyl = trimesh.creation.cylinder(radius=4, height=10)
        cyl.apply_translation([25, 0, -5])
        self.settings = settings_with_structure()
        self.path = Path(tmp) / ("broken.3mf" if broken else "painted.3mf")
        clean = Path(tmp) / "clean.3mf"
        write_3mf([Part(box, "box", 2), Part(cyl, "cyl", 1)], clean, settings=self.settings)
        with zipfile.ZipFile(clean) as zf:
            text = zf.read(MODEL).decode()
            m = read_meshes(zf)[(MODEL, 1)]
        attrs = list(m.attrs)
        top = np.flatnonzero(trimesh.Trimesh(m.V, m.F, process=False).face_normals[:, 2] > 0.9)
        # whole triangles on slots 1, 3 and 4, and one subdivided triangle
        # mixing slot 2 with the part's own (state 0)
        for k, code in zip(top[:4], ["4", "0C", "1C", "80883"]):
            attrs[k] = f' paint_color="{code}"'
        F = m.F
        if broken:
            # damage in the middle of the base, clear of its rim, as on the
            # real part; a hole that wraps a sharp edge comes back chamfered
            lo, hi = m.V[:, :2].min(axis=0), m.V[:, :2].max(axis=0)
            inner = ((m.V[F][:, :, :2] > lo + 1) & (m.V[F][:, :, :2] < hi - 1)).all(axis=(1, 2))
            down = trimesh.Trimesh(m.V, F, process=False).face_normals[:, 2] < -0.9
            bottom = np.flatnonzero(down & inner)
            gone = bottom[:2]
            F = np.vstack([np.delete(F, gone, axis=0), F[bottom[5]][::-1]])     # hole + a reversed twin
            attrs = [a for i, a in enumerate(attrs) if i not in set(gone)] + [""]
        self.mesh = Mesh(m.V, F, attrs)
        rewrite(clean, self.path, {MODEL: replace_mesh(text, 1, self.mesh).encode()})


class PaintCodeTests(unittest.TestCase):
    def test_whole_triangle_codes(self):
        # the codes Bambu Studio writes for a triangle painted in one slot
        self.assertEqual([solid(k) for k in range(1, 6)], ["4", "8", "0C", "1C", "2C"])
        self.assertEqual([states(solid(k)) for k in range(1, 6)], [[1], [2], [3], [4], [5]])

    def test_subdivided_triangle(self):
        self.assertEqual(states("80883"), [2, 2, 0, 2])
        self.assertEqual(states("808006088A6800200A2002002006"),
                         [0] * 10 + [2, 2, 2, 0, 0, 0, 2, 0, 2])

    def test_remap_crossing_three_changes_width_and_comes_back(self):
        wide = remap("80883", {2: 4})
        self.assertEqual(states(wide), [4, 4, 0, 4])
        self.assertEqual(remap(wide, {4: 2}), "80883")

    def test_a_leading_zero_is_data(self):
        self.assertEqual(remap("4", {1: 3}), "0C")      # not "C"
        self.assertEqual(remap("0C", {3: 1}), "4")

    def test_trailing_data_is_refused(self):
        with self.assertRaises(ValueError):
            leaves("18")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.fx = Fixture(self._dir.name)

    def test_replacing_a_mesh_with_itself_changes_nothing(self):
        with zipfile.ZipFile(self.fx.path) as zf:
            text = zf.read(MODEL).decode()
            m = read_meshes(zf)[(MODEL, 1)]
        again = replace_mesh(text, 1, m)
        with zipfile.ZipFile(self.fx.path) as zf:
            self.assertEqual(read_meshes(zf)[(MODEL, 1)].attrs, m.attrs)
        # our writer uses 4 decimals, not 9 significant digits, so compare values
        m2 = read_meshes_from_text(again)[1]
        np.testing.assert_allclose(m2.V, m.V, atol=1e-9)
        self.assertEqual(m2.attrs, m.attrs)

    def test_world_parts_and_slots(self):
        with zipfile.ZipFile(self.fx.path) as zf:
            wp = world_parts(zf)
            meta = parts(zf)
        self.assertEqual([(n, s) for n, s, _ in wp], [("box", 2), ("cyl", 1)])
        self.assertAlmostEqual(min(m.bounds[0, 2] for _, _, m in wp), 0.0, places=3)
        self.assertEqual({m["name"] for m in meta.values()}, {"box", "cyl"})


def read_meshes_from_text(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr(MODEL, text)
        with zipfile.ZipFile(p) as zf:
            return {k[1]: v for k, v in read_meshes(zf).items()}


class RepairTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.fx = Fixture(self._dir.name, broken=True)

    def test_defects_are_counted_like_bambu(self):
        d = defects(self.fx.mesh.F)
        self.assertGreater(d["open_edges"], 0)
        self.assertGreater(d["nonmanifold_edges"], 0)

    def test_repair_is_local_and_closes_the_mesh(self):
        m = self.fx.mesh
        r = repair(m.V, m.F)
        out = trimesh.Trimesh(r.V, r.F, process=False)
        self.assertEqual(defects(r.F)["open_edges"] + defects(r.F)["nonmanifold_edges"], 0)
        self.assertTrue(out.is_watertight and out.is_winding_consistent)
        self.assertAlmostEqual(out.volume, 8000.0, delta=1e-3)
        # every kept triangle is the same triangle, and the paint is all kept
        kept = r.origin >= 0
        np.testing.assert_allclose(r.V[r.F[kept]], m.V[m.F[r.origin[kept]]])
        self.assertLess(r.removed, len(m.F) // 4)
        painted = {i for i, a in enumerate(m.attrs) if "paint_color" in a}
        self.assertTrue(painted <= set(r.origin[kept]))

    def test_patch_on_a_flat_base_is_flat(self):
        m = self.fx.mesh
        r = repair(m.V, m.F)
        # damage clear of the rim is refilled on the base itself
        patch = np.unique(r.F[r.origin < 0])
        self.assertLess(np.abs(r.V[patch, 2] - m.V[:, 2].min()).max(), 1e-6)

    def test_patch_follows_a_curved_surface(self):
        # a hole round the bottom of a sphere: the sagitta is 0.63 mm, so a
        # patch that ignored the cut-out surface would miss by that much
        s = trimesh.creation.icosphere(subdivisions=5, radius=20)
        cut = s.triangles_center[:, 2] < -19.35
        V, F = s.vertices, s.faces
        from x2d.repair import boundary_loops
        loop = boundary_loops(F[~cut])[0]
        pts, tris = fill(V, loop, trimesh.Trimesh(V, F[cut], process=False))
        allV = np.vstack([V, pts])
        r = np.linalg.norm(allV[np.unique(tris)], axis=1)
        self.assertLess(np.abs(r - 20).max(), 0.02)
        closed = trimesh.Trimesh(allV, np.vstack([F[~cut], tris]), process=False)
        self.assertTrue(closed.is_watertight and closed.is_winding_consistent)
        self.assertFalse(bad_faces(closed.faces).any())

    def test_interior_is_woven_in_by_flipping(self):
        # a 10 x 1 outline with only its corners, and a staggered grid inside.
        # Inserting points without the Delaunay flips leaves needles between
        # grid rows; with them, triangles among grid points are near-equilateral.
        uv = np.array([[0, 0], [10, 0], [10, 1], [0, 1]], float)
        step = 0.2
        rows = np.arange(0.2, 0.85, step * np.sqrt(3) / 2)
        grid = np.array([(x + (step / 2 if i % 2 else 0), y) for i, y in enumerate(rows)
                         for x in np.arange(0.2, 9.7, step)])
        T, P = _cdt(np.vstack([uv, grid]), 4, [[0, 1, 2], [0, 2, 3]])
        edges = {frozenset(e) for t in T for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}
        self.assertTrue(all(frozenset((i, (i + 1) % 4)) in edges for i in range(4)))
        self.assertEqual(len(T), 4 + 2 * len(grid) - 2)
        inner = T[(T >= 4).all(axis=1)]
        a, b, c = (np.linalg.norm(P[inner[:, i]] - P[inner[:, j]], axis=1) for i, j in ((0, 1), (1, 2), (2, 0)))
        self.assertLess(np.maximum(np.maximum(a, b), c).max(), 2 * step)

    def test_clean_mesh_is_untouched(self):
        box = trimesh.creation.box((10, 10, 10))
        r = repair(box.vertices, box.faces)
        self.assertEqual(r.removed, 0)
        np.testing.assert_array_equal(r.F, box.faces)


class ReslotTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def test_settings_move_as_blocks(self):
        ps = settings_with_structure()
        order = [4, 1, 3, 2]
        new, report = reslot_settings(ps, order)
        idx = [k - 1 for k in order]
        self.assertEqual(new["filament_colour"], [ps["filament_colour"][i] for i in idx])
        self.assertEqual(new["nozzle_temperature"],
                         [x for i in idx for x in ps["nozzle_temperature"][6 * i:6 * i + 6]])
        self.assertEqual(new["flush_volumes_vector"],
                         [x for i in idx for x in ps["flush_volumes_vector"][2 * i:2 * i + 2]])
        M = ps["flush_volumes_matrix"]
        self.assertEqual(new["flush_volumes_matrix"][:16], [M[i * 4 + j] for i in idx for j in idx])
        self.assertEqual(new["flush_volumes_matrix"][16:], [M[16 + i * 4 + j] for i in idx for j in idx])
        self.assertEqual(new["filament_self_index"], ps["filament_self_index"])
        self.assertEqual(new["different_settings_to_system"],
                         ["process", "f4", "f1", "f3", "f2", "machine"])
        self.assertEqual(new["wall_filament"], "4")          # old 2 is new slot 4
        self.assertEqual(new["support_filament"], "0")
        self.assertEqual(new["mystery_per_something"], ps["mystery_per_something"])
        # four corners, not four filaments: same length, left alone and said so
        self.assertEqual(new["printable_area"], ps["printable_area"])
        self.assertEqual(report["left_as_is"], ["mystery_per_something", "printable_area"])

    def test_model_and_paint_follow(self):
        fx = Fixture(self.tmp)
        out = self.tmp / "ams.3mf"
        order, mapping = [2, 4, 1, 3], {2: 1, 4: 2, 1: 3, 3: 4}
        reslot_3mf(fx.path, out, order)
        with zipfile.ZipFile(fx.path) as a, zipfile.ZipFile(out) as b:
            ma, mb = read_meshes(a)[(MODEL, 1)], read_meshes(b)[(MODEL, 1)]
            pa, pb = parts(a), parts(b)
            sa = json.loads(a.read("Metadata/project_settings.config"))
            sb = json.loads(b.read("Metadata/project_settings.config"))
        for key in pa:
            self.assertEqual(int(pb[key]["extruder"]), mapping[int(pa[key]["extruder"])])
        for i in range(len(ma.F)):
            ca, cb = ma.paint(i), mb.paint(i)
            self.assertEqual(ca is None, cb is None)
            if ca:
                self.assertEqual([mapping.get(s, s) for s in states(ca)], states(cb))
        self.assertEqual(sb["filament_colour"], [sa["filament_colour"][k - 1] for k in order])

    def test_a_used_filament_must_land_somewhere(self):
        fx = Fixture(self.tmp)
        with self.assertRaises(ValueError):
            reslot_3mf(fx.path, self.tmp / "x.3mf", [1, 2, 3])        # the box paints slot 4
        report = reslot_3mf(fx.path, self.tmp / "x.3mf", [1, 2, 3], {4: 1})
        self.assertEqual(report["filaments_after"], 3)


class OverhangTests(unittest.TestCase):
    def test_bridge_cantilever_and_floating(self):
        machine = machine_for(capture())
        pillar = lambda x: trimesh.creation.box((4, 4, 10), trimesh.transformations.translation_matrix([x, 0, 5]))
        slab = trimesh.creation.box((24, 4, 2), trimesh.transformations.translation_matrix([0, 0, 11]))
        stem = trimesh.creation.box((4, 4, 10), trimesh.transformations.translation_matrix([40, 0, 5]))
        cap = trimesh.creation.box((4, 12, 2), trimesh.transformations.translation_matrix([40, 0, 11]))
        block = trimesh.creation.box((4, 4, 4), trimesh.transformations.translation_matrix([60, 0, 8]))
        parts = [("left", pillar(-10)), ("right", pillar(10)), ("slab", slab),
                 ("stem", stem), ("cap", cap), ("float", block)]
        r = overhang_report(parts, machine, 30.0)
        by = r["by_part"]
        self.assertGreater(by["slab"]["bridge_layers"], 0)
        self.assertEqual(by["slab"]["cantilever_layers"], 0)
        self.assertAlmostEqual(by["slab"]["max_reach_mm"], 8.0, delta=0.3)    # 16 mm gap, held both ends
        self.assertGreater(by["cap"]["cantilever_layers"], 0)
        self.assertAlmostEqual(by["cap"]["max_reach_mm"], 4.0, delta=0.3)     # 4 mm ledge each side
        self.assertEqual([f["part"] for f in r["floating"]], ["float"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def test_repair_cli(self):
        fx = Fixture(self.tmp, broken=True)
        out = self.tmp / "fixed.3mf"
        report = run_cli("repair_3mf.py", [fx.path, "--out", out])
        [fixed] = report["repaired"]
        self.assertEqual(fixed["part"], "box")
        self.assertEqual(fixed["after"]["open_edges"] + fixed["after"]["nonmanifold_edges"], 0)
        self.assertEqual(fixed["painted_triangles_cut"], 0)
        with zipfile.ZipFile(fx.path) as a, zipfile.ZipFile(out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            ma, mb = read_meshes(a)[(MODEL, 1)], read_meshes(b)[(MODEL, 1)]
            self.assertEqual(read_meshes(a)[(MODEL, 2)].attrs, read_meshes(b)[(MODEL, 2)].attrs)
        self.assertEqual(sorted(filter(None, (ma.paint(i) for i in range(len(ma.F))))),
                         sorted(filter(None, (mb.paint(i) for i in range(len(mb.F))))))
        again = run_cli("repair_3mf.py", [out, "--out", self.tmp / "again.3mf"])
        self.assertEqual(again["repaired"], [])
        self.assertFalse((self.tmp / "again.3mf").exists())

    def test_reslot_cli(self):
        fx = Fixture(self.tmp)
        out = self.tmp / "ams.3mf"
        report = run_cli("reslot_3mf.py", [fx.path, "--order", 4, 1, 3, 2, "--out", out])
        self.assertEqual(report["mapping"], {"1": 2, "2": 4, "3": 3, "4": 1})
        self.assertEqual(report["colours_after"], ["#6F5034", "#000000", "#FFFFFF", "#FEC600"])
        with zipfile.ZipFile(out) as zf:
            self.assertEqual([(n, s) for n, s, _ in world_parts(zf)], [("box", 4), ("cyl", 2)])

    def test_overhangs_cli(self):
        fx = Fixture(self.tmp)
        report = run_cli("overhangs.py", [fx.path, "--top", 3])
        self.assertEqual(report["support_threshold_angle"], 30.0)
        self.assertEqual(report["floating_islands"], 0)
        self.assertIn("worst_bridge_reach_mm", report)


if __name__ == "__main__":
    unittest.main()
