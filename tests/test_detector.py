import unittest

from kitty_tab_monitor.detector import StabilityTracker, signature


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


if __name__ == "__main__":
    unittest.main()
