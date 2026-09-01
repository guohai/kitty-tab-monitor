"""Command-line entry point for kitty-tab-monitor."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import Config
from .monitor import Monitor


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a number") from e
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def build_parser(default_config: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kitty-tab-monitor",
        description="Monitor kitty tabs and automatically answer safe approval prompts.",
    )
    parser.add_argument(
        "-c", "--config", default=default_config,
        help="path to config.json (default: alongside the package)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="log decisions but never type anything",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run a single pass and exit (useful for testing)",
    )
    parser.add_argument(
        "--auto-mode-fallback-seconds",
        type=_positive_seconds,
        metavar="SECONDS",
        help="send Shift+Tab after this long auto-mode classifier outage (default: 120)",
    )
    keep_awake = parser.add_mutually_exclusive_group()
    keep_awake.add_argument(
        "--keep-awake", dest="keep_awake", action="store_true", default=None,
        help="prevent idle system sleep while tabs are active (default: disabled)",
    )
    keep_awake.add_argument(
        "--no-keep-awake", dest="keep_awake", action="store_false", default=None,
        help="disable an inhibitor enabled by config or environment",
    )
    parser.add_argument(
        "--keep-awake-lease-seconds",
        type=_positive_seconds,
        metavar="SECONDS",
        help="release the inhibitor after this much inactivity (default: config value, 600)",
    )
    return parser


def make_logger(cfg: Config):
    path = cfg.log_path()

    def log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, end="\n\n", flush=True)
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    return log


def main() -> None:
    default_cfg = Path(__file__).resolve().parent.parent / "config.json"
    parser = build_parser(str(default_cfg))
    args = parser.parse_args()

    cfg = Config.load(args.config)
    if args.dry_run:
        cfg.dry_run = True
    if args.auto_mode_fallback_seconds is not None:
        cfg.auto_mode_fallback_seconds = args.auto_mode_fallback_seconds
    if args.keep_awake is not None:
        cfg.keep_awake = args.keep_awake
    if args.keep_awake_lease_seconds is not None:
        cfg.keep_awake_lease_seconds = args.keep_awake_lease_seconds

    log = make_logger(cfg)
    if not cfg.openai_api_key:
        log("ERROR: OPENAI_API_KEY not set (env or config). Exiting.")
        sys.exit(2)

    mon = Monitor(cfg, log)
    if args.once:
        try:
            mon.tick()
        finally:
            mon.awake.close()
        return
    try:
        mon.run()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
