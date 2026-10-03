# Code audit

*Full read-through of `agentd/`, `sim/`, `scripts/`, `tests/` and the repo
root, 2026-10-03. Every finding below was verified against the code (and two
against a live check); file references point at the current tree.*

**Status: implemented (2026-10-03).** A1–A9 are fixed with regression tests,
C1–C9 are fixed, and D1–D7 are cleaned up; the design changes are recorded in
`docs/decisions.md` (D-018 amended, D-023/D-024 added) and the measured wins in
`docs/performance-plan.md` → Results. Deferred on purpose, as planned: the
second read-only SQLite connection and SSE/WS push.

Severity legend: **[bug]** wrong behavior today · **[perf]** costs seconds or
grows with use · **[edge]** only on failure/unusual input · **[clean]**
dead code or litter.

Cross-references to `docs/performance-plan.md` are marked *(perf-plan §N)*.

## What is already solid — don't re-fix

* **XSS**: every interpolation in the phone UI goes through `esc()`
  (`webui.py`); token lives in the URL fragment and is stripped on load.
* **Fail-closed hook path**: malformed payload, unreachable daemon, bad token,
  parse error, timeout — all deny. Verified in both adapters.
* `secrets.compare_digest` for token checks (see A9 for an edge), the token
  is never logged by the server, `action_hash` excludes the raw payload and
  `raw` is never persisted (only `ActionDetail` reaches the DB).
* Startup cancels stale pending approvals; the sweep records decision events;
  per-agent segregation is tested end-to-end.
* The reader/watcher contract (grouped resumed rollouts, session-global event
  ids) — fixed and regression-tested earlier in this effort.

---

## A. Bugs

### A1. The phone chat freezes on any session with >300 events — **[bug]**

`GET /v1/sessions/{id}` returns the **oldest** `limit=300` events
(`db.list_activity_events`: `ORDER BY seq ASC LIMIT ?`, no `tail` mode), and
the chat's re-render key is built from `detail.events.length`
(`webui.js refreshSession`):

```js
const key = "chat|" + JSON.stringify([
  (detail.events || []).length, approvals.map(a => [a.approval_id, a.state])
]);
```

Once a session passes 300 events, the array length is pinned at 300, the key
never changes, and **new messages never render** — only approval-card state
changes (fetched separately) can trigger a re-render. The affected sessions are
exactly the long-running active ones, and the frozen window is the *oldest*
300 events, so the newest turns are invisible — which also defeats D-022
("the chat opens at the newest turn"). Approval cards do get their state
healed from the approval records, so the bug hides as "chat looks stuck".

**Fix**: add a `tail` mode to the events/messages endpoints (newest N,
ascending) + make the key `(last seq seen, approvals state)` instead of array
length. Same work as *(perf-plan §5)* — this raises it from "slow" to
"broken".

### A2. Every per-session approvals query is a full table scan — **[bug][perf]**

The only approvals index is `idx_approvals_agent_state (agent_type,
session_id, state)` (`db.py` SCHEMA), but the hot queries never filter on
`agent_type`:

* `count_pending_for_session` — called once **per session per poll** from
  `GET /v1/sessions` (the N+1, *(perf-plan §4)*)
* `list_approvals(session_id=...)` — every chat-tab poll (limit 200)
* `expire_stale` (`state` + `expires_at`) — every 15 s, and `list_approvals`
  by `state` alone

None can use the index, so each is a full scan of an `approvals` table that is
**never pruned** (see C5). Cheap and immediate:

```sql
CREATE INDEX idx_approvals_session_state ON approvals (session_id, state);
CREATE INDEX idx_approvals_state_expires ON approvals (state, expires_at);
```

### A3. `next_seq` is not atomic — duplicate seq numbers — **[bug][edge]**

`db.next_seq` runs `UPDATE sessions SET last_seq = last_seq + 1` and then, in a
**separate awaited statement**, `SELECT last_seq`. Two concurrent `ingest`
calls (the watcher batch racing a hook POSTing `/v1/events`) can interleave
between the two statements and both receive the same seq. Duplicate seq breaks
`after_seq` paging — the phone silently skips an event as "already seen".
Also costs 2 statements × every event *(perf-plan §1 fixes both: reserve the
batch's seq range in one `UPDATE ... RETURNING`)*.

### A4. Away mode is a no-op — **[bug]**

`POST /v1/away` sets `ctx.away`, `/v1/status` reports it, `agentd away` and
`agentlink-sim away` toggle it — and **nothing consumes it**. Approvals behave
identically with away on or off. The CLI help text ("approvals still fail
closed") describes the ordinary timeout, not away mode. Either wire it into
policy (e.g. auto-deny while away) or remove the flag everywhere.

### A5. `agents.<name>.enabled` is never enforced — **[bug]**

`doctor` and `/v1/status` display the flag, but `approvals.request` never
checks it: a "disabled" agent's hooks are still evaluated and block for a
decision. Should fail closed with a clear reason for disabled agents.

### A6. `[policy] default_effect` is ignored — **[bug]**

`PolicyConfig.default_effect` exists (DEFAULT_TOML comments it "fail closed")
but `PolicyEngine.evaluate` hardcodes `Effect.ALLOW` for workspace reads /
`Effect.ASK` otherwise. Editing config.toml silently does nothing.

### A7. Press-and-hold can be silently cancelled by a poll — **[bug]**

`renderTimeline` rebuilds the entire chat (`main.innerHTML = ""`). A high-risk
**"Hold to allow 1.5s"** press is destroyed mid-hold whenever a poll changes
the render key — the progress bar vanishes, the decision never fires, and the
user gets no feedback. On an active session this can make allowing high-risk
actions nearly impossible. Fix: append-only rendering (also *(perf-plan §5)*)
or suppress re-render while a hold is in progress.

### A8. Orphaned "pending" card if the approval insert fails — **[edge]**

Consequence of the race fix from earlier in this effort: `_ask` now records
`approval_requested` *before* `insert_approval`. If the insert then fails, the
chat keeps a card stuck on pending forever — there is no approval row for a
decision or the sweep to resolve. (Before the reorder the failure mode was a
pending approval with no card, which the sweep did heal.) Likelihood is low
(it takes a DB write failure), but the failure mode is worse. Fix: catch
insert failure → best-effort record a `cancelled` decision event.

### A9. Non-ASCII bearer token → 500 instead of 401 — **[edge]**

`require_token` passes header strings straight to
`secrets.compare_digest`, which raises
`TypeError: comparing strings with non-ASCII characters is not supported`
(verified). HTTP headers are latin-1-decoded, so `curl -H "Authorization:
Bearer tökén"` produces a 500 traceback per request. Auth still fails closed;
it is noise plus an unhandled-exception path. One-line fix: compare
`presented.encode()` vs `context.token.encode()`.

---

## B. Performance, beyond what the perf plan already covers

* **A2 above** — approvals full scans on every poll/sweep.
* **A3 above** — 2 statements per event for seq reservation *(perf-plan §1)*.
* *(perf-plan §1–§5) remain the dominant fixes* — commit-per-record, full
  re-ingest of changed files, blocking startup scan, whole-timeline polling,
  and the sessions N+1. Nothing in this audit replaces those.

---

## C. Robustness & hygiene

### C1. ngrok log handle leaks — **[edge]**

`tunnel.start` opens `target.open("ab")`, hands it to Popen, and never closes
it on the success paths (only on failure/timeout). One leaked fd per
`agentd run --tunnel`.

### C2. UI fetches have no timeout and polls stack — **[edge]**

`webui.js api()` uses bare `fetch` (no `AbortController`), and `setInterval`
fires every 2 s regardless of an in-flight refresh. Over a slow/hung tunnel,
refreshes pile up concurrently and can interleave renders. Add a timeout +
an in-flight guard.

### C3. Pending countdown freezes — **[bug][minor]**

The "5m 30s left" on a pending card is computed at render time; with no other
changes it never ticks. A 1 s interval updating only the countdown span fixes
it without re-rendering.

### C4. ISO strings compared lexicographically in SQL — **[edge]**

`ORDER BY created_at`, `COALESCE(last_activity_at, started_at)`, and
`expires_at <= ?` all rely on string comparison. ISO-8601 sorts correctly
*except* when the microsecond field is omitted (`...:00Z` sorts after
`...:00.5Z` within the same second). Effects are cosmetic (approval order) or
self-correct within a second (expiry). Fix: normalize `iso()` to always emit
6 fractional digits, or store epoch integers.

### C5. Nothing is ever pruned — **[perf]**

`activity_events` (full transcript text bodies), `audit_log` and decided
`approvals` grow without bound; `[activity] retention_days = 7` is parsed and
used nowhere. The DB is already 3.26 MB after days of use (plus a manual
`agentd.db.bak`). The watcher will keep feeding it; add a retention sweep to
the existing `_sweep_loop`.

### C6. Sessions never leave state="running" — **[bug][minor]**

`SessionManager.set_state`/`db.set_session_state` have no callers; every
transcript session is touched with `state="running"` on every scan, so the
"active" badge is permanent and `ActivityKind.SESSION_ENDED` is never
produced. Mark sessions idle after N minutes without file activity — the
watcher already has the timestamps.

### C7. `_configure_logging` can stack handlers — **[edge]**

`cli.run` adds a `FileHandler` to the root logger on each call, no guard
against duplicates if invoked twice in-process.

### C8. Secret redaction is implemented, tested — and never called — **[edge]**

`crypto.redact`/`redact_text` (S7) exist with full test coverage but zero
call sites. Hook commands (`action.action.command`) and transcript text are
stored verbatim in `activity_events` and served to the phone. Policy *flags*
"secret-looking argument" as high risk, and the card then displays the secret.
Decide: redact at rest/display (`redact_text(command)` before storing the
card detail), or document the omission as accepted-for-slice-1.

### C9. Token file permissions — **[edge]**

`load_or_create_token` writes with default perms. Fine on Windows (user
profile ACLs); if this ever runs on POSIX, the token is world-readable. One
line: `os.open(..., 0o600)`.

---

## D. Cleanup

### D1. Repo-root litter — **[clean]**

`project.zip` (290 KB), `files.txt` (a file listing), `agent_files/step1.txt` /
`step2.txt` (containing "one" / "two") — referenced by nothing in the repo.
Delete or move under `scripts/`.

### D2. Ten investigation scripts in `agentd/` — **[clean]**

`_diff.py`, `_dump.py`, `_dupes.py`, `_inspect.py`, `_peek.py`, `_probe.py`,
`_reconcile.py`, `_second.py`, `_sessions.py`, `_verify.py` (untracked). Their
findings are now recorded in `docs/`, and two of them **mutate the live
`~/.agentlink/agentd.db`** when run (`_probe.py` inserts a row with
`seq=999999`; `_reconcile.py` re-scans the real DB). Delete, or move to
`scripts/investigation/` with a read-only warning header.

### D3. Dead DB/manager surface — **[clean]**

`db.get_session` + `SessionManager.get` (no callers), `db.list_sessions` +
`SessionManager.list` (one test only), `db.set_session_state` +
`SessionManager.set_state` (no callers — see C6, which wants it wired in),
`ActivityKind.SESSION_ENDED` (never produced), `ActivityKind.FILE_CHANGED`
(slice 5 placeholder — mark it as such).

### D4. Dead config — **[clean]**

Parsed but unused: `[policy] default_effect` (A6), `[activity]
heartbeat_seconds`, `[activity] retention_days` (C5), `[approvals]
default_expiry_seconds`, `[approvals] diff_preview_max_lines`. Either wire in
or comment them in `DEFAULT_TOML` as slice placeholders so users stop editing
dials that do nothing.

### D5. `AgentsConfig.for_agent` uses raw getattr — **[clean]**

`getattr(self, agent_type, AgentConfig())` returns *anything* with that
attribute name (`for_agent("for_agent")` returns the method itself). Validate
against the known names.

### D6. Reader/adapter duplication — **[clean]**

`transcripts/codex.py` and `transcripts/cline.py` each define their own
timestamp parsing, `_as_text`, `_sort_key`; `adapters/{cline,codex}/mapping.py`
duplicate `tool_kind_for` / `_tool_block` / `_summarize`. When Phase 0
findings land these must change *in lockstep* — extract shared helpers into
`transcripts/base.py` / `adapters/base.py` first.

### D7. `pyproject.toml` masks deprecations — **[clean]**

`filterwarnings = ["ignore::DeprecationWarning"]` hides all deprecation noise
(including FastAPI/Pydantic upgrade warnings). Drop it or scope to the
specific warning.

---

## Suggested order

1. **A2** — two `CREATE INDEX` lines, immediate wins on every poll. *(5 min)*
2. **A1 + A7** with *(perf-plan §5)* — tail endpoint + append-only chat; fixes
   the freeze, the stale window, and the hold-cancel together. *(the structural
   UI change)*
3. **A3** inside *(perf-plan §1)* — bulk seq reservation makes the race
   impossible and removes 2 statements/event. *(same code area)*
4. **A9** — one line. **A8** — small guard. *(quick)*
5. **A4 / A5 / A6** — decide wire-in vs. removal for the three dead config
   contracts; they are user-facing promises in config.toml. *(decision needed)*
6. **C1–C9** as a robustness sweep (C5 retention and C6 session state are the
   two users will notice).
7. **D1–D7** as a final cleanup commit — D2 deletes scripts that write to the
   live DB, so sooner is safer than later.

## Verification notes

* Add tests: tail-endpoint ordering; `EXPLAIN QUERY PLAN` asserts the new
  approvals index is used; concurrent ingest produces unique seq (the A3 race
  is hard to hit deterministically — assert on the single-statement
  reservation instead); non-ASCII token → 401 not 500; away/enabled/
  default_effect behavior once decided.
* The A1 freeze reproduces without a phone: ingest 400 events into a session,
  open `/#/s/<id>`, append more — the chat never updates.
* Re-run `bench_slow.py` after A2 to show the approvals queries leaving the
  profile.
