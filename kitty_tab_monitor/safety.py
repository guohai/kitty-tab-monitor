"""Guardrails applied to whatever the LLM wants to type, plus a global rate limit."""
from __future__ import annotations

import re
import time
from collections import deque


class Guard:
    def __init__(self, cfg):
        self.cfg = cfg
        self.deny = [re.compile(p, re.I) for p in cfg.send_denylist]
        self._actions: deque[float] = deque()

    def send_allowed(self, text: str):
        if len(text) > self.cfg.max_send_len:
            return False, f"text too long ({len(text)} > {self.cfg.max_send_len})"
        if text and not text.isprintable():
            return False, "text contains control characters"
        for pat in self.deny:
            if pat.search(text):
                return False, f"matches denylist /{pat.pattern}/"
        return True, ""

    def rate_ok(self) -> bool:
        now = time.monotonic()
        while self._actions and now - self._actions[0] > 60:
            self._actions.popleft()
        return len(self._actions) < self.cfg.max_actions_per_min

    def record_action(self) -> None:
        self._actions.append(time.monotonic())
