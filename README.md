# AgentLink

Monitor and approve coding-agent activity (Codex, OpenCode) from your phone, with
strict per-agent segregation.

The full specification lives in [`docs/SPEC.md`](docs/SPEC.md). Design decisions
are recorded in [`docs/decisions.md`](docs/decisions.md).

## Status

**Slice 1 complete** — the local approval loop works end to end:

```
agent hook  ->  agentd (loopback API)  ->  decision  ->  hook unblocks
```

| Component | State |
|---|---|
| `agentd` daemon (local API, approvals, policy, audit, sessions) | ✅ slice 1 |
| Codex hook adapter | ✅ slice 1 (contract pending Phase 0) |
| OpenCode session monitoring (chat + activity, no gating) | ✅ |
| **Send prompts into OpenCode sessions from the phone** | ✅ (D-026) |
| **Webhook notifications (approvals + OpenCode turn completions)** | ✅ (D-027) |
| **Autostart at logon (`agentd install`)** | ✅ (D-027) |
| `agentlink-sim` (terminal phone) | ✅ slice 1 |
| Off-LAN access via ngrok tunnel | ✅ `agentd run --tunnel` |
| Session-centric phone UI (sessions → chat / activity / approvals) | ✅ |
| Approvals inline in the chat timeline | ✅ |
| **Installable as an app (PWA shell) over https** | ✅ |
| Transcript ingestion (Codex + OpenCode, both verified against real data) | ✅ |
| Live activity stream | ✅ SSE + 2s poll fallback |
| Cloud relay + E2E crypto | ⏳ slice 2 — the security step before heavy remote use |
| Real hook installation (`agentd install`) | ⏳ slice 3 |
| Git diff engine + file browser | ⏳ slice 5 |
| Web push notifications | ⏳ `docs/mobile-app-plan.md` M2 |

## Layout

```
agentd/     PC daemon (Python) — the single source of truth
sim/        agentlink-sim, a terminal stand-in for the phone
scripts/    Phase 0 probes and the slice 1 demo
docs/       SPEC.md, decisions.md, phase0-findings.md
relay/      cloud relay (slice 2)
pwa/        phone web app (slice 7)
```

## Quick start

```powershell
pip install -e ./agentd
pip install -e ./sim

agentd doctor          # check the setup
agentd run             # start the daemon (leave this running)
agentd install         # optional: start the daemon automatically at logon
```

In a second terminal:

```powershell
agentlink-sim status
agentlink-sim list --state pending
agentlink-sim watch
```

### See the whole loop

With `agentd run` in one terminal:

```powershell
.\scripts\demo-slice1.ps1
```

That fires a synthetic Codex hook (which blocks), shows the pending approval,
approves it, and prints the hook's response.

### Try it by hand

```powershell
# terminal 2 — this blocks until you decide
.\scripts\fake-hook.ps1 -Agent codex -Tool shell -Command "rm -rf build/"

# terminal 3
agentlink-sim list --state pending
agentlink-sim approve <id>
```

## Reaching the phone

The daemon always listens on loopback. Two ways to get your phone to it:

| Mode | Command | Works when |
|---|---|---|
| Same Wi-Fi | `agentd run --lan` | the phone is on the same network |
| Anywhere | `agentd run --tunnel` | the phone is on mobile data or another network |

`--lan` binds `0.0.0.0` and prints `http://<your-lan-ip>:47800/#t=<token>`.

`--tunnel` keeps the daemon on `127.0.0.1` and puts an
[ngrok](https://ngrok.com) tunnel in front of it, printing the public URL
instead. Because ngrok forwards to loopback, **`--tunnel` never needs `--lan`**
— the daemon is never exposed on your LAN.

### Setting up ngrok

```powershell
winget install ngrok.ngrok
ngrok config add-authtoken <your-token>   # free account
agentd doctor                             # should now show: ngrok [ok - ...]
```

Then:

```powershell
agentd run --tunnel
```

If you would rather run ngrok yourself, start `ngrok http 47800` in another
terminal and ask agentd for the URL:

```powershell
agentd tunnel
```

Notes:

- The token travels in the URL fragment (`#t=...`), which browsers never send
  to the server, so it stays out of ngrok's access logs.
- ngrok's free tier shows a one-time browser warning page. Tap through it once;
  the app's own requests send `ngrok-skip-browser-warning`, so they are never
  intercepted.
- **A tunnel URL is public.** Anyone holding the token can approve actions.
  Stop the tunnel when you are done.
- If ngrok is missing or fails to start, `agentd run --tunnel` says so and
  serves locally anyway — the tunnel is a convenience, not a gate.

## The phone UI

Open the URL printed by `agentd run` (or `--lan` / `--tunnel`) and paste the
token. The app is session-centric:

- **Home** lists every session the daemon knows about — Codex threads and
  OpenCode sessions — with its agent, title, workspace, message count and how
  many approvals are waiting. Filter with the `All / Codex / OpenCode` chips.
- **Tap a session** to open it. Three tabs:
  - **Chat** — the real conversation, with approvals interleaved in order: your
    prompts, the agent's replies, its reasoning, every tool call with its
    output, and an inline card wherever the agent had to ask. The card shows
    the decision that was taken — *pending*, *allowed*, *denied*, *expired* or
    *cancelled* — and stays live while it is pending, so you can decide without
    leaving the conversation (D-020). Cards are built from the approval
    records themselves, so a decision taken before a restart (or by a daemon
    that predates activity recording) still shows in the conversation.
    Opening a session lands on the newest
    turn, and a card that is still pending is pinned to the bottom of the chat
    so it is never off-screen. Long sessions keep working: the window is the
    newest few hundred events and polls append only what arrived (D-023), so
    the live end never scrolls out of view.
  - **Activity** — the raw event stream for that session, including approvals.
  - **Approvals** — anything still waiting, with the same press-and-hold rule
    for high-risk actions (D-004).
- Routing is hash-based (`#/s/<session_id>`), so the phone's back gesture works.

Policy auto-allow / auto-deny decisions — a read inside the workspace, say —
never become cards, because there would be one per file read. They stay in
**Activity**.

### Being summoned

You don't have to watch the app. Set a webhook in `~/.agentlink/config.toml`
and the daemon POSTs when something needs you — a Codex approval request, or
an OpenCode turn finishing (the prompt you sent from the phone is done):

```toml
[notifications]
webhook_url = "https://ntfy.sh/your-secret-topic"   # or any JSON endpoint
webhook_format = "ntfy"                               # or "generic"
```

Bodies are redacted before they leave the machine (S7) — a command with an
embedded token reaches the webhook as `Authorization: ***REDACTED***`. Treat
public ntfy topics as semi-public, or self-host. `agentd install` makes the
daemon start at every logon (a hidden launcher in your Startup folder — no
admin rights needed), and `agentd uninstall` removes it.

### Typing into a session

OpenCode sessions can be **driven** from the phone (D-026): the composer
under the chat queues a prompt with the same background service the desktop
TUI uses, so the turn runs on the PC — the prompt and the agent's reply
stream back into the chat like any other message, live over SSE. One prompt
runs per session at a time, prompts are audited, and Codex sessions answer
with a clear "not yet supported" until their channel lands
([`docs/mobile-app-plan.md`](docs/mobile-app-plan.md) is the roadmap —
pairing the app, push notifications, the secure transport, and new sessions
from the phone).

The chat is the conversation, not the harness. A Codex turn boundary
(`task_started` / `task_complete`) and the `exec` / `wait` polling loop behind a
single shell command are recorded as lifecycle and `tool_plumbing` events: they
are in **Activity**, never in **Chat** (D-021).

The chat does **not** come from the hook — a `PreToolUse` hook only ever sees
tool calls. The daemon reads each agent's own transcript instead (D-016):

| Agent | Read from |
|---|---|
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` (+ thread names from `~/.codex/session_index.jsonl`) |
| OpenCode | `~/.local/share/opencode/opencode.db` — its SQLite session store, opened read-only |

Override with `CODEX_HOME` / `OPENCODE_DB` if your agents store state
elsewhere. Reading is strictly read-only and best-effort: a malformed file or
a locked database is skipped, never fatal. OpenCode's reader never touches
the `account` / `credential` tables, which hold secrets.

OpenCode is **monitor-only** (D-025): it has its own permission system, no
AgentLink hook is installed for it, and the daemon only mirrors its sessions
into the chat and activity feed.

## How a decision is made

1. Codex calls its `PreToolUse` hook; the adapter normalises the payload
   into an `Action` and POSTs it to `http://127.0.0.1:47800/v1/approvals`.
2. The policy engine scores it. Reads inside the workspace are allowed
   outright; everything else follows `[policy] default_effect` (`ask` by
   default — D-024).
3. If asked, the daemon records the approval and **blocks the hook**.
4. You decide from the phone (or `agentlink-sim`).
5. The hook exits `0` to allow, or exits `2` (with the reason on stderr,
   which Codex surfaces to the model) to block.

**Everything fails closed.** If the daemon is unreachable, the payload is
malformed, the token is missing, or the approval times out, the action is
denied. Three config knobs make that explicit: `agentd away` (or `POST
/v1/away`) denies asks immediately instead of holding the agent for the whole
timeout; `agents.<name>.enabled = false` fails that agent's hooks closed on
arrival; commands are stored and shown redacted (S7), so a secret pasted into
a command never reaches the phone or the database in the clear.

Housekeeping runs in the background: sessions that have been quiet for 15
minutes drop their "active" badge, and `[activity] retention_days` prunes old
events, audit rows and decided approvals (`0` disables pruning; pending
approvals are never touched).

## Tests

```powershell
cd agentd
python -m pytest -q
```

177 tests, including a real end-to-end run (a live daemon plus the real hook
entrypoint and a real-socket SSE stream), the cross-agent segregation check,
the tunnel wiring (ngrok is faked, so the suite passes without it
installed), the transcript readers driven by synthetic Codex rollouts
(including a resumed thread split across several rollout files, and
incremental re-scan from persisted high-water marks) and a synthetic
OpenCode session database, the session-input guard rails, the notification
payload shapes and triggers, and the chat timeline fold run under node
against the page's own JavaScript.

## Phase 0

The exact Codex hook contract is still formally unverified. Run the probe on
the machine that has the agent installed and fill in
[`docs/phase0-findings.md`](docs/phase0-findings.md):

```powershell
.\scripts\probe-hook.ps1 -Agent codex
```

Until then the adapter uses a deliberately tolerant parser that accepts
several plausible payload shapes. (OpenCode needs no probe — it is read
straight from its database, verified against the real thing.)
