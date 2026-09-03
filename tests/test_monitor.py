import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kitty_tab_monitor.detector import WindowState, signature
from kitty_tab_monitor.monitor import Monitor


class FakeRC:
    def __init__(self, send_ok=True, send_error=None):
        self.send_ok = send_ok
        self.send_error = send_error
        self.focused_tabs = []
        self.sent = []
        self.screen_text = "prompt"

    def focus_tab(self, tab_id):
        self.focused_tabs.append(tab_id)
        return True

    def send_text(self, window_id, payload):
        self.sent.append((window_id, payload))
        if self.send_error:
            raise self.send_error
        return self.send_ok

    def get_text(self, _window_id, extent="screen"):
        return self.screen_text


def make_config(**overrides):
    values = {
        "kitty_socket": "",
        "kitty_rc_password": "",
        "stable_polls": 2,
        "capture_lines": 40,
        "auto_mode_fallback_seconds": 120.0,
        "keep_awake": False,
        "keep_awake_lease_seconds": 600.0,
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
            "starting: version=0.2.1 build_date=2026-09-03 model=test-model "
            "dry_run=False poll=1.0s socket=(auto-discover) "
            "auto_fallback=120s "
            "keep_awake=disabled lease=600s",
        )

    @patch("kitty_tab_monitor.monitor.time.monotonic")
    def test_rate_limited_auto_mode_switches_to_manual_once_after_two_minutes(
        self, monotonic
    ):
        monotonic.side_effect = [100.0, 219.9, 220.0, 300.0, 400.0]
        messages = []
        monitor = self.make_monitor(logger=messages.append)
        monitor.rc = Mock()
        monitor.rc.send_key.return_value = True
        monitor.awake = Mock()
        window = {"window_id": 11, "tab_id": 4}
        screen = (
            "Initializing\u2026\n"
            "Error: claude-opus-5 is temporarily unavailable (rate-limited), so "
            "auto mode cannot determine the safety of Agent right now."
        )
        monitor.rc.get_text.return_value = screen

        self.assertTrue(monitor._recover_from_auto_mode_rate_limit(window, screen))
        self.assertTrue(monitor._recover_from_auto_mode_rate_limit(window, screen))
        monitor.rc.send_key.assert_not_called()

        self.assertTrue(monitor._recover_from_auto_mode_rate_limit(window, screen))
        self.assertTrue(monitor._recover_from_auto_mode_rate_limit(window, screen))

        monitor.rc.focus_tab.assert_not_called()
        monitor.rc.send_key.assert_called_once_with(11, "shift+tab")
        monitor.awake.renew.assert_called_once_with()
        details = json.loads(messages[-1].split(" :: ", 1)[1])
        self.assertEqual(details["action"], "Shift+Tab")

        self.assertFalse(
            monitor._recover_from_auto_mode_rate_limit(window, "manual mode")
        )
        self.assertTrue(monitor._recover_from_auto_mode_rate_limit(window, screen))
        self.assertFalse(monitor.auto_mode_failures[11]["handled"])
        monitor.rc.send_key.assert_called_once()

    @patch.object(Monitor, "_handle")
    def test_screen_changes_renew_one_shared_lease(self, _handle):
        monitor = self.make_monitor()
        monitor.awake = Mock()
        monitor.rc = Mock()
        monitor.rc.ls.return_value = self.window_listing()
        monitor.rc.get_text.side_effect = ["ready", "ready", "new output"]

        monitor.tick()
        monitor.tick()
        monitor.tick()

        self.assertEqual(monitor.awake.tick.call_count, 3)
        self.assertEqual(monitor.awake.renew.call_count, 2)

    @patch.object(Monitor, "_handle")
    def test_unchanged_known_running_task_renews_lease(self, _handle):
        monitor = self.make_monitor()
        monitor.awake = Mock()
        monitor.rc = Mock()
        listing = self.window_listing()
        listing[0]["tabs"][0]["windows"][0]["foreground_processes"] = [
            {"cmdline": ["/usr/bin/python3", "quiet-job.py"]}
        ]
        monitor.rc.ls.return_value = listing
        monitor.rc.get_text.return_value = "quiet task"

        monitor.tick()
        monitor.tick()

        self.assertEqual(monitor.awake.renew.call_count, 2)

    @patch.object(Monitor, "_handle")
    def test_disconnected_mosh_status_does_not_renew_lease(self, _handle):
        monitor = self.make_monitor()
        monitor.awake = Mock()
        monitor.rc = Mock()
        listing = self.window_listing()
        listing[0]["tabs"][0]["windows"][0]["foreground_processes"] = [
            {"cmdline": ["mosh-client", "10.0.0.1", "60001"]}
        ]
        monitor.rc.ls.return_value = listing
        monitor.rc.get_text.side_effect = [
            "remote task output",
            "mosh: Last contact 1 second ago. [To quit: Ctrl-^ .]",
            "mosh: Last contact 2 seconds ago. [To quit: Ctrl-^ .]",
        ]

        monitor.tick()
        monitor.tick()
        monitor.tick()

        self.assertEqual(monitor.awake.tick.call_count, 3)
        self.assertEqual(monitor.awake.renew.call_count, 1)

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
        monitor.awake = Mock()
        state = WindowState()

        window = {
            "window_id": 11,
            "tab_id": 4,
            "workspace": "/repo",
            "cwd": "/repo/src",
        }
        screen_sig = signature("prompt", monitor.cfg.capture_lines)
        monitor._handle(window, state, screen_sig, "prompt", "agent")

        decide_mock.assert_called_once_with(
            monitor.cfg, "agent", "prompt", "/repo", "/repo/src", "local"
        )
        self.assertEqual(monitor.rc.focused_tabs, [])
        self.assertEqual(monitor.rc.sent, [(11, "1\r")])
        self.assertEqual(state.last_handled_sig, screen_sig)
        self.assertGreater(state.last_action_ts, 0)
        monitor.awake.renew.assert_called_once_with()
        details = json.loads(messages[-1].split(" :: ", 1)[1])
        self.assertEqual(details["context"], "prompt")
        self.assertEqual(details["action"], "1 + Enter")
        self.assertEqual(details["reason"], "Best routine choice")

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_send_failure_leaves_prompt_retryable(self, _decide):
        monitor = self.make_monitor(send_ok=False)
        state = WindowState()
        screen_sig = signature("prompt", monitor.cfg.capture_lines)

        monitor._handle(
            {"window_id": 11, "tab_id": 4}, state, screen_sig, "prompt", "agent"
        )

        self.assertEqual(state.last_handled_sig, "")
        self.assertEqual(state.last_action_ts, 0)
        self.assertTrue(monitor.guard.rate_ok())

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_send_exception_leaves_prompt_retryable(self, _decide):
        monitor = self.make_monitor(send_error=TimeoutError("kitty timed out"))
        state = WindowState()
        screen_sig = signature("prompt", monitor.cfg.capture_lines)

        with self.assertRaises(TimeoutError):
            monitor._handle(
                {"window_id": 11, "tab_id": 4}, state, screen_sig, "prompt", "agent"
            )

        self.assertEqual(state.last_handled_sig, "")
        self.assertEqual(state.last_action_ts, 0)

    @patch("kitty_tab_monitor.monitor.decide", return_value=(decision, None))
    def test_changed_target_screen_blocks_stale_llm_answer(self, _decide):
        messages = []
        monitor = self.make_monitor(logger=messages.append)
        monitor.rc.screen_text = "command already running"
        state = WindowState()
        prompt = "Do you want to proceed?\n1. Yes\n2. No"

        monitor._handle(
            {"window_id": 11, "tab_id": 4},
            state,
            signature(prompt, monitor.cfg.capture_lines),
            prompt,
            "agent",
        )

        self.assertEqual(monitor.rc.sent, [])
        self.assertEqual(state.last_handled_sig, "")
        self.assertIn("target changed -> not sending", messages[-1])

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
