# Mobile app plan — the phone as a remote OpenCode

**End goal:** the phone is a *synchronized remote* for the desktop's OpenCode
sessions. Open the app, see the live conversation exactly as the desktop sees
it, type a prompt, watch the agent work on it — from the couch or from
another country. Codex keeps its approval gate; OpenCode becomes a
first-class thing you *drive*.

Each milestone is usable on its own; they stack in this order because each
one de-risks the next.

## M0 — synchronized remote (done, 2026-10-03)

- **Monitor:** chat, raw activity and inline approval cards for Codex and
  OpenCode, anchored to the newest end, incremental ingestion in
  milliseconds (D-018, D-023).
- **Steer:** a composer under the chat sends prompts into OpenCode sessions
  through the same background service the desktop TUI uses
  (`opencode run --session <id>`, fire-and-forget, one in-flight prompt per
  session, every send audited). The prompt and the agent's reply stream back
  into the chat through the transcript reader — verified end to end on a
  real session (D-026).
- **Live:** `GET /v1/stream` (SSE) pushes a tiny "something changed" notice
  per ingest; the phone refetches incrementally the moment anything happens,
  with the 2-second poll kept as the fallback.
- **Installable:** manifest, icon and a shell-only service worker make the
  page an app over https (the tunnel); the worker never caches `/v1`.

## M1 — native input transport

The CLI shim (`opencode run`) is deliberately dumb: it solves discovery and
authentication by borrowing OpenCode's own. Replace it with a proper client
for the background service's HTTP API:

- The pairing flow (`opencode pair` → `/auth/connect/<code>` → `{token}`) is
  the documented way to get a long-lived client token; with it, agentd can
  read the OpenAPI directly and speak the full operation set.
- Wins: no PATH dependency, no process per prompt, structured replies,
  richer operations — **abort/interrupt a running turn** (an "Interrupt"
  button on the phone), create sessions, list models, attach files.
- Probe first (P0-12): pair, fetch `/openapi.json` with the token, catalogue
  the operations, and confirm the token's lifetime/renewal.

## M2 — be summoned, not watching

- **Web push** through the service worker (VAPID keys, subscriptions stored
  in agentd's DB, push fired on `approval_requested` and on
  turn-completion). This is what makes it a *remote* rather than a tab you
  keep open. Android Chrome works today; iOS needs Add-to-Home-Screen
  (16.4+).
- Interim (an afternoon): `[notifications] webhook_url` → ntfy / Pushover /
  a private Discord channel for the same events.

## M3 — a transport worth typing on

Input changes the threat model: the bearer token can now *author* prompts,
not just approve actions. Before the app becomes the daily driver over the
internet:

- Replace ngrok-plus-token-in-fragment with either the **slice-2 relay**
  (E2E crypto, per-device keys, pairing — SPEC §14) — the proper path — or a
  private overlay (Tailscale/ZeroTier) as the pragmatic always-on link.
  agentd already only binds loopback, so an overlay is a one-liner on the
  phone.
- Rate-limit input per session; consider press-and-hold for a
  "dangerous workspace" flag; keep every send in the audit log (already done).

## M4 — full remote-client parity

- **New sessions from the phone**: pick a workspace (opencode's `project`
  table already knows them), create the session, send the first prompt.
- Multiple queued prompts per session (once the API supports it; today the
  endpoint guards at one in-flight).
- Attachments (`opencode run --file`) — share a file straight from the phone
  into a session.
- The git diff engine and file browser (slice 5) so edits can be reviewed at
  depth, not just as the patch preview.

## M5 — always-on

- `agentd install` (slice 3): registers the Codex hook and a Windows
  scheduled task so the daemon survives reboots; a watchdog restarts it.
- The composer degrades loudly: if the OpenCode service is down, the input
  endpoint 503s — the UI should disable the composer with the reason, not
  fail silently.

## Explicitly not

- No writing to `opencode.db` — input goes through OpenCode's own surfaces.
- No reverse-engineering the HTTP API's auth outside the pairing flow (M1
  does it the documented way).
- No native app shell until iOS push forces the issue — the PWA is the app.
- No input channel for Codex until its resume/app-server surface is probed
  (see `remote-codex-direction.md`); the deny-reason channel already gives
  basic steering there.
