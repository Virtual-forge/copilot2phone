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

## D-016 — Chat comes from the agent's own transcript files, not the hook

**Decision:** The `PreToolUse` hook is the *gate*; the agent's on-disk transcript is the *monitor*. Chat turns, reasoning and tool results are read from each agent's own history files by a new transcript subsystem (`agentd/src/agentd/transcripts/`), polled by `agentd/src/agentd/watcher.py`. The hook keeps doing only what it can: gating tool calls.

**Why:** A `PreToolUse` hook only ever sees tool calls. User prompts, assistant text, reasoning and tool *outputs* never pass through it, so a chat view built on the hook alone is impossible. Both agents already persist a complete transcript, so reading it is the only way to show the conversation without changing how the agents run.

**Consequences:** The daemon now reads files outside its own state directory. Readers are strictly read-only and never write to the agent's files. Codex is read from `~/.codex/sessions/**/rollout-*.jsonl` (thread names from `~/.codex/session_index.jsonl`); Cline from `%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<taskId>/`. Both locations are overridable via `CODEX_HOME` / `CLINE_TASKS_DIR` for tests. The Cline reader is written against the documented `api_conversation_history.json` / `ui_messages.json` shapes and has not yet been verified against a real task directory on this machine — it is tolerant and degrades to "no transcript" rather than failing.

**Spec:** §1.2.7, §12, §18.3.
**Implemented:** `agentd/src/agentd/transcripts/{base,codex,cline}.py`, `agentd/src/agentd/watcher.py`.

## D-017 — One activity stream; the chat is a filtered view of it

**Decision:** Every session event — chat turns, reasoning, tool calls, tool results, approvals, lifecycle — is stored as one `ActivityEvent` in the existing `activity_events` table, with a monotonic `seq` per `(agent_type, session_id)`. The chat view is the subset whose `kind` is in `CHAT_KINDS`; the activity feed is all of them. No separate `messages` table.

**Why:** A second table would duplicate every row and force a second sequence space, so ordering between "the chat" and "the feed" could disagree. One table means one ordering, one retention rule (D-006) and one ingest path. `activity_events` already existed in the schema and was unused.

**Consequences:** `activity_events` gained `role` and `text` columns; `sessions` gained `title`, `source`, `last_activity_at`, `message_count` and `last_seq`. `Database.migrate()` now runs an idempotent `_ensure_columns()` pass, because `CREATE TABLE IF NOT EXISTS` never alters an existing table.

**Spec:** §12.1, §12.2, §8.1.
**Implemented:** `agentd/src/agentd/activity.py`, `agentd/src/agentd/db.py`, `agentd/src/agentd/protocol.py`.

## D-018 — Transcript ingestion is idempotent, incremental and best-effort

**Decision:** Every transcript record maps to a deterministic `event_id` (`<agent>:<session>:<index>`), and `index` is the record's position within the *session*, not within one file: when a Codex thread is resumed into a new rollout file that keeps the same session id, the reader merges the files (oldest first) and numbers their records across the concatenation. The scan is incremental in both directions: an in-memory fingerprint (mtime + size per file) skips untouched files within a daemon run, and a per-file high-water mark persisted in `transcript_state` (`pos` — a byte offset just past the last complete line — plus `records`) means a changed file is read from that offset and a restart reads only tails. A whole ingest batch is written in one transaction, and sequence numbers are reserved in a single `UPDATE … RETURNING`, so two concurrent batches can never draw the same `seq`. A malformed line, file or directory is skipped and logged; it never aborts a scan or the daemon.

**Why:** The watcher re-reads files that agents are actively appending to, and restarts re-read everything. Without stable ids the chat would duplicate on every poll; without the fingerprint check a 600-record rollout would be re-parsed every two seconds; without the marks a restart re-read all of it, and the previous per-record commit cost ~4 ms of fsync per line — a changed 1476-record file re-wrote the whole file through SQLite every two seconds (6.4 s of DB work for an empty tail). The per-*file* index was also wrong on its own: Codex writes a new file on every resume (with no replay of the earlier turns), so two files produced the same ids and the shorter resume file overwrote the original's rows — most visibly flipping the original's record at the resume file's last index to that file's `task_complete`.

**Consequences:** A stored event is refreshed from its source only when its file is *fully* re-read — the first scan, a shrunk file, or a bump of `READER_VERSION` (which is how a change to a reader's mapping now takes effect, instead of implicitly on every scan); a tail read inserts only new rows and burns no `seq` (`sessions.last_seq` stays equal to the number of ingested events). The mark only advances past newline-terminated lines, so a line the agent is mid-writing is re-read next scan — torn appends are never skipped, only delayed. A file that shrank, or marks from an older `READER_VERSION`, invalidate the whole session's marks so the records are renumbered once instead of stored twice. Ordering the files by the session's own start timestamps (not mtime) keeps the oldest file in the low index range, which is also where rows written by the first release already lived: a re-scan reconciled them in place rather than orphaning them. Because the index spans files, appending to a file that is *not* the group's last would shift the later files' ids; Codex only ever appends to the active (last) file, so this does not arise in practice. Cline reads two small JSON files per task, so its reader reports no marks and is always re-read in full.

**Spec:** §12.2, §12.4.
**Implemented:** `agentd/src/agentd/watcher.py`, `agentd/src/agentd/activity.py`.

## D-019 — The phone UI is session-centric

**Decision:** The home screen is a list of sessions (agent, title, workspace, message count, pending count, last activity). Tapping one opens a detail view with `Chat | Activity | Approvals` tabs. The approval card and its press-and-hold rule (D-004) are unchanged; they now also appear inside the session they belong to. Routing is hash-based (`#/s/<session_id>`) so the phone's back gesture works.

**Why:** §11.3 already specified an agent space with `Approvals | Activity | Changes | Files`, and §12 a live activity stream. A flat approval inbox does not scale past a handful of pending items and gives no way to see what an agent actually did. Sessions are the natural unit: they are already the partitioning key (D-010) and the thing a user thinks in.

**Consequences:** `GET /v1/sessions` now returns enriched `SessionSummary` objects (it previously returned bare `SessionRecord`s; the `session_id` and `agent_type` fields are unchanged, so existing callers keep working). New endpoints: `GET /v1/sessions/{id}`, `GET /v1/sessions/{id}/messages`, `GET /v1/sessions/{id}/events`, and `POST /v1/events` for hooks and adapters to post activity. The `Changes` and `Files` tabs from §11.3 are still to come (slice C).

**Spec:** §11.3, §11.4, §12, §12.3.
**Implemented:** `agentd/src/agentd/webui.py`, `agentd/src/agentd/local_api.py`.

## D-020 — Approvals are inline in the chat timeline, not a separate view

**Decision:** The Chat tab renders the whole activity stream, not the `CHAT_KINDS` subset. The two events that describe one approval (`approval_requested`, `approval_decided`) fold into a single card that sits at the position of the *request* and shows the outcome — pending, allowed, denied, expired or cancelled. A lone `approval_decided` (a policy auto-allow or auto-deny, which never had a request) stays feed-only and never becomes a chat card. The `Approvals` tab remains as the "what needs me right now" inbox.

**Why:** The user's mental model is a conversation, and an approval is a turn in it: the agent asks, the human answers, the agent proceeds. Splitting that across tabs loses the causal thread — you cannot see *which* tool call the card belongs to. D-017 already put approvals in the same stream as chat, so interleaving them is a rendering change, not a data change.

**Consequences:** The chat orders by `ts` with `seq` as the tiebreak, because the watcher ingests transcript files in batches: an approval recorded live can otherwise receive a lower `seq` than the tool calls that preceded it. The `approval_requested` detail now carries `command`, `paths`, `cwd`, `reasons`, `expires_at` and `workspace_path`, and `approval_decided` carries `state` and `decided_by`, so a card is self-contained — after a decision the record is no longer pending, so a join against `/v1/approvals` would lose the command text. `GET /v1/approvals` gained a `session_id` filter, and a card's state is taken from the approval record when one exists, because the record survives a restart, a sweep and a truncated event window. Every approval *record* renders as a card, positioned by its request time, even when the matching request event is missing — the record is the only trace of a decision taken by a daemon that predates activity recording, across a restart, or outside the fetched window — while a lone `approval_decided` *event* (a policy auto-allow or auto-deny, which never had a record) stays feed-only and never becomes a chat card. `cancel_all()` and `sweep_expired()` now record a decision event; previously they did not, which would have left a card stuck on "pending" forever after a restart. A gated tool call still shows both its `tool_call` bubble and its card, because the hook's `Action` carries no call id to join them on.

**Spec:** §11.3, §12.1, §12.3.
**Implemented:** `agentd/src/agentd/webui.py`, `agentd/src/agentd/approvals.py`, `agentd/src/agentd/db.py`, `agentd/src/agentd/local_api.py`.


## D-021 — The chat is the conversation, not the harness

**Decision:** `ActivityKind` gains `TASK_STARTED`, `TASK_FINISHED` and `TOOL_PLUMBING`, and none of them is in `CHAT_KINDS`. The Codex reader maps `task_started` / `task_complete` / `turn_aborted` to the two lifecycle kinds, and maps the `exec` / `wait` unified-exec calls and their outputs to `TOOL_PLUMBING`. They stay in the activity feed, which is the unfiltered stream.

**Why:** A real Codex session showed 133 events of which 94 were harness mechanics — 24 turn markers and 70 `exec` / `wait` calls and "Script running…" results — so the chat was mostly `task started`, `task complete`, `exec` and `wait` bubbles with the actual conversation buried between them. Those events are the agent runtime talking to itself: `exec` runs a generated JavaScript snippet with the real command buried inside it, and `wait` only polls the cell it started, so one shell command becomes a burst of calls. D-017 already said the chat is the subset of the stream whose kind is in `CHAT_KINDS`; the vocabulary was simply too coarse to express the distinction, because `NOTE` conflated "a system note worth showing" with "a turn boundary" and `TOOL_CALL` conflated "a tool the user cares about" with "exec polling". `task_started` / `task_finished` are also the names SPEC §12.1 already uses.

**Consequences:** The kinds are additive and `kind` is stored as text with no `CHECK` constraint, so no migration is needed. `message_count` and `GET /v1/sessions/{id}/messages` shrink accordingly, because both filter on `CHAT_KINDS`. The classification is derived from the transcript, so it is only applied on ingest: rows already stored keep the kind they were written with, and a session ingested before this change has to be re-ingested to pick it up. A tool result carries only a `call_id`, so the reader remembers each call's name while it walks the file in order to classify the matching output.

**Spec:** §12.1, §12.2.
**Implemented:** `agentd/src/agentd/protocol.py`, `agentd/src/agentd/transcripts/codex.py`.

## D-022 — The chat opens at the newest turn and pins pending cards

**Decision:** Opening a session (or switching back to the Chat tab) scrolls to the bottom of the transcript. A card whose state is still `pending` sorts after every message and every decided card, so it sits at the bottom of the chat. A decided card keeps D-020's position at its request.

**Why:** The chat reads oldest-first, so a session with any history opened on its first message and the user had to scroll the whole transcript to reach what was happening now. And an approval that still needs an answer is the one thing on the screen that is time-critical: leaving it at its request position means it can be hundreds of bubbles above the fold by the time the user looks.

**Consequences:** The poll only follows along when the user was already near the bottom, so a refresh never yanks them away from what they are reading; a fresh open always scrolls. Pinning is a sort key in the fold, not a separate sticky element, so the card still lives in the conversation and still disappears from the bottom once it is decided.

**Spec:** §11.3, §12.3.
**Implemented:** `agentd/src/agentd/webui.py`.

## D-023 — The chat window hangs off the newest end and polls incrementally

**Decision:** `GET /v1/sessions/{id}` and the `/messages` / `/events` endpoints accept `tail=1`, which returns the *newest* `limit` rows in ascending order instead of the oldest. The phone opens a session with a tail fetch and then polls with `after_seq` — the parameter the API already had — appending only what arrived. The re-render key is the highest `seq` seen plus the approval states, not the length of the fetched array.

**Why:** The oldest-first window had two failure modes that hid on exactly the sessions a user watches: once a session passed its event limit the API returned the oldest rows (the live end was cut off entirely, defeating D-022), and because the UI keyed its render on the array *length*, that length pinned at the limit and the chat silently froze — new messages stopped rendering while approval cards kept healing from the approvals list, so it read as "stuck". The same full-window fetch every two seconds also shipped the whole timeline, full text bodies included, to a phone on mobile data.

**Consequences:** A poll costs one tiny request when nothing changed and a short append when something did; the window stays anchored to the live end as the session grows. A render that arrives while a high-risk *Hold to allow* press is in flight is deferred until the hold finishes (or fires) instead of destroying the button mid-press; the pending card's countdown ticks on its own 1-second timer instead of freezing between renders. Fetches time out client-side and never overlap. The Activity tab reuses the same newest window (it previously displayed the oldest rows reversed, i.e. the stale end).

**Spec:** §12.3, §12.4.
**Implemented:** `agentd/src/agentd/local_api.py`, `agentd/src/agentd/webui.py`, `agentd/src/agentd/db.py`.

## D-024 — Config promises are honoured: away, disabled agents, default effect

**Decision:** Three knobs that previously parsed but did nothing are now enforced. Away mode (`POST /v1/away`, `agentd away`) denies `ask` decisions immediately instead of parking the agent for the whole timeout — the hook still fails closed, it just fails fast. `agents.<name>.enabled = false` makes that agent's hooks fail closed on arrival, with an audit entry and no pending row. `[policy] default_effect` (validated to `ask`/`deny`/`allow`) is the effect for actions no rule matched; workspace reads stay allowed outright.

**Why:** A displayed dial that does nothing is worse than no dial: a user who disables an agent believes its tool calls are gated, and one who sets `default_effect = "deny"` believes the machine is locked down. Away mode was settable from three clients and read by `/v1/status` while nothing consumed it.

**Consequences:** Approvals and commands are now stored redacted (S7): `crypto.redact` runs over the card's activity detail and the approval record's action, so the phone shows `Authorization: ***REDACTED***` instead of the secret an agent tried to pass; `action_hash` still hashes the raw action so it stays stable. If the approval row cannot be stored after its request card was written, a `denied` decision event closes the card instead of leaving it pending forever. Two approvals indexes (`(session_id, state)` and `(state, expires_at)`) make the per-session pending count, the per-session approval list and the expiry sweep index lookups instead of full scans, and a housekeeping loop marks quiet sessions idle and applies `[activity] retention_days` (0 disables pruning; pending approvals are never deleted).

**Spec:** §9.3, §9.4, §12.5, §16.1.
**Implemented:** `agentd/src/agentd/approvals.py`, `agentd/src/agentd/policy.py`, `agentd/src/agentd/config.py`, `agentd/src/agentd/local_api.py`, `agentd/src/agentd/db.py`.

## D-025 — The agents are Codex and OpenCode; Cline is dropped

**Decision:** The supported agents are Codex (hook-gated, full approval loop) and OpenCode (monitor-only). The Cline adapter, transcript reader, config entry and `agentd-cline-hook` entrypoint are removed, and the first migration deletes any stored Cline rows — the row mappers only know `AgentType('codex')` and `AgentType('opencode')`, so leftover rows would crash the list endpoints. OpenCode deliberately gets **no** hook adapter: it has its own permission system and the owner does not want AgentLink gating it. AgentLink reads OpenCode for the chat and the feed, nothing else.

**Why:** Cline was never verified on this machine (its Phase 0 table stayed blank, its reader was written against documented-but-unobserved JSON shapes) and it is no longer used. OpenCode is used daily, and its v2 storage is a local SQLite database (`~/.local/share/opencode/opencode.db`) with an append-only, per-session `session_message` log whose `seq` rises monotonically — a cleaner fit for the high-water-mark watcher (D-018) than file tails, and sessions natively carry titles and directories.

**Consequences:** The reader opens the live database **read-only** (WAL allows concurrent readers) and never touches the `account`/`credential` tables, which hold secrets; a locked or unreadable database yields no sessions, never an error. Because OpenCode *updates* a message row while a turn streams (a tool part's `state` fills in as the tool runs), the per-session watermark re-reads the boundary message instead of skipping past it, so a result that arrives after first sight is refreshed in place by event id. Event ids are `opencode:<message_id>[:<part>][:call|:out]` — message ids are globally unique, so they are the index. `user` messages map to user bubbles, assistant content parts to reasoning/text/tool-call/tool-result, `synthetic` (injected shell output) to `NOTE`, and `idle` to a feed-only turn boundary; `system` and `model-switched` are dropped. The phone's agent chips become `All / Codex / OpenCode`. `docs/SPEC.md` still describes Cline throughout: it is the original spec, and this log supersedes it.

**Spec:** §8.1, §12 (superseded in part).
**Implemented:** `agentd/src/agentd/transcripts/opencode.py`, `agentd/src/agentd/db.py`, `agentd/src/agentd/config.py`, `agentd/src/agentd/webui.py`.

## D-026 — The phone can talk back: session input and a live stream

**Decision:** Two additions turn the phone from a gate into a remote. `POST /v1/sessions/{id}/input` queues a prompt for an **OpenCode** session — delivered by shelling out to `opencode run --session <id>`, the same background service the desktop TUI talks to, fired and forgotten with one in-flight prompt per session. `GET /v1/stream` is a server-sent-events channel that pushes one tiny notice per ingest ("this session changed"); the phone refetches incrementally the instant anything happens instead of waiting out the poll, which stays as the fallback. The page also ships a manifest, an icon and a shell-only service worker, making it installable as an app over https.

**Why:** OpenCode's background service owns the sessions, so a prompt delivered through it runs *on the desktop* and lands in the same transcript the reader already mirrors — the prompt and the streaming reply appear in the chat with no new data path (verified against a real session: the CLI is a thin client; the turn completes server-side even if the caller dies). The HTTP API was deliberately not used: its auth handshake is undocumented and the CLI already solves discovery (service state) and authentication. The SSE payload is only *what* changed, so auth, redaction and tail rules still apply to every byte of real data. And the socket-level pairing makes prompts feel synchronous: without it the phone waits out the 2-second poll between "sent" and "seeing it land".

**Consequences:** Input is OpenCode-only for now — Codex sessions get a clear 409 (its channel is the resume/app-server probe in `remote-codex-direction.md`). Prompts are validated (empty → 400; anything the CLI would eat as a flag, i.e. leading `-`, → 503) and every send is audited as `input.sent` with the text redacted. The CLI's per-session stream is appended to `~/.agentlink/logs/opencode-input-<session>.log` for debugging. The input raises the token's power from *approve* to *author*, which is why `docs/mobile-app-plan.md` M3 (relay or overlay transport, per-device keys) gates the "daily driver over the internet" step. The SSE reader is fetch-based because `EventSource` cannot send the Authorization header; it reconnects with backoff and the poll covers outages. The service worker caches the shell (which contains no secrets — the token lives in localStorage) and bypasses `/v1` entirely.

**Spec:** §12.4, §11 (superseded in part).
**Implemented:** `agentd/src/agentd/opencode_input.py`, `agentd/src/agentd/activity.py`, `agentd/src/agentd/local_api.py`, `agentd/src/agentd/webui.py`, `docs/mobile-app-plan.md`.

## D-027 — The remote summons you: notifications, autostart, loud degradation

**Decision:** Work plan P1 (`docs/work-plan.md`). A best-effort `Notifier` POSTs to a configured webhook on the two moments that need a human: a Codex `approval_requested`, and an OpenCode **turn completion** (a prompt steered from the phone finished). `agentd install` drops a hidden-launch `.vbs` into the *user's* Startup folder so the daemon starts at every logon, with `agentd uninstall` and a `doctor` line. And `SessionDetail.input_available` lets the phone disable the composer with the reason instead of failing on send.

**Why:** The remote is useless if you must watch it: without a notification you only find a parked Codex agent when its 600 s have already burned, and a daemon that dies on reboot kills the phone silently. The Startup folder is the one Windows autostart surface a standard user can write — `schtasks /SC ONLOGON` is denied without elevation (discovered live), so the scheduled task was dropped rather than asking for admin. Turn-completion is announced from the ingest path and only for *newly stored* `task_finished` events, because re-reads refresh old rows and re-announcing a finished turn would cry wolf.

**Consequences:** Notifications are fire-and-forget (`notify_soon` schedules the POST; a dead webhook costs its own 5 s timeout, never a decision or an ingest), never raise, and **redact bodies** (S7) — the webhook is a third party, so `ntfy.sh` topics must be treated as semi-public; a self-hosted ntfy or a private Discord webhook is the right target for command text. Two payload shapes: provider-agnostic JSON, and `ntfy` (topic extracted from the URL, `priority=high` for approvals). The launcher runs `pythonw` so no console flashes at logon; two daemons still conflict on the port, so `doctor` reports whether one is already running. Testing got honest about the real machine: the live e2e daemon now points `CODEX_HOME`/`OPENCODE_DB` at empty temp dirs — it used to ingest the user's *live* sessions mid-suite, which raced the stream test — and transcript tests bump file mtimes to a deterministic future, because `utime(now)` could collide with the write on a fast machine.

**Spec:** §12.5, §15.
**Implemented:** `agentd/src/agentd/notifier.py`, `agentd/src/agentd/autostart.py`, `agentd/src/agentd/config.py`, `agentd/src/agentd/approvals.py`, `agentd/src/agentd/activity.py`, `agentd/src/agentd/cli.py`, `agentd/src/agentd/webui.py`, `docs/work-plan.md`.

## D-028 — Remote approvals are a toggle: native on the desktop, phone when away

**Decision:** Codex approvals have two owners, and you choose with one tap. The `remote` flag (default **off**, persisted in the DB) decides who owns an ASK action: off — the hook answers Codex's JSON protocol with `permissionDecision: "ask"` and Codex's **native, refined desktop prompt** appears, exactly as without a hook; on — approvals park as AgentLink cards on the phone. Flipped from the home screen chip ("Phone approvals: on/off"), `agentd remote [--off]`, or `POST /v1/remote`; the choice survives restarts and reboots.

**Why:** A matching `PreToolUse` hook *is* the approval flow for matched calls (P0-2, confirmed) — while it blocks, Codex shows the hook's statusMessage and the native prompt never appears. Wiring every decision permanently to the phone made sitting at the desktop worse than no AgentLink at all; wiring it permanently to the desktop made leaving the desk blind. The `ask` decision — found as `PreToolUsePermissionDecisionWire`'s value in the `codex.exe` binary strings, Claude Code-parity — is what makes the ownership switchable at runtime instead of requiring config surgery.

**Consequences:** One call still has exactly one decision-owner; the toggle picks which. With remote off, agentd creates no approval record, records no card, and blocks nobody — auto-allow (workspace reads) still applies instantly, `[policy] default_effect` still hard-denies when configured, and `away` still overrides everything (an explicit panic mode denies regardless of where you are). `ApprovalState.DEFERRED` is outcome-only: never stored, never a card. A fresh install defaults to desktop-native. The `ask` semantics are read from Codex's own binary and verified at the hook level e2e; the desktop-side acceptance — the native prompt actually appearing — is the one thing only the user can confirm, since it lives in Codex's UI.

**Spec:** §9, §12.5.
**Implemented:** `agentd/src/agentd/protocol.py`, `agentd/src/agentd/approvals.py`, `agentd/src/agentd/local_api.py`, `agentd/src/agentd/adapters/codex/hook_cli.py`, `agentd/src/agentd/webui.py`, `agentd/src/agentd/cli.py`.


