import unittest

from shotcraft.taste import (TASTE_SCHEMA, parse_intensity, parse_lean,
                             parse_verdict, validate_taste)


def rating(**over):
    base = {"lean": "sour", "intensity": 2, "versus": None}
    base.update(over)
    return base


class TestValidate(unittest.TestCase):
    def test_schema_is_three(self):
        self.assertEqual(TASTE_SCHEMA, 3)

    def test_none_is_allowed(self):
        validate_taste(None)

    def test_complete_rating_passes(self):
        validate_taste(rating())

    def test_unknown_lean_raises(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(lean="metallic"))

    def test_intensity_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(intensity=4))

    def test_intensity_must_be_zero_when_lean_is_none(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(lean="none", intensity=2))

    def test_intensity_must_be_nonzero_when_there_is_a_lean(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(lean="sour", intensity=0))

    def test_both_is_a_valid_lean(self):
        validate_taste(rating(lean="both", intensity=3))

    def test_versus_requires_a_known_verdict(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(versus={"shot": "abc", "verdict": "nicer"}))

    def test_versus_accepts_a_verdict(self):
        validate_taste(rating(versus={"shot": "abc", "verdict": "better"}))

    def test_bool_is_not_an_int(self):
        with self.assertRaises(ValueError):
            validate_taste(rating(intensity=True))


class TestParsers(unittest.TestCase):
    def test_lean_keys(self):
        self.assertEqual(parse_lean("s"), "sour")
        self.assertEqual(parse_lean("B"), "bitter")
        self.assertEqual(parse_lean("x"), "both")
        self.assertEqual(parse_lean("-"), "none")

    def test_unknown_lean_key_raises(self):
        with self.assertRaises(ValueError):
            parse_lean("q")

    def test_intensity_parses_digits(self):
        self.assertEqual(parse_intensity("2"), 2)

    def test_intensity_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_intensity("7")

    def test_verdict_keys(self):
        self.assertEqual(parse_verdict("b"), "better")
        self.assertEqual(parse_verdict("w"), "worse")
        self.assertEqual(parse_verdict("="), "same")
