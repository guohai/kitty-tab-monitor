import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitty_tab_monitor.__main__ import build_parser, make_logger


class CliTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser("config.json")

    def test_keep_awake_defaults_to_config(self):
        args = self.parser.parse_args([])

        self.assertIsNone(args.keep_awake)
        self.assertIsNone(args.keep_awake_lease_seconds)
        self.assertIsNone(args.auto_mode_fallback_seconds)

    def test_auto_mode_fallback_override(self):
        args = self.parser.parse_args(["--auto-mode-fallback-seconds", "90"])

        self.assertEqual(args.auto_mode_fallback_seconds, 90.0)

    def test_keep_awake_command_line_overrides(self):
        args = self.parser.parse_args(
            ["--keep-awake", "--keep-awake-lease-seconds", "120"]
        )

        self.assertTrue(args.keep_awake)
        self.assertEqual(args.keep_awake_lease_seconds, 120.0)

    def test_no_keep_awake_override(self):
        args = self.parser.parse_args(["--no-keep-awake"])

        self.assertFalse(args.keep_awake)

    def test_lease_must_be_positive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(["--keep-awake-lease-seconds", "0"])

    def test_positive_seconds_rejects_non_number(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(["--keep-awake-lease-seconds", "ten"])


class LoggerTests(unittest.TestCase):
    @patch("kitty_tab_monitor.__main__.time.strftime", return_value="2026-09-01 12:00:00")
    def test_console_has_blank_line_but_file_does_not(self, _strftime):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "monitor.log"
            config = SimpleNamespace(log_path=lambda: path)
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                make_logger(config)("example")

            expected = "2026-09-01 12:00:00 example"
            self.assertEqual(output.getvalue(), expected + "\n\n")
            self.assertEqual(path.read_text(), expected + "\n")


if __name__ == "__main__":
    unittest.main()
