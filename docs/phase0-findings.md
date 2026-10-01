# Phase 0 findings

Fill this in by running the probes on the machine that has Cline and Codex
installed. Every row here is a **blocking unknown**: the adapter code in
`agentd/src/agentd/adapters/` is written against the *assumed* contract and must
be corrected once the real one is captured.

## How to run the probes

```powershell
# 1. capture whatever the agent sends
.\scripts\probe-hook.ps1 -Agent cline

# 2. inspect the capture
Get-ChildItem "$env:USERPROFILE\.agentlink\probe" | Sort-Object LastWriteTime -Descending
Get-Content "$env:USERPROFILE\.agentlink\probe\cline-input-*.json" | Select-Object -Last 1

# 3. force a block and see whether the agent honours it
'{"cancel": true, "errorMessage": "probe block"}' |
    Set-Content "$env:USERPROFILE\.agentlink\probe\cline-response.json"
.\scripts\probe-hook.ps1 -Agent cline
```

Install the probe as the real hook (see the agent's hook settings) so it is
invoked by the agent itself, not by hand.

---

## P0-1 — Cline `PreToolUse` contract

| Question | Finding |
|---|---|
| Where is the hook configured? (path / setting name) | |
| Exact stdin JSON shape (paste a real capture) | |
| Is the tool name at `preToolUse.toolName`? | |
| Are parameters at `preToolUse.parameters`? | |
| Session id field name | |
| Workspace root field name | |
| Exact stdout shape that **allows** | |
| Exact stdout shape that **blocks** | |
| Is `errorMessage` surfaced to the model? | |
| Exit code on allow / on block | |
| Does the hook actually block the tool call? | |
| Timeout before Cline gives up | |

**Fallback if the hook cannot block:** wrap the agent in a launcher that
intercepts the tool call, or fall back to post-hoc review only.

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
| Longest a Cline hook may block before being killed | |
| Longest a Codex hook may block before being killed | |
| Does the agent retry the hook after a timeout? | |

This sets `agents.<name>.hook_timeout_seconds` in `config.toml` (D-005).

## P0-4 — Concurrent sessions

| Question | Finding |
|---|---|
| Can two Cline tasks run at once? | |
| Can Cline and Codex run at once? | |
| Are hook invocations serialised per agent? | |
| Is the session id stable across a task? | |

## P0-5 — Hook installation surface

| Question | Finding |
|---|---|
| Cline hook file location(s) | |
| Codex hook config location | |
| Does either agent ship a hook installer? | |
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
| 1 | `adapters/cline/mapping.py` | | ☐ |
| 2 | `adapters/cline/hook_cli.py` | | ☐ |
| 3 | `adapters/codex/mapping.py` | | ☐ |
| 4 | `adapters/codex/hook_cli.py` | | ☐ |
