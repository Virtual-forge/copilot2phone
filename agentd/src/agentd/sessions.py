"""Session management, partitioned by agent (SPEC.md §8.1)."""

from __future__ import annotations

from .db import Database
from .protocol import AgentType, SessionRecord


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
    ) -> None:
        """Create the session if new, otherwise refresh its workspace/state."""
        await self._db.upsert_session(
            session_id=session_id,
            agent_type=agent_type,
            workspace_path=workspace_path,
            state=state,
        )

    async def list(self, agent_type: AgentType | None = None) -> list[SessionRecord]:
        return await self._db.list_sessions(agent_type)
