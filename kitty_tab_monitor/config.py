"""Configuration: defaults, JSON file overlay, and environment overrides."""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_dotenv(path: Path) -> dict:
    """Minimal .env parser: KEY=value, optional `export`, # comments, quotes."""
    result: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        result[key] = val
    return result


@dataclass
class Config:
    # --- LLM (OpenAI-compatible) ---
    openai_api_key: str = ""                    # usually from OPENAI_API_KEY env
    openai_base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"                  # cheap + fast; good for frequent polling

    # --- polling / pause detection ---
    poll_interval: float = 1.0                  # seconds between passes
    stable_polls: int = 2                       # identical screens in a row => "paused"
    capture_lines: int = 40                     # last N lines sent to the LLM
    auto_mode_fallback_seconds: float = 120.0    # switch to manual after classifier outage

    # --- system sleep lease ---
    keep_awake: bool = False                    # opt in to preventing idle system sleep
    keep_awake_lease_seconds: float = 600.0     # release after this much inactivity

    # --- acting ---
    action_cooldown: float = 8.0                # min seconds between actions on one tab
    max_actions_per_min: int = 6                # global rate limit
    min_confidence: float = 0.55                # ignore LLM decisions below this
    dry_run: bool = False                       # log what it *would* do, type nothing

    # --- gating / safety ---
    require_heuristic: bool = True              # only call LLM when a screen looks like a prompt
    skip_password_prompts: bool = True          # never answer password/secret prompts
    max_send_len: int = 120                     # refuse to type anything longer
    send_denylist: list[str] = field(default_factory=lambda: [
        r"rm\s+-rf", r"mkfs", r"dd\s+if=", r"drop\s+table",
        r"git\s+push\b.*--force", r"shutdown", r"reboot", r":\(\)\s*\{", r">\s*/dev/sd",
    ])
    window_title_include: list[str] = field(default_factory=list)   # empty = all tabs
    window_title_exclude: list[str] = field(default_factory=lambda: ["tab-monitor"])

    # --- plumbing ---
    kitty_socket: str = ""                      # e.g. unix:/tmp/kitty-1234; "" = inherit KITTY_LISTEN_ON
    kitty_rc_password: str = ""                 # usually from KITTY_RC_PASSWORD env
    log_file: str = "~/.local/share/kitty-tab-monitor/monitor.log"

    # --- prompts (may be given as a list of lines in config.json; joined on load) ---
    system_prompt: str = ""                     # "" => llm.py DEFAULT_SYSTEM_PROMPT
    # Default template tokens: {tab_title}, {workspace}, {cwd}, and {screen_text}.
    user_prompt_template: str = ""

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        data: dict = {}
        cfg_path = Path(path).expanduser() if path else None
        if cfg_path and cfg_path.exists():
            data = json.loads(cfg_path.read_text() or "{}")
        known = {f.name for f in dataclasses.fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in known})

        # Prompts are authored as line arrays in config.json for readability; join them.
        if isinstance(cfg.system_prompt, list):
            cfg.system_prompt = "\n".join(cfg.system_prompt)
        if isinstance(cfg.user_prompt_template, list):
            cfg.user_prompt_template = "\n".join(cfg.user_prompt_template)

        # Overlay a sibling .env file (next to config.json); a real shell
        # environment variable still wins over the .env file.
        dotenv = _parse_dotenv(cfg_path.parent / ".env") if cfg_path else {}
        env = {**dotenv, **os.environ}

        cfg.openai_api_key = env.get("OPENAI_API_KEY", cfg.openai_api_key)
        cfg.openai_base_url = env.get("OPENAI_BASE_URL", cfg.openai_base_url)
        cfg.model = env.get("KTM_MODEL", cfg.model)
        cfg.kitty_socket = env.get("KTM_SOCKET", env.get("KITTY_LISTEN_ON", cfg.kitty_socket))
        cfg.kitty_rc_password = env.get("KITTY_RC_PASSWORD", cfg.kitty_rc_password)
        if "KTM_KEEP_AWAKE" in env:
            cfg.keep_awake = str(env["KTM_KEEP_AWAKE"]).strip().lower() in (
                "1", "true", "yes", "on"
            )
        if "KTM_KEEP_AWAKE_LEASE_SECONDS" in env:
            try:
                cfg.keep_awake_lease_seconds = float(
                    env["KTM_KEEP_AWAKE_LEASE_SECONDS"]
                )
            except ValueError:
                pass
        if "KTM_AUTO_MODE_FALLBACK_SECONDS" in env:
            try:
                cfg.auto_mode_fallback_seconds = float(
                    env["KTM_AUTO_MODE_FALLBACK_SECONDS"]
                )
            except ValueError:
                pass
        if "KTM_DRY_RUN" in env:
            cfg.dry_run = str(env["KTM_DRY_RUN"]).strip().lower() in ("1", "true", "yes", "on")
        return cfg

    def log_path(self) -> Path:
        p = Path(self.log_file).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
