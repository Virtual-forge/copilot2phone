"""Codex transcript reader.

Codex writes one JSONL "rollout" per session under
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` and keeps a flat index of
human-readable thread names in ``~/.codex/session_index.jsonl``.

Each rollout line is ``{"timestamp": ..., "type": ..., "payload": {...}}`` where
``type`` is one of ``session_meta``, ``event_msg``, ``response_item``,
``turn_context`` or ``world_state``. Only the first three carry anything we show.
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
    parse_iso_ts,
    sort_key,
)

AGENT = AgentType.CODEX

_TOOL_CALL_TYPES = ("function_call", "custom_tool_call", "local_shell_call")
_TOOL_RESULT_TYPES = (
    "function_call_output",
    "custom_tool_call_output",
    "local_shell_call_output",
)

#: Codex's unified-exec surface. ``exec`` runs a generated JavaScript snippet
#: (the real command is buried inside it) and ``wait`` polls the cell it
#: started, so one shell command becomes a burst of calls and "Script running…"
#: results. That is harness plumbing, not conversation: it is recorded as
#: :data:`ActivityKind.TOOL_PLUMBING` so the feed keeps it and the chat does not.
_PLUMBING_TOOLS = frozenset({"exec", "wait"})


def codex_home() -> Path:
    """Return the Codex state directory (``CODEX_HOME`` overrides)."""
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def _iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _first_record(path: Path) -> dict[str, Any] | None:
    for record in _iter_jsonl(path):
        return record
    return None


def _text_from_content(content: Any) -> str:
    """Flatten an OpenAI-style content list (or plain string) into text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part)


class CodexTranscriptReader:
    """Reads Codex rollout files into normalised transcript events."""

    agent_type = AGENT

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or codex_home()

    # --- discovery --------------------------------------------------------

    def discover(self) -> list[TranscriptSession]:
        root = self._home / "sessions"
        if not root.exists():
            return []
        names = self._thread_names()
        # A resumed Codex thread writes a *new* rollout file that reuses the
        # session id (and does not replay the earlier turns). Group the files
        # into one session, oldest first: otherwise their per-file record
        # indices collide on ``event_id`` and the shorter resume file silently
        # overwrites the original's rows.
        grouped: dict[str, list[tuple[datetime | None, Path, str, datetime | None]]] = {}
        for path in root.rglob("rollout-*.jsonl"):
            meta = self._session_meta(path)
            if meta is None:
                continue
            session_id, workspace, started = meta
            try:
                updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                updated = started
            grouped.setdefault(session_id, []).append(
                (started, path, workspace, updated)
            )

        epoch = datetime.min.replace(tzinfo=timezone.utc)
        found: list[TranscriptSession] = []
        for session_id, parts in grouped.items():
            parts.sort(key=lambda part: (part[0] or part[3] or epoch, part[1].name))
            paths = [part[1] for part in parts]
            starts = [part[0] for part in parts if part[0] is not None]
            updateds = [part[3] for part in parts if part[3] is not None]
            workspace = next((part[2] for part in parts if part[2]), "")
            found.append(
                TranscriptSession(
                    agent_type=AGENT,
                    session_id=session_id,
                    workspace_path=workspace,
                    title=names.get(session_id, ""),
                    started_at=min(starts) if starts else None,
                    updated_at=max(updateds) if updateds else None,
                    source_path=paths[0],
                    source_paths=paths,
                )
            )
        found.sort(key=sort_key, reverse=True)
        return found

    def _thread_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for record in _iter_jsonl(self._home / "session_index.jsonl"):
            session_id = record.get("id")
            name = record.get("thread_name")
            if isinstance(session_id, str) and isinstance(name, str) and name:
                names[session_id] = name
        return names

    def _session_meta(self, path: Path) -> tuple[str, str, datetime | None] | None:
        record = _first_record(path)
        if record is None or record.get("type") != "session_meta":
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id") or payload.get("id")
        if not isinstance(session_id, str) or not session_id:
            return None
        workspace = payload.get("cwd")
        if not workspace:
            roots = payload.get("runtime_workspace_roots")
            if isinstance(roots, list) and roots:
                workspace = roots[0]
        started = parse_iso_ts(payload.get("timestamp")) or parse_iso_ts(
            record.get("timestamp")
        )
        return session_id, str(workspace or ""), started

    # --- reading ----------------------------------------------------------

    def read(
        self,
        session: TranscriptSession,
        starts: dict[str, tuple[int, int]] | None = None,
    ) -> TranscriptRead:
        """Read the session's rollout files, oldest first.

        ``starts`` says where a previous scan stopped each file; records below
        that point are already stored and are skipped. Record indices run
        across the whole session either way, so the event ids are stable.
        """
        starts = starts or {}
        events: list[TranscriptEvent] = []
        # call_id -> is this call harness plumbing? A tool result carries only
        # the call id, so the call has to be remembered to classify its output.
        calls: dict[str, bool] = {}
        marks: dict[str, tuple[int, int]] = {}
        offset = 0
        for path in session.paths:
            seek, prior = starts.get(str(path), (0, 0))
            pos, records = self._consume(session, path, seek, prior, offset, events, calls)
            marks[str(path)] = (pos, records)
            offset += records
        return TranscriptRead(events, marks=marks)

    def _consume(
        self,
        session: TranscriptSession,
        path: Path,
        seek: int,
        prior: int,
        offset: int,
        events: list[TranscriptEvent],
        calls: dict[str, bool],
    ) -> tuple[int, int]:
        """Consume one rollout file from byte ``seek``.

        Returns ``(pos, records)``: how far the file was consumed — never past
        a line that might still be being written — and how many valid records
        that makes in total. Malformed or non-object lines consume no index,
        matching the numbering a full re-read produces.
        """
        pos = seek
        index = prior
        try:
            handle = path.open("rb")
        except OSError:
            return seek, prior
        with handle:
            if seek:
                handle.seek(seek)
            for raw in handle:
                if raw.endswith(b"\n"):
                    pos += len(raw)
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                event = self._to_event(session, offset + index, record, calls)
                index += 1
                if event is not None:
                    events.append(event)
        return pos, index

    def _to_event(
        self,
        session: TranscriptSession,
        index: int,
        record: dict[str, Any],
        calls: dict[str, bool],
    ) -> TranscriptEvent | None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        ts = parse_iso_ts(record.get("timestamp")) or session.started_at or utcnow()
        event_id = f"codex:{session.session_id}:{index}"

        rtype = record.get("type")
        if rtype == "session_meta":
            return TranscriptEvent(
                event_id=event_id,
                kind=ActivityKind.SESSION_STARTED,
                ts=ts,
                summary="session started",
                detail={
                    "cwd": payload.get("cwd"),
                    "originator": payload.get("originator"),
                },
            )
        if rtype == "event_msg":
            return self._event_msg(event_id, ts, payload)
        if rtype == "response_item":
            return self._response_item(event_id, ts, payload, calls)
        return None

    def _event_msg(
        self, event_id: str, ts: datetime, payload: dict[str, Any]
    ) -> TranscriptEvent | None:
        ptype = payload.get("type")
        if ptype == "user_message":
            text = as_text(payload.get("message") or payload.get("text"))
            return TranscriptEvent(
                event_id, ActivityKind.USER_MESSAGE, ts,
                summary=clip(text), text=text, role=MessageRole.USER,
            )
        if ptype == "agent_message":
            text = as_text(payload.get("message") or payload.get("text"))
            return TranscriptEvent(
                event_id, ActivityKind.ASSISTANT_MESSAGE, ts,
                summary=clip(text), text=text, role=MessageRole.ASSISTANT,
            )
        if ptype == "task_started":
            return TranscriptEvent(
                event_id, ActivityKind.TASK_STARTED, ts,
                summary="task started", detail={"turn_id": payload.get("turn_id")},
            )
        if ptype == "task_complete":
            return TranscriptEvent(
                event_id, ActivityKind.TASK_FINISHED, ts, summary="task complete"
            )
        if ptype == "turn_aborted":
            return TranscriptEvent(
                event_id, ActivityKind.TASK_FINISHED, ts,
                summary="turn aborted", detail={"aborted": True},
            )
        return None

    def _response_item(
        self,
        event_id: str,
        ts: datetime,
        payload: dict[str, Any],
        calls: dict[str, bool],
    ) -> TranscriptEvent | None:
        ptype = payload.get("type")

        if ptype == "message":
            text = _text_from_content(payload.get("content"))
            if not text.strip():
                return None
            role = payload.get("role")
            if role == "user":
                return TranscriptEvent(
                    event_id, ActivityKind.USER_MESSAGE, ts,
                    summary=clip(text), text=text, role=MessageRole.USER,
                )
            if role == "assistant":
                return TranscriptEvent(
                    event_id, ActivityKind.ASSISTANT_MESSAGE, ts,
                    summary=clip(text), text=text, role=MessageRole.ASSISTANT,
                )
            if role == "developer":
                # Codex's per-turn instruction preamble; not part of the chat.
                return None
            return TranscriptEvent(
                event_id, ActivityKind.NOTE, ts,
                summary=clip(text), text=text, role=MessageRole.SYSTEM,
                detail={"role": role},
            )

        if ptype == "reasoning":
            text = _text_from_content(payload.get("summary")) or _text_from_content(
                payload.get("content")
            )
            if not text.strip():
                return None
            return TranscriptEvent(
                event_id, ActivityKind.REASONING, ts,
                summary=clip(text), text=text, role=MessageRole.ASSISTANT,
            )

        if ptype in _TOOL_CALL_TYPES:
            name = str(payload.get("name") or ptype)
            args = as_text(
                payload.get("arguments")
                or payload.get("input")
                or payload.get("action")
            )
            call_id = payload.get("call_id")
            plumbing = name in _PLUMBING_TOOLS
            if isinstance(call_id, str):
                calls[call_id] = plumbing
            return TranscriptEvent(
                event_id,
                ActivityKind.TOOL_PLUMBING if plumbing else ActivityKind.TOOL_CALL,
                ts, summary=name, text=args, role=MessageRole.ASSISTANT,
                detail={"tool_name": name, "call_id": call_id},
            )

        if ptype in _TOOL_RESULT_TYPES:
            output = as_text(payload.get("output"))
            call_id = payload.get("call_id")
            plumbing = calls.get(call_id, False) if isinstance(call_id, str) else False
            return TranscriptEvent(
                event_id,
                ActivityKind.TOOL_PLUMBING if plumbing else ActivityKind.TOOL_RESULT,
                ts, summary=clip(output), text=output, role=MessageRole.TOOL,
                detail={"call_id": call_id},
            )

        return None
