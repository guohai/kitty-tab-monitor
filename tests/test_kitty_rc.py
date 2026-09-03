import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitty_tab_monitor.kitty_rc import KittyRC, _cwd_path, _workspace_for_cwd, iter_windows


class WorkspaceTests(unittest.TestCase):
    def tearDown(self):
        _workspace_for_cwd.cache_clear()

    def test_decodes_file_url(self):
        self.assertEqual(_cwd_path("file:///tmp/project%20name"), "/tmp/project name")

    def test_uses_nearest_git_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            child = root / "src" / "feature"
            child.mkdir(parents=True)

            self.assertEqual(_workspace_for_cwd(str(child)), str(root))

    def test_uses_foreground_process_cwd_as_fallback(self):
        data = [{
            "id": 1,
            "tabs": [{
                "id": 2,
                "title": "agent",
                "windows": [{
                    "id": 3,
                    "title": "shell",
                    "foreground_processes": [{"cwd": "file:///tmp/example"}],
                }],
            }],
        }]

        window = next(iter_windows(data))

        self.assertEqual(window["cwd"], "/tmp/example")
        self.assertEqual(window["workspace"], "/tmp/example")
        self.assertEqual(
            window["foreground_processes"],
            [{"cwd": "file:///tmp/example"}],
        )
        self.assertEqual(window["session_type"], "local")

    def test_marks_ssh_and_mosh_windows_as_remote(self):
        for executable in ("ssh", "mosh-client"):
            data = [{
                "tabs": [{
                    "windows": [{
                        "id": 3,
                        "foreground_processes": [
                            {"cmdline": [f"/usr/bin/{executable}", "host"]}
                        ],
                    }],
                }],
            }]

            self.assertEqual(next(iter_windows(data))["session_type"], "remote")


class KittyRCTests(unittest.TestCase):
    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_passes_password_via_environment_not_command_line(self, run):
        with patch.dict(os.environ, {"KITTY_PUBLIC_KEY": "1:key"}):
            KittyRC(socket="unix:/tmp/kitty", password="secret")._run(["ls"])

        args, kwargs = run.call_args
        self.assertNotIn("secret", args[0])
        self.assertEqual(kwargs["env"]["KITTY_RC_PASSWORD"], "secret")

    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_skips_password_when_public_key_is_unavailable(self, run):
        with patch.dict(os.environ, {}, clear=True):
            KittyRC(socket="unix:/tmp/kitty", password="secret")._run(["ls"])

        self.assertIsNone(run.call_args.kwargs["env"])

    @patch("kitty_tab_monitor.kitty_rc.os.stat")
    @patch("kitty_tab_monitor.kitty_rc.glob.glob")
    def test_discovers_single_owned_socket(self, glob_paths, os_stat):
        glob_paths.return_value = ["/tmp/kitty-123"]
        os_stat.return_value = SimpleNamespace(st_mode=0o140600, st_uid=os.getuid())

        socket = KittyRC()._discover_socket()

        self.assertEqual(socket, "unix:/tmp/kitty-123")

    @patch.object(KittyRC, "_discover_socket", return_value="unix:/tmp/kitty-123")
    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_auto_discovery_prevents_terminal_transport(self, run, _discover):
        KittyRC()._run(["ls"])

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["kitty", "@", "--to", "unix:/tmp/kitty-123"])

    @patch("kitty_tab_monitor.kitty_rc.glob.glob", return_value=[])
    def test_missing_socket_has_actionable_error(self, _glob_paths):
        with self.assertRaisesRegex(RuntimeError, "KTM_SOCKET"):
            KittyRC()._discover_socket()

    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_password_protected_error_names_required_setup(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"Error: Remote control is disabled",
        )

        with self.assertRaisesRegex(RuntimeError, "KITTY_PUBLIC_KEY"):
            KittyRC(socket="unix:/tmp/kitty-123").ls()

    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_get_text_failure_includes_window_and_stderr(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"Error: target window not found",
        )

        with self.assertRaisesRegex(
            RuntimeError, "window 17: Error: target window not found"
        ):
            KittyRC(socket="unix:/tmp/kitty-123").get_text(17)

    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_send_key_targets_exact_window(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        sent = KittyRC(socket="unix:/tmp/kitty-123").send_key(17, "shift+tab")

        self.assertTrue(sent)
        self.assertEqual(
            run.call_args.args[0][-4:],
            ["send-key", "--match", "id:17", "shift+tab"],
        )

    @patch("kitty_tab_monitor.kitty_rc.subprocess.run")
    def test_send_text_targets_exact_window_without_focusing(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        sent = KittyRC(socket="unix:/tmp/kitty-123").send_text(17, "2\r")

        self.assertTrue(sent)
        self.assertEqual(
            run.call_args.args[0][-4:],
            ["send-text", "--match", "id:17", "--stdin"],
        )
        self.assertEqual(run.call_args.kwargs["input"], b"2\r")


if __name__ == "__main__":
    unittest.main()
