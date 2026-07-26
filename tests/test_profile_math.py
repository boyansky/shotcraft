import unittest
from shotcraft.profile_math import resolve, resolve_points, interpolate

VARS = [
    {"key": "flow_Saturation Flowrate", "value": 1},
    {"key": "pressure_1", "value": 6},
]

class TestResolve(unittest.TestCase):
    def test_plain_number_passes_through(self):
        self.assertEqual(resolve(1.5, VARS), 1.5)

    def test_dollar_reference_resolves(self):
        self.assertEqual(resolve("$pressure_1", VARS), 6.0)

    def test_key_with_spaces_resolves(self):
        self.assertEqual(resolve("$flow_Saturation Flowrate", VARS), 1.0)

    def test_unknown_variable_raises(self):
        with self.assertRaises(KeyError):
            resolve("$nope", VARS)

    def test_bare_string_raises(self):
        with self.assertRaises(ValueError):
            resolve("pressure_1", VARS)

class TestInterpolate(unittest.TestCase):
    PTS = [(0.0, 0.0), (10.0, 5.0)]

    def test_midpoint(self):
        self.assertAlmostEqual(interpolate(self.PTS, 5.0), 2.5)

    def test_clamps_below_range(self):
        self.assertEqual(interpolate(self.PTS, -3.0), 0.0)

    def test_clamps_above_range(self):
        self.assertEqual(interpolate(self.PTS, 99.0), 5.0)

    def test_single_point_is_constant(self):
        self.assertEqual(interpolate([(0.0, 5.5)], 42.0), 5.5)

    def test_duplicate_x_takes_later_value(self):
        self.assertEqual(interpolate([(0.0, 1.0), (0.0, 2.0)], 0.0), 1.0)

    def test_empty_points_raises(self):
        with self.assertRaises(ValueError):
            interpolate([], 1.0)

class TestResolvePoints(unittest.TestCase):
    def test_resolves_both_axes(self):
        pts = resolve_points([[0, "$pressure_1"], [9.5, 2]], VARS)
        self.assertEqual(pts, [(0.0, 6.0), (9.5, 2.0)])

if __name__ == "__main__":
    unittest.main()
