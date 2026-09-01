import os
import unittest
from pathlib import Path
from unittest.mock import patch

from kitty_tab_monitor.config import Config


class ConfigTests(unittest.TestCase):
    def test_keep_awake_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Config.load()

        self.assertFalse(config.keep_awake)
        self.assertEqual(config.auto_mode_fallback_seconds, 120.0)

    def test_environment_can_enable_keep_awake(self):
        with patch.dict(os.environ, {"KTM_KEEP_AWAKE": "1"}, clear=True):
            config = Config.load()

        self.assertTrue(config.keep_awake)

    def test_environment_can_override_auto_mode_fallback(self):
        with patch.dict(
            os.environ, {"KTM_AUTO_MODE_FALLBACK_SECONDS": "75"}, clear=True
        ):
            config = Config.load()

        self.assertEqual(config.auto_mode_fallback_seconds, 75.0)

    def test_configured_prompt_prefers_auto_mode_for_safe_commands(self):
        project_config = Path(__file__).resolve().parent.parent / "config.json"

        with patch.dict(os.environ, {}, clear=True):
            config = Config.load(str(project_config))

        self.assertIn('"Yes, and switch to auto mode"', config.system_prompt)
        self.assertIn('"always allow"/"don\'t ask again"', config.system_prompt)
        self.assertIn("choose the persistent approval", config.system_prompt)
        self.assertIn("does not make an unsafe current command safe", config.system_prompt)


if __name__ == "__main__":
    unittest.main()
