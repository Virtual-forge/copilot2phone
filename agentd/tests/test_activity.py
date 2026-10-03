"""Tests for the session activity stream."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agentd.activity import IncomingEvent
from agentd.protocol import (
    ActivityKind,
    AgentType,
    ApprovalState,
    Decision,
    MessageRole,
    ToolKind,
    iso,
    utcnow,
)

WORKSPACE = "C:/work/project"


def ev(kind: ActivityKind, **kwargs) -> IncomingEvent:
    return IncomingEvent(kind=kind, **kwargs)


async def ingest(ctx, events, *, session_id="s1", agent=AgentType.CODEX, **kwargs):
    return await ctx.activity.ingest(
        agent_type=agent,
        session_id=session_id,
        workspace_path=WORKSPACE,
        events=events,
        **kwargs,
    )


# --- ingest ---------------------------------------------------------------


async def test_ingest_assigns_monotonic_seq(ctx):
    await ingest(
        ctx,
        [
            ev(ActivityKind.USER_MESSAGE, summary="hi", role=MessageRole.USER),
            ev(
                ActivityKind.ASSISTANT_MESSAGE,
                summary="hello",
                role=MessageRole.ASSISTANT,
            ),
        ],
    )
    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert [event.seq for event in events] == [1, 2]
    assert [event.kind for event in events] == [
        ActivityKind.USER_MESSAGE,
        ActivityKind.ASSISTANT_MESSAGE,
    ]


async def test_ingest_is_idempotent_by_event_id(ctx):
    batch = [
        ev(
            ActivityKind.USER_MESSAGE,
            summary="hi",
            role=MessageRole.USER,
            event_id="codex:s1:0",
        )
    ]
    assert await ingest(ctx, batch) == 1
    assert await ingest(ctx, batch) == 0
    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert len(events) == 1


async def test_reingest_refreshes_a_stored_event(ctx):
    """A re-read reconciles the derived fields, so a reader change takes effect."""
    assert await ingest(
        ctx,
        [ev(ActivityKind.NOTE, summary="task complete", event_id="codex:s1:7")],
    ) == 1
    # The same transcript line, now classified as a lifecycle marker.
    assert await ingest(
        ctx,
        [
            ev(
                ActivityKind.TASK_FINISHED,
                summary="task complete",
                event_id="codex:s1:7",
            )
        ],
    ) == 0

    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert len(events) == 1
    assert events[0].kind is ActivityKind.TASK_FINISHED
    assert events[0].seq == 1  # ordering is fixed at first sight


async def test_seq_is_partitioned_per_session(ctx):
    await ingest(ctx, [ev(ActivityKind.USER_MESSAGE, summary="a")], session_id="s1")
    await ingest(ctx, [ev(ActivityKind.USER_MESSAGE, summary="b")], session_id="s2")
    first = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    second = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s2")
    assert [event.seq for event in first] == [1]
    assert [event.seq for event in second] == [1]


async def test_events_after_seq(ctx):
    await ingest(
        ctx,
        [
            ev(ActivityKind.USER_MESSAGE, summary="one"),
            ev(ActivityKind.ASSISTANT_MESSAGE, summary="two"),
            ev(ActivityKind.ASSISTANT_MESSAGE, summary="three"),
        ],
    )
    events = await ctx.activity.events(
        agent_type=AgentType.CODEX, session_id="s1", after_seq=1
    )
    assert [event.summary for event in events] == ["two", "three"]


# --- chat view ------------------------------------------------------------


async def test_messages_view_filters_to_chat_kinds(ctx):
    await ingest(
        ctx,
        [
            ev(ActivityKind.SESSION_STARTED, summary="session started"),
            ev(ActivityKind.USER_MESSAGE, summary="hi", role=MessageRole.USER),
            ev(
                ActivityKind.TOOL_CALL,
                summary="shell",
                text="ls",
                role=MessageRole.ASSISTANT,
                detail={"tool_name": "shell", "tool_kind": "command"},
            ),
            ev(ActivityKind.APPROVAL_REQUESTED, summary="approval requested: shell"),
        ],
    )
    messages = await ctx.activity.messages(agent_type=AgentType.CODEX, session_id="s1")
    assert [message.kind for message in messages] == [
        ActivityKind.USER_MESSAGE,
        ActivityKind.TOOL_CALL,
    ]
    assert messages[1].tool_name == "shell"
    assert messages[1].tool_kind is ToolKind.COMMAND


# --- session enrichment ---------------------------------------------------


async def test_session_summary_tracks_activity(ctx):
    await ingest(
        ctx,
        [ev(ActivityKind.USER_MESSAGE, summary="hi", role=MessageRole.USER)],
        title="Fix the parser",
        source="transcript",
    )
    summary = await ctx.sessions.summary("s1")
    assert summary is not None
    assert summary.title == "Fix the parser"
    assert summary.source == "transcript"
    assert summary.message_count == 1
    assert summary.last_activity_at is not None


async def test_summaries_are_newest_first(ctx):
    await ingest(ctx, [ev(ActivityKind.USER_MESSAGE, summary="old")], session_id="old")
    await ingest(ctx, [ev(ActivityKind.USER_MESSAGE, summary="new")], session_id="new")
    summaries = await ctx.sessions.summaries()
    assert [summary.session_id for summary in summaries] == ["new", "old"]


# --- approval lifecycle ---------------------------------------------------


def make_action(kind: ToolKind, name: str, **detail):
    from agentd.protocol import Action, ActionDetail, Tool

    return Action(
        agent_type=AgentType.CODEX,
        session_id="s1",
        workspace_path=WORKSPACE,
        tool=Tool(name=name, kind=kind),
        action=ActionDetail(**detail),
    )


async def test_auto_allowed_action_records_activity(ctx):
    await ctx.approvals.request(
        make_action(ToolKind.FILE_READ, "read_file", summary="read", paths=[f"{WORKSPACE}/a.py"])
    )
    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert [event.kind for event in events] == [ActivityKind.APPROVAL_DECIDED]
    assert events[0].detail["decision"] == "allow"


async def test_blocking_approval_records_request_and_decision(ctx):
    task = asyncio.create_task(
        ctx.approvals.request(
            make_action(ToolKind.COMMAND, "execute_command", summary="ls", command="ls")
        )
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    pending = []
    while loop.time() < deadline:
        pending = await ctx.approvals.list(state=ApprovalState.PENDING)
        if pending:
            break
        await asyncio.sleep(0.01)
    assert pending

    await ctx.approvals.decide(
        pending[0].approval_id, decision=Decision.DENY, reason="nope", decided_by="test"
    )
    await asyncio.wait_for(task, timeout=5)

    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert [event.kind for event in events] == [
        ActivityKind.APPROVAL_REQUESTED,
        ActivityKind.APPROVAL_DECIDED,
    ]
    assert events[1].detail["decision"] == "deny"
    assert events[1].detail["reason"] == "nope"


# --- batch write path ------------------------------------------------------


async def _last_seq(ctx, session_id: str = "s1") -> int:
    cursor = await ctx.db.conn.execute(
        "SELECT last_seq FROM sessions WHERE session_id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return int(row["last_seq"]) if row else 0


async def test_reingest_burns_no_seq(ctx):
    """A re-read of stored events is free: same ids, no new seq numbers."""
    batch = [
        ev(ActivityKind.USER_MESSAGE, summary="hi", event_id=f"codex:s1:{i}")
        for i in range(5)
    ]
    assert await ingest(ctx, batch) == 5
    seq_after_first = await _last_seq(ctx)

    assert await ingest(ctx, batch) == 0
    assert await _last_seq(ctx) == seq_after_first


async def test_concurrent_ingests_never_share_a_seq(ctx):
    """Two interleaved batches must draw disjoint sequence numbers: a
    duplicate seq would make ``after_seq`` paging silently skip an event."""
    batch_one = [
        ev(ActivityKind.USER_MESSAGE, summary="a", event_id=f"codex:s1:{i}")
        for i in range(20)
    ]
    batch_two = [
        ev(ActivityKind.USER_MESSAGE, summary="b", event_id=f"codex:s1:{20 + i}")
        for i in range(20)
    ]
    await asyncio.gather(ingest(ctx, batch_one), ingest(ctx, batch_two))

    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    seqs = [event.seq for event in events]
    assert len(seqs) == len(set(seqs)) == 40
    assert seqs == sorted(seqs)


# --- housekeeping -----------------------------------------------------------


async def test_prune_applies_retention(ctx):
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await ingest(
        ctx,
        [ev(ActivityKind.USER_MESSAGE, summary="old", ts=old, event_id="codex:s1:0")],
    )
    await ingest(
        ctx,
        [ev(ActivityKind.USER_MESSAGE, summary="new", event_id="codex:s1:1")],
    )

    stats = await ctx.db.prune_before(iso(datetime(2021, 1, 1, tzinfo=timezone.utc)))
    assert stats["events"] == 1

    events = await ctx.activity.events(agent_type=AgentType.CODEX, session_id="s1")
    assert [event.summary for event in events] == ["new"]
    summary = await ctx.sessions.summary("s1")
    assert summary.message_count == 1  # recomputed, not left stale


async def test_stale_sessions_go_idle(ctx):
    await ingest(
        ctx,
        [ev(ActivityKind.USER_MESSAGE, summary="hi", ts=utcnow() - timedelta(days=1))],
    )
    assert (await ctx.sessions.summary("s1")).state == "running"

    changed = await ctx.db.mark_stale_sessions_idle(iso(utcnow() - timedelta(minutes=15)))
    assert changed == 1
    assert (await ctx.sessions.summary("s1")).state == "idle"


# --- live change notifications (D-026) --------------------------------------


async def test_ingest_wakes_stream_subscribers(ctx):
    """Every ingest nudges the live streams: tiny notices, no data."""
    queue = ctx.activity.subscribe()
    try:
        await ingest(
            ctx,
            [ev(ActivityKind.USER_MESSAGE, summary="hi", event_id="wake:1")],
        )
        notice = queue.get_nowait()
        assert notice == {"agent_type": "codex", "session_id": "s1"}

        # a quiet second ingest wakes again; unsubscribed queues hear nothing
        quiet = ctx.activity.subscribe()
        ctx.activity.unsubscribe(queue)
        await ingest(
            ctx,
            [ev(ActivityKind.USER_MESSAGE, summary="again", event_id="wake:2")],
        )
        assert queue.empty()
        assert quiet.get_nowait() == {"agent_type": "codex", "session_id": "s1"}
        ctx.activity.unsubscribe(quiet)
    finally:
        ctx.activity.unsubscribe(queue)
