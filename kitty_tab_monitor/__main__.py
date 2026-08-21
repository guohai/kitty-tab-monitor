"""Entry point: python -m kitty_tab_monitor [--dry-run] [--once] [-c config.json]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import Config
from .monitor import Monitor


def make_logger(cfg: Config):
    path = cfg.log_path()

    def log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    return log


def main() -> None:
    default_cfg = Path(__file__).resolve().parent.parent / "config.json"
    ap = argparse.ArgumentParser(prog="kitty-tab-monitor")
    ap.add_argument("-c", "--config", default=str(default_cfg),
                    help="path to config.json (default: alongside the package)")
    ap.add_argument("--dry-run", action="store_true",
                    help="log decisions but never type anything")
    ap.add_argument("--once", action="store_true",
                    help="run a single pass and exit (useful for testing)")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.dry_run:
        cfg.dry_run = True

    log = make_logger(cfg)
    if not cfg.openai_api_key:
        log("ERROR: OPENAI_API_KEY not set (env or config). Exiting.")
        sys.exit(2)

    mon = Monitor(cfg, log)
    if args.once:
        mon.tick()
        return
    try:
        mon.run()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
