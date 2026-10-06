# Work plan & tracker

*The concrete work order for the remote-OpenCode app. Every item is tracked
here and the file is updated in the same commit as the work it describes.
Strategic background lives in `mobile-app-plan.md` (M0–M5) and
`remote-codex-direction.md`; this file is the execution view.*

**Legend:** ☐ todo · ◐ in progress · ☑ done (date) · ✗ dropped (reason)

---

## P1 — "Never miss a turn" (approved)

The two things that make the remote feel dead when you are not babysitting
it: you must keep the app open to notice anything, and the daemon must be
restarted by hand after every reboot.

- ☑ **1a. Webhook notifications** (2026-10-03) — config `[notifications]`, a
  best-effort `Notifier` (never blocks or breaks the approval path), fired
  on `approval_requested` (Codex waiting on you) and on OpenCode **turn
  completion** (your remote prompt finished → phone buzzes). Two payload
  shapes: provider-agnostic JSON and `ntfy`. Bodies are redacted (S7) —
  the webhook is a third party. Verified live against a local listener.
- ☑ **1b. `agentd install`** (2026-10-03) — a hidden-launch `.vbs` in the
  *user's* Startup folder (`schtasks /SC ONLOGON` is denied for standard
  users, so the folder is the no-admin surface). `agentd uninstall`,
  a `doctor` line, and a live end-to-end proof: the installed launcher
  was executed via wscript and brought the daemon up hidden.
- ☑ **1c. Loud degradation** (2026-10-03) — `SessionDetail.input_available`
  + a disabled composer with the reason ("OpenCode CLI is not on the
  PC's PATH"), instead of a surprise 503 on send.

## P2 — "Worth typing on" (pending your decision)

- ☐ **2a. Tailscale/overlay mode** — daemon stays on loopback, the phone
  joins the tailnet; no public URL, no token in fragments. Mostly docs +
  `doctor` checks. **Open question for you: do you already run Tailscale,
  or should `doctor` walk you through it?**
- ☐ **2b. Slice-2 relay** (E2E crypto, per-device keys, pairing) — the
  proper long-term answer; multi-session effort, worth it once this is a
  daily driver. Separate approval required before starting.

## P3 — Native OpenCode client (M1, queued)

- ☐ **P0-12 probe:** pair once (`opencode pair`), fetch `/openapi.json`
  with the token, catalogue operations (prompt, abort/interrupt, create
  session, attachments) and the token's lifetime.
- ☐ Replace the CLI shim with the HTTP client; add the **Interrupt** button
  on running turns.

## P4 — Codex quality-of-life (queued, small)

- ☐ Free-text **deny reason** on the phone card (the hook already surfaces
  it to the model — cheap steering).
- ☐ **Diff preview** in Codex approval cards (the hook already carries the
  patch; `ActionDetail.diff_preview` is still unused).

## P5 — Approval ownership toggle (user request, 2026-10-06)

- ☑ **5a. Phone-approvals toggle** (2026-10-06, D-028) — the ask that made
  this possible was found in `codex.exe` itself: the hook protocol supports
  `permissionDecision: "ask"`, which hands the decision back to Codex's
  built-in approval prompt. Off (default) = refined native prompt on the
  desktop; on = AgentLink cards on the phone. One-tap chip on the home
  screen, `agentd remote [--off]`, persisted across restarts. Away and
  `[policy] default_effect` still override. Pending: the user's 30-second
  desktop acceptance test (native prompt appears with the toggle off).

## Explicitly not now

Native app shell (PWA suffices), writing to `opencode.db`, input for Codex
(waiting on the resume/app-server probe), Cline resurrection.

---

## Change log

- 2026-10-03 — plan written; P1 approved and started (1a ◐).
- 2026-10-03 — P1 complete (1a/1b/1c ☑): notifications verified against a
  local listener, the Startup-folder launcher executed live via wscript
  and brought the daemon up hidden, the composer now degrades loudly.
  177 tests. Also fixed two flakes for good: the live e2e daemon no longer
  watches the *real* agents during tests (it was ingesting live sessions
  and racing the stream test), and transcript tests bump file mtimes to a
  deterministic future instead of "now". Next: P2a needs your call
  (Tailscale?); P3 probe P0-12 queued.
- 2026-10-05 — investigated "native Codex prompt disappears with the hook
  enabled" (user report). Confirmed and documented in P0-2: a matching
  PreToolUse hook **replaces** the built-in approval prompt; native pending
  approvals are not observable in Codex's state DBs, so one call can have
  only one decision-owner. Documented the desktop surface (same web app in
  a browser / installed as a PWA, or `agentlink-sim`) in the README.
  Open follow-up if the literal native prompt is wanted back: probe for a
  hook "fall back to native" response (needs desktop-UI participation).
- 2026-10-06 — **P5 ☑ (D-028): the phone-approvals toggle.** Found the
  mechanism in `codex.exe`'s binary strings: the PreToolUse hook protocol
  has a JSON output with `permissionDecision: "allow" | "deny" | "ask"` —
  and `ask` hands the decision back to Codex's built-in approval prompt.
  So the hook now answers `ask` when the toggle is off (native, refined
  desktop prompt; nothing parks on the phone) and gates on the phone when
  it is on. One-tap chip on the home screen, `agentd remote [--off]`,
  `POST /v1/remote`, persisted across restarts. Away and hard-deny policy
  still override. 184 tests. Pending: the user's 30-second desktop
  acceptance test.
