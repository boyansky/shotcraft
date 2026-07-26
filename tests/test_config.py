import json, os, pathlib, tempfile, unittest
from unittest.mock import patch

from shotcraft import config
from shotcraft.config import NotConfigured


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(root / "cfg"),
            "XDG_DATA_HOME": str(root / "data"),
        }, clear=False)
        env.start(); self.addCleanup(env.stop)
        for name in config.ENV_VARS + ("SHOTCRAFT_HOME",):
            os.environ.pop(name, None)


class TestLocations(Base):
    def test_config_and_data_are_separate_places(self):
        self.assertNotEqual(config.config_home(), config.data_home())

    def test_data_is_not_inside_the_package(self):
        pkg = pathlib.Path(config.__file__).resolve().parent
        self.assertNotIn(pkg, config.data_home().resolve().parents)

    def test_shotcraft_home_overrides_data_location(self):
        with patch.dict(os.environ, {"SHOTCRAFT_HOME": "/tmp/elsewhere"}):
            self.assertEqual(config.data_home(), pathlib.Path("/tmp/elsewhere"))


class TestPrecedence(Base):
    def test_unset_raises_with_an_actionable_message(self):
        with self.assertRaises(NotConfigured) as ctx:
            config.base_url()
        self.assertIn("setup", str(ctx.exception))

    def test_unset_returns_none_when_not_required(self):
        self.assertIsNone(config.base_url(required=False))

    def test_file_is_used_when_no_env(self):
        config.save(base_url="http://192.168.1.42")
        self.assertEqual(config.base_url(), "http://192.168.1.42")

    def test_env_beats_the_file(self):
        config.save(base_url="http://from-file")
        with patch.dict(os.environ, {"SHOTCRAFT_API_URL": "http://from-env"}):
            self.assertEqual(config.base_url(), "http://from-env")

    def test_the_ecosystem_env_name_is_honoured(self):
        with patch.dict(os.environ, {"METICULOUS_API_URL": "http://legacy"}):
            self.assertEqual(config.base_url(), "http://legacy")

    def test_our_name_wins_over_the_ecosystem_one(self):
        with patch.dict(os.environ, {"METICULOUS_API_URL": "http://legacy",
                                     "SHOTCRAFT_API_URL": "http://ours"}):
            self.assertEqual(config.base_url(), "http://ours")

    def test_trailing_slash_is_stripped(self):
        config.save(base_url="http://x/")
        self.assertEqual(config.base_url(), "http://x")

    def test_blank_env_does_not_mask_the_file(self):
        config.save(base_url="http://from-file")
        with patch.dict(os.environ, {"SHOTCRAFT_API_URL": "   "}):
            self.assertEqual(config.base_url(), "http://from-file")


class TestSave(Base):
    def test_save_creates_the_directory(self):
        config.save(base_url="http://x")
        self.assertTrue(config.config_path().exists())

    def test_save_merges_rather_than_replacing(self):
        config.save(base_url="http://x")
        config.save(machine_name="Exalted")
        stored = json.loads(config.config_path().read_text())
        self.assertEqual(stored["base_url"], "http://x")
        self.assertEqual(stored["machine_name"], "Exalted")

    def test_corrupt_config_is_treated_as_empty_not_fatal(self):
        config.config_home().mkdir(parents=True, exist_ok=True)
        config.config_path().write_text("{ not json")
        self.assertEqual(config.load(), {})


if __name__ == "__main__":
    unittest.main()
