import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kitty_tab_monitor.keep_awake import (
    KeepAwakeLease,
    _LeaseLock,
    _backend_command,
    has_running_task,
    remote_connection_unresponsive,
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeLock:
    def __init__(self, available=True):
        self.available = available
        self.held = False
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self):
        self.acquire_calls += 1
        if not self.available:
            return False
        self.held = True
        return True

    def release(self):
        if self.held:
            self.release_calls += 1
            self.held = False


class FakeProcess:
    def __init__(self):
        self.pid = 987654
        self.returncode = None
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class BackendTests(unittest.TestCase):
    def test_macos_uses_idle_system_assertion_without_display_assertion(self):
        backend, command = _backend_command(
            "Darwin", which=lambda name: f"/usr/bin/{name}", pid=4321
        )

        self.assertEqual(backend, "caffeinate")
        self.assertEqual(command, ["/usr/bin/caffeinate", "-i", "-w", "4321"])
        self.assertNotIn("-d", command)

    def test_linux_uses_systemd_inhibitor_and_parent_watcher(self):
        backend, command = _backend_command(
            "Linux",
            which=lambda name: "/usr/bin/systemd-inhibit"
            if name == "systemd-inhibit" else None,
            pid=4321,
        )

        self.assertEqual(backend, "systemd-inhibit")
        self.assertEqual(command[0], "/usr/bin/systemd-inhibit")
        self.assertIn("--what=sleep", command)
        self.assertIn("4321", command)
        self.assertNotIn("caffeinate", " ".join(command))

    def test_linux_falls_back_to_gnome_inhibitor(self):
        backend, command = _backend_command(
            "Linux",
            which=lambda name: "/usr/bin/gnome-session-inhibit"
            if name == "gnome-session-inhibit" else None,
            pid=4321,
        )

        self.assertEqual(backend, "gnome-session-inhibit")
        self.assertEqual(command[0], "/usr/bin/gnome-session-inhibit")
        self.assertIn("--inhibit=suspend", command)

    def test_windows_uses_system_required_without_display_required(self):
        backend, command = _backend_command("Windows", which=lambda _name: None, pid=4321)
        script = command[2]

        self.assertEqual(backend, "SetThreadExecutionState")
        self.assertIn("ES_SYSTEM_REQUIRED", script)
        self.assertNotIn("ES_DISPLAY_REQUIRED", script)
        self.assertEqual(command[-1], "4321")

    def test_unsupported_platform_has_no_backend(self):
        self.assertEqual(
            _backend_command("Plan9", which=lambda _name: None, pid=4321),
            (None, None),
        )


class LeaseTests(unittest.TestCase):
    def make_lease(self, clock=None, process=None, lock=None, messages=None):
        process = process or FakeProcess()
        popen = Mock(return_value=process)
        messages = messages if messages is not None else []
        with patch(
            "kitty_tab_monitor.keep_awake._backend_command",
            return_value=("test-inhibitor", ["inhibit"]),
        ):
            lease = KeepAwakeLease(
                True,
                10,
                messages.append,
                clock=clock or Clock(),
                popen=popen,
                lock=lock or FakeLock(),
            )
        return lease, popen, process

    def test_repeated_renewals_start_only_one_inhibitor(self):
        clock = Clock()
        lease, popen, _process = self.make_lease(clock=clock)

        lease.renew()
        clock.now += 5
        lease.renew()

        popen.assert_called_once()
        self.assertEqual(lease.deadline, 115.0)

    @unittest.skipIf(os.name == "nt", "POSIX process-group behavior")
    @patch("kitty_tab_monitor.keep_awake.os.killpg")
    def test_expiration_stops_inhibitor_and_releases_lock(self, killpg):
        clock = Clock()
        lock = FakeLock()
        messages = []
        lease, popen, process = self.make_lease(
            clock=clock, lock=lock, messages=messages
        )
        lease.renew()

        clock.now += 11
        lease.tick()

        popen.assert_called_once()
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertEqual(lock.release_calls, 1)
        self.assertIsNone(lease.process)
        self.assertIn("after inactivity", messages[-1])

    def test_competing_monitor_does_not_start_an_inhibitor(self):
        messages = []
        lease, popen, _process = self.make_lease(
            lock=FakeLock(available=False), messages=messages
        )

        lease.renew()
        lease.renew()

        popen.assert_not_called()
        self.assertEqual(messages, ["keep-awake lease owned by another monitor process"])

    def test_unavailable_backend_logs_once(self):
        messages = []
        with patch(
            "kitty_tab_monitor.keep_awake._backend_command", return_value=(None, None)
        ):
            lease = KeepAwakeLease(True, 10, messages.append, lock=FakeLock())

        lease.renew()
        lease.renew()

        self.assertEqual(
            messages, ["keep-awake unavailable: no supported OS inhibitor found"]
        )

    @unittest.skipIf(os.name == "nt", "flock behavior is covered separately on Windows")
    def test_lock_allows_only_one_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "keep-awake.lock")
            first = _LeaseLock(path)
            second = _LeaseLock(path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


class RunningTaskTests(unittest.TestCase):
    @staticmethod
    def window(*cmdlines):
        return {
            "foreground_processes": [
                {"cmdline": command} for command in cmdlines
            ]
        }

    def test_visible_shell_running_marker_is_active(self):
        self.assertTrue(has_running_task(self.window(), "1 shell still running"))
        self.assertTrue(has_running_task(self.window(), "Waiting\u2026"))

    def test_approval_prompt_is_inactive_even_with_old_waiting_marker(self):
        screen = "Waiting\u2026\nDo you want to proceed?\n1. Yes\n2. No"
        self.assertFalse(
            has_running_task(self.window(["/usr/bin/python3", "job.py"]), screen)
        )

    def test_password_prompt_is_inactive(self):
        self.assertFalse(
            has_running_task(self.window(["sudo", "command"]), "Password: ")
        )

    def test_non_shell_foreground_process_is_active(self):
        self.assertTrue(
            has_running_task(self.window(["/usr/bin/python3", "job.py"]), "quiet")
        )

    def test_idle_shell_and_remote_session_are_not_active(self):
        self.assertFalse(has_running_task(self.window(["/bin/zsh"]), "prompt"))
        self.assertFalse(has_running_task(self.window(["ssh", "host"]), "prompt"))
        self.assertFalse(
            has_running_task(self.window(["mosh-client", "host"]), "prompt")
        )

    def test_interactive_agent_alone_is_not_permanently_active(self):
        self.assertFalse(
            has_running_task(
                self.window(["node", "/usr/local/bin/claude"]), "agent prompt"
            )
        )

    def test_mosh_last_contact_counter_is_unresponsive(self):
        window = self.window(["mosh-client", "10.0.0.1", "60001"])

        self.assertTrue(
            remote_connection_unresponsive(
                window,
                "mosh: Last contact 37 seconds ago. [To quit: Ctrl-^ .]",
            )
        )

    def test_ssh_network_failure_is_unresponsive(self):
        window = self.window(["/usr/bin/ssh", "build-host"])

        self.assertTrue(
            remote_connection_unresponsive(
                window,
                "ssh: connect to host build-host port 22: Operation timed out",
            )
        )

    def test_connected_remote_output_is_responsive(self):
        window = self.window(["/usr/bin/ssh", "build-host"])

        self.assertFalse(remote_connection_unresponsive(window, "tests: 42 passed"))

    def test_local_network_error_text_does_not_classify_remote(self):
        window = self.window(["/usr/bin/python3", "job.py"])

        self.assertFalse(
            remote_connection_unresponsive(window, "Connection timed out")
        )


if __name__ == "__main__":
    unittest.main()
