# AgentLink — Build Specification v1

Remote approvals, change review and monitoring for coding agents (**Cline** and **Codex** first) from a phone, with strict multi-agent segregation.

- **Audience:** an autonomous coding agent implementing the whole system, plus a human reviewer.
- **Owner's environment:** Windows, VS Code, corporate laptop behind a proxy, Cline + Codex VS Code extensions.
- **Date of spec:** 2026-09-30 (Updated: Codex Hook Architecture + Multi-Agent Segregation).

---

## 0. How to use this document

1. Build in the phase order of §14. **Do not skip Phase 0**: it verifies behaviours that could not be confirmed from documentation.
2. Anything labelled **Decision** is final. Do not re-litigate it.
3. Anything labelled **Verify (P0-x)** is an unconfirmed fact. Run the experiment, write the result to `docs/phase0-findings.md`, then apply the stated default or fallback.
4. Never stop to ask the owner questions. If something is ambiguous, pick the safest option (fail closed), and record the choice in `docs/decisions.md`.
5. The security requirements in §6 are non-negotiable.
6. Every phase ends only when its acceptance criteria pass automatically (tests) or via the scripted manual check listed.
7. Prefer small, typed, tested modules. No module over ~400 lines.

---

## 1. Goal and scope

### 1.1 Problem
Coding agents in VS Code (Cline, Codex extension) stop and wait for approvals, and produce file changes the owner wants to review, but only the PC UI exposes this. The owner wants to be away from the desk and still: get notified, approve or deny actions, see what changed, and read code.

Furthermore, when multiple agents are in use (or when switching between Cline and Codex), their tasks, approvals, diffs, and chat streams must **not** be mixed into a single chaotic inbox. They must be cleanly segregated in the mobile interface.

### 1.2 In scope (v1)
1. **PC daemon (`agentd`)** running on the Windows laptop, tracking sessions and approvals partitioned by `agent_type` (`cline`, `codex`).
2. **Workspace diff engine**: what changed in a repo since an agent task started, and since HEAD; per-file diffs; read-only file browsing, scoped to the agent's active workspace.
3. **Approval adapter for Cline** (hook based: `PreToolUse.ps1`).
4. **Approval adapter for Codex** (hook based: `PreToolUse` in `~/.codex/config.toml` or `hooks.json`).
5. **Relay service** (cloud, outbound-only from the PC, end-to-end encrypted).
6. **Phone PWA with Agent Segregation**:
   - Dedicated views/tabs for **Cline** and **Codex** (separate inboxes, diffs, feeds, and branding/badges).
   - Unified overview dashboard showing high-level agent statuses.
   - Push notifications tagged by agent source.
   - Pairing, settings, and key management.
7. **Live activity stream**: a per-agent, near-real-time feed of agent events (task started, tool call, tool result, assistant message, task finished, error) plus agent status/heartbeat, so the phone stays in sync with *what the agent is doing*, not only with *what it is asking*.
8. **Phone simulator CLI** (`agentlink-sim`) used for testing before the PWA exists.

### 1.3 Out of scope (v1)
- Antigravity, Copilot and other agents (adapter interface for future addition is in §18).
- Sending new prompts to agents from the phone, remote terminal, editing files from the phone.
- Token-by-token model streaming. v1 streams **discrete events** (tool calls, messages, status), not raw model deltas.
- Multi-user / team features. One owner, one or more paired phones, one or more PCs.
- Native iOS/Android apps (PWA first).

---

## 2. Environment and constraints

| Item | Value | Consequence |
|---|---|---|
| OS | Windows 10/11, no admin rights assumed | No services needing admin; use per-user install and Task Scheduler (user-level). |
| Network | Corporate proxy, possible TLS inspection | PC→relay must work through an HTTP(S) proxy using the OS trust store (via Python `truststore`); outbound 443 only. No inbound ports. |
| Phone | Unknown (iOS or Android) | PWA + Web Push (VAPID). iOS needs 16.4+ and "Add to Home Screen" for push. |
| Cline | VS Code extension, hooks feature | Windows hooks must be `HookName.ps1` PowerShell scripts invoking `agentd-cline-hook`. |
| Codex | VS Code extension / Codex CLI, hooks feature | Native `PreToolUse` hook configured in `config.toml` / `hooks.json` invoking `agentd-codex-hook`. No binary proxy or compiled `.exe` required. |
| Segregation | Multiple active agents possible | All database tables, messages, and UI stores partition state by `(agent_type, session_id, workspace_path)`. |
| Languages | Owner uses Python and TypeScript | `agentd` + `relay` in Python 3.11+; PWA in TypeScript. |

---

## 3. Decisions (final)

| # | Decision |
|---|---|
| D1 | `agentd` and `relay` are Python 3.11+ (asyncio). The PWA is TypeScript + React + Vite. |
| D2 | Transport: PC and phone both connect **outbound** to a cloud **relay** over WSS (HTTPS port 443). The relay serves the PWA static files from the same origin. |
| D3 | All message content between PC and phone is **end-to-end encrypted** (libsodium `crypto_box`: PyNaCl on PC, `libsodium-wrappers` in PWA). The relay sees only routing metadata and ciphertext. |
| D4 | Push notifications via **Web Push (VAPID)**, payload contains **no content** (generic text + opaque id + agent badge). |
| D5 | **Fail closed.** If anything in the approval path is broken (daemon down, hook error, malformed message, timeout), the action is denied (Cline: `{"cancel": true}`; Codex: exit code `2`). |
| D6 | Diffs come from **git tree snapshots** (Appendix A, already validated), independent of any agent. Scoped per agent workspace. |
| D7 | Cline adapter = **`PreToolUse` hook** (`PreToolUse.ps1` → `agentd-cline-hook` → daemon local API), blocking until the phone decides. Tagged with `agent_type: "cline"`. |
| D8 | Codex adapter = **`PreToolUse` hook** (configured in `~/.codex/config.toml` → `agentd-codex-hook` → daemon local API), blocking until the phone decides. Tagged with `agent_type: "codex"`. Exit code `0` = allow, `2` = deny. |
| D9 | Persistence: SQLite on both agentd and relay (WAL mode). All operational tables partition by `agent_type` and `session_id`. |
| D10 | Approvals are bound to a hash of the action, expire (default 10 min, aligned with hook timeout) and are single-use. |
| D11 | The relay is deployed with Docker Compose + Caddy (automatic TLS) on any small VPS. Hosting provider is the owner's choice and is not part of the code. |
| D12 | The phone never auto-approves. Every approval needs an explicit tap. High-risk approvals need press-and-hold. |
| D13 | **Agent Segregation:** The PWA strictly isolates Cline and Codex into distinct tabs/spaces. Each agent has its own visual theme, pending approval badge count, diff viewer, and history feed. The user can switch between `[All Agents]`, `[Cline]`, and `[Codex]` at any time. |

---

## 4. Architecture

```
 VS Code / CLI (Windows)
 ┌──────────────────────────┐    hook stdin/stdout     ┌──────────────────────────────────┐
 │ Cline extension          │ ───────────────────────► │ PreToolUse.ps1 → agentd-cline-hook│
 └──────────────────────────┘                          └───────────────┬──────────────────┘
 ┌──────────────────────────┐    hook stdin/stdout     ┌───────────────▼──────────────────┐
 │ Codex extension / CLI    │ ───────────────────────► │ config.toml   → agentd-codex-hook│
 └──────────────────────────┘                          └───────────────┬──────────────────┘
                                                          HTTP 127.0.0.1:47800 (local API)
                                                          { agent_type, session_id, ... }
                                        ┌─────────────────────────────▼─────────────────────┐
                                        │ agentd (daemon)                                   │
                                        │  approvals • policy • sessions • git diff engine  │
                                        │  partitioned by agent_type ('cline' | 'codex')    │
                                        │  SQLite • crypto • relay client                   │
                                        └─────────────────────────────┬─────────────────────┘
                                                                      │ WSS (outbound, via corp proxy)
                                        ┌─────────────────────────────▼─────────────────────┐
                                        │ relay (cloud)  FastAPI + SQLite + Web Push        │
                                        │  routes ciphertext envelopes + delivers Web Push  │
                                        └─────────────────────────────┬─────────────────────┘
                                                                      │ WSS + Web Push
                                        ┌─────────────────────────────▼─────────────────────┐
                                        │ Phone PWA (React)                                 │
                                        │  ┌───────────────┬───────────────┬──────────────┐ │
                                        │  │ Overview (All)│  Cline Space  │  Codex Space │ │
                                        │  └───────────────┴───────────────┴──────────────┘ │
                                        │  - Isolated Approval Inboxes & Badges             │
                                        │  - Workspace-scoped Diff Viewers                  │
                                        │  - Agent-specific Activity Logs                   │
                                        └───────────────────────────────────────────────────┘
```

### 4.1 Component responsibilities

| Component | Responsibility |
|---|---|
| `agentd` | Single source of truth on the PC. Owns sessions, approvals, policy, git diff engine, watcher, encryption keys, relay connection. Exposes a local HTTP API (`http://127.0.0.1:47800`) to adapters. Enforces strict `agent_type` metadata on all actions and approvals. |
| Cline adapter | Translates Cline hook JSON on stdin to approval request with `agent_type: "cline"`; blocks on `agentd`; outputs `{"cancel": false}` or `{"cancel": true, "errorMessage": "..."}` on stdout. |
| Codex adapter | Translates Codex `PreToolUse` hook JSON on stdin to approval request with `agent_type: "codex"`; blocks on `agentd`; exits with code `0` (allow) or `2` (deny) with explanation on stderr. |
| `relay` | Authenticates devices, routes opaque encrypted envelopes, stores undelivered ones briefly, sends content-free Web Push, serves the PWA. |
| PWA | Renders segregated agent spaces (`Cline` view, `Codex` view, and `Overview`), routes decisions, manages private decryption key in IndexedDB. |

### 4.2 Key flows

**Segregated Approval Flow:**
1. Agent (Cline or Codex) prepares to run a tool $\rightarrow$ executes its adapter hook.
2. Hook CLI (`agentd-cline-hook` or `agentd-codex-hook`) calls `POST /v1/approvals` with payload tagged with `agent_type` (`cline` or `codex`), `session_id`, and `workspace_path`.
3. `agentd` checks policy:
   - Pre-approved by policy $\rightarrow$ returns immediate `allow`.
   - Approval required $\rightarrow$ creates approval record with `action_hash`, encrypts payload, transmits envelope via relay.
4. Relay sends Web Push notification: `"[Cline] Approval required: bash command"` or `"[Codex] Approval required: file edit"`.
5. User taps push or opens PWA:
   - The badge on the specific agent tab (e.g. `Cline (1)`) highlights in real time.
   - Opening the **Cline Tab** displays only Cline approvals in Cline's purple theme; opening the **Codex Tab** displays only Codex approvals in Codex's green theme.
6. User reviews the proposed tool, arguments, and workspace context, then taps "Approve" or "Deny".
7. Phone sends signed decision back through the relay.
8. `agentd` validates and unblocks the waiting adapter:
   - Cline: hook prints `{"cancel": false}` or `{"cancel": true}`.
   - Codex: hook exits code `0` (allow) or code `2` (deny).

**Workspace Diff View Flow (Scoped per Agent):**
1. In the PWA, user navigates to the active agent tab and taps "View Workspace Changes".
2. Phone requests `workspace.changes.get` with the agent's active `workspace_path`.
3. `agentd` calculates git tree snapshots (`git write-tree`) for that specific repo.
4. Returns the list of changed files and line additions/deletions for that agent's current task.

---

## 5. Repository layout (monorepo)

```
agentlink/
├─ README.md
├─ docs/
│  ├─ phase0-findings.md        # written during Phase 0
│  ├─ decisions.md              # decisions log
│  └─ protocol.md               # protocol & payload schemas (§7)
├─ agentd/
│  ├─ pyproject.toml            # console scripts: agentd, agentd-cline-hook, agentd-codex-hook
│  ├─ src/agentd/
│  │  ├─ cli.py                 # typer CLI: run, pair, status, install, doctor, away
│  │  ├─ config.py              # TOML config (pydantic-settings)
│  │  ├─ paths.py               # %USERPROFILE%\.agentlink\*
│  │  ├─ db.py                  # SQLite with (agent_type, session_id) partitioning
│  │  ├─ crypto.py              # keys, box/unbox (PyNaCl), hashing, redaction
│  │  ├─ protocol.py            # pydantic models: Envelopes, Actions, AgentType enum
│  │  ├─ local_api.py           # aiohttp/FastAPI server on 127.0.0.1:47800
│  │  ├─ relay_client.py        # WSS client (truststore OS trust + aiohttp), reconnect
│  │  ├─ approvals.py           # state machine + async waiters per agent
│  │  ├─ policy.py              # rules engine + risk heuristics
│  │  ├─ sessions.py            # session management partitioned by agent
│  │  ├─ gitdiff.py             # git tree snapshot diff engine
│  │  ├─ watcher.py             # watchfiles + debounce
│  │  ├─ files.py               # safe file tree/content reading
│  │  ├─ activity.py            # live event ingestion, ordering, cursors
│  │  ├─ audit.py               # append-only audit log
│  │  └─ adapters/
│  │     ├─ base.py             # BaseHookAdapter interface
│  │     ├─ cline/
│  │     │  ├─ hook_cli.py      # agentd-cline-hook entrypoint
│  │     │  ├─ mapping.py       # Cline JSON stdin -> Action
│  │     │  ├─ install.py       # registers PreToolUse.ps1 in Cline config
│  │     │  └─ PreToolUse.ps1   # PowerShell invocation shim
│  │     └─ codex/
│  │        ├─ hook_cli.py      # agentd-codex-hook entrypoint
│  │        ├─ mapping.py       # Codex JSON stdin -> Action
│  │        ├─ install.py       # writes [[hooks.PreToolUse]] to ~/.codex/config.toml
│  │        └─ config_template.toml
│  └─ tests/
├─ relay/
│  ├─ pyproject.toml
│  ├─ src/relay/{main.py,db.py,auth.py,hub.py,push.py,models.py}
│  ├─ tests/
│  ├─ Dockerfile
│  ├─ docker-compose.yml        # relay + caddy
│  └─ Caddyfile
├─ pwa/
│  ├─ package.json, vite.config.ts, tsconfig.json
│  └─ src/
│     ├─ main.tsx, sw.ts
│     ├─ crypto/               # libsodium wrappers
│     ├─ net/                  # WSS client, sync, reconnect
│     ├─ store/
│     │  ├─ index.ts           # root store
│     │  ├─ clineSlice.ts      # isolated Cline state (inbox, feed, diffs)
│     │  ├─ codexSlice.ts      # isolated Codex state (inbox, feed, diffs)
│     │  ├─ activitySlice.ts   # per-agent live event feed + cursors
│     │  └─ agentsSlice.ts     # global agent registry and active view state
│     ├─ screens/
│     │  ├─ OverviewScreen.tsx # multi-agent dashboard & status pills
│     │  ├─ AgentScreen.tsx    # segregated view: Cline or Codex space
│     │  ├─ ActivityScreen.tsx # live per-agent event feed
│     │  ├─ DiffViewerScreen.tsx
│     │  ├─ FileBrowserScreen.tsx # read-only file tree/content
│     │  ├─ PairingScreen.tsx
│     │  └─ SettingsScreen.tsx
│     └─ components/
│        ├─ AgentBadge.tsx     # distinct visual branding for Cline vs Codex
│        ├─ ApprovalCard.tsx
│        ├─ StatusPill.tsx     # idle/running/waiting/error/offline
│        └─ NavTabBar.tsx      # [Overview] [Cline (badge)] [Codex (badge)]
├─ sim/                         # agentlink-sim: CLI phone simulator (Python)
└─ scripts/                     # dev helpers (gen-vapid.py, test-hooks.py, ...)
```

---

## 6. Security (non-negotiable)

### 6.1 Threat model

| # | Adversary | Must not be able to |
|---|---|---|
| T1 | Relay operator / compromised relay | Read content, forge decisions, learn file contents or commands |
| T2 | Network attacker / TLS-inspecting proxy | Read or modify traffic, replay frames |
| T3 | Malicious page or XSS in the PWA origin | Exfiltrate keys, auto-approve |
| T4 | Unprivileged local process on the PC | Call the local API, approve actions |
| T5 | Replay / tampering of a decision | Re-use an old "allow" for a new action |
| T6 | Lost or stolen phone | Approve without the device key, read history |

### 6.2 Requirements

| # | Requirement |
|---|---|
| S1 | **E2E encryption.** All content is `crypto_box`-encrypted between PC and phone. The relay stores and forwards ciphertext only; it never holds a private key or plaintext. |
| S2 | **Local API auth.** `127.0.0.1:47800` binds to loopback only and requires a per-install bearer token at `%USERPROFILE%\.agentlink\local_api_token` (ACL: current user only, created `0600`-equivalent). Requests without a valid token are rejected `401`. |
| S3 | **Pairing.** Out-of-band, short-lived, single-use. QR + 8-char code, 5-minute TTL, constant-time comparison, max 5 attempts then the pairing is invalidated. |
| S4 | **Signed decisions.** Every decision is signed with the phone's Ed25519 key. `agentd` verifies signature, `approval_id`, `action_hash`, `nonce`, and expiry before acting. |
| S5 | **Replay protection.** `approval_id` is single-use; each device has a monotonic `nonce`; duplicates and expired decisions are rejected and audited. |
| S6 | **Key storage.** PC private key under `%USERPROFILE%\.agentlink\keys\` with user-only ACL. Phone private key in IndexedDB (non-extractable where the platform allows). Keys are never logged, never sent to the relay. |
| S7 | **Redaction.** `crypto.py` redacts secrets (env values, tokens, private keys, `Authorization` headers) from payloads before encryption, using a deny-list plus entropy heuristic. |
| S8 | **Content-free push.** Push payloads carry no content (D4): generic text + opaque id + agent badge only. |
| S9 | **Fail closed.** Any error in the approval path denies the action (D5). |
| S10 | **Audit.** Every approval, decision, policy evaluation, pairing, and revocation is appended to an append-only `audit_log`. The app never deletes rows from it. |
| S11 | **Rate limiting.** The relay limits messages per device; `agentd` limits the local API per token. Exceeding limits returns `429` and is audited. |
| S12 | **TLS.** Relay terminates TLS 1.2+ via Caddy. The PC uses the OS trust store (`truststore`) so corporate TLS inspection works. Optional certificate pinning via config. |
| S13 | **PWA hardening.** Strict CSP (no `unsafe-eval`, no third-party scripts), SRI on assets, service worker scope limited to the app origin, no `innerHTML` for agent-supplied content. |
| S14 | **Rotation.** `agentd pair --rotate` issues new device keys and revokes the old device. |
| S15 | **Revocation.** `agentd devices revoke <id>` marks a device revoked; the relay drops envelopes addressed to it and rejects its auth. |

### 6.3 Fail-closed matrix

| Failure | Cline result | Codex result |
|---|---|---|
| `agentd` not running / unreachable | `{"cancel": true}` | exit `2` |
| Hook timeout (default 10 min) | `{"cancel": true}` | exit `2` |
| Malformed hook input | `{"cancel": true}` | exit `2` |
| Relay unreachable | `{"cancel": true}` | exit `2` |
| Decision signature invalid | `{"cancel": true}` | exit `2` |
| Policy says deny | `{"cancel": true}` | exit `2` |
| Policy says allow | `{"cancel": false}` | exit `0` |
| Phone approves | `{"cancel": false}` | exit `0` |

---

## 7. Protocol and payload schemas

### 7.1 Transport

- PC↔relay and phone↔relay: **WSS** to `wss://<relay-host>/v1/ws`, JSON text frames.
- Every application message is wrapped in an **envelope**. The relay reads only the envelope header (routing metadata); the body is opaque ciphertext.
- The relay never decrypts. `type` is duplicated in the header so the relay can route/notify without reading content.

### 7.2 Envelope

```json
{
  "v": 1,
  "id": "uuid-v4",
  "from": "pc:<device_id>",
  "to": "phone:<device_id>",
  "ts": "2026-09-30T12:00:00Z",
  "type": "approval.request",
  "nonce": "base64-24-bytes",
  "ciphertext": "base64"
}
```

`ciphertext = crypto_box(plaintext_json, nonce, recipient_pubkey, sender_privkey)`.

### 7.3 Message catalog

| Direction | `type` | Purpose |
|---|---|---|
| PC → Phone | `approval.request` | New approval awaiting a decision |
| PC → Phone | `approval.resolved` | Approval decided/expired/cancelled (sync other phones) |
| PC → Phone | `activity.event` | One live agent event |
| PC → Phone | `session.update` | Session started/ended/state change |
| PC → Phone | `agent.status` | Status + heartbeat for one agent |
| PC → Phone | `workspace.changes.result` | Reply to `workspace.changes.get` |
| PC → Phone | `file.tree.result` | Reply to `file.tree.get` |
| PC → Phone | `file.content.result` | Reply to `file.content.get` |
| PC → Phone | `error` | Correlated error for a request |
| Phone → PC | `approval.decision` | Allow/deny a specific approval |
| Phone → PC | `workspace.changes.get` | Request diff for a workspace |
| Phone → PC | `file.tree.get` | Request a directory listing |
| Phone → PC | `file.content.get` | Request file content (read-only) |
| Phone → PC | `session.list` | Request sessions for an agent |
| Phone → PC | `agent.status.get` | Request current status |
| Phone → PC | `sync.resume` | Resume activity feed from a cursor |
| Both ↔ Relay | `hello`, `auth`, `auth.ok`, `auth.err`, `ack`, `ping`, `pong`, `push.register` | Connection lifecycle |

### 7.4 Payload schemas (plaintext, after decryption)

**`approval.request`**
```json
{
  "approval_id": "uuid",
  "agent_type": "cline",
  "session_id": "uuid",
  "workspace_path": "C:\\src\\proj",
  "pc_device_id": "uuid",
  "tool": { "name": "execute_command", "kind": "command" },
  "action": {
    "summary": "Run: npm test",
    "command": "npm test",
    "cwd": "C:\\src\\proj",
    "paths": [],
    "diff_preview": null
  },
  "risk": { "level": "medium", "reasons": ["shell command", "network access"] },
  "action_hash": "sha256:...",
  "created_at": "2026-09-30T12:00:00Z",
  "expires_at": "2026-09-30T12:10:00Z"
}
```
`tool.kind` ∈ `command | file_edit | file_read | file_delete | network | other`.
For `file_edit`, `action.diff_preview` carries a unified diff (truncated to a configurable cap, default 200 lines).

**`approval.decision`**
```json
{
  "approval_id": "uuid",
  "decision": "allow",
  "reason": "looks fine",
  "phone_device_id": "uuid",
  "decided_at": "2026-09-30T12:03:00Z",
  "nonce": 42,
  "sig": "base64-ed25519-over-canonical-json"
}
```

**`activity.event`**
```json
{
  "event_id": "uuid",
  "seq": 1043,
  "agent_type": "codex",
  "session_id": "uuid",
  "workspace_path": "C:\\src\\proj",
  "kind": "tool_call",
  "ts": "2026-09-30T12:02:11Z",
  "summary": "read_file src/app.ts",
  "detail": { "tool": "read_file", "path": "src/app.ts" }
}
```
`kind` ∈ `task_started | tool_call | tool_result | message | task_finished | error`.

**`agent.status`**
```json
{
  "agent_type": "cline",
  "session_id": "uuid",
  "state": "waiting_approval",
  "since": "2026-09-30T12:00:00Z",
  "last_heartbeat": "2026-09-30T12:02:30Z",
  "pending_approvals": 2
}
```
`state` ∈ `idle | running | waiting_approval | error | offline`.

**`workspace.changes.get` / `.result`**
```json
{ "agent_type": "cline", "session_id": "uuid", "workspace_path": "C:\\src\\proj", "since": "task_start|head" }
```
```json
{
  "workspace_path": "C:\\src\\proj",
  "base": "task_start",
  "files": [
    { "path": "src/app.ts", "status": "modified", "additions": 12, "deletions": 3 },
    { "path": "src/new.ts", "status": "added", "additions": 40, "deletions": 0 }
  ],
  "truncated": false
}
```

**`file.tree.get` / `.result`** — `{ "workspace_path": "...", "path": "src" }` → `{ "entries": [{ "name": "app.ts", "type": "file", "size": 1234 }] }`.

**`file.content.get` / `.result`** — `{ "workspace_path": "...", "path": "src/app.ts" }` → `{ "path": "...", "encoding": "utf-8", "content": "...", "truncated": false }`.

**`error`** — `{ "code": "not_found", "message": "...", "retryable": false, "correlation_id": "uuid" }`.

### 7.5 Versioning

- `v` is the protocol major. A receiver rejects unknown majors with `error.code = "unsupported_version"`.
- New **optional** fields may be added within a major; receivers ignore unknown fields.
- Removing or changing the meaning of a field requires a major bump.

### 7.6 Ordering and delivery

- `activity.event.seq` is a per-`(agent_type, session_id)` monotonic counter. The phone stores the last `seq` per agent and sends `sync.resume` with it after a reconnect.
- The relay buffers undelivered envelopes for a bounded window (default 24 h) and drops expired ones.
- Approvals are **not** buffered past their `expires_at`; an expired approval is resolved as `expired` and the hook fails closed.

---

## 8. Data model

SQLite, WAL mode, on both `agentd` and `relay`. All timestamps are UTC RFC3339. All operational tables are **partitioned by `agent_type`** (and `session_id` where applicable); the only cross-agent query allowed is the Overview aggregate.

### 8.1 `agentd` tables

| Table | Columns |
|---|---|
| `devices` | `device_id PK, kind ('pc'\|'phone'), name, pubkey, created_at, revoked_at, last_seen` |
| `sessions` | `session_id PK, agent_type, workspace_path, pc_device_id, started_at, ended_at, state` |
| `approvals` | `approval_id PK, agent_type, session_id, workspace_path, tool_name, tool_kind, action_json, action_hash, risk_level, risk_reasons, state, created_at, expires_at, decided_at, decision, decision_reason, decided_by` |
| `activity_events` | `event_id PK, seq, agent_type, session_id, workspace_path, kind, ts, summary, detail_json` |
| `snapshots` | `session_id, workspace_path, base_tree, head_tree, created_at` |
| `policy_rules` | `id PK, agent_type, match_json, effect, priority, enabled` |
| `audit_log` | `seq PK AUTOINCREMENT, ts, actor, action, subject, detail_json` (append-only) |
| `meta` | `key PK, value` |

Indexes: `approvals(agent_type, session_id, state)`, `approvals(agent_type, created_at)`, `activity_events(agent_type, session_id, seq)`, `sessions(agent_type, workspace_path)`.

`approvals.state` ∈ `pending | allowed | denied | expired | cancelled`.

### 8.2 `relay` tables

| Table | Columns |
|---|---|
| `devices` | `device_id PK, kind, pubkey, created_at, revoked_at, last_seen` |
| `pairings` | `code_hash PK, pc_device_id, created_at, expires_at, used_at, attempts` |
| `envelopes` | `id PK, from_device, to_device, type, ciphertext, created_at, delivered_at, expires_at` |
| `push_subscriptions` | `device_id, endpoint, p256dh, auth, created_at` |

The relay stores **no** `agent_type`, `session_id`, `workspace_path`, or plaintext — those live only inside the ciphertext.

### 8.3 Partitioning rule

Every read/write path takes `agent_type` as a mandatory argument. A query that omits `agent_type` (except the Overview aggregate and `devices`/`audit_log`) is a bug and must fail a test in §13.

---

## 9. Policy and risk engine

### 9.1 Rule schema

```json
{
  "id": "allow-reads",
  "agent_type": "cline",
  "match": { "tool_kind": ["file_read"], "path_glob": ["**/*"] },
  "effect": "allow",
  "priority": 100,
  "enabled": true
}
```
`effect` ∈ `allow | deny | ask`. Rules are evaluated by descending `priority`; the **first match wins**. No match ⇒ `ask` (fail closed).

### 9.2 Risk heuristics

| Signal | Level contribution |
|---|---|
| `tool_kind = file_read` | low |
| `tool_kind = file_edit` | medium |
| `tool_kind = command` | medium |
| `tool_kind = file_delete` | high |
| Command matches destructive patterns (`rm -rf`, `del /f`, `format`, `git push --force`, `DROP TABLE`, `curl \| sh`) | high |
| Path outside `workspace_path` | high |
| Network egress tool | medium |
| Secret-looking argument (token/key pattern) | high |

`risk.level` = max of contributions. `high` ⇒ the PWA requires **press-and-hold** (D12).

### 9.3 Defaults

- Default policy ships **deny-by-default for `high`**, `ask` for `medium`, `allow` for `file_read` inside the workspace.
- Policy is per `agent_type`; a rule for `cline` never applies to `codex`.
- Every evaluation is written to `audit_log` with the matched rule id (or `none`).

### 9.4 `away` mode semantics

`agentd away on` sets a global mode that:
- keeps `allow` rules as-is,
- converts `ask` to **deny** unless the phone is reachable and the approval is answered within the timeout,
- never auto-approves anything,
- tags every decision with `mode: "away"` in the audit log.

`agentd away off` restores normal behaviour. The mode is surfaced in the PWA header.

---

## 10. Notifications

- **Web Push (VAPID).** The relay holds the VAPID keypair; the PWA subscribes and registers the subscription via `push.register`.
- **Content-free payload** (D4): `{ "id": "<opaque>", "badge": "cline|codex", "kind": "approval|activity|status" }`. No command text, no paths, no code.
- **Title/body** are generic and generated on the phone from the decrypted envelope after the app opens: e.g. `[Cline] Approval required`.
- **Badging.** The service worker sets the app badge to the total pending count and per-agent counts are shown on the tab bar.
- **Dedupe.** One push per pending approval; repeated events for the same `approval_id` are collapsed.
- **Quiet hours.** Configurable window in Settings; during quiet hours pushes are suppressed but the in-app badge still updates.
- **iOS caveat.** Requires iOS 16.4+ and "Add to Home Screen"; the PWA shows a one-time setup hint when push is unavailable.
- **No action buttons.** Because D12 forbids auto-approve, notifications never carry an "Approve" action; tapping opens the relevant agent space.

---

## 11. PWA UX and agent segregation

### 11.1 Navigation

`NavTabBar`: `[Overview] [Cline (n)] [Codex (n)]`. Badge counts are per agent and update in real time. The active tab is stored in `agentsSlice`.

### 11.2 Overview screen

- One card per agent with a `StatusPill` (`idle | running | waiting_approval | error | offline`), pending-approval count, active workspace, and last activity time.
- A combined "Needs attention" list of pending approvals across agents, each row labelled with its agent badge; tapping deep-links into that agent's space.
- Global header shows `away` mode and relay connection state.

### 11.3 Agent space (Cline / Codex)

- **Theme:** Cline = purple, Codex = green (D13). Applied to header, badges, and accents.
- **Tabs inside the space:** `Approvals | Activity | Changes | Files`.
- **Approvals:** only this agent's pending approvals. Each `ApprovalCard` shows tool name, kind, summary, command/paths, risk level + reasons, workspace, and (for edits) a diff preview.
- **Activity:** the live event feed (§12) for this agent, newest first, with kind icons.
- **Changes:** the workspace diff viewer (§4.2), scoped to this agent's `workspace_path`.
- **Files:** read-only file tree + content viewer, scoped to the workspace.

### 11.4 Approval interaction

- `low`/`medium` risk: single explicit tap on **Approve** or **Deny**.
- `high` risk: **press-and-hold** (≥ 1.5 s) to approve; a plain tap only denies.
- Deny always allows an optional reason.
- The card shows a live countdown to `expires_at`; on expiry it becomes non-actionable and shows "Expired — action denied".
- Decisions are optimistic in the UI but reconciled by `approval.resolved`.

### 11.5 Offline behaviour

- The app is usable read-only offline from the last synced state; decisions are queued and sent on reconnect **only if** the approval has not expired.
- A queued decision that has expired is dropped and the user is told.

### 11.6 Key management

- Pairing stores the phone keypair in IndexedDB; Settings shows device name, paired PCs, and a **Revoke** action.
- Settings offers "Rotate keys" (calls `agentd pair --rotate`) and "Unpair".

### 11.7 Accessibility

- All actions reachable by keyboard; press-and-hold has a keyboard equivalent (hold `Enter`).
- Colour is never the only signal: each agent also has a distinct icon and text label.

---

## 12. Live activity and sync

### 12.1 Event sources

| Agent | Source of events |
|---|---|
| Cline | `PreToolUse` hook (tool_call), plus post-tool hook if available (tool_result); task start/end from session lifecycle |
| Codex | `PreToolUse` hook (tool_call), plus session lifecycle events |

Where an agent exposes no post-tool hook, `agentd` synthesises `tool_result` from the next event or a timeout, and marks it `synthetic: true`.

### 12.2 Pipeline

1. Adapter posts an event to `POST /v1/events` (local API) with `agent_type`, `session_id`, `workspace_path`.
2. `activity.py` assigns a monotonic `seq` per `(agent_type, session_id)`, persists it, and updates the agent's status.
3. The event is encrypted and sent as `activity.event`; the relay forwards it and (optionally) sends a content-free push for high-signal kinds only (`task_finished`, `error`).
4. The phone appends to the agent's `activitySlice` feed.

### 12.3 Status derivation

`state` is derived, not stored ad hoc:
- `waiting_approval` if ≥ 1 pending approval for the agent,
- else `running` if an event arrived within the heartbeat window (default 60 s),
- else `idle` if the session is open but quiet,
- `error` on an `error` event until the next non-error event,
- `offline` if no heartbeat for 3× the window or the PC is disconnected.

### 12.4 Resume and retention

- The phone persists the last `seq` per agent and sends `sync.resume` on reconnect; `agentd` replays missed events.
- `activity_events` are retained for a configurable window (default 7 days) and pruned by a background task; `audit_log` is never pruned.

---

## 13. Testing strategy

| Layer | Scope | Tooling |
|---|---|---|
| Unit | crypto, policy, risk, gitdiff, protocol models, status derivation | `pytest` (agentd/relay), `vitest` (pwa) |
| Contract | hook JSON → `Action` mapping for Cline and Codex, using recorded fixtures | `pytest` + `scripts/test-hooks.py` |
| Integration | local API, approvals state machine, relay routing, envelope round-trip | `pytest-asyncio`, in-process relay |
| E2E | sim phone ↔ agentd ↔ relay ↔ sim hook, full approve/deny/expire paths | `agentlink-sim` + `pytest` |
| Security | replay, tampered signature, expired decision, cross-agent leakage, token-less local API | dedicated `tests/security/` |
| PWA | store isolation, badge counts, press-and-hold, offline queue | `vitest` + Testing Library |

**Mandatory regression tests**
1. A decision for a `cline` approval can never resolve a `codex` approval (cross-agent isolation).
2. A replayed `approval.decision` is rejected and audited.
3. A decision with a mismatched `action_hash` is rejected.
4. Every failure in §6.3 produces the exact fail-closed output for each agent.
5. A query without `agent_type` fails the partitioning test (§8.3).
6. An expired approval resolves as `expired` and the hook denies.

---

## 14. Build phases

Each phase ends only when its **acceptance criteria** pass automatically (tests) or via the listed scripted manual check (§0.6).

### Phase 0 — Verification (do not skip)

Run each experiment, record the result in `docs/phase0-findings.md`, then apply the stated default/fallback.

| ID | Verify | Default if unconfirmed |
|---|---|---|
| P0-1 | Cline `PreToolUse.ps1` hook contract: exact stdin JSON, whether it blocks, and the exact stdout schema for allow/deny | Assume `{"cancel": bool, "errorMessage": str}`; if it cannot block, fall back to a wrapper that gates the tool via a deny + retry |
| P0-2 | Cline hook timeout and whether it is configurable | Assume 10 min; align D10 expiry |
| P0-3 | Codex `PreToolUse` hook support in `~/.codex/config.toml` / `hooks.json`: schema, blocking, exit-code semantics | Assume exit `0`=allow / `2`=deny; if unsupported, fall back to a Codex CLI wrapper |
| P0-4 | Codex hook timeout | Assume 10 min |
| P0-5 | Whether either agent exposes a post-tool hook (for `tool_result` events) | Assume no; synthesise `tool_result` (§12.1) |
| P0-6 | `git write-tree` snapshot behaviour on the owner's repos (Appendix A) | Assume validated; re-run the Appendix A script |
| P0-7 | Corporate proxy + TLS inspection with `truststore` from Python | Assume works; if not, allow a custom CA bundle path in config |
| P0-8 | Web Push delivery on the owner's phone (iOS vs Android) | Assume Android works; iOS requires A2HS |

**Acceptance:** `docs/phase0-findings.md` exists, every P0 row has a recorded result and a chosen path, and `docs/decisions.md` records any fallback taken.

### Phase 1 — Core daemon

Deliverables: `paths.py`, `config.py`, `db.py`, `crypto.py`, `protocol.py`, `audit.py`, `local_api.py` (auth + health), `cli.py` (`run`, `status`, `doctor`).

**Acceptance:** `agentd doctor` passes; local API rejects token-less requests (`401`); DB migrates and partitions by `agent_type`; unit tests green.

### Phase 2 — Approvals + policy

Deliverables: `approvals.py`, `policy.py`, `sessions.py`, `POST /v1/approvals`, async waiters, expiry, single-use, `action_hash`.

**Acceptance:** approve/deny/expire/timeout paths pass; replay and hash-mismatch rejected; fail-closed matrix (§6.3) verified for both agents.

### Phase 3 — Cline adapter

Deliverables: `adapters/cline/*`, `PreToolUse.ps1`, `install.py`, `agentd-cline-hook`.

**Acceptance:** a real Cline tool call blocks and is decided from `agentlink-sim`; deny produces `{"cancel": true}`; `agentd install cline` is idempotent.

### Phase 4 — Codex adapter

Deliverables: `adapters/codex/*`, `config_template.toml`, `install.py`, `agentd-codex-hook`.

**Acceptance:** a real Codex tool call blocks and is decided from `agentlink-sim`; deny exits `2`; `agentd install codex` is idempotent.

### Phase 5 — Diff engine + files

Deliverables: `gitdiff.py`, `files.py`, `watcher.py`, `workspace.changes.*`, `file.tree.*`, `file.content.*`.

**Acceptance:** diffs match `git diff` for modified/added/deleted files; non-git workspaces return a clear error; path traversal outside the workspace is refused.

### Phase 6 — Relay

Deliverables: `relay/*`, Dockerfile, compose, Caddyfile, pairing, envelope routing, buffering, Web Push.

**Acceptance:** two devices pair; envelopes route both ways; undelivered envelopes survive a relay restart; push arrives with no content; revoked device is dropped.

### Phase 7 — Live activity

Deliverables: `activity.py`, `POST /v1/events`, `activity.event`, `agent.status`, `sync.resume`.

**Acceptance:** events appear in order with monotonic `seq`; reconnect replays missed events; status derivation matches §12.3.

### Phase 8 — PWA

Deliverables: all screens/slices/components, service worker, push, pairing, settings.

**Acceptance:** Cline and Codex spaces are fully isolated (no cross-agent leakage in stores or UI); badges are correct; press-and-hold required for `high`; offline queue behaves per §11.5.

### Phase 9 — Hardening + packaging

Deliverables: security tests, rate limiting, redaction, rotation/revocation, Task Scheduler install, docs.

**Acceptance:** all §13 mandatory regression tests green; `agentd install` sets up a user-level scheduled task; README quickstart works on a clean machine.

---

## 15. Operations

### 15.1 Install (Windows, no admin)

1. `pipx install ./agentd` (or `pip install --user`).
2. `agentd install` writes config to `%USERPROFILE%\.agentlink\config.toml`, creates the local API token, and registers a **user-level** Task Scheduler entry (`agentd run`) that starts at logon and restarts on failure.
3. `agentd install cline` and `agentd install codex` register the hooks (idempotent; back up the original config first).
4. `agentd pair` prints a QR + 8-char code for the phone.

### 15.2 CLI

| Command | Purpose |
|---|---|
| `agentd run` | Run the daemon (foreground; used by the scheduled task) |
| `agentd status` | Show sessions, pending approvals, relay state, per agent |
| `agentd doctor` | Self-check: config, keys, DB, git, proxy, relay reachability, hooks installed |
| `agentd pair [--rotate]` | Pair a phone / rotate keys |
| `agentd devices [list\|revoke <id>]` | Manage paired devices |
| `agentd install [cline\|codex]` | Install hooks / scheduled task |
| `agentd away [on\|off]` | Toggle away mode (§9.4) |
| `agentd logs [--follow]` | Tail the daemon log |

### 15.3 Logs, backup, upgrade

- Logs: `%USERPROFILE%\.agentlink\logs\agentd.log`, rotating, **redacted** (S7). Never log plaintext payloads or keys.
- Backup: `%USERPROFILE%\.agentlink\agentd.db` + `keys\` (keys are the sensitive part; back up encrypted).
- Upgrade: stop the task, `pipx upgrade`, `agentd doctor`, restart. DB migrations run automatically and are forward-only.
- Relay: `docker compose up -d`; Caddy handles TLS. Back up the relay SQLite volume.

### 15.4 Proxy

`config.toml` accepts `proxy = "http://..."` or `"system"`; the relay client uses `truststore` for the OS trust store and honours `NO_PROXY`.

---

## 16. Configuration reference

### 16.1 `agentd` — `%USERPROFILE%\.agentlink\config.toml`

```toml
[relay]
url = "wss://relay.example.com/v1/ws"
proxy = "system"            # or an explicit URL
ca_bundle = ""              # optional custom CA for TLS inspection

[approvals]
timeout_seconds = 600       # aligned with hook timeout (D10)
default_expiry_seconds = 600
diff_preview_max_lines = 200

[activity]
heartbeat_seconds = 60
retention_days = 7

[policy]
default_effect = "ask"      # fail closed

[agents.cline]
enabled = true
hook_timeout_seconds = 600

[agents.codex]
enabled = true
hook_timeout_seconds = 600

[logging]
level = "info"
```

### 16.2 `relay` — environment

`RELAY_HOST`, `RELAY_PORT`, `RELAY_DB_PATH`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `ENVELOPE_TTL_HOURS` (default 24), `RATE_LIMIT_PER_MIN`.

### 16.3 PWA — Settings screen

Relay URL, device name, quiet hours, notification toggle, theme (follows agent), key rotation, unpair.

---

## 17. Failure modes and error handling

| Failure | Behaviour |
|---|---|
| `agentd` down | Hooks fail closed (§6.3); PWA shows `offline`; relay buffers envelopes |
| Relay down | PC retries with exponential backoff + jitter; hooks fail closed; PWA shows disconnected |
| Phone offline | Approvals expire; PC denies; PWA reconciles on reconnect |
| Hook crash / bad JSON | Fail closed; error written to `audit_log` and the daemon log |
| Duplicate hook invocation | Idempotent by `action_hash` within the session window |
| DB locked | WAL + busy timeout; retry once, then fail closed |
| Clock skew | All expiry checks use the PC clock; the phone's countdown is advisory only |
| Unknown protocol major | Reject with `unsupported_version`; hooks fail closed |
| Workspace not a git repo | Diff returns a clear error; file browsing still works |
| Path outside workspace | Refused (`403`) and audited |

---

## 18. Adapter interface (future agents)

Adding an agent must not require changes to the daemon core, the relay, or the PWA's segregation model.

### 18.1 Contract

```python
class BaseHookAdapter(Protocol):
    agent_type: AgentType          # enum member, e.g. AgentType.CODEX

    def parse(self, stdin_json: dict) -> Action: ...
    def render_allow(self, action: Action) -> tuple[str, int]: ...   # (stdout, exit_code)
    def render_deny(self, action: Action, reason: str) -> tuple[str, int]: ...
    def install(self, config_path: Path) -> None: ...
```

- `Action` is the normalised model in `protocol.py` (`tool`, `action`, `workspace_path`, `session_id`, `agent_type`).
- `render_allow` / `render_deny` encapsulate the agent-specific output (Cline JSON vs Codex exit codes).
- `install` is idempotent and backs up the target config.

### 18.2 Registration

1. Add a member to `AgentType` (`cline`, `codex`, `…`).
2. Add `adapters/<name>/` with `hook_cli.py`, `mapping.py`, `install.py`, and any shim.
3. Add a console script in `pyproject.toml` (`agentd-<name>-hook`).
4. Add a theme + badge in the PWA (`AgentBadge`, `agentsSlice`) and a tab in `NavTabBar`.
5. Add contract fixtures and a Phase-0-style verification row.

### 18.3 Rules

- An adapter never talks to the relay directly; it only calls the local API.
- An adapter never decides policy; it only reports the action and renders the daemon's verdict.
- Every adapter must fail closed on any error.

---

## Appendix A — Git tree snapshot diff engine

Validated approach (D6). Independent of any agent.

### A.1 Algorithm

1. On session start, record the workspace's current tree: `git -C <ws> write-tree` → `base_tree`. Also record `head_tree = git rev-parse HEAD^{tree}`.
2. To compute "since task start": `git diff-tree -r --no-commit-id --name-status <base_tree> <current_tree>` where `current_tree` is a fresh `write-tree` (which includes staged + unstaged tracked changes).
3. To compute "since HEAD": diff `head_tree` against `current_tree`.
4. Per-file line counts: `git diff --numstat <a> <b> -- <path>`.
5. Per-file patch: `git diff <a> <b> -- <path>` (truncated to `diff_preview_max_lines`).

### A.2 Untracked and non-git files

- `write-tree` ignores untracked files. To include them, run `git add -N .` (intent-to-add) before `write-tree`, or enumerate untracked files with `git ls-files --others --exclude-standard` and report them as `added` with a synthetic diff.
- If the workspace is not a git repo, the diff endpoint returns `error.code = "not_a_git_repo"`; file browsing (§11.3 Files) still works.

### A.3 Safety

- All git invocations use `-C <workspace_path>` and a fixed argument list (no shell interpolation).
- Paths are resolved and checked to be inside `workspace_path` before any read.
- Binary files are reported as `binary` with no patch.

### A.4 Validation script

`scripts/test-hooks.py` (and a `gitdiff` fixture test) must confirm that the engine's output matches `git diff` for a repo containing modified, added, deleted, renamed, and untracked files.









