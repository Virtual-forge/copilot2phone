"""Tests for the transcript readers and the watcher."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from agentd.protocol import ActivityKind, AgentType
from agentd.transcripts import (
    READER_VERSION,
    CodexTranscriptReader,
    OpencodeTranscriptReader,
)
from agentd.watcher import TranscriptWatcher

CODEX_LINES = [
    {
        "timestamp": "2026-10-01T10:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "session_id": "abc",
            "cwd": "C:/work/project",
            "originator": "codex_cli",
        },
    },
    {
        "timestamp": "2026-10-01T10:00:01.000Z",
        "type": "event_msg",
        "payload": {"type": "task_started", "turn_id": "t1"},
    },
    {
        "timestamp": "2026-10-01T10:00:02.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "list the files"}],
        },
    },
    {
        "timestamp": "2026-10-01T10:00:03.000Z",
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "I should run ls"}],
        },
    },
    {
        "timestamp": "2026-10-01T10:00:04.000Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "shell",
            "arguments": '{"command":"ls"}',
            "call_id": "c1",
        },
    },
    {
        "timestamp": "2026-10-01T10:00:05.000Z",
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "c1",
            "output": "a.py\nb.py",
        },
    },
    {
        "timestamp": "2026-10-01T10:00:06.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Two files."}],
        },
    },
    {
        "timestamp": "2026-10-01T10:00:07.000Z",
        "type": "event_msg",
        "payload": {"type": "task_complete"},
    },
]


def write_codex(home: Path) -> Path:
    day = home / "sessions" / "2026" / "10" / "01"
    day.mkdir(parents=True, exist_ok=True)
    rollout = day / "rollout-2026-10-01T10-00-00-abc.jsonl"
    rollout.write_text(
        "\n".join(json.dumps(line) for line in CODEX_LINES) + "\n", encoding="utf-8"
    )
    (home / "session_index.jsonl").write_text(
        json.dumps({"id": "abc", "thread_name": "List files"}) + "\n", encoding="utf-8"
    )
    return rollout


def write_resumed_codex(home: Path) -> None:
    """Two rollout files that share one session id (a Codex resume).

    The newer file is written first, and it does not replay the older turn, so
    the reader has to order them by the session's own timestamps and merge them.
    """
    day = home / "sessions" / "2026" / "10" / "01"
    day.mkdir(parents=True, exist_ok=True)

    def rollout(text: str, hour: int) -> list[dict]:
        return [
            {
                "timestamp": f"2026-10-01T{hour:02d}:00:00.000Z",
                "type": "session_meta",
                "payload": {"session_id": "abc", "cwd": "C:/work/project"},
            },
            {
                "timestamp": f"2026-10-01T{hour:02d}:00:01.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
            {
                "timestamp": f"2026-10-01T{hour:02d}:00:02.000Z",
                "type": "event_msg",
                "payload": {"type": "task_complete"},
            },
        ]

    (day / "rollout-2026-10-01T11-00-00-abc.jsonl").write_text(
        "\n".join(json.dumps(line) for line in rollout("second", 11)) + "\n",
        encoding="utf-8",
    )
    (day / "rollout-2026-10-01T10-00-00-abc.jsonl").write_text(
        "\n".join(json.dumps(line) for line in rollout("first", 10)) + "\n",
        encoding="utf-8",
    )


# --- codex ----------------------------------------------------------------


def test_codex_discover(tmp_path):
    home = tmp_path / "codex"
    write_codex(home)
    sessions = CodexTranscriptReader(home).discover()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.agent_type is AgentType.CODEX
    assert session.session_id == "abc"
    assert session.workspace_path == "C:/work/project"
    assert session.title == "List files"
    assert session.started_at is not None


def test_codex_read_maps_every_kind(tmp_path):
    home = tmp_path / "codex"
    write_codex(home)
    reader = CodexTranscriptReader(home)
    session = reader.discover()[0]
    events = reader.read(session)
    kinds = [event.kind for event in events]
    assert kinds == [
        ActivityKind.SESSION_STARTED,
        ActivityKind.TASK_STARTED,
        ActivityKind.USER_MESSAGE,
        ActivityKind.REASONING,
        ActivityKind.TOOL_CALL,
        ActivityKind.TOOL_RESULT,
        ActivityKind.ASSISTANT_MESSAGE,
        ActivityKind.TASK_FINISHED,
    ]
    user = next(e for e in events if e.kind is ActivityKind.USER_MESSAGE)
    assert user.text == "list the files"
    call = next(e for e in events if e.kind is ActivityKind.TOOL_CALL)
    assert call.detail["tool_name"] == "shell"
    assert call.text == '{"command":"ls"}'
    result = next(e for e in events if e.kind is ActivityKind.TOOL_RESULT)
    assert result.text == "a.py\nb.py"


def test_codex_event_ids_are_stable(tmp_path):
    home = tmp_path / "codex"
    write_codex(home)
    reader = CodexTranscriptReader(home)
    session = reader.discover()[0]
    first = [event.event_id for event in reader.read(session)]
    second = [event.event_id for event in reader.read(session)]
    assert first == second
    assert len(set(first)) == len(first)


def test_codex_resumed_rollouts_are_one_session(tmp_path):
    """A resumed thread's rollout files merge instead of colliding on event_id.

    Codex keeps the session id when it resumes a thread and writes the new
    turns to a fresh rollout file that does not replay the earlier ones.
    Reading the files separately gives both the same ``codex:<session>:<index>``
    ids, so the shorter resume file overwrote the original's rows.
    """
    home = tmp_path / "codex"
    write_resumed_codex(home)

    reader = CodexTranscriptReader(home)
    sessions = reader.discover()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "abc"
    assert [path.name for path in session.paths] == [
        "rollout-2026-10-01T10-00-00-abc.jsonl",
        "rollout-2026-10-01T11-00-00-abc.jsonl",
    ]

    events = reader.read(session)
    ids = [event.event_id for event in events]
    assert len(set(ids)) == len(ids)
    assert ids[0] == "codex:abc:0"
    # the resume file's first record continues the session's index space
    assert ids[3] == "codex:abc:3"
    texts = [event.text for event in events if event.kind is ActivityKind.USER_MESSAGE]
    assert texts == ["first", "second"]


def test_codex_missing_home_is_empty(tmp_path):
    reader = CodexTranscriptReader(tmp_path / "nope")
    assert reader.discover() == []


def test_codex_unified_exec_is_plumbing(tmp_path):
    """Codex's exec/wait polling loop is harness mechanics, not conversation."""
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "10" / "01"
    day.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "timestamp": "2026-10-01T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"session_id": "abc", "cwd": "C:/work/project"},
        },
        {
            "timestamp": "2026-10-01T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "input": 'const r = await tools.exec_command({cmd:"ls"})',
                "call_id": "c1",
            },
        },
        {
            "timestamp": "2026-10-01T10:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "c1",
                "output": "Script running with cell ID 1",
            },
        },
        {
            "timestamp": "2026-10-01T10:00:03.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "wait",
                "arguments": '{"cell_id":"1"}',
                "call_id": "c2",
            },
        },
        {
            "timestamp": "2026-10-01T10:00:04.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "c2",
                "output": "Script completed",
            },
        },
    ]
    (day / "rollout-2026-10-01T10-00-00-abc.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )

    reader = CodexTranscriptReader(home)
    events = reader.read(reader.discover()[0])
    assert [event.kind for event in events] == [
        ActivityKind.SESSION_STARTED,
        ActivityKind.TOOL_PLUMBING,
        ActivityKind.TOOL_PLUMBING,
        ActivityKind.TOOL_PLUMBING,
        ActivityKind.TOOL_PLUMBING,
    ]
    # The call keeps its name; the output is matched back to it by call id.
    assert events[1].detail["tool_name"] == "exec"
    assert events[3].detail["tool_name"] == "wait"
    assert events[2].detail["call_id"] == "c1"
    assert events[4].detail["call_id"] == "c2"


# --- opencode ---------------------------------------------------------------


def write_opencode(home: Path) -> Path:
    """A synthetic OpenCode database: only the columns the reader queries.

    The real v2 schema is a superset (``account``/``credential`` tables hold
    secrets and are deliberately never touched by the reader).
    """
    home.mkdir(parents=True, exist_ok=True)
    db = home / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE project (
          id TEXT PRIMARY KEY,
          worktree TEXT NOT NULL
        );
        CREATE TABLE session_v2 (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          directory TEXT NOT NULL,
          title TEXT,
          time_created INTEGER NOT NULL,
          time_updated INTEGER NOT NULL,
          time_archived INTEGER
        );
        CREATE TABLE session_message (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          type TEXT NOT NULL,
          seq INTEGER NOT NULL,
          time_created INTEGER NOT NULL,
          data TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO project VALUES ('prj_1', 'C:/work/project')")
    conn.execute(
        "INSERT INTO session_v2 VALUES"
        " ('ses_1', 'prj_1', 'C:/work/project', 'List the files',"
        " 1790980000000, 1790980009000, NULL)"
    )
    messages = [
        ("msg_1", "user", 4, {"text": "list the files"}),
        (
            "msg_2",
            "assistant",
            5,
            {
                "content": [
                    {
                        "type": "reasoning",
                        "text": "I should run ls",
                        "time": {"created": 1790980002000},
                    },
                    {"type": "text", "text": "Running it."},
                    {
                        "type": "tool",
                        "id": "t1",
                        "name": "glob",
                        "state": {
                            "status": "completed",
                            "input": {"pattern": "*"},
                            "content": [{"type": "text", "text": "a.py\nb.py"}],
                        },
                    },
                ]
            },
        ),
        ("msg_3", "idle", 6, {"outcome": "success"}),
        (
            "msg_4",
            "synthetic",
            7,
            {"text": "<shell>ok</shell>", "description": "pytest"},
        ),
        ("msg_5", "model-switched", 8, {"to": "x"}),
        (
            "msg_6",
            "assistant",
            9,
            {
                "content": [
                    {
                        "type": "tool",
                        "id": "t2",
                        "name": "bash",
                        "state": {"status": "running", "input": {"command": "ls"}},
                    },
                ]
            },
        ),
    ]
    for message_id, mtype, seq, data in messages:
        conn.execute(
            "INSERT INTO session_message VALUES (?, 'ses_1', ?, ?, ?, ?)",
            (message_id, mtype, seq, 1790980000000 + seq * 1000, json.dumps(data)),
        )
    conn.commit()
    conn.close()
    return db


def test_opencode_discover(tmp_path):
    db = write_opencode(tmp_path)
    sessions = OpencodeTranscriptReader(db).discover()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.agent_type is AgentType.OPENCODE
    assert session.session_id == "ses_1"
    assert session.workspace_path == "C:/work/project"
    assert session.title == "List the files"
    assert session.started_at is not None
    assert session.started_at.year == 2026


def test_opencode_read_maps_every_message(tmp_path):
    db = write_opencode(tmp_path)
    reader = OpencodeTranscriptReader(db)
    events = reader.read(reader.discover()[0])
    kinds = [event.kind for event in events]
    assert kinds == [
        ActivityKind.USER_MESSAGE,
        ActivityKind.REASONING,
        ActivityKind.ASSISTANT_MESSAGE,
        ActivityKind.TOOL_CALL,
        ActivityKind.TOOL_RESULT,
        ActivityKind.TASK_FINISHED,  # idle: feed-only turn boundary
        ActivityKind.NOTE,           # synthetic: injected shell output
        ActivityKind.TOOL_CALL,      # in-flight tool: call, no result yet
    ]
    user = events[0]
    assert user.text == "list the files"
    call = events[3]
    assert call.detail["tool_name"] == "glob"
    assert json.loads(call.text) == {"pattern": "*"}
    result = events[4]
    assert result.text == "a.py\nb.py"
    running = events[7]
    assert running.detail["status"] == "running"


def test_opencode_event_ids_are_stable(tmp_path):
    db = write_opencode(tmp_path)
    reader = OpencodeTranscriptReader(db)
    session = reader.discover()[0]
    first = [event.event_id for event in reader.read(session)]
    second = [event.event_id for event in reader.read(session)]
    assert first == second
    assert len(set(first)) == len(first)
    # OpenCode message ids are globally unique, so they are the index
    assert first[0] == "opencode:msg_1"
    assert first[3] == "opencode:msg_2:2:call"


def test_opencode_missing_db_is_empty(tmp_path):
    assert OpencodeTranscriptReader(tmp_path / "nope.db").discover() == []


def test_opencode_archived_sessions_are_hidden(tmp_path):
    db = write_opencode(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE session_v2 SET time_archived = 1")
    conn.commit()
    conn.close()
    assert OpencodeTranscriptReader(db).discover() == []


def test_opencode_watermark_rereads_the_boundary_message(tmp_path):
    """A tool that was still running when first seen must get its result on
    the next scan: the watermark re-reads the boundary message, and the
    stored events are refreshed in place by id (D-018)."""
    db = write_opencode(tmp_path)
    reader = OpencodeTranscriptReader(db)
    session = reader.discover()[0]
    first = reader.read(session)
    assert first.marks[str(db)] == (0, 9)

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE session_message SET data = ? WHERE id = 'msg_6'",
        (
            json.dumps(
                {
                    "content": [
                        {
                            "type": "tool",
                            "id": "t2",
                            "name": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "ls"},
                                "content": [{"type": "text", "text": "a.py"}],
                            },
                        }
                    ]
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    tail = reader.read(session, starts={str(db): first.marks[str(db)]})
    assert [event.event_id for event in tail] == [
        "opencode:msg_6:0:call",
        "opencode:msg_6:0:out",
    ]
    assert tail.marks[str(db)] == (0, 9)
    assert tail[1].text == "a.py"


async def test_watcher_ingests_opencode_sessions(ctx, tmp_path):
    db = write_opencode(tmp_path / "oc")
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[OpencodeTranscriptReader(db)],
    )

    assert await watcher.scan_once() > 0
    assert await watcher.scan_once() == 0  # unchanged database

    summaries = await ctx.sessions.summaries()
    assert [summary.session_id for summary in summaries] == ["ses_1"]
    assert summaries[0].title == "List the files"
    # the idle marker is feed-only, so the chat holds seven of the eight events
    assert summaries[0].message_count == 7

    # a new message lands: only it is inserted
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO session_message VALUES ('msg_7', 'ses_1', 'user', 10,"
        " 1790980100000, ?)",
        (json.dumps({"text": "thanks"}),),
    )
    conn.commit()
    conn.close()
    assert await watcher.scan_once() == 1
    messages = await ctx.activity.messages(
        agent_type=AgentType.OPENCODE, session_id="ses_1"
    )
    assert messages[-1].text == "thanks"


# --- watcher --------------------------------------------------------------


async def test_watcher_ingests_and_skips_unchanged(ctx, tmp_path):
    home = tmp_path / "codex"
    write_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )

    first = await watcher.scan_once()
    assert first > 0

    # Nothing changed on disk, so the second pass is a no-op.
    assert await watcher.scan_once() == 0

    summaries = await ctx.sessions.summaries()
    assert [summary.session_id for summary in summaries] == ["abc"]
    assert summaries[0].source == "transcript"
    assert summaries[0].title == "List files"
    # five of the eight lines are chat kinds: the session and turn markers are
    # lifecycle, so they stay in the feed and out of the chat.
    assert summaries[0].message_count == 5

    messages = await ctx.activity.messages(agent_type=AgentType.CODEX, session_id="abc")
    assert [message.kind for message in messages] == [
        ActivityKind.USER_MESSAGE,
        ActivityKind.REASONING,
        ActivityKind.TOOL_CALL,
        ActivityKind.TOOL_RESULT,
        ActivityKind.ASSISTANT_MESSAGE,
    ]


async def test_watcher_picks_up_appended_lines(ctx, tmp_path):
    home = tmp_path / "codex"
    rollout = write_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )
    await watcher.scan_once()

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-10-01T10:00:08.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "thanks"}],
                    },
                }
            )
            + "\n"
        )

    assert await watcher.scan_once() == 1
    messages = await ctx.activity.messages(agent_type=AgentType.CODEX, session_id="abc")
    assert messages[-1].text == "thanks"


async def test_watcher_merges_resumed_rollouts(ctx, tmp_path):
    """Both halves of a resumed Codex session survive ingestion.

    Each rollout file alone maps its records to ``codex:abc:0``, ``:1``, ...;
    if they were ingested as separate sessions the second file would refresh
    (and mislabel) the first file's rows.
    """
    home = tmp_path / "codex"
    write_resumed_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )

    await watcher.scan_once()
    # Nothing changed on disk; a resumed session is still one fingerprint.
    assert await watcher.scan_once() == 0

    summaries = await ctx.sessions.summaries()
    assert [summary.session_id for summary in summaries] == ["abc"]
    assert summaries[0].message_count == 2

    messages = await ctx.activity.messages(agent_type=AgentType.CODEX, session_id="abc")
    assert [message.text for message in messages] == ["first", "second"]


# --- incremental scans (high-water marks) ----------------------------------


async def _last_seq(ctx, session_id: str) -> int:
    cursor = await ctx.db.conn.execute(
        "SELECT last_seq FROM sessions WHERE session_id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return int(row["last_seq"]) if row else 0


async def test_watcher_resumes_from_its_marks(ctx, tmp_path):
    """A changed file is read from its high-water mark, not from the top."""
    home = tmp_path / "codex"
    rollout = write_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )
    assert await watcher.scan_once() > 0

    marks = await ctx.db.get_transcript_marks("codex", "abc")
    mark = marks[str(rollout)]
    assert mark.records == len(CODEX_LINES)
    assert mark.pos == rollout.stat().st_size
    seq_after_first = await _last_seq(ctx, "abc")

    # The mtime moved (an agent touched the file) but the tail is empty:
    # nothing new, nothing burned.
    os.utime(rollout, None)
    assert await watcher.scan_once() == 0
    assert await _last_seq(ctx, "abc") == seq_after_first

    # One appended line: exactly one event, exactly one new seq.
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-10-01T10:00:08.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "third"}],
                    },
                }
            )
            + "\n"
        )
    assert await watcher.scan_once() == 1
    assert await _last_seq(ctx, "abc") == seq_after_first + 1
    messages = await ctx.activity.messages(agent_type=AgentType.CODEX, session_id="abc")
    assert messages[-1].text == "third"


async def test_reader_version_bump_forces_a_full_refresh(ctx, tmp_path):
    """Marks made by an older reader mapping are ignored: the next scan
    re-reads and refreshes the stored rows instead of tailing past them."""
    home = tmp_path / "codex"
    rollout = write_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )
    await watcher.scan_once()

    await ctx.db.conn.execute("UPDATE transcript_state SET version = version - 1")
    await ctx.db.conn.commit()
    os.utime(rollout, None)

    # Every row is refreshed, none is new.
    assert await watcher.scan_once() == 0
    marks = await ctx.db.get_transcript_marks("codex", "abc")
    assert marks[str(rollout)].version == READER_VERSION
    assert marks[str(rollout)].records == len(CODEX_LINES)


async def test_shrunk_rollout_forces_a_full_refresh(ctx, tmp_path):
    """A file that got smaller invalidates its marks: renumbering from a
    stale offset would store every later line twice."""
    home = tmp_path / "codex"
    rollout = write_codex(home)
    watcher = TranscriptWatcher(
        activity=ctx.activity,
        sessions=ctx.sessions,
        db=ctx.db,
        readers=[CodexTranscriptReader(home)],
    )
    await watcher.scan_once()

    # Halve the file behind the watcher's back, then change it again.
    lines = rollout.read_text(encoding="utf-8").splitlines(keepends=True)
    rollout.write_text("".join(lines[: len(lines) // 2]), encoding="utf-8")
    await watcher.scan_once()

    marks = await ctx.db.get_transcript_marks("codex", "abc")
    assert marks[str(rollout)].records == len(lines) // 2
    assert marks[str(rollout)].pos == rollout.stat().st_size
