"""Parser tests - no CAD kernel needed, so they run on any machine / CI."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stencilgen import gerber

FIXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name):
    return os.path.join(FIXT, name)


def area_of(ring):
    return abs(gerber.area(ring))


class TestLayerDetection(unittest.TestCase):
    def test_file_function(self):
        self.assertEqual(gerber.file_function(fx("profile.ger")), "profile,np")
        self.assertEqual(gerber.file_function(fx("paste_top.ger")), "paste,top")
        self.assertIsNone(gerber.file_function(fx("nosuch.txt")))


class TestParse(unittest.TestCase):
    def test_top_paste(self):
        lay = gerber.parse(fx("paste_top.ger"))
        self.assertEqual(len(lay.flashes), 5)
        self.assertIn("RotRect", lay.macros)
        self.assertIn(10, lay.apertures)

    def test_groups_and_shapes(self):
        lay = gerber.parse(fx("paste_top.ger"))
        groups = gerber.flash_groups(lay)
        self.assertEqual(len(groups), 5)
        # one ring per simple aperture, one group for the macro pad
        areas = sorted(area_of(g[0]) for g in groups)
        self.assertAlmostEqual(areas[0], 0.1958, places=2)      # 0.5 mm circle
        self.assertAlmostEqual(areas[1], 0.6000, places=2)      # 1.0 x 0.6 rect
        self.assertAlmostEqual(max(areas), 1.2000, places=2)    # macro: 1.5 x 0.8

    def test_obround_is_not_a_rectangle(self):
        lay = gerber.parse(fx("paste_top.ger"))
        groups = gerber.flash_groups(lay)
        obround = [g for g in groups if abs(area_of(g[0]) - 0.6419) < 1e-3]
        self.assertEqual(len(obround), 1, "0.6x1.2 obround area should be 0.6419")

    def test_board_outline(self):
        lay = gerber.parse(fx("profile.ger"))
        self.assertEqual(gerber.flash_groups(lay), [])
        self.assertTrue(lay.polylines, "outline is drawn with strokes")


class TestMacroInterpreter(unittest.TestCase):
    def test_vector_line_primitive(self):
        # primitive 20: width must be taken from field 2, not the exposure flag
        prims = ["20,1,0.5,0,0,2,0,0"]
        rings = gerber._macro_rings(prims, [])
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        self.assertAlmostEqual(max(xs) - min(xs), 2.5, places=6)
        self.assertAlmostEqual(max(ys) - min(ys), 0.5, places=6)

    def test_center_line_rotation(self):
        prims = ["21,1,2,0.5,0,0,90"]
        rings = gerber._macro_rings(prims, [])
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        self.assertAlmostEqual(max(xs) - min(xs), 0.5, places=6)
        self.assertAlmostEqual(max(ys) - min(ys), 2.0, places=6)

    def test_parameter_expressions(self):
        self.assertAlmostEqual(gerber._val("0-$1", [-0.1]), 0.1)
        self.assertAlmostEqual(gerber._val("$1x2", [0.25]), 0.5)


if __name__ == "__main__":
    unittest.main()
