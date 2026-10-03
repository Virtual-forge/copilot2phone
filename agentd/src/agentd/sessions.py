"""Session management, partitioned by agent (SPEC.md §8.1)."""

from __future__ import annotations

from datetime import datetime

from .db import Database
from .protocol import AgentType, SessionSummary


class SessionManager:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def touch(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        workspace_path: str,
        state: str = "running",
        title: str | None = None,
        source: str | None = None,
        started_at: datetime | None = None,
        last_activity_at: datetime | None = None,
    ) -> None:
        """Create the session if new, otherwise refresh its workspace/state."""
        await self._db.upsert_session(
            session_id=session_id,
            agent_type=agent_type,
            workspace_path=workspace_path,
            state=state,
            title=title,
            source=source,
            started_at=started_at,
            last_activity_at=last_activity_at,
        )

    async def summary(self, session_id: str) -> SessionSummary | None:
        return await self._db.get_session_summary(session_id)

    async def summaries(
        self, agent_type: AgentType | None = None, limit: int = 200
    ) -> list[SessionSummary]:
        return await self._db.list_session_summaries(agent_type, limit)
