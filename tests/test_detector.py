import unittest

from kitty_tab_monitor.detector import (
    StabilityTracker,
    looks_like_decision,
    looks_like_password,
    signature,
)


class StabilityTests(unittest.TestCase):
    def test_tmux_clock_does_not_reset_screen_stability(self):
        prompt = "Do you want to proceed?\n1. Yes\n2. No\n\n"
        first = prompt + '[vox_eval]0:claude* 1:zsh- "task" 23:40 24-Aug-26'
        second = prompt + '[vox_eval]0:claude* 1:zsh- "task" 23:41 24-Aug-26'

        self.assertEqual(signature(first, 40), signature(second, 40))

        tracker = StabilityTracker(stable_polls=2, capture_lines=40)
        tracker.update(1, first)
        state, _signature = tracker.update(1, second)

        self.assertTrue(tracker.is_paused(state))

    def test_tmux_padding_does_not_hide_decision_prompt(self):
        screen = (
            "Do you want to proceed?\n"
            "1. Yes\n"
            "2. No\n"
            + ("\n" * 20)
            + '[vox_eval]0:claude* 1:zsh- "task" 00:18 25-Aug-26'
        )

        self.assertTrue(looks_like_decision(screen)[0])

    def test_tmux_padding_does_not_hide_password_prompt(self):
        screen = (
            "Password:\n"
            + ("\n" * 20)
            + '[vox_eval]0:claude* 1:zsh- "task" 00:18 25-Aug-26'
        )

        self.assertTrue(looks_like_password(screen))


if __name__ == "__main__":
    unittest.main()
