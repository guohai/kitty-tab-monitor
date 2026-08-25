import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kitty_tab_monitor.detector import WindowState
from kitty_tab_monitor.monitor import Monitor


class FakeRC:
    def __init__(self, send_ok=True, send_error=None):
        self.send_ok = send_ok
        self.send_error = send_error
        self.focused_tabs = []
        self.sent = []

    def focus_tab(self, tab_id):
        self.focused_tabs.append(tab_id)
        return True

    def send_text(self, window_id, payload):
        self.sent.append((window_id, payload))
        if self.send_error:
            raise self.send_error
        return self.send_ok


def make_config(**overrides):
    values = {
        "kitty_socket": "",
        "kitty_rc_password": "",
        "stable_polls": 2,
        "capture_lines": 40,
        "send_denylist": [],
        "window_title_include": [],
        "window_title_exclude": [],
        "max_send_len": 120,
        "max_actions_per_min": 6,
        "min_confidence": 0.55,
        "dry_run": False,
        "model": "test-model",
        "poll_interval": 1.0,
        "action_cooldown": 8.0,
        "require_heuristic": True,
        "skip_password_prompts": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MonitorActionTests(unittest.TestCase):
    decision = {
        "is_waiting": True,
        "action": "type",
        "text_to_send": "1",
        "press_enter": True,
        "confidence": 0.9,
        "reason": "Best routine choice",
    }

    def make_monitor(self, send_ok=True, send_error=None, logger=None):
        monitor = Monitor(make_config(), logger or (lambda _message: None))
        monitor.rc = FakeRC(send_ok=send_ok, send_error=send_error)
        return monitor

    @staticmethod
    def window_listing():
        return [{
            "id": 1,
            "tabs": [{
                "id": 4,
                "title": "agent",
                "windows": [{
                    "id": 11,
                    "title": "shell",
                    "cwd": "/repo",
                }],
            }],
        }]

    @patch.object(Monitor, "tick", side_effect=KeyboardInterrupt)
    def test_startup_log_includes_version_and_build_date(self, _tick):
        messages = []
        monitor = self.make_monitor(logger=messages.append)

        with self.assertRaises(KeyboardInterrupt):
            monitor.run()

        self.assertEqual(
            messages[0],
            "starting: version=0.1.2 build_date=2026-08-24 model=test-model "
            "dry_run=False poll=1.0s socket=(auto-discover)",
        )

    def test_extracts_command_without_approval_menu(self):
        screen = """Bash command · from the general-purpose agent

   │ source .env 2>/dev/null; npx tsx -e "
   │ import { stopModerator } from './server/agora';
   │ stopModerator('A46').then(() => console.log('stopped ok'));
   │ "
   Run shell command

 'source' evaluates arguments as shell code
 Do you want to proceed?
 1. Yes
 2. No
"""

        context = Monitor._screen_context(screen)

        self.assertEqual(
            context,
            "source .env 2>/dev/null; npx tsx -e \" import { stopModerator } from "
            "'./server/agora'; stopModerator('A46').then(() => console.log('stopped ok')); \"",
        )

    @patch.object(Monitor, "_handle")
    def test_get_text_failure_preserves_valid_screen_for_retry(self, handle):
        messages = []
        monitor = self.make_monitor(logger=messages.append)
        monitor.rc = Mock()
        monitor.rc.ls.return_value = self.window_listing()
        prompt = "Do you want to proceed?\n1. Yes\n2. No"
        monitor.rc.get_text.side_effect = [
            prompt,
            RuntimeError("kitty stderr: target unavailable"),
            prompt,
        ]

        monitor.tick()
        monitor.tick()
        monitor.tick()

        state = monitor.tracker.states[11]
        self.assertEqual(state.stable_count, 2)
        handle.assert_called_once()
        self.assertIn("kitty stderr: target unavailable", messages[0])

    @patch.object(Monitor, "_handle")
    def test_empty_screen_preserves_valid_screen_for_retry(self, handle):
        messages = []
        monitor = self.make_monitor(logger=messages.append)
        monitor.rc = Mock()
        monitor.rc.ls.return_value = self.window_listing()
        prompt = "Do you want to proceed?\n1. Yes\n2. No"
        monitor.rc.get_text.side_effect = [prompt, "\n", prompt]

        monitor.tick()
        monitor.tick()
        monitor.tick()

        state = monitor.tracker.states[11]
        self.assertEqual(state.stable_count, 2)
        handle.assert_called_once()
        self.assertIn("screen read returned empty", messages[0])

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_sends_choice_and_enter(self, decide_mock):
        messages = []
        monitor = self.make_monitor(logger=messages.append)
        state = WindowState()

        window = {
            "window_id": 11,
            "tab_id": 4,
            "workspace": "/repo",
            "cwd": "/repo/src",
        }
        monitor._handle(window, state, "screen-a", "prompt", "agent")

        decide_mock.assert_called_once_with(
            monitor.cfg, "agent", "prompt", "/repo", "/repo/src"
        )
        self.assertEqual(monitor.rc.focused_tabs, [4])
        self.assertEqual(monitor.rc.sent, [(11, "1\r")])
        self.assertEqual(state.last_handled_sig, "screen-a")
        self.assertGreater(state.last_action_ts, 0)
        details = json.loads(messages[-1].split(" :: ", 1)[1])
        self.assertEqual(details["context"], "prompt")
        self.assertEqual(details["action"], "1 + Enter")
        self.assertEqual(details["reason"], "Best routine choice")

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_send_failure_leaves_prompt_retryable(self, _decide):
        monitor = self.make_monitor(send_ok=False)
        state = WindowState()

        monitor._handle({"window_id": 11, "tab_id": 4}, state, "screen-a", "prompt", "agent")

        self.assertEqual(state.last_handled_sig, "")
        self.assertEqual(state.last_action_ts, 0)
        self.assertTrue(monitor.guard.rate_ok())

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_send_exception_leaves_prompt_retryable(self, _decide):
        monitor = self.make_monitor(send_error=TimeoutError("kitty timed out"))
        state = WindowState()

        with self.assertRaises(TimeoutError):
            monitor._handle(
                {"window_id": 11, "tab_id": 4}, state, "screen-a", "prompt", "agent"
            )

        self.assertEqual(state.last_handled_sig, "")
        self.assertEqual(state.last_action_ts, 0)

    @patch("kitty_tab_monitor.monitor.decide")
    def test_rate_limit_defers_decision(self, decide_mock):
        monitor = self.make_monitor()
        monitor.guard.cfg.max_actions_per_min = 0
        state = WindowState()

        monitor._handle({"window_id": 11, "tab_id": 4}, state, "screen-a", "prompt", "agent")

        decide_mock.assert_not_called()
        self.assertEqual(state.last_handled_sig, "")


if __name__ == "__main__":
    unittest.main()
