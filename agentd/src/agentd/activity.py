"""Session activity stream (SPEC.md §12).

Everything a session does — chat turns, tool calls, approvals, lifecycle — is
recorded here as an :class:`ActivityEvent` with a monotonic ``seq`` per
 ``(agent_type, session_id)``. The chat view is simply the subset of events whose
kind is a message kind (see :data:`agentd.protocol.CHAT_KINDS`).

Events carry a caller-supplied ``event_id`` so re-reading a transcript file is
idempotent: the same line always maps to the same id and is ignored on re-scan.

Every ingest also wakes the live-stream subscribers (D-026): the SSE endpoint
hands them "this session changed", and the phone refetches incrementally
instead of waiting out the poll interval.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .crypto import new_id
from .db import Database
from .protocol import (
    ActivityEvent,
    ActivityKind,
    AgentType,
    MessageRecord,
    MessageRole,
    utcnow,
)
from .sessions import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class IncomingEvent:
    """A normalised event on its way into the store."""

    kind: ActivityKind
    summary: str = ""
    text: str = ""
    role: MessageRole | None = None
    ts: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    event_id: str | None = None


class ActivityManager:
    """Writes and reads the per-session activity stream."""

    def __init__(self, *, db: Database, sessions: SessionManager) -> None:
        self._db = db
        self._sessions = sessions
        self._listeners: set[asyncio.Queue] = set()

    # --- live change notifications (D-026) ------------------------------

    def subscribe(self) -> asyncio.Queue:
        """Register for "something changed" wake-ups; see :meth:`_wake`."""
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def _wake(self, agent_type: AgentType, session_id: str) -> None:
        """Nudge every live stream that this session changed.

        The payload is intentionally tiny — *what* changed, not the change —
        so clients refetch through the normal authenticated endpoints.
        """
        if not self._listeners:
            return
        notice = {"agent_type": agent_type.value, "session_id": session_id}
        for queue in list(self._listeners):
            queue.put_nowait(notice)

    async def ingest(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        workspace_path: str,
        events: list[IncomingEvent],
        title: str | None = None,
        source: str | None = None,
        started_at: datetime | None = None,
    ) -> int:
        """Store a batch of events. Returns how many were newly inserted.

        The whole batch is written in a single transaction, and sequence
        numbers are reserved once for the events that are actually new — a
        re-scan of an unchanged file costs nothing and burns no ``seq``.
        """
        if not events:
            return 0

        await self._sessions.touch(
            agent_type=agent_type,
            session_id=session_id,
            workspace_path=workspace_path,
            state="running",
            title=title,
            source=source,
            started_at=started_at,
        )

        fresh: list[ActivityEvent] = []
        seen: set[str] = set()
        latest: datetime | None = None
        for item in events:
            event_id = item.event_id or new_id()
            if event_id in seen:  # the same line twice in one batch
                continue
            seen.add(event_id)
            event = ActivityEvent(
                event_id=event_id,
                seq=0,  # assigned below, only for events that are new
                agent_type=agent_type,
                session_id=session_id,
                workspace_path=workspace_path,
                kind=item.kind,
                role=item.role,
                ts=item.ts or utcnow(),
                summary=item.summary,
                text=item.text,
                detail=item.detail,
            )
            fresh.append(event)
            if latest is None or event.ts > latest:
                latest = event.ts

        known = await self._db.existing_activity_ids(sorted(seen))
        new = [event for event in fresh if event.event_id not in known]
        refresh = [event for event in fresh if event.event_id in known]
        if new:
            base = await self._db.reserve_seqs(session_id, len(new))
            for offset, event in enumerate(new):
                event.seq = base + offset

        inserted = await self._db.store_activity(new=new, refresh=refresh)

        if latest is not None:
            count = await self._db.count_messages(
                agent_type=agent_type, session_id=session_id
            )
            await self._db.set_session_activity(
                session_id=session_id, last_activity_at=latest, message_count=count
            )
        # Wake the live streams: the phone refetches incrementally instead
        # of waiting out the poll interval.
        self._wake(agent_type, session_id)
        return inserted

    async def record(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        workspace_path: str,
        kind: ActivityKind,
        summary: str = "",
        text: str = "",
        role: MessageRole | None = None,
        detail: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Record a single event (used by the approval lifecycle)."""
        await self.ingest(
            agent_type=agent_type,
            session_id=session_id,
            workspace_path=workspace_path,
            events=[
                IncomingEvent(
                    kind=kind,
                    summary=summary,
                    text=text,
                    role=role,
                    detail=detail or {},
                    ts=ts,
                )
            ],
        )

    async def events(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        after_seq: int = 0,
        limit: int = 500,
        tail: bool = False,
    ) -> list[ActivityEvent]:
        return await self._db.list_activity_events(
            agent_type=agent_type,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            tail=tail,
        )

    async def messages(
        self,
        *,
        agent_type: AgentType,
        session_id: str,
        after_seq: int = 0,
        limit: int = 500,
        tail: bool = False,
    ) -> list[MessageRecord]:
        return await self._db.list_messages(
            agent_type=agent_type,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            tail=tail,
        )
