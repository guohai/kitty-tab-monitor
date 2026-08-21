# kitty-tab-monitor

Watches every kitty tab, detects the ones that have **gone quiet while waiting for a
decision** (a numbered menu, `y/n`, `continue?`, a trailing `?`), sends the last lines
to an LLM, and then **selects that tab and types the LLM's answer + Enter** — fully
automatically.

Built for the case where you have several agents/CLIs running across tabs (e.g. Claude
Code permission prompts, `apt` `[Y/n]`, interactive installers) and you don't want to
babysit them.

```
❯ 1. Yes            <-  tab goes idle showing this
  2. No                 monitor detects "paused + decision", asks the LLM,
                        focuses the tab, types "1<Enter>"
```

## How it works

1. **Poll** — every `poll_interval`s, read each window's screen via `kitty @ get-text`.
2. **Pause detection** — hash the last `capture_lines`; if unchanged for `stable_polls`
   passes, the tab is considered idle (output has stopped).
3. **Heuristic pre-filter** — only idle screens that *look* like a prompt reach the LLM
   (keeps token spend down). Toggle with `require_heuristic`.
4. **LLM decision** — the last lines go to OpenAI, which returns JSON:
   `{is_waiting, action, text_to_send, press_enter, confidence, reason}`.
5. **Act** — `kitty @ focus-tab` then `kitty @ send-text` types the answer (+ Enter).

## Requirements

- kitty with remote control enabled (see `install.sh`)
- Python 3.8+ (no third-party packages — uses only the stdlib)
- An `OPENAI_API_KEY`

## Install

```bash
./install.sh                 # enables allow_remote_control + listen_on in kitty.conf
# quit & reopen kitty
cp .env.example .env         # then edit .env: OPENAI_API_KEY, OPENAI_BASE_URL
kitty @ set-tab-title tab-monitor   # in the tab you'll run the monitor from
./run.sh --dry-run           # observe decisions without typing anything
./run.sh                      # go live
```

### Credentials via `.env`

Put your key and base URL in a `.env` file **next to `config.json`**:

```
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
```

The loader reads `.env` automatically. Real shell environment variables (if set)
override the file, and `.env` is git-ignored. `OPENAI_BASE_URL` lets you point at any
OpenAI-compatible gateway.

Requests are **streamed** (the client reassembles the SSE chunks) because some gateways
— including Codex-style proxies — reject non-streaming calls with `stream must be true`.
This is transparent and also works against stock OpenAI. A browser-like `User-Agent` is
sent so Cloudflare-fronted gateways don't return `403 error 1010`.

Run it from **inside a kitty window** so it inherits `KITTY_LISTEN_ON` automatically.
Otherwise set `kitty_socket` in `config.json` (or `KTM_SOCKET`) to your `listen_on` value.

## Configuration (`config.json`)

| key | meaning |
|-----|---------|
| `model` | model name your endpoint accepts (this setup uses `gpt-5.5`) |
| `poll_interval` | seconds between passes |
| `stable_polls` | identical screens in a row before a tab counts as "paused" |
| `capture_lines` | how many trailing lines are sent to the LLM |
| `action_cooldown` | min seconds between actions on the same tab |
| `max_actions_per_min` | global rate limit |
| `min_confidence` | ignore LLM decisions below this |
| `dry_run` | log what it would do, type nothing |
| `require_heuristic` | only call the LLM on screens that look like prompts |
| `skip_password_prompts` | never answer password/secret prompts |
| `send_denylist` | regexes; refuse to type matching text |
| `window_title_include` | only watch tabs whose title matches (empty = all) |
| `window_title_exclude` | never touch matching tabs (default: `tab-monitor`) |

Env overrides: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `KTM_MODEL`, `KTM_SOCKET`, `KTM_DRY_RUN`.

## ⚠️ Safety

This types into live shells **without confirmation** and will auto-answer prompts —
**including safety prompts meant for a human** (like Claude Code's own approval gates).
That is powerful and risky. Mitigations built in, all configurable:

- The LLM is instructed to pick the **safe/decline** option for destructive/irreversible
  actions (`rm -rf`, force-push, dropping a DB, formatting, etc.).
- `send_denylist` blocks typing dangerous strings regardless of the LLM.
- `skip_password_prompts` avoids secret prompts.
- `window_title_include` lets you **scope it to specific tabs** (strongly recommended —
  e.g. only tabs titled `agent`).
- `dry_run` / `--dry-run` and `min_confidence` for cautious rollout.
- Per-tab cooldown + global rate limit prevent runaway keystroke loops.

Start with `--dry-run` and a narrow `window_title_include` before going fully automatic.

## Uninstall

Remove the `allow_remote_control` / `listen_on` lines from `kitty.conf` (a timestamped
backup was saved by `install.sh`), and stop the process.
