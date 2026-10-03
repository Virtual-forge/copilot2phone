"""OpenCode transcript reader.

OpenCode v2 keeps its sessions in a local SQLite database at
``~/.local/share/opencode/opencode.db`` (override with ``OPENCODE_DB``):
``session_v2`` rows are the threads (with title, directory and epoch-ms
timestamps) and ``session_message`` rows are an append-only, per-session
message log whose ``seq`` rises monotonically — exactly the shape the
high-water marks need.

The database is opened **read-only** and every query is best-effort (D-018):
a missing, locked or unreadable database yields no sessions, never an error.
Only ``session_v2``, ``session_message`` and ``project`` are ever read — the
``account``/``credential`` tables hold secrets and are deliberately not
touched.

Message mapping:

* ``user``         → ``user_message`` (``data.text``)
* ``assistant``    → one event per content part: ``reasoning`` →
  ``reasoning``, ``text`` → ``assistant_message``, ``tool`` → a
  ``tool_call`` plus a ``tool_result`` once the tool's state says it is done
* ``synthetic``    → ``note`` (injected shell output and the like, which are
  part of the conversation the agent sees)
* ``idle``        → ``task_finished`` (feed-only turn boundary)
* anything else    (``system``, ``model-switched``, ...) is dropped as
  harness bookkeeping.

Because OpenCode *updates* the message row while a turn is streaming (the
``tool`` part's state fills in as it runs), the watermark re-reads the last
message instead of skipping past it: the boundary row is re-scanned until a
newer message exists, and the stored events are refreshed in place by id.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..protocol import ActivityKind, AgentType, MessageRole, utcnow
from .base import (
    TranscriptEvent,
    TranscriptRead,
    TranscriptSession,
    as_text,
    clip,
    sort_key,
)

AGENT = AgentType.OPENCODE

#: A tool part carries its result inside ``state``; only these statuses mean
#: the output below belongs to the call.
_TOOL_DONE = ("completed", "failed", "error")


def opencode_db_path() -> Path:
    """Return OpenCode's session database (``OPENCODE_DB`` overrides)."""
    override = os.environ.get("OPENCODE_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _connect(path: Path) -> sqlite3.Connection:
    """Open OpenCode's live database read-only (WAL allows concurrent readers)."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def _ms_to_dt(value: Any) -> datetime | None:
    """OpenCode stores epoch milliseconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


class OpencodeTranscriptReader:
    """Reads OpenCode sessions out of its local SQLite database."""

    agent_type = AGENT

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or opencode_db_path()

    # --- discovery --------------------------------------------------------

    def discover(self) -> list[TranscriptSession]:
        if not self._db_path.exists():
            return []
        try:
            conn = _connect(self._db_path)
        except sqlite3.Error:
            return []
        try:
            rows = conn.execute(
                "SELECT s.id, s.directory, s.title, s.time_created, s.time_updated,"
                " p.worktree"
                " FROM session_v2 s LEFT JOIN project p ON p.id = s.project_id"
                " WHERE s.time_archived IS NULL"
                " ORDER BY s.time_updated DESC"
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        found: list[TranscriptSession] = []
        for row in rows:
            found.append(
                TranscriptSession(
                    agent_type=AGENT,
                    session_id=str(row["id"]),
                    workspace_path=str(row["directory"] or row["worktree"] or ""),
                    title=str(row["title"] or "").strip(),
                    started_at=_ms_to_dt(row["time_created"]),
                    updated_at=_ms_to_dt(row["time_updated"]),
                    source_path=self._db_path,
                )
            )
        found.sort(key=sort_key, reverse=True)
        return found

    # --- reading ----------------------------------------------------------

    def read(
        self,
        session: TranscriptSession,
        starts: dict[str, tuple[int, int]] | None = None,
    ) -> TranscriptRead:
        """Return the session's events, oldest first.

        ``starts`` carries the previous scan's watermark — the highest
        message ``seq`` already ingested for this session. The read includes
        that boundary message again, so a tool whose result was still
        streaming when it was first seen gets refreshed in place (D-018)
        instead of being skipped forever.
        """
        source = str(session.source_path or self._db_path)
        watermark = (starts or {}).get(source, (0, 0))[1]
        try:
            conn = _connect(self._db_path)
        except sqlite3.Error:
            return TranscriptRead(marks={source: (0, watermark)})
        try:
            rows = conn.execute(
                "SELECT id, type, seq, time_created, data FROM session_message"
                " WHERE session_id = ? AND seq >= ?"
                " ORDER BY seq ASC",
                (session.session_id, watermark),
            ).fetchall()
        except sqlite3.Error:
            return TranscriptRead(marks={source: (0, watermark)})
        finally:
            conn.close()

        events: list[TranscriptEvent] = []
        for row in rows:
            watermark = max(watermark, int(row["seq"]))
            ts = _ms_to_dt(row["time_created"]) or session.started_at or utcnow()
            try:
                data = json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            events.extend(
                self._message_events(str(row["id"]), str(row["type"]), ts, data)
            )
        return TranscriptRead(events, marks={source: (0, watermark)})

    def _message_events(
        self, message_id: str, mtype: str, ts: datetime, data: dict[str, Any]
    ) -> list[TranscriptEvent]:
        base = f"opencode:{message_id}"
        if mtype == "user":
            text = str(data.get("text") or "")
            if not text.strip():
                return []
            return [
                TranscriptEvent(
                    base, ActivityKind.USER_MESSAGE, ts,
                    summary=clip(text), text=text, role=MessageRole.USER,
                )
            ]
        if mtype == "synthetic":
            text = str(data.get("text") or "")
            if not text.strip():
                return []
            return [
                TranscriptEvent(
                    base, ActivityKind.NOTE, ts,
                    summary=clip(str(data.get("description") or "injected")),
                    text=text, role=MessageRole.SYSTEM,
                    detail={"synthetic": True},
                )
            ]
        if mtype == "idle":
            return [
                TranscriptEvent(
                    base, ActivityKind.TASK_FINISHED, ts,
                    summary="turn ended",
                    detail={"outcome": data.get("outcome")},
                )
            ]
        if mtype != "assistant":
            return []  # system, model-switched, ...: harness bookkeeping

        events: list[TranscriptEvent] = []
        content = data.get("content")
        for index, part in enumerate(content if isinstance(content, list) else []):
            if not isinstance(part, dict):
                continue
            part_id = f"{base}:{index}"
            part_ts = _ms_to_dt((part.get("time") or {}).get("created")) or ts
            ptype = part.get("type")
            if ptype == "reasoning":
                text = str(part.get("text") or "")
                if text.strip():
                    events.append(
                        TranscriptEvent(
                            part_id, ActivityKind.REASONING, part_ts,
                            summary=clip(text), text=text,
                            role=MessageRole.ASSISTANT,
                        )
                    )
            elif ptype == "text":
                text = str(part.get("text") or "")
                if text.strip():
                    events.append(
                        TranscriptEvent(
                            part_id, ActivityKind.ASSISTANT_MESSAGE, part_ts,
                            summary=clip(text), text=text,
                            role=MessageRole.ASSISTANT,
                        )
                    )
            elif ptype == "tool":
                events.extend(self._tool_events(part_id, part, part_ts))
        return events

    def _tool_events(
        self, part_id: str, part: dict[str, Any], ts: datetime
    ) -> list[TranscriptEvent]:
        name = str(part.get("name") or "tool")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        events = [
            TranscriptEvent(
                f"{part_id}:call", ActivityKind.TOOL_CALL, ts,
                summary=name, text=as_text(state.get("input")),
                role=MessageRole.ASSISTANT,
                detail={
                    "tool_name": name,
                    "call_id": part.get("id"),
                    "status": state.get("status"),
                },
            )
        ]
        if str(state.get("status")) in _TOOL_DONE:
            output = "\n".join(
                str(item.get("text") or "")
                for item in (state.get("content") or [])
                if isinstance(item, dict)
            ).strip()
            events.append(
                TranscriptEvent(
                    f"{part_id}:out", ActivityKind.TOOL_RESULT, ts,
                    summary=clip(output or str(state.get("status"))),
                    text=output or str(state.get("status")),
                    role=MessageRole.TOOL,
                    detail={"call_id": part.get("id"), "status": state.get("status")},
                )
            )
        return events
