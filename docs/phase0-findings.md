# Phase 0 findings

Fill in by running the probes on the machine that has the agents
installed. Every row here is a **blocking unknown**: the adapter code in
`agentd/src/agentd/adapters/` is written against the *assumed* contract and must
be corrected once the real one is captured.

> **Note (D-025):** Cline is no longer supported; its probe (P0-1) was removed.
> OpenCode needs no hook probe — it is read straight from its SQLite session
> store, verified against the real database. The Codex contract below is
> *partially* confirmed by real usage: the audit log shows hooks arriving with
> correct session ids and tool names, and deny reasons reach the model.

## How to run the probes

```powershell
# 1. capture whatever the agent sends
.\scripts\probe-hook.ps1 -Agent codex

# 2. inspect the capture
Get-ChildItem "$env:USERPROFILE\.agentlink\probe" | Sort-Object LastWriteTime -Descending
Get-Content "$env:USERPROFILE\.agentlink\probe\codex-input-*.json" | Select-Object -Last 1

# 3. force a block and see whether the agent honours it
.\scripts\probe-hook.ps1 -Agent codex -ExitCode 2
```

Install the probe as the real hook (see the agent's hook settings) so it is
invoked by the agent itself, not by hand.

---

## P0-2 — Codex `PreToolUse` contract

| Question | Finding |
|---|---|
| Where is the hook configured? (`config.toml` key) | **`~/.codex/config.toml`**, gated by `[features] hooks = true`; `[[hooks.PreToolUse]]` with `matcher` (e.g. `"*"`) plus `[[hooks.PreToolUse.hooks]]` `{type, command, timeout, statusMessage}`; a `[hooks.state]` block tracks trusted-hashes per hook entry |
| Exact stdin JSON shape (paste a real capture) | empirical (audit log): `session_id`, `cwd`, `tool_name`, `tool_input` arrive — the tolerant parser in `adapters/codex/mapping.py` matches reality |
| Tool name field | `tool_name` |
| Tool input field | `tool_input` |
| Session id field | `session_id` — and it **matches the rollout session id**, so cards land in the right chat |
| Exit code on allow | `0` (e2e-verified) |
| Exit code on block | `2`, reason on stderr (e2e-verified) |
| Is stderr surfaced to the model? | yes — deny reasons steer the agent (verified live) |
| Does the hook actually block the tool call? | **yes** (e2e-verified: the hook blocks until decided; the agent waits) |
| **Does a matching hook replace the built-in approval prompt?** | **yes (confirmed, 2026-10-05)** — with `matcher = "*"` wired, Codex's own approve/deny prompt never appears for matched calls; while the hook blocks, the desktop shows the hook's `statusMessage` instead. The hook *is* the approval UI for those calls |
| JSON output protocol (stdout)? | **yes — read from `codex.exe`'s embedded strings (2026-10-06)**: an envelope with `decision`, `hookSpecificOutput`, `reason`, `stopReason`, `suppressOutput`, `systemMessage`; `PreToolUseHookSpecificOutputWire` carries `hookEventName`, `permissionDecision`, `permissionDecisionReason`, `additionalContext` (Claude Code-parity), and the permission decision includes **`ask`** — the "no opinion, let the built-in approval decide" answer. Exit codes 0/2 remain valid in parallel (own error string: "hook exited with code 2 but did not write a blocking reason to stderr") |
| Does `ask` fall back to the native prompt? | shipped as the D-028 defer path (`permissionDecision: "ask"` from `agentd-codex-hook` when the phone-approvals toggle is off); hook-level e2e green — the desktop-side acceptance (native prompt appears) is the one thing only the user can confirm |
| Is there a "fall back to native approval" response? | **unknown — the key open question.** Needs a desktop-UI probe: temporarily point the hook at a script that exits with each candidate code (empty/1/other, JSON variants) and watch whether Codex's own prompt appears |
| Can pending approvals be observed outside the hook? | **no** — checked `queue_1.sqlite` and `state_5.sqlite`: no approval/permission tables; native pending approvals live only in the desktop UI, so an unhooked "mirror native approvals to the phone" is not buildable from state |
| Timeout before Codex gives up | hook config carries its own `timeout` (user's is 600s); whether Codex kills or ignores past it — untested |

## P0-3 — Hook timeout ceiling

| Question | Finding |
|---|---|
| Longest a Codex hook may block before being killed | |
| Does the agent retry the hook after a timeout? | |

This sets `agents.<name>.hook_timeout_seconds` in `config.toml` (D-005).

## P0-4 — Concurrent sessions

| Question | Finding |
|---|---|
| Can two Codex threads run at once? | |
| Can Codex and OpenCode run at once? | |
| Are hook invocations serialised per agent? | |
| Is the session id stable across a task? | |

## P0-5 — Hook installation surface

| Question | Finding |
|---|---|
| Codex hook config location | |
| Does the agent ship a hook installer? | |
| Are hooks per-workspace or global? | |

## P0-6 — Corporate proxy / TLS inspection

| Question | Finding |
|---|---|
| Is outbound WSS to the relay allowed? | |
| Is TLS intercepted (custom root CA)? | |
| Does `truststore` pick up the OS trust store? | |
| Is a proxy URL required? | |

## P0-7 — Git availability

| Question | Finding |
|---|---|
| Is `git` on PATH? | |
| Version | |
| Are target workspaces git repos? | |
| Any repos with unusual config (LFS, submodules)? | |

## P0-8 — Windows Task Scheduler

| Question | Finding |
|---|---|
| Can a task run at logon without elevation? | |
| Does it survive sleep/resume? | |
| Where do stdout/stderr go? | |

---

## Adapter corrections required

List every change needed in `agentd/src/agentd/adapters/` once the above is
known. Each entry should name the file and the exact change.

| # | File | Change | Done |
|---|---|---|---|
| 1 | `adapters/codex/mapping.py` | | ☐ |
| 2 | `adapters/codex/hook_cli.py` | | ☐ |
