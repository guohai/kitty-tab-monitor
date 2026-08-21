"""Main loop: poll windows -> find paused+awaiting-decision ones -> ask LLM ->
select the tab and type the answer."""
from __future__ import annotations

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
        self.rc = KittyRC(socket=cfg.kitty_socket)
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

    # --- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        self.log(f"starting: model={self.cfg.model} dry_run={self.cfg.dry_run} "
                 f"poll={self.cfg.poll_interval}s socket={self.cfg.kitty_socket or '(inherited)'}")
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
                self.log(f"[win {wid}] paused, looks like a password prompt -> skipping")
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
        tail = _tail(text, self.cfg.capture_lines)

        decision, err = decide(self.cfg, title, tail)
        if err:
            self.log(f"[win {wid}] LLM error: {err}")
            return

        # Mark handled now: even a 'do nothing' verdict shouldn't re-fire on the
        # same unchanged screen next poll.
        st.last_handled_sig = sig

        reason = decision.get("reason", "")
        if decision.get("action") != "type":
            if decision.get("is_waiting"):
                # Blocked on a decision the LLM won't make on its own (e.g. a
                # dangerous / irreversible action) -> hold it for a human.
                self.log(f"[win {wid}] ⚠ HELD FOR HUMAN REVIEW (tab {tab_id}) :: {reason}")
            else:
                self.log(f"[win {wid}] not acting :: {reason}")
            return

        conf = float(decision.get("confidence", 0) or 0)
        if conf < self.cfg.min_confidence:
            self.log(f"[win {wid}] confidence {conf:.2f} < {self.cfg.min_confidence} -> skip")
            return

        send = str(decision.get("text_to_send", ""))
        allowed, why = self.guard.send_allowed(send)
        if not allowed:
            self.log(f"[win {wid}] BLOCKED by guard: {why} (proposed={send!r})")
            return
        if not self.guard.rate_ok():
            self.log(f"[win {wid}] rate limit ({self.cfg.max_actions_per_min}/min) -> skip")
            return

        payload = send + ("\r" if decision.get("press_enter", True) else "")

        if self.cfg.dry_run:
            self.log(f"[win {wid}] DRY-RUN: would select tab {tab_id} and send "
                     f"{payload!r} (conf={conf:.2f}) :: {decision.get('reason', '')}")
            return

        self.rc.focus_tab(tab_id)                 # select the tab
        ok = self.rc.send_text(wid, payload)      # type answer (+ Enter)
        self.guard.record_action()
        st.last_action_ts = time.monotonic()
        self.log(f"[win {wid}] {'SENT' if ok else 'SEND FAILED'} {payload!r} "
                 f"(conf={conf:.2f}) :: {decision.get('reason', '')}")
