import unittest
from types import SimpleNamespace

from kitty_tab_monitor.safety import Guard


class GuardTests(unittest.TestCase):
    def setUp(self):
        config = SimpleNamespace(
            max_send_len=120,
            max_actions_per_min=6,
            send_denylist=[r"rm\s+-rf"],
        )
        self.guard = Guard(config)

    def test_allows_menu_choice(self):
        self.assertEqual(self.guard.send_allowed("2"), (True, ""))

    def test_blocks_embedded_enter(self):
        allowed, reason = self.guard.send_allowed("y\rmalicious command")

        self.assertFalse(allowed)
        self.assertIn("control characters", reason)

    def test_blocks_denylisted_text(self):
        allowed, reason = self.guard.send_allowed("rm -rf /tmp/example")

        self.assertFalse(allowed)
        self.assertIn("denylist", reason)


if __name__ == "__main__":
    unittest.main()
