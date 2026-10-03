"""SQLite persistence (SPEC.md §8.1).

Every operational read/write takes ``agent_type`` so that each agent's state
can never be mixed (D13 / §8.3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from .protocol import (
    CHAT_KINDS,
    ActionDetail,
    ActivityEvent,
    ActivityKind,
    AgentType,
    ApprovalRecord,
    ApprovalState,
    Decision,
    MessageRecord,
    MessageRole,
    Risk,
    RiskLevel,
    SessionSummary,
    Tool,
    ToolKind,
    iso,
    parse_iso,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  device_id   TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  name        TEXT,
  pubkey      TEXT,
  created_at  TEXT NOT NULL,
  revoked_at  TEXT,
  last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id       TEXT PRIMARY KEY,
  agent_type       TEXT NOT NULL,
  workspace_path   TEXT NOT NULL,
  pc_device_id     TEXT,
  started_at       TEXT NOT NULL,
  ended_at         TEXT,
  state            TEXT NOT NULL DEFAULT 'idle',
  title            TEXT,
  source           TEXT,
  last_activity_at TEXT,
  message_count    INTEGER NOT NULL DEFAULT 0,
  last_seq         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id     TEXT PRIMARY KEY,
  agent_type      TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  workspace_path  TEXT NOT NULL,
  tool_name       TEXT NOT NULL,
  tool_kind       TEXT NOT NULL,
  action_json     TEXT NOT NULL,
  action_hash     TEXT NOT NULL,
  risk_level      TEXT NOT NULL,
  risk_reasons    TEXT NOT NULL,
  state           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  decided_at      TEXT,
  decision        TEXT,
  decision_reason TEXT,
  decided_by      TEXT
);

CREATE TABLE IF NOT EXISTS activity_events (
  event_id       TEXT PRIMARY KEY,
  seq            INTEGER NOT NULL,
  agent_type     TEXT NOT NULL,
  session_id     TEXT NOT NULL,
  workspace_path TEXT NOT NULL,
  kind           TEXT NOT NULL,
  role           TEXT,
  ts             TEXT NOT NULL,
  summary        TEXT NOT NULL,
  text           TEXT NOT NULL DEFAULT '',
  detail_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  session_id     TEXT NOT NULL,
  workspace_path TEXT NOT NULL,
  base_tree      TEXT,
  head_tree      TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (session_id, workspace_path)
);

CREATE TABLE IF NOT EXISTS policy_rules (
  id         TEXT PRIMARY KEY,
  agent_type TEXT NOT NULL,
  match_json TEXT NOT NULL,
  effect     TEXT NOT NULL,
  priority   INTEGER NOT NULL DEFAULT 0,
  enabled    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  subject     TEXT,
  detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_state (
  agent_type  TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  source_path TEXT NOT NULL,
  pos         INTEGER NOT NULL DEFAULT 0,
  records     INTEGER NOT NULL DEFAULT 0,
  size        INTEGER NOT NULL DEFAULT 0,
  version     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (agent_type, session_id, source_path)
);

CREATE INDEX IF NOT EXISTS idx_approvals_agent_state
  ON approvals (agent_type, session_id, state);
CREATE INDEX IF NOT EXISTS idx_approvals_agent_created
  ON approvals (agent_type, created_at);
-- The hot approvals queries never filter on agent_type: the per-session
-- pending count (once per session per poll), the per-session approval list
-- (every chat poll) and the expiry sweep all filter on (session_id, state)
-- or (state, expires_at) alone. Without these two, each is a full scan of a
-- table that retention keeps growing.
CREATE INDEX IF NOT EXISTS idx_approvals_session_state
  ON approvals (session_id, state);
CREATE INDEX IF NOT EXISTS idx_approvals_state_expires
  ON approvals (state, expires_at);
CREATE INDEX IF NOT EXISTS idx_activity_agent_seq
  ON activity_events (agent_type, session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_ws
  ON sessions (agent_type, workspace_path);
"""


def _row_to_approval(row: aiosqlite.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"],
        agent_type=AgentType(row["agent_type"]),
        session_id=row["session_id"],
        workspace_path=row["workspace_path"],
        tool=Tool(name=row["tool_name"], kind=ToolKind(row["tool_kind"])),
        action=ActionDetail.model_validate(json.loads(row["action_json"])),
        risk=Risk(
            level=RiskLevel(row["risk_level"]),
            reasons=json.loads(row["risk_reasons"]),
        ),
        action_hash=row["action_hash"],
        state=ApprovalState(row["state"]),
        created_at=parse_iso(row["created_at"]),
        expires_at=parse_iso(row["expires_at"]),
        decided_at=parse_iso(row["decided_at"]) if row["decided_at"] else None,
        decision=Decision(row["decision"]) if row["decision"] else None,
        decision_reason=row["decision_reason"],
        decided_by=row["decided_by"],
    )


def _row_to_session_summary(row: aiosqlite.Row) -> SessionSummary:
    return SessionSummary(
        session_id=row["session_id"],
        agent_type=AgentType(row["agent_type"]),
        workspace_path=row["workspace_path"],
        title=row["title"] or "",
        state=row["state"],
        source=row["source"] or "hook",
        started_at=parse_iso(row["started_at"]),
        ended_at=parse_iso(row["ended_at"]) if row["ended_at"] else None,
        last_activity_at=(
            parse_iso(row["last_activity_at"]) if row["last_activity_at"] else None
        ),
        message_count=int(row["message_count"] or 0),
    )


def _row_to_activity(row: aiosqlite.Row) -> ActivityEvent:
    return ActivityEvent(
        event_id=row["event_id"],
        seq=int(row["seq"]),
        agent_type=AgentType(row["agent_type"]),
        session_id=row["session_id"],
        workspace_path=row["workspace_path"],
        kind=ActivityKind(row["kind"]),
        role=MessageRole(row["role"]) if row["role"] else None,
        ts=parse_iso(row["ts"]),
        summary=row["summary"] or "",
        text=row["text"] or "",
        detail=json.loads(row["detail_json"]) if row["detail_json"] else {},
    )


def _row_to_message(row: aiosqlite.Row) -> MessageRecord:
    detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
    tool_name = detail.get("tool_name")
    tool_kind = detail.get("tool_kind")
    return MessageRecord(
        message_id=row["event_id"],
        seq=int(row["seq"]),
        agent_type=AgentType(row["agent_type"]),
        session_id=row["session_id"],
        role=MessageRole(row["role"]) if row["role"] else MessageRole.SYSTEM,
        kind=ActivityKind(row["kind"]),
        ts=parse_iso(row["ts"]),
        text=row["text"] or "",
        tool_name=str(tool_name) if tool_name else None,
        tool_kind=ToolKind(tool_kind) if tool_kind else None,
        detail=detail,
    )


@dataclass
class TranscriptMark:
    """How far one transcript source file has been consumed (D-018).

    ``pos`` is the byte offset just past the last fully-written line that has
    been ingested, ``records`` the number of valid records consumed before it
    (they number a session's event ids), and ``size`` the file size when the
    mark was made so a shrunk file is detected without reading it.
    """

    pos: int = 0
    records: int = 0
    size: int = 0
    version: int = 0


class Database:
    """Async SQLite wrapper. One connection, WAL mode."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # The WAL-recommended setting: commits no longer wait for an fsync per
        # transaction, only checkpoints do. A crash can lose the very last
        # transaction, never corrupt the database.
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()

    async def migrate(self) -> None:
        await self.conn.executescript(SCHEMA)
        await self._ensure_columns()
        await self._purge_removed_agents()
        await self.conn.commit()

    async def _purge_removed_agents(self) -> None:
        """Delete data belonging to agents this build no longer supports.

        D-025 dropped Cline; rows left behind would crash the row mappers
        (``AgentType('cline')`` no longer exists), so the first migration
        after the swap removes them. The audit log is append-only history and
        keeps its rows.
        """
        keep = ", ".join(f"'{agent.value}'" for agent in AgentType)
        for table in ("activity_events", "approvals", "transcript_state", "sessions"):
            cursor = await self.conn.execute(
                f"DELETE FROM {table} WHERE agent_type NOT IN ({keep})"
            )
            await cursor.close()

    async def _ensure_columns(self) -> None:
        """Add columns introduced after the first release (idempotent).

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a
        database created by an older build needs these added by hand.
        """
        wanted: dict[str, dict[str, str]] = {
            "sessions": {
                "title": "TEXT",
                "source": "TEXT",
                "last_activity_at": "TEXT",
                "message_count": "INTEGER NOT NULL DEFAULT 0",
                "last_seq": "INTEGER NOT NULL DEFAULT 0",
            },
            "activity_events": {
                "role": "TEXT",
                "text": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in wanted.items():
            cursor = await self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cursor.fetchall()}
            await cursor.close()
            for name, decl in columns.items():
                if name not in existing:
                    cursor = await self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}"
                    )
                    await cursor.close()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # --- approvals --------------------------------------------------------

    async def insert_approval(self, rec: ApprovalRecord) -> None:
        await self.conn.execute(
            """
            INSERT INTO approvals (
              approval_id, agent_type, session_id, workspace_path,
              tool_name, tool_kind, action_json, action_hash,
              risk_level, risk_reasons, state, created_at, expires_at,
              decided_at, decision, decision_reason, decided_by
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec.approval_id,
                rec.agent_type.value,
                rec.session_id,
                rec.workspace_path,
                rec.tool.name,
                rec.tool.kind.value,
                json.dumps(rec.action.model_dump(mode="json")),
                rec.action_hash,
                rec.risk.level.value,
                json.dumps(rec.risk.reasons),
                rec.state.value,
                iso(rec.created_at),
                iso(rec.expires_at),
                iso(rec.decided_at) if rec.decided_at else None,
                rec.decision.value if rec.decision else None,
                rec.decision_reason,
                rec.decided_by,
            ),
        )
        await self.conn.commit()

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        cursor = await self.conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_approval(row) if row else None

    async def list_approvals(
        self,
        *,
        agent_type: AgentType | None = None,
        session_id: str | None = None,
        state: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_type is not None:
            clauses.append("agent_type = ?")
            params.append(agent_type.value)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.conn.execute(
            f"SELECT * FROM approvals {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_approval(row) for row in rows]

    async def set_decision(
        self,
        approval_id: str,
        *,
        state: ApprovalState,
        decision: Decision | None,
        reason: str | None,
        decided_by: str | None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE approvals
               SET state = ?, decision = ?, decision_reason = ?,
                   decided_by = ?, decided_at = ?
             WHERE approval_id = ?
            """,
            (
                state.value,
                decision.value if decision else None,
                reason,
                decided_by,
                iso(utcnow()),
                approval_id,
            ),
        )
        await self.conn.commit()

    async def expire_stale(self, now_iso: str) -> list[str]:
        """Mark pending approvals past their expiry. Returns the ids expired.

        The ``state = pending`` guard on the UPDATE means an approval decided
        between the SELECT and the UPDATE is left alone; the trailing SELECT
        reports only the rows that really moved, so callers never record an
        expiry for something that was actually allowed.
        """
        cursor = await self.conn.execute(
            "SELECT approval_id FROM approvals WHERE state = ? AND expires_at <= ?",
            (ApprovalState.PENDING.value, now_iso),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        ids = [row["approval_id"] for row in rows]
        if not ids:
            return []

        placeholders = ",".join("?" for _ in ids)
        await self.conn.execute(
            f"""
            UPDATE approvals
               SET state = ?, decision = ?, decision_reason = ?, decided_at = ?
             WHERE approval_id IN ({placeholders}) AND state = ?
            """,
            (
                ApprovalState.EXPIRED.value,
                Decision.DENY.value,
                "expired",
                now_iso,
                *ids,
                ApprovalState.PENDING.value,
            ),
        )
        await self.conn.commit()

        cursor = await self.conn.execute(
            f"SELECT approval_id FROM approvals WHERE approval_id IN ({placeholders}) "
            f"AND state = ?",
            (*ids, ApprovalState.EXPIRED.value),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [row["approval_id"] for row in rows]

    async def count_pending(self, agent_type: AgentType | None = None) -> int:
        if agent_type is None:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE state = ?",
                (ApprovalState.PENDING.value,),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE state = ? AND agent_type = ?",
                (ApprovalState.PENDING.value, agent_type.value),
            )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["n"]) if row else 0

    # --- sessions ---------------------------------------------------------

    async def upsert_session(
        self,
        *,
        session_id: str,
        agent_type: AgentType,
        workspace_path: str,
        state: str = "idle",
        title: str | None = None,
        source: str | None = None,
        started_at: datetime | None = None,
        last_activity_at: datetime | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO sessions (
              session_id, agent_type, workspace_path, started_at, state,
              title, source, last_activity_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
              workspace_path = excluded.workspace_path,
              state = excluded.state,
              title = COALESCE(excluded.title, sessions.title),
              source = COALESCE(excluded.source, sessions.source),
              last_activity_at = COALESCE(
                excluded.last_activity_at, sessions.last_activity_at
              )
            """,
            (
                session_id,
                agent_type.value,
                workspace_path,
                iso(started_at or utcnow()),
                state,
                title,
                source,
                iso(last_activity_at) if last_activity_at else None,
            ),
        )
        await self.conn.commit()

    async def get_session_summary(self, session_id: str) -> SessionSummary | None:
        cursor = await self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_session_summary(row) if row else None

    async def list_session_summaries(
        self, agent_type: AgentType | None = None, limit: int = 200
    ) -> list[SessionSummary]:
        order = "ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?"
        if agent_type is None:
            cursor = await self.conn.execute(f"SELECT * FROM sessions {order}", (limit,))
        else:
            cursor = await self.conn.execute(
                f"SELECT * FROM sessions WHERE agent_type = ? {order}",
                (agent_type.value, limit),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_session_summary(row) for row in rows]

    async def set_session_activity(
        self, *, session_id: str, last_activity_at: datetime, message_count: int
    ) -> None:
        await self.conn.execute(
            "UPDATE sessions SET last_activity_at = ?, message_count = ? "
            "WHERE session_id = ?",
            (iso(last_activity_at), message_count, session_id),
        )
        await self.conn.commit()

    async def reserve_seqs(self, session_id: str, count: int) -> int:
        """Reserve ``count`` sequence numbers; returns the first of the range.

        One statement, so two concurrent ingest batches can never draw the
        same number (which would silently break ``after_seq`` paging). Returns
        0 when the session row does not exist — callers ``touch()`` first.
        """
        cursor = await self.conn.execute(
            "UPDATE sessions SET last_seq = last_seq + ? WHERE session_id = ? "
            "RETURNING last_seq",
            (count, session_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return 0
        return int(row[0]) - count + 1

    # --- activity ---------------------------------------------------------

    async def existing_activity_ids(self, event_ids: list[str]) -> set[str]:
        """Which of these event ids are already stored? One query per chunk."""
        found: set[str] = set()
        for start in range(0, len(event_ids), 500):
            chunk = event_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await self.conn.execute(
                f"SELECT event_id FROM activity_events "
                f"WHERE event_id IN ({placeholders})",
                chunk,
            )
            rows = await cursor.fetchall()
            await cursor.close()
            found.update(row["event_id"] for row in rows)
        return found

    async def store_activity(
        self,
        *,
        new: list[ActivityEvent],
        refresh: list[ActivityEvent],
    ) -> int:
        """Insert new events and refresh stored ones in **one** transaction.

        A stored event keeps its ``seq`` and ``ts``; the fields derived from
        the transcript (``kind``, ``role``, ``summary``, ``text``,
        ``detail``) are rewritten so a full re-read reconciles them (D-018).
        Returns how many of ``new`` were actually inserted — a concurrent
        writer may have won the race, and ``DO NOTHING`` keeps that harmless.
        """
        inserted = 0
        try:
            if new:
                cursor = await self.conn.executemany(
                    """
                    INSERT INTO activity_events (
                      event_id, seq, agent_type, session_id, workspace_path,
                      kind, role, ts, summary, text, detail_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    [
                        (
                            event.event_id,
                            event.seq,
                            event.agent_type.value,
                            event.session_id,
                            event.workspace_path,
                            event.kind.value,
                            event.role.value if event.role else None,
                            iso(event.ts),
                            event.summary,
                            event.text,
                            json.dumps(event.detail, default=str),
                        )
                        for event in new
                    ],
                )
                inserted = cursor.rowcount or 0
                await cursor.close()
            if refresh:
                await self.conn.executemany(
                    """
                    UPDATE activity_events
                       SET kind = ?, role = ?, summary = ?, text = ?, detail_json = ?
                     WHERE event_id = ?
                    """,
                    [
                        (
                            event.kind.value,
                            event.role.value if event.role else None,
                            event.summary,
                            event.text,
                            json.dumps(event.detail, default=str),
                            event.event_id,
                        )
                        for event in refresh
                    ],
                )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        return inserted

    async def list_activity_events(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        after_seq: int = 0,
        limit: int = 500,
        tail: bool = False,
    ) -> list[ActivityEvent]:
        """The session's events, ascending.

        ``tail=True`` returns the *newest* ``limit`` events in ascending
        order instead of the oldest — what a chat window opened on the live
        end of a long session needs.
        """
        if tail:
            cursor = await self.conn.execute(
                "SELECT * FROM ("
                "  SELECT * FROM activity_events "
                "  WHERE agent_type = ? AND session_id = ? AND seq > ? "
                "  ORDER BY seq DESC LIMIT ?"
                ") ORDER BY seq ASC",
                (agent_type.value, session_id, after_seq, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM activity_events "
                "WHERE agent_type = ? AND session_id = ? AND seq > ? "
                "ORDER BY seq ASC LIMIT ?",
                (agent_type.value, session_id, after_seq, limit),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_activity(row) for row in rows]

    async def list_messages(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        after_seq: int = 0,
        limit: int = 500,
        tail: bool = False,
    ) -> list[MessageRecord]:
        kinds = sorted(kind.value for kind in CHAT_KINDS)
        placeholders = ",".join("?" for _ in kinds)
        if tail:
            cursor = await self.conn.execute(
                f"SELECT * FROM ("
                f"  SELECT * FROM activity_events "
                f"  WHERE agent_type = ? AND session_id = ? AND seq > ? "
                f"  AND kind IN ({placeholders}) ORDER BY seq DESC LIMIT ?"
                f") ORDER BY seq ASC",
                (agent_type.value, session_id, after_seq, *kinds, limit),
            )
        else:
            cursor = await self.conn.execute(
                f"SELECT * FROM activity_events "
                f"WHERE agent_type = ? AND session_id = ? AND seq > ? "
                f"AND kind IN ({placeholders}) ORDER BY seq ASC LIMIT ?",
                (agent_type.value, session_id, after_seq, *kinds, limit),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_message(row) for row in rows]

    async def count_messages(self, *, agent_type: AgentType, session_id: str) -> int:
        kinds = sorted(kind.value for kind in CHAT_KINDS)
        placeholders = ",".join("?" for _ in kinds)
        cursor = await self.conn.execute(
            f"SELECT COUNT(*) AS n FROM activity_events "
            f"WHERE agent_type = ? AND session_id = ? AND kind IN ({placeholders})",
            (agent_type.value, session_id, *kinds),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["n"]) if row else 0

    async def count_pending_for_session(self, session_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM approvals WHERE session_id = ? AND state = ?",
            (session_id, ApprovalState.PENDING.value),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["n"]) if row else 0

    async def pending_counts_by_session(self) -> dict[str, int]:
        """Every session's pending count in one query (no per-session N+1)."""
        cursor = await self.conn.execute(
            "SELECT session_id, COUNT(*) AS n FROM approvals WHERE state = ? "
            "GROUP BY session_id",
            (ApprovalState.PENDING.value,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {row["session_id"]: int(row["n"]) for row in rows}

    # --- housekeeping -----------------------------------------------------

    async def mark_stale_sessions_idle(self, before_iso: str) -> int:
        """Mark ``running`` sessions whose last activity precedes ``before_iso``.

        Nothing ever ended a session before, so the home screen showed every
        transcript session as active forever.
        """
        cursor = await self.conn.execute(
            "UPDATE sessions SET state = 'idle' WHERE state = 'running' "
            "AND COALESCE(last_activity_at, started_at) < ?",
            (before_iso,),
        )
        changed = cursor.rowcount or 0
        await cursor.close()
        await self.conn.commit()
        return changed

    async def prune_before(self, cutoff_iso: str) -> dict[str, int]:
        """Apply ``[activity] retention_days``: delete rows older than the cutoff.

        Activity events, audit rows and *decided* approvals are removed;
        pending approvals are never touched. ``message_count`` is recomputed
        for every session that lost events so the home screen stays truthful.
        """
        stats = {"events": 0, "audit": 0, "approvals": 0}
        cursor = await self.conn.execute(
            "SELECT DISTINCT agent_type, session_id FROM activity_events "
            "WHERE ts < ?",
            (cutoff_iso,),
        )
        affected = [
            (row["agent_type"], row["session_id"])
            for row in await cursor.fetchall()
        ]
        await cursor.close()

        cursor = await self.conn.execute(
            "DELETE FROM activity_events WHERE ts < ?", (cutoff_iso,)
        )
        stats["events"] = cursor.rowcount or 0
        await cursor.close()

        for agent_value, session_id in affected:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM activity_events "
                "WHERE agent_type = ? AND session_id = ? AND kind IN ("
                "  'user_message','assistant_message','reasoning',"
                "  'tool_call','tool_result','error','note')",
                (agent_value, session_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            await self.conn.execute(
                "UPDATE sessions SET message_count = ? WHERE session_id = ?",
                (int(row["n"]) if row else 0, session_id),
            )

        cursor = await self.conn.execute(
            "DELETE FROM audit_log WHERE ts < ?", (cutoff_iso,)
        )
        stats["audit"] = cursor.rowcount or 0
        await cursor.close()

        cursor = await self.conn.execute(
            "DELETE FROM approvals WHERE state != ? "
            "AND COALESCE(decided_at, created_at) < ?",
            (ApprovalState.PENDING.value, cutoff_iso),
        )
        stats["approvals"] = cursor.rowcount or 0
        await cursor.close()

        await self.conn.commit()
        return stats

    # --- transcript high-water marks (D-018) -------------------------------

    async def get_transcript_marks(
        self, agent_type: str, session_id: str
    ) -> dict[str, TranscriptMark]:
        cursor = await self.conn.execute(
            "SELECT * FROM transcript_state WHERE agent_type = ? AND session_id = ?",
            (agent_type, session_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {
            row["source_path"]: TranscriptMark(
                pos=int(row["pos"]),
                records=int(row["records"]),
                size=int(row["size"]),
                version=int(row["version"]),
            )
            for row in rows
        }

    async def set_transcript_marks(
        self,
        agent_type: str,
        session_id: str,
        marks: dict[str, TranscriptMark],
    ) -> None:
        if not marks:
            return
        await self.conn.executemany(
            """
            INSERT INTO transcript_state (
              agent_type, session_id, source_path, pos, records, size, version
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(agent_type, session_id, source_path) DO UPDATE SET
              pos = excluded.pos,
              records = excluded.records,
              size = excluded.size,
              version = excluded.version
            """,
            [
                (
                    agent_type,
                    session_id,
                    source_path,
                    mark.pos,
                    mark.records,
                    mark.size,
                    mark.version,
                )
                for source_path, mark in marks.items()
            ],
        )
        await self.conn.commit()

    # --- audit ------------------------------------------------------------

    async def append_audit(
        self,
        *,
        actor: str,
        action: str,
        subject: str | None,
        detail: dict[str, Any],
    ) -> None:
        await self.conn.execute(
            "INSERT INTO audit_log (ts, actor, action, subject, detail_json) VALUES (?,?,?,?,?)",
            (iso(utcnow()), actor, action, subject, json.dumps(detail, default=str)),
        )
        await self.conn.commit()

    async def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    # --- meta -------------------------------------------------------------

    async def get_meta(self, key: str) -> str | None:
        cursor = await self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()


