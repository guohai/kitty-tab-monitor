# Project Rules

## Purpose

`kitty-tab-monitor` watches all kitty terminal windows, including background tabs and
tabs in other kitty OS windows. It detects stable approval prompts, asks an LLM for the
best safe choice, and sends that choice to the exact kitty window.

## Decision Policy

- Keep safe tasks moving automatically.
- For a safe command/action, prefer "always allow" or "don't ask again" when that persistent
  permission is itself narrowly and safely scoped. This avoids repeated prompts.
- Otherwise choose a safe one-time approval. Hold for human review only when neither the
  persistent nor one-time action is safe.
- Treat broad, ambiguous, or potentially unsafe persistent permission as unsafe and fall
  back to one-time approval.
- Creating, modifying, and removing files inside the monitored tab's workspace is safe.
- A clearly local Docker test-database reset or cleanup is safe when it is part of a local
  test or gate workflow, including scoped SQL `DELETE` statements. Production, remote,
  shared, and ambiguously targeted database deletion requires human review.
- Writes under `~/.claude/jobs/*/tmp/*` are safe test/job artifacts. Prefer persistent
  permission only when it is scoped to the exact matching job `tmp` directory. Broader
  access to `~/.claude` is unsafe; fall back to a safe one-time approval when available.
- Apart from that exact Claude job artifact exception, hold for human review before
  modifying or removing files outside the workspace. Resolve relative paths from the tab's
  current directory; ambiguous paths require review.
- A `kill` command is safe when every target is a numeric PID greater than or equal to
  1000. PID 1, any PID below 1000, name-based kills, and system-wide kills require review.
- Hold for human review before rebooting, restarting, or shutting down the host system, or
  before other system-wide destructive operations such as formatting a disk.
- Never answer password, passphrase, PIN, OTP, token, or other secret-entry prompts.

## LLM Contract

- Send only the captured screen text, tab title, workspace, and current directory needed
  to make the decision.
- The response contains `is_waiting`, `action`, `text_to_send`, `press_enter`,
  `confidence`, and a concise 2-8 word `reason`.
- Do not ask the LLM to echo or summarize the command for logging. Context is already
  available locally, and echoing it wastes output tokens and can reduce accuracy.
- Validate every model response before any text reaches kitty.

## Logging

- Extract command context locally from the captured screen. For agent command prompts,
  use the block between `Bash command`/`Shell command` and `Run shell command`.
- End each window-specific decision log with valid single-line JSON containing `context`,
  `action`, and `reason`.
- Preserve the full visible command, including paths, arguments, and identifiers. Do not
  shorten it or include the surrounding approval question and menu.
- The JSON `action` records the model's effective input, such as `1 + Enter`; `reason` is
  the model's concise rationale for choosing or withholding the action.

## Kitty Integration

- Use kitty's remote-control socket; never fall back to terminal escape transport.
- Prefer `allow_remote_control socket-only` for local operation. Auto-discover only Unix
  sockets owned by the current user, and require `KTM_SOCKET` when discovery is ambiguous.
- Exclude the monitor's own kitty window and honor configured title include/exclude rules.
- Target terminal windows by kitty window ID even when their tabs are not visible.

## Verification

- Keep the implementation dependency-free and compatible with Python 3.8+.
- Run `python3 -m unittest discover -s tests -v` after behavior changes.
- Run `python3 -m compileall -q kitty_tab_monitor tests`, `python3 -m json.tool config.json`,
  `bash -n install.sh run.sh`, and `git diff --check` before handoff.
