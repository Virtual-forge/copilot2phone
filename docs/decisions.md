# Decisions log

Append-only. Newest at the bottom. Each entry: what was decided, why, and where it is specified.

## D-001 — Live activity is in scope (v1)
**Decision:** v1 streams discrete per-agent events (task started, tool call, tool result, message, task finished, error) plus status/heartbeat, so the phone stays in sync with what the agent is doing — not only with approvals.
**Why:** The original spec only delivered approvals + diffs; "stay in sync" was implied by a `feed` slice but never specified.
**Spec:** §1.2.7, §7.3, §12.

## D-002 — Token-by-token streaming is out of scope
**Decision:** v1 streams discrete events, not raw model deltas.
**Why:** Keeps bandwidth, E2E payload size, and battery reasonable; deltas add little for remote approval/monitoring.
**Spec:** §1.3.

## D-003 — Approval expiry / hook timeout default = 600 s
**Decision:** 10 minutes, configurable per agent.
**Why:** Aligns D10 with the assumed hook timeout; long enough to answer from a phone, short enough to fail closed quickly.
**Spec:** §16.1, §14 P0-2/P0-4.

## D-004 — High-risk approvals require press-and-hold ≥ 1.5 s
**Decision:** `high` risk needs a sustained press to approve; a plain tap only denies. Keyboard equivalent: hold `Enter`.
**Why:** Implements D12's "press-and-hold" with a concrete, testable threshold; prevents accidental approvals.
**Spec:** §11.4, §11.7.

## D-005 — Heartbeat window = 60 s; offline at 3×
**Decision:** `running` if an event arrived within 60 s; `offline` after 180 s or PC disconnect.
**Why:** Gives a responsive status pill without excessive traffic.
**Spec:** §12.3, §16.1.

## D-006 — Activity retention = 7 days; audit log never pruned
**Decision:** `activity_events` pruned after 7 days; `audit_log` is append-only and never pruned.
**Why:** Bounds DB growth while preserving the security-relevant record (S10).
**Spec:** §12.4, §8.1.

## D-007 — `away` mode never auto-approves
**Decision:** `away on` keeps `allow` rules, converts `ask` to deny unless answered in time, and tags decisions with `mode: "away"`.
**Why:** The spec listed an `away` CLI command but never defined it; fail-closed is the safe reading of D5.
**Spec:** §9.4.

## D-008 — Untracked files included in diffs via intent-to-add
**Decision:** Run `git add -N .` before `write-tree` (or enumerate with `git ls-files --others --exclude-standard`) so new files appear as `added`.
**Why:** `write-tree` alone ignores untracked files, which would hide agent-created files.
**Spec:** Appendix A.2.

## D-009 — Non-git workspaces: diff errors, file browsing still works
**Decision:** Diff returns `error.code = "not_a_git_repo"`; the read-only file browser remains available.
**Why:** D6 is git-based, but §1.2.2 promises file browsing; degrading gracefully is better than failing the whole workspace.
**Spec:** §17, Appendix A.2.

## D-010 — Partitioning key includes `pc_device_id`
**Decision:** Sessions and approvals carry `pc_device_id` in addition to `(agent_type, session_id, workspace_path)`.
**Why:** §1.3 allows multiple PCs; without a device id, two PCs could collide on `session_id`/`workspace_path`.
**Spec:** §7.4, §8.1.

## D-011 — Push notifications carry no action buttons
**Decision:** Notifications never include an "Approve" action; tapping opens the relevant agent space.
**Why:** D12 forbids auto-approve; an action button would bypass the explicit-tap requirement.
**Spec:** §10.

## D-012 — Protocol versioning is major-only, additive within a major
**Decision:** Unknown majors are rejected; unknown optional fields are ignored.
**Why:** Allows forward-compatible additions without breaking older clients.
**Spec:** §7.5.

## D-013 — `high` risk is `ask`, not auto-deny
**Decision:** The default policy auto-allows only `file_read` inside the workspace. Everything else — including `high` risk — is `ask`, so it reaches the phone. A `high`-risk approval still fails closed if it is not answered before the timeout.
**Why:** §9.3 said "deny-by-default for `high`", which read literally would mean high-risk actions never reach the phone and would contradict D-004 (press-and-hold for high risk). "Deny-by-default" is therefore interpreted as *the default outcome when unanswered is deny*, not *never ask*.
**Spec:** §9.3, §9.4, D-004.
**Implemented:** `agentd/src/agentd/policy.py` (`PolicyEngine.evaluate`).

## D-014 — Slice 1 ships the local approval loop only
**Decision:** Slice 1 delivers `agentd` (local API, approvals, policy, audit, sessions), both hook adapters, and `agentlink-sim` talking to the daemon over loopback. No relay, no E2E crypto, no PWA, no diff engine, no activity stream.
**Why:** Proves the riskiest part (blocking hook + fail-closed state machine) before investing in transport and UI. The `Action`/`ApprovalOutcome` models are already the wire format, so slice 2 swaps the transport without touching the state machine.
**Spec:** §14 Phase 1.
**Deferred to:** slice 2 (relay + crypto), slice 3–4 (real hook installation), slice 5 (diff/files), slice 6 (activity), slice 7 (PWA).


## D-015 — Off-LAN access uses an ngrok tunnel, not a relay

**Decision:** Until the slice 2 relay exists, `agentd run --tunnel` exposes the local API through an ngrok tunnel. The daemon keeps listening on `127.0.0.1`; ngrok forwards to loopback, so `--tunnel` never implies `--lan`. `agentd tunnel` reports the public URL of an ngrok agent the user started themselves, and `agentd doctor` reports whether ngrok is available.

**Why:** The phone demo has to work when the phone is not on the same Wi-Fi, and the relay is not built yet. A tunnel is a few dozen lines and reuses the existing bearer-token auth, whereas a relay would mean designing pairing and E2E crypto now. Keeping the daemon on loopback also means the LAN is never exposed, which is strictly safer than `--lan`.

**Consequences:** The public URL is reachable by anyone holding the token, so the CLI prints an explicit warning. ngrok's free tier injects a browser warning page; the web UI sends `ngrok-skip-browser-warning` so its own requests are not intercepted. A missing or failing ngrok is reported and the daemon still serves locally — the tunnel is a convenience, not a gate. This is a stopgap: slice 2 replaces it with the relay, and the tunnel is expected to survive only as a developer convenience.

**Spec:** §14 Phase 1 (demo), §7 (transport, deferred to slice 2).
**Implemented:** `agentd/src/agentd/tunnel.py`, `agentd run --tunnel`, `agentd tunnel`, `agentd doctor`.

