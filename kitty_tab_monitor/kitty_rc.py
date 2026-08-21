"""Thin wrapper over kitty's remote-control CLI (`kitty @ ...`).

Uses subprocess so it is dependency-free and works from any shell that can
reach the kitty control socket (inherit KITTY_LISTEN_ON, or pass --to).
"""
from __future__ import annotations

import json
import subprocess


class KittyRC:
    def __init__(self, socket: str = "", kitty_bin: str = "kitty"):
        self.socket = socket
        self.kitty = kitty_bin

    def _cmd(self, args: list[str]) -> list[str]:
        base = [self.kitty, "@"]
        if self.socket:
            base += ["--to", self.socket]
        return base + args

    def _run(self, args: list[str], input_bytes: bytes | None = None, timeout: int = 8):
        return subprocess.run(
            self._cmd(args), input=input_bytes,
            capture_output=True, timeout=timeout,
        )

    def ls(self) -> list:
        r = self._run(["ls"])
        if r.returncode != 0:
            raise RuntimeError("kitty @ ls failed: " + r.stderr.decode(errors="replace").strip())
        return json.loads(r.stdout.decode())

    def get_text(self, window_id: int, extent: str = "screen") -> str:
        r = self._run(["get-text", "--match", f"id:{window_id}", "--extent", extent])
        if r.returncode != 0:
            return ""
        return r.stdout.decode(errors="replace")

    def send_text(self, window_id: int, text: str) -> bool:
        # --stdin sends the exact bytes, so nothing in `text` is treated as an escape.
        r = self._run(["send-text", "--match", f"id:{window_id}", "--stdin"],
                      input_bytes=text.encode())
        return r.returncode == 0

    def focus_tab(self, tab_id: int) -> bool:
        return self._run(["focus-tab", "--match", f"id:{tab_id}"]).returncode == 0

    def focus_window(self, window_id: int) -> bool:
        return self._run(["focus-window", "--match", f"id:{window_id}"]).returncode == 0


def iter_windows(ls_data: list):
    """Flatten `kitty @ ls` output into one dict per window."""
    for osw in ls_data:
        for tab in osw.get("tabs", []):
            for w in tab.get("windows", []):
                yield {
                    "os_window_id": osw.get("id"),
                    "tab_id": tab.get("id"),
                    "tab_title": tab.get("title", "") or "",
                    "window_id": w.get("id"),
                    "window_title": w.get("title", "") or "",
                    "is_focused": bool(w.get("is_focused") or w.get("is_active")),
                }
