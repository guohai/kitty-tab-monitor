"""Cross-platform system-sleep inhibitor with a renewable activity lease."""
from __future__ import annotations

import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .detector import _stable_tail, looks_like_decision, looks_like_password
from .kitty_rc import is_remote_session


_RUNNING_MARKERS = [
    re.compile(r"(?i)\b\d+\s+shells?\s+(?:still\s+)?running\b"),
    re.compile(r"(?i)\b(?:running|waiting)(?:\.{3}|\u2026)"),
    re.compile(r"(?i)\besc to interrupt\b"),
    re.compile(r"(?i)\bbackground (?:command|job)\b.*\b(?:running|waiting)\b"),
]

_REMOTE_LOSS_MARKERS = [
    re.compile(r"(?i)\bmosh:\s*last contact\b.*\bago\b"),
    re.compile(r"(?i)\bnothing received from (?:the )?server\b"),
    re.compile(r"(?i)\b(?:connection|operation) timed out\b"),
    re.compile(r"(?i)\bconnection (?:closed|lost|reset|refused)\b"),
    re.compile(r"(?i)\b(?:broken pipe|no route to host)\b"),
    re.compile(r"(?i)\bnetwork is (?:down|unreachable)\b"),
    re.compile(r"(?i)\b(?:could not resolve hostname|temporary failure in name resolution)\b"),
]

_IDLE_PROGRAMS = {
    "bash", "csh", "dash", "fish", "ksh", "nu", "pwsh", "sh", "tcsh", "zsh",
    "autossh", "cmd.exe", "powershell.exe", "kitty", "mosh", "mosh-client",
    "screen", "ssh", "tmux",
}

_INTERACTIVE_AGENTS = {"aider", "claude", "codex", "gemini", "opencode"}
_DEFAULT_LOCK_PATH = "~/.local/share/kitty-tab-monitor/keep-awake.lock"
_START_RETRY_SECONDS = 30.0


def remote_connection_unresponsive(window: dict, screen_text: str) -> bool:
    """Ignore changing disconnect status from otherwise idle remote sessions."""
    if not is_remote_session(window):
        return False
    tail = _stable_tail(screen_text, 8)
    return any(pattern.search(tail) for pattern in _REMOTE_LOSS_MARKERS)


def has_running_task(window: dict, screen_text: str) -> bool:
    """Best-effort detection for tasks that remain active without screen output."""
    waiting, _kind = looks_like_decision(screen_text)
    if waiting or looks_like_password(screen_text):
        return False

    tail = _stable_tail(screen_text, 30)
    if any(pattern.search(tail) for pattern in _RUNNING_MARKERS):
        return True

    for process in window.get("foreground_processes") or []:
        cmdline = process.get("cmdline") or []
        if not cmdline:
            continue
        if isinstance(cmdline, str):
            cmdline = [cmdline]
        names = [Path(str(part)).name.lower().lstrip("-") for part in cmdline]
        executable = names[0]
        if executable in _IDLE_PROGRAMS:
            continue
        if any(name in _INTERACTIVE_AGENTS for name in names):
            continue
        if any(agent in " ".join(names) for agent in _INTERACTIVE_AGENTS):
            continue
        return True
    return False


def _parent_watch_command(pid: int) -> list[str]:
    script = (
        "import os,sys,time\n"
        "pid=int(sys.argv[1])\n"
        "while True:\n"
        " try: os.kill(pid, 0)\n"
        " except OSError: break\n"
        " time.sleep(2)\n"
    )
    return [sys.executable, "-c", script, str(pid)]


def _backend_command(system: str | None = None, which=None, pid: int | None = None):
    system = system or platform.system()
    which = which or shutil.which
    pid = pid or os.getpid()

    if system == "Darwin":
        caffeinate = which("caffeinate")
        if caffeinate:
            # -i blocks idle system sleep; it does not block display sleep.
            return "caffeinate", [caffeinate, "-i", "-w", str(pid)]
        return None, None

    if system == "Linux":
        inhibitor = which("systemd-inhibit")
        if inhibitor:
            return "systemd-inhibit", [
                inhibitor,
                "--what=sleep",
                "--who=kitty-tab-monitor",
                "--why=monitored terminal activity",
                "--mode=block",
                *_parent_watch_command(pid),
            ]
        gnome = which("gnome-session-inhibit")
        if gnome:
            return "gnome-session-inhibit", [
                gnome,
                "--inhibit=suspend",
                "--reason=monitored terminal activity",
                *_parent_watch_command(pid),
            ]
        return None, None

    if system == "Windows":
        script = (
            "import ctypes,sys\n"
            "kernel32=ctypes.windll.kernel32\n"
            "ES_CONTINUOUS=0x80000000\n"
            "ES_SYSTEM_REQUIRED=0x00000001\n"
            "SYNCHRONIZE=0x00100000\n"
            "parent=kernel32.OpenProcess(SYNCHRONIZE,False,int(sys.argv[1]))\n"
            "if parent:\n"
            " kernel32.SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)\n"
            " kernel32.WaitForSingleObject(parent,0xffffffff)\n"
            " kernel32.SetThreadExecutionState(ES_CONTINUOUS)\n"
            " kernel32.CloseHandle(parent)\n"
        )
        return "SetThreadExecutionState", [sys.executable, "-c", script, str(pid)]

    return None, None


class _LeaseLock:
    """Single inhibitor owner across duplicate monitor processes."""

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.handle = None

    def acquire(self) -> bool:
        if self.handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self.handle.close()
        self.handle = None


class KeepAwakeLease:
    def __init__(
        self,
        enabled: bool,
        lease_seconds: float,
        logger,
        lock_path: str = _DEFAULT_LOCK_PATH,
        clock=None,
        popen=None,
        lock=None,
    ):
        self.enabled = bool(enabled) and float(lease_seconds) > 0
        self.lease_seconds = float(lease_seconds)
        self.log = logger
        self.clock = clock or time.monotonic
        self.popen = popen or subprocess.Popen
        self.lock = lock or _LeaseLock(lock_path)
        self.process = None
        self.deadline = 0.0
        self._unavailable_logged = False
        self._locked_logged = False
        self._next_start_at = 0.0

        if self.enabled:
            self.backend_name, self.command = _backend_command()
        else:
            self.backend_name, self.command = "disabled", None
        if self.enabled and not self.command:
            self.backend_name = "unavailable"

    def renew(self) -> None:
        if not self.enabled:
            return
        self.deadline = self.clock() + self.lease_seconds
        self._ensure_running()

    def tick(self) -> None:
        if not self.enabled:
            return
        now = self.clock()
        if self.deadline and now >= self.deadline:
            self.deadline = 0.0
            self._stop("after inactivity")
        elif self.deadline > now:
            self._ensure_running()

    def close(self) -> None:
        self.deadline = 0.0
        self._stop("on shutdown")

    def _ensure_running(self) -> None:
        if not self.command:
            if not self._unavailable_logged:
                self.log("keep-awake unavailable: no supported OS inhibitor found")
                self._unavailable_logged = True
            return
        if self.process is not None:
            if self.process.poll() is None:
                return
            self.process = None
            self.lock.release()
            self._next_start_at = self.clock() + _START_RETRY_SECONDS
        if self.clock() < self._next_start_at:
            return
        if not self.lock.acquire():
            if not self._locked_logged:
                self.log("keep-awake lease owned by another monitor process")
                self._locked_logged = True
            return
        self._locked_logged = False
        try:
            self.process = self.popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
            )
        except Exception as e:  # noqa: BLE001
            self.lock.release()
            self._next_start_at = self.clock() + _START_RETRY_SECONDS
            self.log(f"keep-awake start failed ({self.backend_name}): {e}")
            return
        self.log(
            f"keep-awake acquired via {self.backend_name} "
            f"(lease={self.lease_seconds:g}s)"
        )

    def _stop(self, reason: str) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=2)
            self.log(f"keep-awake released {reason}")
        self.lock.release()
