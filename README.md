# kitty-tab-monitor

Watches every kitty tab, detects the ones that have **gone quiet while waiting for a
decision** (a numbered menu, `y/n`, `continue?`, a trailing `?`), sends the last lines
to an LLM, and then **types the LLM's answer + Enter directly into that window without
changing your focused tab** — fully automatically.

Built for the case where you have several agents/CLIs running across tabs (e.g. Claude
Code permission prompts, `apt` `[Y/n]`, interactive installers) and you don't want to
babysit them.

```
❯ 1. Yes            <-  tab goes idle showing this
  2. No                 monitor detects "paused + decision", asks the LLM,
                        types "1<Enter>" without changing focus
```

## How it works

1. **Poll** — every `poll_interval`s, read each window's screen via `kitty @ get-text`.
2. **Pause detection** — hash the last `capture_lines`; if unchanged for `stable_polls`
   passes, the tab is considered idle (output has stopped). The tmux status line and the
   blank padding above it are excluded from stability and prompt detection.
3. **Heuristic pre-filter** — only idle screens that *look* like a prompt reach the LLM
   (keeps token spend down). Toggle with `require_heuristic`.
4. **LLM decision** — the last lines, current directory, and workspace go to an
   OpenAI-compatible endpoint, which returns JSON:
   `{is_waiting, action, text_to_send, press_enter, confidence, reason}`.
5. **Act** — `kitty @ send-text --match id:<window>` types the answer (+ Enter) directly
   into the target window without changing the active OS window, kitty tab, or pane. The
   monitor first re-reads that window and aborts if the approval screen has changed since
   the LLM request began.
6. **Keep awake** — activity in any monitored tab renews one shared 10-minute system-sleep
   lease. Stable screens that show a known running task also renew it, even without output.

If auto mode reports that its safety classifier is temporarily rate-limited for two
continuous minutes, the monitor sends `Shift+Tab` once to switch that tab back to manual
mode. It will not send the key again unless the error disappears and later returns.

Keep-awake is disabled by default and can be enabled with `--keep-awake` or in
`config.json`. The lease owns at most one inhibitor process, even if the screen changes
every poll or two monitor instances are started. After all monitored tabs remain inactive
for `keep_awake_lease_seconds`, the inhibitor exits and normal system sleep resumes.
Stopping the monitor releases it immediately.

Silent SSH sessions do not count as running tasks. Changing mosh disconnect indicators,
such as `Last contact ... ago`, and SSH/mosh network-error status also do not renew the
lease. With no activity in another monitored tab, normal sleep is allowed after the lease
expires following the last genuine remote output.

- macOS uses `caffeinate -i`; it prevents idle system sleep but still allows display sleep.
- Linux uses `systemd-inhibit`, falling back to `gnome-session-inhibit`.
- Windows uses the native `SetThreadExecutionState` system-sleep assertion.
- Unsupported systems keep monitoring normally and log that no inhibitor is available.

Window-specific log entries end with JSON containing the exact visible command/context and
the model's chosen action and short rationale, for example
`{"context":"kill 3083188 3083199","action":"1 + Enter","reason":"safe high-PID cleanup"}`.
Context is extracted locally from the screen rather than echoed by the model, saving output
tokens and preserving the original command. Each process startup also logs the package
`version` and `build_date` so the running code can be identified from the log.
Console entries have a blank line between them for easier review; the file remains compact
with exactly one newline per entry.

For safe prompts the model picks the choice that keeps the task moving. File changes and
removals inside the tab's workspace and numeric-PID process cleanup are allowed. Clearly
local Docker test-database resets are also allowed when they are part of a local test/gate
workflow; production, remote, shared, or ambiguous database deletion still requires review.
Workspace-local development environment loading and local environment-value inspection are
also allowed when nothing is transmitted elsewhere. When a safe prompt offers `Yes, and
switch to auto mode`, that choice is preferred over ordinary `Yes`; auto mode never makes
an unsafe current command safe.
Absolute and external paths are not rejected merely for being outside the reported workspace.
Harmless operations such as changing directory, inspecting files, loading development
environment files, setting shell variables, and running tests/builds are approved based on
their effects. For SSH/mosh tabs, the remote workspace is inferred from visible commands
rather than compared with kitty's local launcher directory.
Writes under `~/.claude/jobs/*/tmp/*` count as safe test/job artifacts, and an "always allow"
choice scoped to the exact job `tmp` directory is preferred. Broader `~/.claude` access is
not allowed persistently. A `kill` whose targets are all PID 1000 or above is safe;
small-PID and name-based kills require review. For safe commands, the model prefers a safely
scoped "always allow" or "don't ask again" choice so subsequent work continues without the
same prompt. It falls back to one-time approval when persistent permission is unavailable,
broad, or ambiguous. Approval labels such as `Parse error`, `Contains simple_expansion`, and
`cannot be statically analyzed` are not command failures; the model evaluates the underlying
command and approves it when safe. Other choices that would change or remove files outside
the workspace, reboot/shutdown the host, or perform system-wide destructive operations are
left untouched for human review. The workspace is the nearest Git root above the tab's
current directory, or the current directory when it is not in a repo.

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

Keep-awake behavior can also be overridden for one run:

```bash
./run.sh --keep-awake --keep-awake-lease-seconds 600  # enable for 10 minutes
./run.sh --no-keep-awake  # override an enabled environment or custom config
```

Run `./run.sh --help` for all command-line options.

### Credentials via `.env`

Put your key and base URL in a `.env` file **next to `config.json`**:

```
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
# If kitty.conf uses `allow_remote_control password`:
KITTY_RC_PASSWORD=...
```

The loader reads `.env` automatically. Real shell environment variables (if set)
override the file, and `.env` is git-ignored. `OPENAI_BASE_URL` lets you point at any
OpenAI-compatible gateway.

`KITTY_RC_PASSWORD` is passed to kitty through the child-process environment rather than
the command line. Password mode also requires `KITTY_PUBLIC_KEY`, which kitty injects into
new child processes. The installer's default `socket-only` mode needs neither variable.

Requests are **streamed** (the client reassembles the SSE chunks) because some gateways
— including Codex-style proxies — reject non-streaming calls with `stream must be true`.
This is transparent and also works against stock OpenAI. A browser-like `User-Agent` is
sent so Cloudflare-fronted gateways don't return `403 error 1010`.

Run it from **inside a kitty window** so it can identify and exclude its own window. The
monitor uses `KITTY_LISTEN_ON` when available; otherwise it discovers the current user's
`/tmp/kitty-*` socket. Set `kitty_socket` in `config.json` (or `KTM_SOCKET`) when using a
custom socket path or when more than one kitty instance is running.

## Configuration (`config.json`)

| key | meaning |
|-----|---------|
| `model` | model name your endpoint accepts (this setup uses `gpt-5.5`) |
| `poll_interval` | seconds between passes (default: 2) |
| `stable_polls` | identical screens in a row before a tab counts as "paused" |
| `capture_lines` | how many trailing lines are sent to the LLM |
| `auto_mode_fallback_seconds` | delay before one-time `Shift+Tab` recovery (default: 120) |
| `keep_awake` | renew a system-sleep inhibitor while tabs are active (default: false) |
| `keep_awake_lease_seconds` | inactivity timeout before releasing it (default: 600) |
| `action_cooldown` | min seconds between actions on the same tab |
| `max_actions_per_min` | global rate limit |
| `min_confidence` | ignore LLM decisions below this |
| `dry_run` | log what it would do, type nothing |
| `require_heuristic` | only call the LLM on screens that look like prompts |
| `skip_password_prompts` | never answer password/secret prompts |
| `send_denylist` | regexes; refuse to type matching text |
| `window_title_include` | only watch tabs whose title matches (empty = all) |
| `window_title_exclude` | never touch matching tabs (default: `tab-monitor`) |
| `kitty_socket` | kitty control socket; normally inherited from `KITTY_LISTEN_ON` |

Env overrides: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `KITTY_RC_PASSWORD`, `KTM_MODEL`,
`KTM_SOCKET`, `KTM_AUTO_MODE_FALLBACK_SECONDS`, `KTM_KEEP_AWAKE`,
`KTM_KEEP_AWAKE_LEASE_SECONDS`, `KTM_DRY_RUN`.

## ⚠️ Safety

This types into live shells **without confirmation** and will auto-answer prompts —
**including safety prompts meant for a human** (like Claude Code's own approval gates).
That is powerful and risky. Mitigations built in, all configurable:

- The LLM is instructed to proceed with safe and workspace-local actions. Narrow exceptions
  cover local Docker test cleanup and exact Claude job `tmp` artifacts; other out-of-workspace
  file changes/removals and system reboot/shutdown actions require human review.
- `send_denylist` blocks typing dangerous strings regardless of the LLM.
- Control characters and embedded newlines are rejected before text reaches kitty.
- Failed or empty kitty screen reads are logged and retried without discarding the last valid
  stability state.
- `skip_password_prompts` avoids secret prompts.
- `window_title_include` lets you **scope it to specific tabs** (strongly recommended —
  e.g. only tabs titled `agent`).
- `dry_run` / `--dry-run` and `min_confidence` for cautious rollout.
- Per-tab cooldown + global rate limit prevent runaway keystroke loops.

Start with `--dry-run` and a narrow `window_title_include` before going fully automatic.

## Uninstall

Remove the `allow_remote_control` / `listen_on` lines from `kitty.conf` (a timestamped
backup was saved by `install.sh`), and stop the process.
