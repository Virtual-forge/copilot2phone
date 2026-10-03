"""Polls each agent's transcript files and folds them into the activity stream.

The approval hook only sees tool calls, so this is what fills in the chat. It is
read-only and best-effort: a malformed file is skipped, never fatal.

Two shortcuts keep a scan cheap while an agent is actively writing:

* an in-memory fingerprint (mtime + size per source file) skips files that have
  not changed since the last pass;
* a per-file high-water mark persisted in the DB says where the previous scan
  stopped consuming, so a changed file is read from that byte offset instead
  of being re-parsed from the top (D-018). Restarts inherit the marks too, so
  they do not re-read history either.

A mark is only trusted when the reader that made it has the same mapping
version; a bump (``READER_VERSION``) forces one full re-read so the stored
rows are refreshed, and a file that shrank does the same because renumbering
the records would otherwise store each line twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from .activity import ActivityManager, IncomingEvent
from .db import Database, TranscriptMark
from .sessions import SessionManager
from .transcripts import (
    READER_VERSION,
    CodexTranscriptReader,
    OpencodeTranscriptReader,
    TranscriptReader,
    TranscriptSession,
)

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 2.0


class TranscriptWatcher:
    """Background scanner that keeps the activity stream in sync with disk."""

    def __init__(
        self,
        *,
        activity: ActivityManager,
        sessions: SessionManager,
        db: Database,
        readers: list[TranscriptReader] | None = None,
        interval: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._activity = activity
        self._sessions = sessions
        self._db = db
        self._readers: list[TranscriptReader] = (
            readers
            if readers is not None
            else [CodexTranscriptReader(), OpencodeTranscriptReader()]
        )
        self._interval = interval
        self._fingerprints: dict[str, tuple[tuple[float, int], ...]] = {}

    async def scan_once(self) -> int:
        """Read every changed transcript and ingest new events. Returns count."""
        total = 0
        for reader in self._readers:
            try:
                sessions = await asyncio.to_thread(reader.discover)
            except Exception:  # pragma: no cover - defensive
                logger.exception("transcript discovery failed for %s", reader.agent_type)
                continue
            for session in sessions:
                try:
                    total += await self._ingest_session(reader, session)
                except Exception:  # pragma: no cover - defensive
                    logger.exception(
                        "transcript ingest failed for %s", session.session_id
                    )
        return total

    async def _ingest_session(
        self, reader: TranscriptReader, session: TranscriptSession
    ) -> int:
        paths = session.paths
        if not paths:
            return 0

        key = f"{reader.agent_type.value}:{session.session_id}"
        stamps = self._fingerprint(paths)
        if stamps is not None and self._fingerprints.get(key) == stamps:
            return 0

        marks = await self._db.get_transcript_marks(
            reader.agent_type.value, session.session_id
        )
        starts = self._starts(marks, paths, stamps)
        result = await asyncio.to_thread(reader.read, session, starts)

        inserted = 0
        if result:
            incoming = [
                IncomingEvent(
                    kind=event.kind,
                    summary=event.summary,
                    text=event.text,
                    role=event.role,
                    ts=event.ts,
                    detail=event.detail,
                    event_id=event.event_id,
                )
                for event in result
            ]
            inserted = await self._activity.ingest(
                agent_type=session.agent_type,
                session_id=session.session_id,
                workspace_path=session.workspace_path,
                events=incoming,
                title=session.title or None,
                source="transcript",
                started_at=session.started_at,
            )

        # Marks advance even when the tail produced no event: harness records
        # that map to nothing were still consumed, and skipping past them next
        # time is the whole point.
        if result.marks:
            await self._db.set_transcript_marks(
                agent_type=reader.agent_type.value,
                session_id=session.session_id,
                marks={
                    str(path): TranscriptMark(
                        pos=result.marks[str(path)][0],
                        records=result.marks[str(path)][1],
                        size=int(stamps[index][1]) if stamps is not None else 0,
                        version=READER_VERSION,
                    )
                    for index, path in enumerate(paths)
                    if str(path) in result.marks
                },
            )

        # Only now is the file "caught up": if ingest failed above, the next
        # scan retries it instead of silently skipping the changed file.
        if stamps is not None:
            self._fingerprints[key] = stamps
        return inserted

    def _starts(
        self,
        marks: dict[str, TranscriptMark],
        paths: list[Path],
        stamps: tuple[tuple[float, int], ...] | None,
    ) -> dict[str, tuple[int, int]]:
        """Where the previous scan stopped each file.

        A missing mark simply reads that file in full (a new resume file,
        say). Marks from an older reader mapping, or a file that shrank,
        invalidate *all* of the session's marks: the whole session is
        re-read so every stored row is refreshed in place.
        """
        starts: dict[str, tuple[int, int]] = {}
        for index, path in enumerate(paths):
            mark = marks.get(str(path))
            if mark is None:
                starts[str(path)] = (0, 0)
                continue
            if mark.version != READER_VERSION:
                return {}
            if (
                stamps is not None
                and mark.size
                and index < len(stamps)
                and mark.size > int(stamps[index][1])
            ):
                return {}
            starts[str(path)] = (mark.pos, mark.records)
        return starts

    def _fingerprint(
        self, paths: list[Path]
    ) -> tuple[tuple[float, int], ...] | None:
        """A cheap change detector: newest mtime plus total size per source."""
        stamps: list[tuple[float, int]] = []
        for path in paths:
            stamp = self._fingerprint_one(path)
            if stamp is None:
                return None
            stamps.append(stamp)
        return tuple(stamps)

    def _fingerprint_one(self, path: Path) -> tuple[float, int] | None:
        try:
            if path.is_dir():
                newest = 0.0
                size = 0
                for child in path.iterdir():
                    if child.is_file():
                        stat = child.stat()
                        newest = max(newest, stat.st_mtime)
                        size += stat.st_size
                return (newest, size)
            stat = path.stat()
            return (stat.st_mtime, stat.st_size)
        except OSError:
            return None

    async def run(self) -> None:
        """Scan forever, sleeping between passes."""
        while True:
            with contextlib.suppress(Exception):
                await self.scan_once()
            await asyncio.sleep(self._interval)
