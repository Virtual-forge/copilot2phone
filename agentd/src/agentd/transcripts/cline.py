"""Cline transcript reader.

Cline keeps one directory per task under
``%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<taskId>/``.
The chat lives in ``api_conversation_history.json`` (Anthropic-shaped) and the
UI stream in ``ui_messages.json``. Both are read tolerantly; the API history is
preferred for chat when present, with the UI stream as a fallback.

The task directory name is ``<epoch-ms>_<suffix>``, so the start time comes from
the name when no metadata is available.
"""

from __future__ import annotations

import json
import os
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

AGENT = AgentType.CLINE


def cline_tasks_dir() -> Path:
    """Return Cline's task directory (``CLINE_TASKS_DIR`` overrides)."""
    override = os.environ.get("CLINE_TASKS_DIR")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "tasks"


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _to_dt(value: Any) -> datetime | None:
    """Accept epoch seconds, epoch milliseconds or an ISO string."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


class ClineTranscriptReader:
    """Reads Cline task directories into normalised transcript events."""

    agent_type = AGENT

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self._tasks_dir = tasks_dir or cline_tasks_dir()

    # --- discovery --------------------------------------------------------

    def discover(self) -> list[TranscriptSession]:
        root = self._tasks_dir
        if not root.exists():
            return []
        found: list[TranscriptSession] = []
        for task_dir in root.iterdir():
            if not task_dir.is_dir():
                continue
            try:
                updated = datetime.fromtimestamp(task_dir.stat().st_mtime, tz=timezone.utc)
            except OSError:
                updated = None
            found.append(
                TranscriptSession(
                    agent_type=AGENT,
                    session_id=task_dir.name,
                    workspace_path="",
                    title=self._title(task_dir),
                    started_at=self._started_at(task_dir),
                    updated_at=updated,
                    source_path=task_dir,
                )
            )
        found.sort(key=sort_key, reverse=True)
        return found

    def _title(self, task_dir: Path) -> str:
        meta = _load_json(task_dir / "task_metadata.json")
        if isinstance(meta, dict):
            task = meta.get("task")
            if isinstance(task, str) and task.strip():
                return clip(task, 120)
        return ""

    def _started_at(self, task_dir: Path) -> datetime | None:
        prefix = task_dir.name.split("_", 1)[0]
        if prefix.isdigit():
            return _to_dt(int(prefix))
        return None

    # --- reading ----------------------------------------------------------

    def read(
        self,
        session: TranscriptSession,
        starts: dict[str, tuple[int, int]] | None = None,
    ) -> TranscriptRead:
        """Cline keeps two small JSON files per task, so every scan reads all
        of it and reports no consumption marks — ``starts`` is accepted for
        the shared reader contract and ignored."""
        task_dir = session.source_path
        if task_dir is None:
            return TranscriptRead()
        events = self._read_api_history(session, task_dir)
        if not events:
            events = self._read_ui_messages(session, task_dir)
        return TranscriptRead(events)

    def _read_api_history(
        self, session: TranscriptSession, task_dir: Path
    ) -> list[TranscriptEvent]:
        data = _load_json(task_dir / "api_conversation_history.json")
        if not isinstance(data, list):
            return []
        events: list[TranscriptEvent] = []
        for index, message in enumerate(data):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            ts = session.started_at or utcnow()
            base = f"cline:{session.session_id}:api:{index}"
            if isinstance(content, str):
                event = self._text_event(base, role, content, ts)
                if event is not None:
                    events.append(event)
                continue
            if not isinstance(content, list):
                continue
            for part_index, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                part_id = f"{base}:{part_index}"
                ptype = part.get("type")
                if ptype == "text":
                    event = self._text_event(part_id, role, part.get("text"), ts)
                    if event is not None:
                        events.append(event)
                elif ptype == "tool_use":
                    name = str(part.get("name") or "tool")
                    events.append(
                        TranscriptEvent(
                            part_id, ActivityKind.TOOL_CALL, ts,
                            summary=name, text=as_text(part.get("input")),
                            role=MessageRole.ASSISTANT,
                            detail={"tool_name": name},
                        )
                    )
                elif ptype == "tool_result":
                    output = as_text(part.get("content"))
                    events.append(
                        TranscriptEvent(
                            part_id, ActivityKind.TOOL_RESULT, ts,
                            summary=clip(output), text=output, role=MessageRole.TOOL,
                        )
                    )
        return events

    def _text_event(
        self, event_id: str, role: Any, text: Any, ts: datetime
    ) -> TranscriptEvent | None:
        body = as_text(text)
        if not body.strip():
            return None
        if role == "user":
            return TranscriptEvent(
                event_id, ActivityKind.USER_MESSAGE, ts,
                summary=clip(body), text=body, role=MessageRole.USER,
            )
        if role == "assistant":
            return TranscriptEvent(
                event_id, ActivityKind.ASSISTANT_MESSAGE, ts,
                summary=clip(body), text=body, role=MessageRole.ASSISTANT,
            )
        return TranscriptEvent(
            event_id, ActivityKind.NOTE, ts,
            summary=clip(body), text=body, role=MessageRole.SYSTEM,
        )

    def _read_ui_messages(
        self, session: TranscriptSession, task_dir: Path
    ) -> list[TranscriptEvent]:
        data = _load_json(task_dir / "ui_messages.json")
        if not isinstance(data, list):
            return []
        events: list[TranscriptEvent] = []
        for index, message in enumerate(data):
            if not isinstance(message, dict):
                continue
            ts = _to_dt(message.get("ts")) or session.started_at or utcnow()
            event_id = f"cline:{session.session_id}:ui:{index}"
            text = as_text(message.get("text"))
            mtype = message.get("type")
            if mtype == "ask":
                events.append(self._ask_event(event_id, ts, message.get("ask"), text))
            elif mtype == "say":
                events.append(self._say_event(event_id, ts, message.get("say"), text))
        return [event for event in events if event is not None]

    def _ask_event(
        self, event_id: str, ts: datetime, ask: Any, text: str
    ) -> TranscriptEvent | None:
        if ask == "command":
            return TranscriptEvent(
                event_id, ActivityKind.TOOL_CALL, ts,
                summary="command", text=text, role=MessageRole.ASSISTANT,
                detail={"tool_name": "execute_command"},
            )
        if ask in ("tool", "use_mcp_server"):
            return TranscriptEvent(
                event_id, ActivityKind.TOOL_CALL, ts,
                summary=str(ask), text=text, role=MessageRole.ASSISTANT,
                detail={"tool_name": str(ask)},
            )
        if ask == "followup":
            return TranscriptEvent(
                event_id, ActivityKind.ASSISTANT_MESSAGE, ts,
                summary=clip(text), text=text, role=MessageRole.ASSISTANT,
            )
        return TranscriptEvent(
            event_id, ActivityKind.NOTE, ts,
            summary=clip(text), text=text, role=MessageRole.SYSTEM,
            detail={"ask": ask},
        )

    def _say_event(
        self, event_id: str, ts: datetime, say: Any, text: str
    ) -> TranscriptEvent | None:
        if say == "user_feedback":
            return TranscriptEvent(
                event_id, ActivityKind.USER_MESSAGE, ts,
                summary=clip(text), text=text, role=MessageRole.USER,
            )
        if say == "text":
            return TranscriptEvent(
                event_id, ActivityKind.ASSISTANT_MESSAGE, ts,
                summary=clip(text), text=text, role=MessageRole.ASSISTANT,
            )
        if say in ("tool", "command_output"):
            return TranscriptEvent(
                event_id, ActivityKind.TOOL_RESULT, ts,
                summary=clip(text), text=text, role=MessageRole.TOOL,
            )
        if say == "error":
            return TranscriptEvent(
                event_id, ActivityKind.ERROR, ts,
                summary=clip(text), text=text, role=MessageRole.SYSTEM,
            )
        return TranscriptEvent(
            event_id, ActivityKind.NOTE, ts,
            summary=clip(text), text=text, role=MessageRole.SYSTEM,
            detail={"say": say},
        )
