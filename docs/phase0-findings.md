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
| Where is the hook configured? (`config.toml` key) | |
| Exact stdin JSON shape (paste a real capture) | |
| Tool name field | |
| Tool input field | |
| Session id field | |
| Exit code on allow | |
| Exit code on block | |
| Is stderr surfaced to the model? | |
| Does the hook actually block the tool call? | |
| Timeout before Codex gives up | |

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
