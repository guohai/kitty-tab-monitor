import unittest

from kitty_tab_monitor.detector import (
    StabilityTracker,
    looks_like_auto_mode_rate_limit,
    looks_like_decision,
    looks_like_password,
    signature,
)


class StabilityTests(unittest.TestCase):
    def test_tmux_status_line_does_not_reset_screen_stability(self):
        prompt = "Do you want to proceed?\n1. Yes\n2. No\n\n"
        status_pairs = (
            (
                '[session_a]0:claude* 1:zsh- "task" 23:40 24-Aug-26',
                '[session_a]0:claude* 1:zsh- "task" 23:41 24-Aug-26',
            ),
            (
                '[session_b] 0:claude* 1:zsh- "task" 07:29 04-Sep-26',
                '[session_b] 0:claude* 1:zsh- "task" 07:30 04-Sep-26',
            ),
            (
                "[session_c] 2:agent* 3:shell- host=devbox load=10%",
                "[session_c] 2:agent* 3:shell- host=devbox load=11%",
            ),
        )
        for first_status, second_status in status_pairs:
            with self.subTest(status=first_status):
                first = prompt + first_status
                second = prompt + second_status

                self.assertEqual(signature(first, 40), signature(second, 40))

                tracker = StabilityTracker(stable_polls=2, capture_lines=40)
                tracker.update(1, first)
                state, _signature = tracker.update(1, second)

                self.assertTrue(tracker.is_paused(state))

    def test_claude_activity_timers_do_not_reset_screen_stability(self):
        prompt = (
            "Bash command · from the general-purpose agent\n"
            "flutter test test/deck_api_test.dart\n"
            "Do you want to proceed?\n"
            "1. Yes\n"
            "2. Yes, and don’t ask again for: flutter test *\n"
            "3. No\n"
        )
        first = (
            "* Smooshing… (34m 16s · ↓ 86.0k tokens)\n"
            + prompt
            + "◯ general-purpose  Deck task  6h 46m 13s · ↓ 48.8k tokens"
        )
        second = (
            "* Smooshing… (34m 18s · ↓ 87.4k tokens)\n"
            + prompt
            + "◯ general-purpose  Deck task  6h 46m 15s · ↓ 49.1k tokens"
        )

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
            + '[session_a]0:claude* 1:zsh- "task" 00:18 25-Aug-26'
        )

        self.assertTrue(looks_like_decision(screen)[0])

    def test_tmux_padding_does_not_hide_password_prompt(self):
        screen = (
            "Password:\n"
            + ("\n" * 20)
            + '[session_a]0:claude* 1:zsh- "task" 00:18 25-Aug-26'
        )

        self.assertTrue(looks_like_password(screen))

    def test_detects_auto_mode_classifier_rate_limit(self):
        screen = (
            "Initializing\u2026\n"
            "Error: claude-opus-5 is temporarily unavailable (rate-limited), so "
            "auto mode cannot determine the safety of Agent right now."
        )

        self.assertTrue(looks_like_auto_mode_rate_limit(screen))
        self.assertFalse(looks_like_auto_mode_rate_limit("Initializing\u2026"))


if __name__ == "__main__":
    unittest.main()
