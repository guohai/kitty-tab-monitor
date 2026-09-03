"""Pause detection + cheap heuristics for 'this tab is awaiting a decision'.

The heuristic is a pre-filter that keeps us from calling the LLM on every
stable screen. The LLM makes the actual decision.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Selector glyphs seen in TUI menus (Claude Code, gum, fzf, inquirer, ...).
_SEL = r">\*❯›▶→➤"

_DECISION_PATTERNS = [
    re.compile(rf"(?im)^\s*[{_SEL}]?\s*\(?\d+[\.\)]\s+\S"),          # "❯ 1. Yes", "2) No", "(3) Skip"
    re.compile(r"(?i)\b(y\s*/\s*n|n\s*/\s*y|yes\s*/\s*no)\b"),        # y/n, yes/no
    re.compile(r"(?i)\[\s*[yn]\s*/\s*[yn]\s*\]"),                     # [Y/n] [y/N]
    re.compile(r"(?i)\b(do you want to|are you sure|proceed|continue|"
               r"overwrite|replace|confirm|select an option|choose one|"
               r"pick an option|which one)\b"),
    re.compile(r"(?m)\?\s*$"),                                        # any line ending in '?'
    re.compile(r"(?i)press\s+(enter|return|any key)\b"),
    re.compile(r"(?i)(--\s*more\s*--|\(END\)|\(q to quit\)|:\s*$)"),  # pagers (less/more)
]

_PASSWORD_PATTERNS = [
    re.compile(r"(?i)(password|passphrase|pin|otp|2fa|verification code|secret|token)\s*[:?]\s*$"),
    re.compile(r"(?i)\benter\b.*\b(password|passphrase|pin)\b"),
]

_AUTO_MODE_RATE_LIMIT = re.compile(
    r"(?is)temporarily unavailable\s*\(rate-limited\).*"
    r"auto mode cannot determine the safety"
)

_TMUX_STATUS_LINE = re.compile(
    r"^\[[^\]\r\n]+\]\d+:\S.*\b\d{1,2}:\d{2}"
    r"(?:\s+\d{1,2}-[A-Za-z]{3}-\d{2,4})?\s*$"
)

# Claude keeps these counters moving while a subagent approval menu is waiting.
# Normalize only elapsed values followed by its activity separator so ordinary
# command output containing durations still participates in stability checks.
_CLAUDE_ACTIVITY_ELAPSED = re.compile(
    r"(?<![\w.])(?:(?:\d+\s*h\s*)?(?:\d+\s*m\s*)?\d+\s*s)(?=\s*·)"
)
_CLAUDE_ACTIVITY_TOKENS = re.compile(
    r"(?P<marker>·\s*[↓↑]\s*)\d+(?:\.\d+)?[kKmM]?\s+tokens\b"
)


def _tail(text: str, n: int) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1].strip():   # drop the blank area below a prompt
        lines.pop()
    return "\n".join(lines[-n:])


def _stable_tail(text: str, n: int) -> str:
    """Remove volatile terminal UI details before hashing terminal content."""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _TMUX_STATUS_LINE.match(lines[-1]):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    normalized = []
    for line in lines[-n:]:
        line = _CLAUDE_ACTIVITY_ELAPSED.sub("<elapsed>", line)
        line = _CLAUDE_ACTIVITY_TOKENS.sub(r"\g<marker><tokens>", line)
        normalized.append(line)
    return "\n".join(normalized)


def signature(text: str, n: int) -> str:
    return hashlib.sha1(_stable_tail(text, n).encode()).hexdigest()


def looks_like_decision(text: str, n: int = 15):
    tail = _stable_tail(text, n)
    if not tail.strip():
        return False, None
    for pat in _DECISION_PATTERNS:
        if pat.search(tail):
            return True, pat.pattern[:30]
    return False, None


def looks_like_password(text: str, n: int = 6) -> bool:
    tail = _stable_tail(text, n)
    return any(p.search(tail) for p in _PASSWORD_PATTERNS)


def looks_like_auto_mode_rate_limit(text: str, n: int = 20) -> bool:
    return bool(_AUTO_MODE_RATE_LIMIT.search(_stable_tail(text, n)))


@dataclass
class WindowState:
    last_sig: str = ""
    stable_count: int = 0
    last_handled_sig: str = ""
    last_action_ts: float = 0.0


class StabilityTracker:
    """Tracks how long each window's tail has stayed unchanged."""

    def __init__(self, stable_polls: int, capture_lines: int):
        self.stable_polls = stable_polls
        self.capture_lines = capture_lines
        self.states: dict[int, WindowState] = {}

    def update(self, window_id: int, text: str):
        st = self.states.setdefault(window_id, WindowState())
        sig = signature(text, self.capture_lines)
        if sig == st.last_sig:
            st.stable_count += 1
        else:
            st.last_sig = sig
            st.stable_count = 1
        return st, sig

    def is_paused(self, st: WindowState) -> bool:
        return st.stable_count >= self.stable_polls
