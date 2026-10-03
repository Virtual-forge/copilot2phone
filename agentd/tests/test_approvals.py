"""Tests for the approval state machine."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentd.approvals import ApprovalConflict, ApprovalManager, ApprovalNotFound
from agentd.config import Config
from agentd.local_api import build_context
from agentd.protocol import (
    Action,
    ActionDetail,
    ActivityKind,
    AgentType,
    ApprovalState,
    Decision,
    Effect,
    Tool,
    ToolKind,
)

WORKSPACE = "C:/work/project"


def make_action(
    kind: ToolKind = ToolKind.COMMAND,
    name: str = "execute_command",
    agent: AgentType = AgentType.CLINE,
    session: str = "s1",
    **detail,
) -> Action:
    return Action(
        agent_type=agent,
        session_id=session,
        workspace_path=WORKSPACE,
        tool=Tool(name=name, kind=kind),
        action=ActionDetail(**detail),
    )


async def wait_for_pending(manager: ApprovalManager, count: int = 1, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        pending = await manager.list(state=ApprovalState.PENDING)
        if len(pending) >= count:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} pending approval(s)")


# --- auto decisions -------------------------------------------------------


async def test_workspace_read_is_auto_allowed(ctx):
    outcome = await ctx.approvals.request(
        make_action(ToolKind.FILE_READ, "read_file", paths=[f"{WORKSPACE}/a.py"])
    )
    assert outcome.allowed
    assert outcome.state is ApprovalState.ALLOWED
    assert outcome.policy_effect is Effect.ALLOW
    assert outcome.waited_seconds < 1.0
    # auto-allowed actions do not create an approval record
    assert await ctx.approvals.list() == []


# --- blocking decisions ---------------------------------------------------


async def test_command_blocks_until_allowed(ctx):
    task = asyncio.create_task(ctx.approvals.request(make_action(command="ls -la")))
    pending = await wait_for_pending(ctx.approvals)
    assert not task.done()

    await ctx.approvals.decide(
        pending[0].approval_id, decision=Decision.ALLOW, decided_by="test"
    )
    outcome = await asyncio.wait_for(task, timeout=5)
    assert outcome.allowed
    assert outcome.state is ApprovalState.ALLOWED
    assert outcome.decision is Decision.ALLOW


async def test_command_blocks_until_denied(ctx):
    task = asyncio.create_task(ctx.approvals.request(make_action(command="ls -la")))
    pending = await wait_for_pending(ctx.approvals)

    await ctx.approvals.decide(
        pending[0].approval_id,
        decision=Decision.DENY,
        reason="not now",
        decided_by="test",
    )
    outcome = await asyncio.wait_for(task, timeout=5)
    assert not outcome.allowed
    assert outcome.state is ApprovalState.DENIED
    assert outcome.reason == "not now"


async def test_timeout_expires_and_denies(home):
    config = Config()
    config.agents.cline.hook_timeout_seconds = 0.2
    context = await build_context(config, db_path=home / "timeout.db", token="t")
    try:
        outcome = await context.approvals.request(make_action(command="ls"))
        assert outcome.state is ApprovalState.EXPIRED
        assert outcome.decision is Decision.DENY
        assert not outcome.allowed
        records = await context.approvals.list()
        assert records[0].state is ApprovalState.EXPIRED
    finally:
        await context.db.close()


# --- decision guards ------------------------------------------------------


async def test_deciding_twice_is_a_conflict(ctx):
    task = asyncio.create_task(ctx.approvals.request(make_action(command="ls")))
    pending = await wait_for_pending(ctx.approvals)
    approval_id = pending[0].approval_id

    await ctx.approvals.decide(approval_id, decision=Decision.ALLOW, decided_by="test")
    await asyncio.wait_for(task, timeout=5)

    with pytest.raises(ApprovalConflict):
        await ctx.approvals.decide(approval_id, decision=Decision.DENY, decided_by="test")


async def test_deciding_unknown_id_raises(ctx):
    with pytest.raises(ApprovalNotFound):
        await ctx.approvals.decide("nope", decision=Decision.ALLOW, decided_by="test")


async def test_deciding_an_expired_approval_is_a_conflict(home):
    config = Config()
    config.agents.cline.hook_timeout_seconds = 0.2
    context = await build_context(config, db_path=home / "expired.db", token="t")
    try:
        outcome = await context.approvals.request(make_action(command="ls"))
        assert outcome.state is ApprovalState.EXPIRED
        with pytest.raises(ApprovalConflict):
            await context.approvals.decide(
                outcome.approval_id, decision=Decision.ALLOW, decided_by="test"
            )
    finally:
        await context.db.close()


# --- shutdown / fail closed ----------------------------------------------


async def test_cancel_all_fails_closed(ctx):
    tasks = [
        asyncio.create_task(ctx.approvals.request(make_action(command=f"cmd{i}")))
        for i in range(3)
    ]
    await wait_for_pending(ctx.approvals, count=3)

    cancelled = await ctx.approvals.cancel_all("test shutdown")
    assert cancelled == 3

    outcomes = await asyncio.gather(*tasks)
    assert all(not outcome.allowed for outcome in outcomes)
    assert all(outcome.state is ApprovalState.CANCELLED for outcome in outcomes)


# --- partitioning ---------------------------------------------------------


async def test_pending_counts_are_partitioned_by_agent(ctx):
    cline = asyncio.create_task(ctx.approvals.request(make_action(command="ls")))
    codex = asyncio.create_task(
        ctx.approvals.request(
            make_action(name="shell", agent=AgentType.CODEX, session="s2", command="ls")
        )
    )
    await wait_for_pending(ctx.approvals, count=2)

    counts = await ctx.approvals.pending_counts()
    assert counts == {"cline": 1, "codex": 1}

    cline_records = await ctx.approvals.list(agent_type=AgentType.CLINE)
    codex_records = await ctx.approvals.list(agent_type=AgentType.CODEX)
    assert len(cline_records) == 1
    assert len(codex_records) == 1
    assert cline_records[0].agent_type is AgentType.CLINE
    assert codex_records[0].agent_type is AgentType.CODEX

    await ctx.approvals.cancel_all()
    await asyncio.gather(cline, codex)


async def test_sessions_are_recorded_per_agent(ctx):
    await ctx.approvals.request(
        make_action(ToolKind.FILE_READ, "read_file", paths=[f"{WORKSPACE}/a.py"])
    )
    await ctx.approvals.request(
        make_action(
            ToolKind.FILE_READ,
            "read_file",
            agent=AgentType.CODEX,
            session="s2",
            paths=[f"{WORKSPACE}/b.py"],
        )
    )
    cline_sessions = await ctx.sessions.summaries(AgentType.CLINE)
    codex_sessions = await ctx.sessions.summaries(AgentType.CODEX)
    assert [s.session_id for s in cline_sessions] == ["s1"]
    assert [s.session_id for s in codex_sessions] == ["s2"]


# --- config contracts -------------------------------------------------------


async def test_disabled_agent_fails_closed_immediately(ctx):
    ctx.config.agents.cline.enabled = False
    outcome = await ctx.approvals.request(make_action(command="ls"))
    assert not outcome.allowed
    assert outcome.state is ApprovalState.DENIED
    assert "disabled" in outcome.reason
    # nothing parked in pending: nobody would ever answer it
    assert await ctx.approvals.list(state=ApprovalState.PENDING) == []


async def test_away_mode_denies_asks_without_blocking(ctx):
    ctx.approvals.away = True
    outcome = await ctx.approvals.request(make_action(command="ls"))
    assert not outcome.allowed
    assert outcome.state is ApprovalState.DENIED
    assert outcome.reason == "away mode is on"
    assert outcome.waited_seconds < 1.0
    assert await ctx.approvals.list(state=ApprovalState.PENDING) == []
    # policy auto-allow still applies while away
    allowed = await ctx.approvals.request(
        make_action(ToolKind.FILE_READ, "read_file", paths=[f"{WORKSPACE}/a.py"])
    )
    assert allowed.allowed
    ctx.approvals.away = False


async def test_default_effect_deny_is_honoured(ctx):
    ctx.config.policy.default_effect = "deny"
    outcome = await ctx.approvals.request(make_action(command="ls"))
    assert not outcome.allowed
    assert outcome.policy_effect is Effect.DENY
    # workspace reads are still allowed outright
    allowed = await ctx.approvals.request(
        make_action(ToolKind.FILE_READ, "read_file", paths=[f"{WORKSPACE}/a.py"])
    )
    assert allowed.allowed


async def test_stored_approval_is_redacted(ctx):
    """The command reaches the phone minus any secret embedded in it (S7)."""
    secret = "sk-abcdefghijklmnopqrst"
    task = asyncio.create_task(
        ctx.approvals.request(make_action(command=f"curl -H 'Authorization: Bearer {secret}' https://x"))
    )
    pending = await wait_for_pending(ctx.approvals)
    assert secret not in pending[0].action.command

    events = await ctx.activity.events(agent_type=AgentType.CLINE, session_id="s1")
    requested = next(
        event for event in events if event.kind is ActivityKind.APPROVAL_REQUESTED
    )
    assert secret not in json.dumps(requested.detail)
    assert secret not in json.dumps(requested.summary)

    await ctx.approvals.decide(pending[0].approval_id, decision=Decision.DENY)
    await asyncio.wait_for(task, timeout=5)


async def test_failed_insert_closes_the_requested_card(ctx, monkeypatch):
    """If the approval row cannot be stored, its card must not hang on
    'pending' forever — nothing else would ever resolve it."""

    async def boom(record):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ctx.db, "insert_approval", boom)
    with pytest.raises(RuntimeError):
        await ctx.approvals.request(make_action(command="ls"))

    events = await ctx.activity.events(agent_type=AgentType.CLINE, session_id="s1")
    kinds = [event.kind for event in events]
    assert ActivityKind.APPROVAL_REQUESTED in kinds
    assert ActivityKind.APPROVAL_DECIDED in kinds


# --- audit ----------------------------------------------------------------


async def test_audit_records_the_lifecycle(ctx):
    task = asyncio.create_task(ctx.approvals.request(make_action(command="ls")))
    pending = await wait_for_pending(ctx.approvals)
    await ctx.approvals.decide(
        pending[0].approval_id, decision=Decision.ALLOW, decided_by="test"
    )
    await asyncio.wait_for(task, timeout=5)

    actions = [entry["action"] for entry in await ctx.audit.recent(limit=50)]
    assert "policy.evaluate" in actions
    assert "approval.created" in actions
    assert "approval.decided" in actions

