"""Transcript readers: pull chat history out of each agent's own files.

The approval hook only ever sees tool calls, so the chat transcript has to come
from the agent's own on-disk history. Every agent stores it differently, so each
reader normalises to :class:`TranscriptEvent` and the daemon folds those into the
activity stream.

Readers are read-only and best-effort: a malformed file is skipped, never fatal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..protocol import ActivityKind, AgentType, MessageRole

#: Bump when any reader's record-to-event mapping changes. Scan marks made
#: with an older version are ignored, so the next scan re-reads and refreshes
#: every affected session instead of tailing past the change (D-018).
READER_VERSION = 2


@dataclass
class TranscriptSession:
    """One session discovered on disk."""

    agent_type: AgentType
    session_id: str
    workspace_path: str = ""
    title: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None
    source_path: Path | None = None
    #: A Codex thread can be resumed into a new rollout file that keeps the
    #: same session id. The reader groups those files into one session so
    #: ``read`` sees the whole conversation in order. Cline always has one.
    source_paths: list[Path] = field(default_factory=list)

    @property
    def paths(self) -> list[Path]:
        """Every file (or directory) backing this session, oldest first."""
        if self.source_paths:
            return list(self.source_paths)
        return [self.source_path] if self.source_path is not None else []


@dataclass
class TranscriptEvent:
    """One normalised entry from a transcript, oldest-first within a session."""

    event_id: str
    kind: ActivityKind
    ts: datetime
    summary: str = ""
    text: str = ""
    role: MessageRole | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class TranscriptRead(list):
    """The events of one scan, plus how far each source file was consumed.

    It *is* a list of :class:`TranscriptEvent`, so existing callers keep
    working. ``marks`` maps ``str(source_path)`` to ``(pos, records)``: the
    byte offset just past the last complete ingested line, and the number of
    valid records consumed before it (records are what number a session's
    event ids). A reader that cannot track consumption — Cline reads a
    couple of small JSON files — reports no marks and is re-read in full.
    """

    def __init__(
        self,
        events: Iterable[TranscriptEvent] = (),
        marks: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        super().__init__(events)
        self.marks: dict[str, tuple[int, int]] = dict(marks or {})


@runtime_checkable
class TranscriptReader(Protocol):
    """What every agent transcript reader must provide."""

    agent_type: AgentType

    def discover(self) -> list[TranscriptSession]:
        """Return every session this agent has on disk, newest first."""

    def read(
        self,
        session: TranscriptSession,
        starts: dict[str, tuple[int, int]] | None = None,
    ) -> TranscriptRead:
        """Return the session's events, oldest first.

        ``starts`` maps ``str(source_path)`` to ``(pos, records)`` — where a
        previous scan stopped consuming that file. Records before it are
        skipped (they are already stored); the returned ``marks`` say where
        this scan stopped.
        """


def clip(text: str, limit: int = 160) -> str:
    """Collapse whitespace and truncate, for one-line summaries."""
    collapsed = " ".join(str(text).split())
    return collapsed[:limit]


def as_text(value: Any) -> str:
    """Render any hook/transcript value as display text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def parse_iso_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601-ish string, tolerating a trailing ``Z``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sort_key(session: TranscriptSession) -> datetime:
    """Order sessions newest-first for discovery."""
    return session.updated_at or session.started_at or datetime.min.replace(
        tzinfo=timezone.utc
    )
