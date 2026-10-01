"""SQLite persistence (SPEC.md §8.1).

Every operational read/write takes ``agent_type`` so that Cline and Codex state
can never be mixed (D13 / §8.3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from .protocol import (
    ActionDetail,
    AgentType,
    ApprovalRecord,
    ApprovalState,
    Decision,
    Risk,
    RiskLevel,
    SessionRecord,
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
  session_id     TEXT PRIMARY KEY,
  agent_type     TEXT NOT NULL,
  workspace_path TEXT NOT NULL,
  pc_device_id   TEXT,
  started_at     TEXT NOT NULL,
  ended_at       TEXT,
  state          TEXT NOT NULL DEFAULT 'idle'
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
  ts             TEXT NOT NULL,
  summary        TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_approvals_agent_state
  ON approvals (agent_type, session_id, state);
CREATE INDEX IF NOT EXISTS idx_approvals_agent_created
  ON approvals (agent_type, created_at);
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


def _row_to_session(row: aiosqlite.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        agent_type=AgentType(row["agent_type"]),
        workspace_path=row["workspace_path"],
        started_at=parse_iso(row["started_at"]),
        ended_at=parse_iso(row["ended_at"]) if row["ended_at"] else None,
        state=row["state"],
    )


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
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()

    async def migrate(self) -> None:
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

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
        state: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_type is not None:
            clauses.append("agent_type = ?")
            params.append(agent_type.value)
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

    async def expire_stale(self, now_iso: str) -> int:
        """Mark pending approvals past their expiry. Returns rows changed."""
        cursor = await self.conn.execute(
            """
            UPDATE approvals
               SET state = ?, decision = ?, decision_reason = ?, decided_at = ?
             WHERE state = ? AND expires_at <= ?
            """,
            (
                ApprovalState.EXPIRED.value,
                Decision.DENY.value,
                "expired",
                now_iso,
                ApprovalState.PENDING.value,
                now_iso,
            ),
        )
        await self.conn.commit()
        changed = cursor.rowcount or 0
        await cursor.close()
        return changed

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
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO sessions (session_id, agent_type, workspace_path, started_at, state)
            VALUES (?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
              workspace_path = excluded.workspace_path,
              state = excluded.state
            """,
            (session_id, agent_type.value, workspace_path, iso(utcnow()), state),
        )
        await self.conn.commit()

    async def list_sessions(self, agent_type: AgentType | None = None) -> list[SessionRecord]:
        if agent_type is None:
            cursor = await self.conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC"
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM sessions WHERE agent_type = ? ORDER BY started_at DESC",
                (agent_type.value,),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_session(row) for row in rows]

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


