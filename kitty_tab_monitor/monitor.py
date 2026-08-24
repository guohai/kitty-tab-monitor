"""Main loop: poll windows -> find paused+awaiting-decision ones -> ask LLM ->
select the tab and type the answer."""
from __future__ import annotations

import json
import os
import re
import time

from .detector import StabilityTracker, _tail, looks_like_decision, looks_like_password
from .kitty_rc import KittyRC, iter_windows
from .llm import decide
from .safety import Guard


def _env_int(name: str):
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return None


class Monitor:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.log = logger
        self.rc = KittyRC(socket=cfg.kitty_socket, password=cfg.kitty_rc_password)
        self.tracker = StabilityTracker(cfg.stable_polls, cfg.capture_lines)
        self.guard = Guard(cfg)
        self.include = [re.compile(p, re.I) for p in cfg.window_title_include]
        self.exclude = [re.compile(p, re.I) for p in cfg.window_title_exclude]
        # If launched inside kitty, never act on our own window.
        self.self_window_id = _env_int("KITTY_WINDOW_ID")

    def _title_ok(self, title: str) -> bool:
        if self.include and not any(p.search(title) for p in self.include):
            return False
        if any(p.search(title) for p in self.exclude):
            return False
        return True

    @staticmethod
    def _log_details(context: str, action: str, reason: str) -> str:
        context = " ".join(str(context).split()) or "terminal decision"
        reason = " ".join(str(reason).split())
        return json.dumps(
            {"context": context, "action": action, "reason": reason},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _screen_context(text: str) -> str:
        """Extract the exact command block used by agent approval prompts."""
        lines = text.splitlines()
        start = next(
            (
                i + 1
                for i, line in enumerate(lines)
                if re.match(r"^\s*(?:Bash|Shell) command\b", line, re.I)
            ),
            None,
        )
        if start is None:
            return " ".join(text.split()) or "terminal decision"

        end = next(
            (
                i
                for i in range(start, len(lines))
                if re.match(r"^\s*Run shell command\s*$", lines[i], re.I)
            ),
            None,
        )
        if end is None:
            return " ".join(text.split()) or "terminal decision"

        command_lines = []
        for line in lines[start:end]:
            line = re.sub(r"^\s*│\s?", "", line.rstrip())
            if line.strip():
                command_lines.append(line.strip())
        return " ".join(command_lines) or " ".join(text.split()) or "terminal decision"

    @staticmethod
    def _decision_action(decision) -> str:
        if decision.get("action") != "type":
            return "none"
        text = decision.get("text_to_send", "")
        if decision.get("press_enter", True):
            return f"{text} + Enter" if text else "Enter"
        return text or "none"

    # --- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        self.log(f"starting: model={self.cfg.model} dry_run={self.cfg.dry_run} "
                 f"poll={self.cfg.poll_interval}s "
                 f"socket={self.cfg.kitty_socket or '(auto-discover)'}")
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                self.log(f"tick error: {e}")
            time.sleep(self.cfg.poll_interval)

    def tick(self) -> None:
        for w in iter_windows(self.rc.ls()):
            wid = w["window_id"]
            if wid is None or wid == self.self_window_id:
                continue
            title = f"{w['tab_title']} {w['window_title']}".strip()
            if not self._title_ok(title):
                continue

            text = self.rc.get_text(wid, extent="screen")
            st, sig = self.tracker.update(wid, text)

            if not self.tracker.is_paused(st):
                continue
            if sig == st.last_handled_sig:
                continue  # already dealt with this exact screen
            if time.monotonic() - st.last_action_ts < self.cfg.action_cooldown:
                continue
            if self.cfg.skip_password_prompts and looks_like_password(text):
                self.log(
                    f"[win {wid}] paused, looks like a password prompt -> skipping "
                    f"{self._log_details('password prompt', 'skip', 'password prompt')}"
                )
                st.last_handled_sig = sig
                continue
            if self.cfg.require_heuristic:
                ok, _kind = looks_like_decision(text)
                if not ok:
                    continue

            self._handle(w, st, sig, text, title)

    # --- decision + action -------------------------------------------------
    def _handle(self, w, st, sig, text, title) -> None:
        wid, tab_id = w["window_id"], w["tab_id"]

        # Defer new decisions while the action budget is exhausted. Leaving the
        # signature unhandled makes the prompt eligible again when the budget resets.
        if not self.guard.rate_ok():
            return

        tail = _tail(text, self.cfg.capture_lines)

        decision, err = decide(
            self.cfg, title, tail, w.get("workspace", ""), w.get("cwd", "")
        )
        if err:
            details = self._log_details(title, "error", err)
            self.log(f"[win {wid}] LLM error {details}")
            return

        reason = decision.get("reason", "")
        context = self._screen_context(tail)
        details = self._log_details(context, self._decision_action(decision), reason)
        if decision.get("action") != "type":
            st.last_handled_sig = sig
            if decision.get("is_waiting"):
                # Blocked on a decision the LLM won't make on its own (e.g. a
                # dangerous / irreversible action) -> hold it for a human.
                self.log(
                    f"[win {wid}] ⚠ HELD FOR HUMAN REVIEW (tab {tab_id}) :: "
                    f"{details}"
                )
            else:
                self.log(f"[win {wid}] not acting :: {details}")
            return

        conf = float(decision.get("confidence", 0) or 0)
        if conf < self.cfg.min_confidence:
            st.last_handled_sig = sig
            self.log(
                f"[win {wid}] confidence {conf:.2f} < {self.cfg.min_confidence} "
                f"-> skip :: {details}"
            )
            return

        send = str(decision.get("text_to_send", ""))
        allowed, why = self.guard.send_allowed(send)
        if not allowed:
            st.last_handled_sig = sig
            self.log(
                f"[win {wid}] BLOCKED by guard: {why} (proposed={send!r}) :: {details}"
            )
            return

        payload = send + ("\r" if decision.get("press_enter", True) else "")

        if self.cfg.dry_run:
            st.last_handled_sig = sig
            self.log(f"[win {wid}] DRY-RUN: would select tab {tab_id} and send "
                     f"{payload!r} (conf={conf:.2f}) :: {details}")
            return

        focused = self.rc.focus_tab(tab_id)       # select the tab
        ok = self.rc.send_text(wid, payload)      # type answer (+ Enter)
        if ok:
            st.last_handled_sig = sig
            self.guard.record_action()
            st.last_action_ts = time.monotonic()
        self.log(f"[win {wid}] {'SENT' if ok else 'SEND FAILED'} {payload!r} "
                 f"(conf={conf:.2f}, focused={focused}) :: {details}")
