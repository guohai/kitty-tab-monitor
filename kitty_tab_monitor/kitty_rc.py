"""Thin wrapper over kitty's remote-control CLI (`kitty @ ...`).

Uses subprocess so it is dependency-free and works from any shell that can
reach the kitty control socket (inherit KITTY_LISTEN_ON, or pass --to).
"""
from __future__ import annotations

import glob
import json
import os
import stat
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse


def _cwd_path(value) -> str:
    """Convert kitty's cwd value (usually a file URL) into a local path."""
    if not isinstance(value, str) or not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return value


@lru_cache(maxsize=256)
def _workspace_for_cwd(value: str) -> str:
    """Use the nearest Git root as the workspace, falling back to the cwd."""
    cwd = _cwd_path(value)
    if not cwd:
        return ""
    path = Path(cwd).expanduser()
    try:
        if not path.is_dir():
            return cwd
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return str(candidate)
    except OSError:
        pass
    return str(path)


def _window_cwd(window: dict) -> str:
    cwd = _cwd_path(window.get("cwd"))
    if cwd:
        return cwd
    for process in reversed(window.get("foreground_processes") or []):
        cwd = _cwd_path(process.get("cwd"))
        if cwd:
            return cwd
    return ""


class KittyRC:
    def __init__(self, socket: str = "", kitty_bin: str = "kitty", password: str = ""):
        self.socket = socket
        self.kitty = kitty_bin
        self.password = password

    def _cmd(self, args: list[str]) -> list[str]:
        base = [self.kitty, "@"]
        if self.socket:
            base += ["--to", self.socket]
        return base + args

    def _discover_socket(self) -> str:
        """Find this user's kitty socket without falling back to terminal escapes."""
        patterns = ["/tmp/kitty-*"]
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            patterns.append(str(Path(runtime_dir) / "kitty-*"))

        paths = set()
        for pattern in patterns:
            paths.update(glob.glob(pattern))

        candidates = []
        for path in sorted(paths):
            try:
                info = os.stat(path)
            except OSError:
                continue
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                candidates.append(path)

        if not candidates:
            raise RuntimeError(
                "no kitty control socket found; set KTM_SOCKET to kitty's listen_on address"
            )
        if len(candidates) == 1:
            return "unix:" + candidates[0]

        kitty_pid = os.environ.get("KITTY_PID", "")
        expected = f"/tmp/kitty-{kitty_pid}" if kitty_pid.isdigit() else ""
        if expected in candidates:
            return "unix:" + expected

        raise RuntimeError(
            "multiple kitty control sockets found; set KTM_SOCKET to the correct listen_on address"
        )

    def _run(self, args: list[str], input_bytes: bytes | None = None, timeout: int = 8):
        if not self.socket:
            self.socket = self._discover_socket()
        env = None
        if self.password and os.environ.get("KITTY_PUBLIC_KEY"):
            # kitty reads this variable by default, keeping the secret out of argv.
            env = {**os.environ, "KITTY_RC_PASSWORD": self.password}
        return subprocess.run(
            self._cmd(args), input=input_bytes,
            capture_output=True, timeout=timeout, env=env,
        )

    def ls(self) -> list:
        r = self._run(["ls"])
        if r.returncode != 0:
            detail = r.stderr.decode(errors="replace").strip()
            if "Remote control is disabled" in detail:
                detail += "; use socket-only mode or restart kitty to obtain KITTY_PUBLIC_KEY"
            raise RuntimeError("kitty @ ls failed: " + detail)
        return json.loads(r.stdout.decode())

    def get_text(self, window_id: int, extent: str = "screen") -> str:
        r = self._run(["get-text", "--match", f"id:{window_id}", "--extent", extent])
        if r.returncode != 0:
            detail = r.stderr.decode(errors="replace").strip()
            if not detail:
                detail = f"exit status {r.returncode}"
            raise RuntimeError(
                f"kitty @ get-text failed for window {window_id}: {detail}"
            )
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
                cwd = _window_cwd(w)
                yield {
                    "os_window_id": osw.get("id"),
                    "tab_id": tab.get("id"),
                    "tab_title": tab.get("title", "") or "",
                    "window_id": w.get("id"),
                    "window_title": w.get("title", "") or "",
                    "cwd": cwd,
                    "workspace": _workspace_for_cwd(cwd),
                    "is_focused": bool(w.get("is_focused") or w.get("is_active")),
                }
