# Performance findings & fix plan

*Diagnosed 2026-10-03. "The app is slow" was investigated end to end: the
transcript watcher, the SQLite write path, the HTTP API, and the phone UI's
polling. Everything below is measured, not guessed.*

**Status: implemented — see [Results](#results) for the after numbers and
`docs/decisions.md` D-018/D-023/D-024 for the design.**

## How the numbers were measured

Two throwaway benchmark scripts (kept for re-running after the fixes) reproduce
the real workload:

- One synthetic Codex rollout of **1476 records / 463 KB** — the size of the
  biggest real rollout on this machine (`~/.codex/sessions/2026/09/13/...`).
- The real `~/.codex` tree (12 rollouts, 5 logical sessions, one of them 4.5 MB).
- Temp DB under `C:\Users\hp\AppData\Local\Temp\opencode\`:
  `bench_slow.py` (watcher + API paths) and `bench_slow2.py` (statement vs
  commit cost breakdown).

## Measured baseline

| Path | Time |
|---|---|
| First scan of one 1476-record rollout (blocks startup) | **6.89 s** |
| Re-scan after one changed line (**0 new events**) | **6.37 s** — runs every 2 s while an agent works |
| → of which `COMMIT` per event | **4.05 ms/event** (5.97 s total) |
| → same statements, one commit for the batch | 0.52 ms/event (**7.8× faster**) |
| → of which `next_seq` (UPDATE+SELECT per event) | 0.95 ms/event (1.41 s) |
| Home screen: `summaries` + per-session pending count | 46 ms/poll at 61 sessions (N+1 queries) |
| `discover()` every 2 s (real codex tree) | 0.01 s — negligible |
| `list_messages` / `events` / `count_messages` (indexed) | 2–14 ms — fine |

Storage: SQLite, one connection, WAL, `synchronous` left at the default
(FULL). File: `%USERPROFILE%\.agentlink\agentd.db` (3.26 MB at diagnosis).

## Findings

| # | Finding | Cost today |
|---|---|---|
| 1 | `insert_activity_event` does SELECT + upsert + **`commit()` per record** (`db.py`, called via `activity.ingest`) | 4.05 ms/event → ~6 s per 1476-record rollout |
| 2 | Every scan of a changed file re-ingests **all** stored records (no tail ingestion); `next_seq` burns a seq number per stored record (`activity.ingest`) | 6.37 s of DB work for **0 new events**, on every 2-second tick while an agent is active |
| 3 | Startup **blocks** on `await watcher.scan_once()` in the app lifespan before serving (`local_api.py`); fingerprints are memory-only, so every restart re-reads everything | Tens of seconds of "offline" after every `agentd run` |
| 4 | The chat tab re-downloads up to 300 events + 200 approvals every 2 s (`webui.js refreshSession`); never uses the existing `after_seq` parameter. The render is diffed, the download is not | ~600 rows incl. full `text` bodies per poll — megabytes over mobile data |
| 5 | `GET /v1/sessions` runs one pending-count query **per session, per poll** (N+1; `local_api.list_sessions`) | 46 ms @ 61 sessions, ~150 ms @ the 200-session cap |
| 6 | `list_activity_events` / `list_messages` use `ORDER BY seq ASC LIMIT n`, so big sessions return the **oldest** n rows — the live tail is cut off | Phone shows stale history on exactly the active sessions |
| 7 | **One SQLite connection** — watcher writes and phone reads serialize; a scan starves the API | Every phone poll queues behind multi-second write bursts |

Not slow (don't chase these): the SQL read paths (`idx_activity_agent_seq`
does its job), `discover()`'s tree walk (10 ms), the UI's render diffing.

## Fix plan (in execution order)

### 1. Batch the write path — `db.py`, `activity.py`

Biggest win, low risk. Measured 7.8× on the dominant cost.

- One transaction per ingest batch, one `commit()` at the end; rollback on error
  (replaces the current "crash mid-batch leaves a prefix stored" behavior).
- Replace the per-record existence SELECT with one
  `SELECT event_id ... WHERE event_id IN (batch)`; `executemany` the inserts and
  the refresh-updates separately. Preserve the "newly inserted" count.
- Bulk `next_seq`: `UPDATE sessions SET last_seq = last_seq + ?` once per
  batch, assign seqs **only to genuinely new events** — stored events keep
  theirs, so re-scans stop burning seq entirely.
- `PRAGMA synchronous=NORMAL` (the WAL-recommended setting) for what commits
  remain.

*Expected: re-scan drops from ~6.4 s to ~1.5 s (parse-bound); first scan to ~2 s.*

### 2. Tail-only ingestion — `watcher.py`, `transcripts/codex.py`, `db.py`

Kills the re-scan cost instead of shrinking it.

- New `transcript_state` table (`CREATE TABLE IF NOT EXISTS` — migration-free):
  per-(session, file) high-water mark = records already ingested + reader
  mapping version.
- `read()` takes a start offset: lines below the mark are counted (cheap) but
  not JSON-parsed; only the tail is parsed and inserted.
- Full re-read only when the version bumps (keeps D-018's "a reader mapping
  change takes effect on the next scan" guarantee, but explicitly) or when the
  file shrinks.
- Persisted marks also make restarts cheap, so fingerprint persistence is not
  needed.

*Expected: re-scan of an active file goes from ~6.4 s to milliseconds.*

### 3. Unblock startup — `local_api.py`

One line: drop the `await watcher.scan_once()` from the lifespan;
`watcher.run()` already scans immediately, just without blocking the API.

### 4. Kill the N+1 — `db.py`, `local_api.py`

One `SELECT session_id, COUNT(*) ... WHERE state = 'pending' GROUP BY
session_id` joined onto the sessions list instead of a query per session.

### 5. Chat polling + live tail — `webui.py`, `local_api.py`

- Track the last `seq` in the UI; poll `GET /v1/sessions/{id}/events?after_seq=`
  (already supported) and append; full fetch only on open.
- Add a `tail` mode to the messages/events endpoints (newest n, ascending) and
  use it for the initial load — also fixes finding 6.
- Fetch approvals only when the session's pending count changes.

*Expected: per-poll bytes drop by ~100× on the chat tab; newest turns always
visible.*

### 6. Finding 7 (single connection) — deferred on purpose

With 1–3 the write bursts shrink from seconds to milliseconds, which mostly
dissolves the contention. A second read-only connection (WAL allows 1 writer +
N readers) is the remaining lever if the phone still stutters. Not in this
pass. SSE/WS is likewise out of scope.

### Docs

Update D-018 in `decisions.md` (batched transactions, high-water marks, no seq
burn) and the README's status/test notes where behavior changes.

## Verification

- All 152 existing tests pass.
- New tests: batch idempotency (re-ingest inserts 0, burns 0 seq), tail
  ingestion (append → only new events; version bump → full refresh), aggregate
  pending counts, tail endpoint ordering.
- Re-run `bench_slow.py` / `bench_slow2.py` and record before/after in this
  file.

## Results (2026-10-03, after implementing §1–§5)

Same machine, same synthetic 1476-record / 463 KB rollout as the baseline:

| Path | Before | After |
|---|---|---|
| First scan of one rollout, cold DB | 6.89 s | **0.09 s** |
| Re-scan of a changed file, empty tail (the every-2s case) | 6.37 s | **3 ms** |
| Re-scan after one appended turn | ~6.4 s | **26 ms** |
| First scan after a daemon restart | ~6.9 s (full re-read) | **4 ms** (marks persist) |
| Home screen incl. pending counts, 61 sessions | 46 ms (N+1) | **1 ms** |
| Batched store of 1476 events | 5.97 s (commit each) | **0.02 s** (one commit) |
| `sessions.last_seq` after 4 scans | 4431 (burning) | **1478 = event count** |

The watcher's steady-state work while an agent is active went from ~6.4 s of
DB writes per 2-second tick to milliseconds; startup no longer blocks on the
first scan (`local_api.py` starts the watcher task instead of awaiting it);
the chat window is anchored to the newest end and polls with `after_seq` instead
of re-downloading the whole timeline; the per-session pending counts collapsed
from one query per session per poll to one aggregate query.

Benchmark scripts: `C:\Users\hp\AppData\Local\Temp\opencode\bench_slow.py`
and `bench_slow2.py` (throwaway, re-created if lost — the numbers above are
the record).
