# Remote Codex — direction notes

*The vision: the phone becomes a live front-end to the desktop agent — watch
the conversation, decide approvals, and eventually **type into the same
session**. This file records the suggested path from "remote gate" to "remote
Codex", in tiers ordered by value-per-effort. It extends the README's slice
table (relay = 2, diff engine = 5, PWA = 7); it does not replace it.*

## Where we are — Tier 0: observer + gate (done)

The phone shows the real conversation (chat from the agent's own transcripts),
the raw activity feed, and inline approval cards carrying the decision taken.
Ingestion is incremental and measured in milliseconds, the chat window is
anchored to the live end, and the daemon answers immediately on start. What
the phone *cannot* do yet: notice you without being open, or say anything
back to the agent.

## Tier 1 — make it feel live (small, immediate)

1. **SSE stream.** A `/v1/stream` endpoint (per-session, plus a global
   "something changed" ping) and an `EventSource` in the page. Kills the 2 s
   poll latency in the open tab and is a prerequisite for anything
   interactive. ~a day.
2. **Push notifications.** `[notifications] webhook_url` fired on
   `approval_requested` and on expiry → ntfy / Pushover / a private Discord
   channel. The single biggest "remote" win: you stop watching the app and
   start being summoned. ~half a day; the real web-push version arrives with
   the slice-7 PWA.
3. **Diff preview in the approval card.** The hook already receives the
   patch/content (`Action.raw`); surface it through the existing
   `ActionDetail.diff_preview` field (and the already-parsed
   `[approvals] diff_preview_max_lines`), render it in the card and in
   `agentlink-sim show`. File edits stop being approved blind. ~a day.

## Tier 2 — steering through the channel that already exists (small)

The deny reason is *already delivered to the model*: Codex surfaces hook
stderr (exit 2) to the agent, so a typed decision is a
way to talk to the agent today, with zero new plumbing:

- Add a free-text **reason / reply** field to the phone card and to quick-reply
  presets ("use the venv python", "skip the cleanup", "stop — ask me first").
  `POST /v1/approvals/{id}/decision` already carries `reason`; the hook already
  forwards it. ~a day.

This is "steer the agent from your phone" without touching session internals.

## Tier 3 — typing into the session (the real goal)

**OpenCode is now the front-runner here.** Probing the installed v2.0.22
(2026-10-03) found first-class remote surfaces: `opencode serve` ("start the
v2 API and web server"), `opencode api` (call the running server by OpenAPI
operation), `opencode run --session <id> --prompt "..."` (non-interactive
message into an existing session), and — inside its own database —
`session_inbox` / `session_pending` tables with delivery semantics, i.e.
queued session input is a *native concept*, not a hack. The first probe
sequence is therefore: capture `opencode serve`'s OpenAPI
(`opencode api` with no args lists operations), find the session/prompt
operation, and check whether the TUI and a server client can drive the same
session concurrently (P0-11). If yes, Tier 3 for OpenCode is days, not
weeks: an input endpoint on agentd that proxies to the local opencode
server, a composer on the phone, every injected input audited and mirrored
into the activity feed as your own turn. No PTY, no scraping.

For Codex, sessions are processes reading stdin in a terminal; rollouts are
Codex's append-only output, so they cannot be written back into (D-016). Two
candidate channels — **probe before building**:

**(a) Preferred — Codex's own protocol.** Recent Codex builds ship an
app-server / MCP surface (JSON-RPC over stdio) that its own IDE extension
uses, where threads, turns and interrupts are first-class. If the installed
version has it, agentd speaks JSON-RPC: real turns sent from the phone, exact
session correlation, no terminal scraping. Probe P0-9: inspect
`codex --help` for `app-server` / `mcp` / `proto` subcommands and capture the
handshake.

**Probe result (2026-10-03):** on this machine Codex runs as the **VS Code
extension** (`openai.chatgpt` / `openai.codex-audio`); there is no `codex`
CLI on PATH, and `~/.codex` shows the modern layout (plugins, MCP OAuth
locks). The extension talks to the bundled core over exactly the app-server
protocol — so the CLI route means installing the standalone CLI
(`npm i -g @openai/codex`) and speaking JSON-RPC to it directly.

**(b) Fallback — agentd hosts the session.** `agentd codex` launches Codex in
a ConPTY (pywinpty on Windows), mirrors the output to the phone, and exposes
`POST /v1/sessions/{id}/input` writing keystrokes; the phone gets a composer.
This makes agentd the *host*, which also solves slice-3 hook installation
(agentd wires its own hooks at spawn) and gives perfect session-id correlation
instead of payload guessing. Note: with the IDE-extension setup the extension
owns the running session's input, so (b) applies to *new* CLI-hosted sessions,
not to typing into one already open in VS Code.

**(c) Stepping stone that fits the current setup — phone-queued follow-ups
via resume.** Codex continues a session through *resume*, and a resumed thread
reuses the session id and writes a new rollout file — the exact shape the
transcript layer already merges. So agentd can, without touching any running
process: park a message typed on the phone, watch the rollout until the turn
ends, then `codex resume <session_id> "<message>"` (or the extension's
resume surface) and the message lands in the same conversation, visible on
both the desktop and the phone. Not real-time injection into a live turn, but
it is "input into the same session" with a small, safe blast radius and
works with sessions the IDE owns. Probe P0-10: confirm the installed Codex's
resume surface and whether a prompt can be passed non-interactively.

Either way:

- Every injected input is audited (`input.sent`), rate-limited, and shown in
  the Activity feed as your own turn.
- Dangerous input (interrupting a turn — the Ctrl+C equivalent) gets the same
  press-and-hold treatment as high-risk approvals (D-004).
- **Trust model:** today the bearer token can *approve* actions; Tier 3 lets
  it *author* them. That should not travel over ngrok with the token in the
  URL fragment — wait for the slice-2 relay with per-device keys.

## Tier 4 — a real remote client

- Start sessions from the phone (pick a workspace → agentd spawns Codex): the
  phone stops being a mirror.
- File browser + git diffs (slice 5): review edits at depth, not just the
  patch preview.
- The relay (slice 2) becomes the transport for all of the above.

## Prerequisites and probes

| # | Probe | Answers |
|---|---|---|
| P0-11 | OpenCode server: `opencode serve` + `opencode api` — the operation list, the session/prompt call, and whether a server client and the TUI can drive one session concurrently | **answered (2026-10-03, D-026):** the background service accepts prompts via `opencode run --session <id>` (verified into a real session); the HTTP API needs the pairing token (`opencode pair` → `/auth/connect/<code>` → `{token}`) — that's M1 of `mobile-app-plan.md` |
| P0-12 | Pair once, fetch `/openapi.json` with the token, catalogue operations (abort/interrupt, create session, attachments) and the token's lifetime | whether the CLI shim can be replaced by a native client |
| P0-9 | Codex surfaces: `codex --help`, `app-server` / `mcp` subcommands | whether Tier 3(a) exists in the installed version — partially answered: the CLI is not installed; the VS Code extension bundles the core |
| P0-10 | Resume surface: does `codex resume` (or the extension) accept a prompt non-interactively, and does the resumed rollout reuse the session id? | whether Tier 3(c) can be built cheaply |
| P0-1 / P0-2 | The still-blank hook contract tables (`docs/phase0-findings.md`) | how reliably the Tier-2 deny-reason channel reaches the model |
| P0-3 | Hook timeout ceilings | how long input may be buffered before the agent gives up |

## What *not* to do

- Don't write to `~/.codex/sessions/**` — the rollouts are Codex's output, not
  an input queue. (Reading OpenCode's `opencode.db` is fine; writing to it is
  what the `serve` API is for.)
- Don't ship Tier 3 over the public-tunnel-plus-token-in-fragment transport.
- Don't build input for OpenCode by scraping its TUI — its server API exists
  precisely so nobody has to.
