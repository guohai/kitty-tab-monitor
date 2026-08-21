"""LLM decision via an OpenAI-compatible chat endpoint (stdlib urllib, no deps).

Streams the response (some gateways, e.g. Codex proxies, require stream=true) and
reassembles it. Returns a decision dict:
  {is_waiting: bool, action: "type"|"none", text_to_send: str,
   press_enter: bool, confidence: float, reason: str}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Fallbacks used only when config.json supplies no system_prompt / user_prompt_template.
DEFAULT_SYSTEM_PROMPT = """You are an automation controller for the kitty terminal.
You are shown the last visible lines of ONE terminal tab that has gone quiet and \
may be waiting for the user to make a decision (a numbered menu, a yes/no prompt, \
"continue?", or a question).

Your job is to keep the task moving: when the tab is safely blocked awaiting a \
keystroke, answer with the single short choice that lets it proceed.

Respond with ONLY a JSON object, no prose and no code fences, with these fields:
  "is_waiting": boolean - true only if the tab is clearly blocked awaiting a choice.
  "action": "type" or "none".
  "text_to_send": string - the exact characters to type, usually one menu number \
like "1" or a letter like "y". Empty string when action is "none".
  "press_enter": boolean - whether to press Enter after the text.
  "confidence": number between 0 and 1.
  "reason": short string.

Guidelines:
- If the screen is still producing output, shows an error trace, or is just a shell \
prompt with no question, set is_waiting=false and action="none".
- Pick the option that lets a routine task continue (e.g. "Yes", "1", "y") when it is \
clearly safe and reversible, so the job keeps going without a human.
- If the choice is DANGEROUS, destructive, or irreversible (deleting data, force push, \
overwriting files, dropping a database, formatting a disk, rm -rf, resetting state, \
spending money, sending messages), do NOT answer: set action="none" and is_waiting=true \
and explain in "reason" that it needs human review. Never auto-approve a dangerous \
command, and do not guess "No" on its behalf either — leave it for a human.
- Never output long shell commands. text_to_send is a menu selection or a single short word.
"""

DEFAULT_USER_TEMPLATE = (
    "Tab title: {tab_title}\n\n"
    "Last visible lines of this paused tab:\n---\n{screen_text}\n---\n"
    "Return the JSON decision."
)


def _headers(cfg) -> dict:
    return {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
        # Gateways behind Cloudflare block the default urllib UA (403 error 1010).
        "User-Agent": _UA,
        "Accept": "text/event-stream",
    }


def _post_stream(cfg, body: dict):
    """POST a streaming chat request; return (assembled_text, error)."""
    req = urllib.request.Request(
        cfg.openai_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=_headers(cfg),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            parts: list[str] = []
            for raw in resp:                       # iterate SSE lines as they arrive
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                piece = (choice.get("delta") or {}).get("content")
                if piece is None:                  # non-streaming-shaped chunk
                    piece = (choice.get("message") or {}).get("content")
                if piece:
                    parts.append(piece)
            return "".join(parts), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, f"request failed: {e}"


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip()
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


def decide(cfg, tab_title: str, screen_text: str):
    if not cfg.openai_api_key:
        return None, "no OPENAI_API_KEY set"

    system = (cfg.system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    template = (cfg.user_prompt_template or "").strip() or DEFAULT_USER_TEMPLATE
    # Token replacement (not str.format) so braces in terminal content can't break it.
    user = template.replace("{tab_title}", tab_title).replace("{screen_text}", screen_text)
    body = {
        "model": cfg.model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    # Prefer strict JSON mode; if the gateway rejects response_format, retry without it.
    text, err = _post_stream(cfg, {**body, "response_format": {"type": "json_object"}})
    if err and ("response_format" in err or "json" in err.lower()):
        text, err = _post_stream(cfg, body)
    if err:
        return None, err

    decision = _extract_json(text or "")
    if decision is None:
        return None, f"could not parse JSON from response: {text[:200]!r}"
    return decision, None
