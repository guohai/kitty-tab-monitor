"""Poll kitty windows and answer paused decision prompts in place."""
from __future__ import annotations

import json
import os
import re
import time

from . import __build_date__, __version__
from .detector import (
    StabilityTracker,
    _tail,
    looks_like_auto_mode_rate_limit,
    looks_like_decision,
    looks_like_password,
    signature,
)
from .kitty_rc import KittyRC, iter_windows
from .keep_awake import (
    KeepAwakeLease,
    has_running_task,
    remote_connection_unresponsive,
)
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
        self.awake = KeepAwakeLease(
            cfg.keep_awake,
            cfg.keep_awake_lease_seconds,
            logger,
        )
        self.auto_mode_failures = {}
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

    def _target_still_matches(self, wid: int, expected_sig: str) -> bool:
        """Ensure an LLM response still applies to the exact target window."""
        try:
            current = self.rc.get_text(wid, extent="screen")
        except Exception as e:  # noqa: BLE001 - a missing target must never receive input
            self.log(f"[win {wid}] pre-send screen read error -> not sending: {e}")
            return False
        if not current.strip():
            self.log(f"[win {wid}] pre-send screen read returned empty -> not sending")
            return False
        if signature(current, self.cfg.capture_lines) != expected_sig:
            details = self._log_details(
                _tail(current, 8),
                "none",
                "target screen changed before send",
            )
            self.log(f"[win {wid}] target changed -> not sending :: {details}")
            return False
        return True

    # --- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        self.log(f"starting: version={__version__} build_date={__build_date__} "
                 f"model={self.cfg.model} dry_run={self.cfg.dry_run} "
                 f"poll={self.cfg.poll_interval}s "
                 f"socket={self.cfg.kitty_socket or '(auto-discover)'} "
                 f"auto_fallback={self.cfg.auto_mode_fallback_seconds:g}s "
                 f"keep_awake={self.awake.backend_name} "
                 f"lease={self.cfg.keep_awake_lease_seconds:g}s")
        try:
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - keep the loop alive
                    self.log(f"tick error: {e}")
                time.sleep(self.cfg.poll_interval)
        finally:
            self.awake.close()

    def tick(self) -> None:
        self.awake.tick()
        for w in iter_windows(self.rc.ls()):
            wid = w["window_id"]
            if wid is None or wid == self.self_window_id:
                continue
            title = f"{w['tab_title']} {w['window_title']}".strip()
            if not self._title_ok(title):
                continue

            try:
                text = self.rc.get_text(wid, extent="screen")
            except Exception as e:  # noqa: BLE001 - retry this window next poll
                self.log(f"[win {wid}] screen read error -> retrying: {e}")
                continue
            if not text.strip():
                self.log(f"[win {wid}] screen read returned empty -> retrying")
                continue

            previous = self.tracker.states.get(wid)
            previous_sig = previous.last_sig if previous else ""
            st, sig = self.tracker.update(wid, text)
            remote_down = remote_connection_unresponsive(w, text)
            if not remote_down and (
                not previous_sig or sig != previous_sig or has_running_task(w, text)
            ):
                self.awake.renew()

            if self._recover_from_auto_mode_rate_limit(w, text):
                continue

            if not self.tracker.is_paused(st):
                continue
            if sig == st.last_handled_sig:
                continue  # already dealt with this exact screen
            if (st.last_action_ts and
                    time.monotonic() - st.last_action_ts < self.cfg.action_cooldown):
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

    def _recover_from_auto_mode_rate_limit(self, w, text: str) -> bool:
        wid = w["window_id"]
        if not looks_like_auto_mode_rate_limit(text):
            self.auto_mode_failures.pop(wid, None)
            return False

        now = time.monotonic()
        state = self.auto_mode_failures.setdefault(
            wid, {"first_seen": now, "handled": False}
        )
        if state["handled"]:
            return True
        if now - state["first_seen"] < self.cfg.auto_mode_fallback_seconds:
            return True

        details = self._log_details(
            _tail(text, 12),
            "Shift+Tab",
            "auto mode rate-limited for two minutes",
        )
        if self.cfg.dry_run:
            state["handled"] = True
            self.log(
                f"[win {wid}] DRY-RUN: would send Shift+Tab without changing focus "
                f":: {details}"
            )
            return True

        expected_sig = signature(text, self.cfg.capture_lines)
        if not self._target_still_matches(wid, expected_sig):
            return True
        ok = self.rc.send_key(wid, "shift+tab")
        if ok:
            state["handled"] = True
            self.awake.renew()
        self.log(
            f"[win {wid}] {'SENT' if ok else 'SEND FAILED'} Shift+Tab "
            f"(focus_changed=False) :: {details}"
        )
        return True

    # --- decision + action -------------------------------------------------
    def _handle(self, w, st, sig, text, title) -> None:
        wid, tab_id = w["window_id"], w["tab_id"]

        # Defer new decisions while the action budget is exhausted. Leaving the
        # signature unhandled makes the prompt eligible again when the budget resets.
        if not self.guard.rate_ok():
            return

        tail = _tail(text, self.cfg.capture_lines)

        decision, err = decide(
            self.cfg,
            title,
            tail,
            w.get("workspace", ""),
            w.get("cwd", ""),
            w.get("session_type", "local"),
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
            self.log(f"[win {wid}] DRY-RUN: would send without changing focus "
                     f"{payload!r} (conf={conf:.2f}) :: {details}")
            return

        if not self._target_still_matches(wid, sig):
            return
        ok = self.rc.send_text(wid, payload)
        if ok:
            st.last_handled_sig = sig
            self.guard.record_action()
            st.last_action_ts = time.monotonic()
            self.awake.renew()
        self.log(f"[win {wid}] {'SENT' if ok else 'SEND FAILED'} {payload!r} "
                 f"(conf={conf:.2f}, focus_changed=False) :: {details}")
