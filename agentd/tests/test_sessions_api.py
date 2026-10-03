"""End-to-end tests for the session-centric API."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from agentd.protocol import (
    ActionDetail,
    AgentType,
    ApprovalRecord,
    ApprovalState,
    Risk,
    RiskLevel,
    Tool,
    ToolKind,
    utcnow,
)

WORKSPACE = "C:/work/project"


def event(**overrides) -> dict:
    base = {
        "agent_type": "codex",
        "session_id": "s1",
        "workspace_path": WORKSPACE,
        "kind": "user_message",
        "summary": "hi",
        "text": "hi",
        "role": "user",
    }
    base.update(overrides)
    return base


def command_action(session_id: str = "s1") -> dict:
    return {
        "agent_type": "cline",
        "session_id": session_id,
        "workspace_path": WORKSPACE,
        "tool": {"name": "execute_command", "kind": "command"},
        "action": {"summary": "ls", "command": "ls"},
    }


async def poll_pending(client, count: int = 1, timeout: float = 5.0) -> list[dict]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get("/v1/approvals", params={"state": "pending"})
        response.raise_for_status()
        items = response.json()
        if len(items) >= count:
            return items
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} pending approval(s)")


# --- ingest ---------------------------------------------------------------


async def test_post_events_then_list_sessions(client):
    response = await client.post(
        "/v1/events",
        json=[
            event(event_id="codex:s1:0"),
            event(
                kind="assistant_message",
                role="assistant",
                summary="hello",
                text="hello",
                event_id="codex:s1:1",
            ),
        ],
    )
    assert response.status_code == 200
    assert response.json() == {"received": 2, "inserted": 2}

    sessions = (await client.get("/v1/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert sessions[0]["agent_type"] == "codex"
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["last_activity_at"] is not None


async def test_post_events_is_idempotent(client):
    payload = [event(event_id="codex:s1:0")]
    await client.post("/v1/events", json=payload)
    again = await client.post("/v1/events", json=payload)
    assert again.json() == {"received": 1, "inserted": 0}
    assert len((await client.get("/v1/sessions")).json()) == 1


async def test_events_require_auth(client):
    response = await client.post(
        "/v1/events", json=[event()], headers={"Authorization": ""}
    )
    assert response.status_code == 401


async def test_sessions_filter_by_agent(client):
    await client.post("/v1/events", json=[event(event_id="codex:s1:0")])
    await client.post(
        "/v1/events",
        json=[
            event(
                agent_type="cline",
                session_id="s2",
                event_id="cline:s2:0",
            )
        ],
    )
    codex = (await client.get("/v1/sessions", params={"agent_type": "codex"})).json()
    assert [item["session_id"] for item in codex] == ["s1"]


# --- detail ---------------------------------------------------------------


async def test_session_detail_returns_messages(client):
    await client.post("/v1/events", json=[event(event_id="codex:s1:0")])
    detail = (await client.get("/v1/sessions/s1")).json()
    assert detail["session_id"] == "s1"
    assert [message["kind"] for message in detail["messages"]] == ["user_message"]
    assert detail["messages"][0]["text"] == "hi"
    assert [item["kind"] for item in detail["events"]] == ["user_message"]


async def test_session_messages_after_seq(client):
    await client.post(
        "/v1/events",
        json=[
            event(event_id="codex:s1:0"),
            event(
                kind="assistant_message",
                role="assistant",
                text="hello",
                event_id="codex:s1:1",
            ),
        ],
    )
    messages = (
        await client.get("/v1/sessions/s1/messages", params={"after_seq": 1})
    ).json()
    assert [message["text"] for message in messages] == ["hello"]


async def test_unknown_session_is_404(client):
    assert (await client.get("/v1/sessions/nope")).status_code == 404
    assert (await client.get("/v1/sessions/nope/messages")).status_code == 404
    assert (await client.get("/v1/sessions/nope/events")).status_code == 404


# --- the newest window (long sessions) --------------------------------------


async def test_tail_returns_newest_rows(client):
    """``tail=1`` hands back the newest rows in ascending order, so a session
    past its window keeps showing the live end instead of the oldest rows."""
    await client.post(
        "/v1/events",
        json=[
            event(event_id=f"codex:s1:{i}", summary=str(i), text=str(i))
            for i in range(6)
        ],
    )
    newest = (
        await client.get("/v1/sessions/s1/events", params={"tail": 1, "limit": 3})
    ).json()
    assert [item["summary"] for item in newest] == ["3", "4", "5"]

    messages = (
        await client.get("/v1/sessions/s1/messages", params={"tail": 1, "limit": 2})
    ).json()
    assert [item["text"] for item in messages] == ["4", "5"]

    detail = (
        await client.get("/v1/sessions/s1", params={"tail": 1, "limit": 4})
    ).json()
    assert [item["summary"] for item in detail["events"]] == ["2", "3", "4", "5"]


def test_token_compare_tolerates_non_ascii():
    """Header values are latin-1-decoded, so a non-ASCII token has to be a
    clean rejection, not a ``TypeError`` turned 500."""
    from agentd.local_api import _token_matches

    assert _token_matches("real-token", "real-token")
    assert not _token_matches("tökén", "real-token")
    assert not _token_matches("real-token", "other-token")


# --- approvals feed the stream --------------------------------------------


async def test_pending_approval_records_requested_event(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)

    events = (await client.get("/v1/sessions/s1/events")).json()
    assert any(item["kind"] == "approval_requested" for item in events)

    await client.post(
        f"/v1/approvals/{pending[0]['approval_id']}/decision",
        json={"decision": "deny", "reason": "later"},
    )
    await asyncio.wait_for(task, timeout=5)

    events = (await client.get("/v1/sessions/s1/events")).json()
    kinds = [item["kind"] for item in events]
    assert kinds == ["approval_requested", "approval_decided"]


async def test_pending_count_is_reported_per_session(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)

    sessions = (await client.get("/v1/sessions")).json()
    assert sessions[0]["pending_approvals"] == 1

    await client.post(
        f"/v1/approvals/{pending[0]['approval_id']}/decision", json={"decision": "deny"}
    )
    await asyncio.wait_for(task, timeout=5)

    sessions = (await client.get("/v1/sessions")).json()
    assert sessions[0]["pending_approvals"] == 0


# --- what the inline chat card needs --------------------------------------


async def test_approval_events_carry_what_the_card_needs(client):
    """The inline chat card is built from the event detail alone.

    After a decision the approval is no longer pending, so the card cannot rely
    on a join against ``/v1/approvals``: the command and the outcome both have
    to be on the events themselves.
    """
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)
    approval_id = pending[0]["approval_id"]

    events = (await client.get("/v1/sessions/s1/events")).json()
    requested = next(item for item in events if item["kind"] == "approval_requested")
    detail = requested["detail"]
    assert detail["approval_id"] == approval_id
    assert detail["tool"] == "execute_command"
    assert detail["command"] == "ls"
    assert detail["risk"] == "medium"
    assert detail["expires_at"]

    await client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "allow", "decided_by": "phone"},
    )
    await asyncio.wait_for(task, timeout=5)

    events = (await client.get("/v1/sessions/s1/events")).json()
    decided = next(item for item in events if item["kind"] == "approval_decided")
    assert decided["detail"]["approval_id"] == approval_id
    assert decided["detail"]["state"] == "allowed"
    assert decided["detail"]["decided_by"] == "phone"


async def test_approvals_filter_by_session(client):
    """The chat fetches one session's approvals for authoritative card state."""
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action("s1")))
    await poll_pending(client)

    mine = (await client.get("/v1/approvals", params={"session_id": "s1"})).json()
    assert [item["session_id"] for item in mine] == ["s1"]
    other = (await client.get("/v1/approvals", params={"session_id": "other"})).json()
    assert other == []

    await client.post(
        f"/v1/approvals/{mine[0]['approval_id']}/decision", json={"decision": "deny"}
    )
    await asyncio.wait_for(task, timeout=5)


async def test_cancel_all_records_a_decision_event(ctx, client):
    """Shutdown must not leave a card stuck on 'pending'."""
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    await poll_pending(client)

    assert await ctx.approvals.cancel_all("test shutdown") == 1
    await asyncio.wait_for(task, timeout=5)

    events = (await client.get("/v1/sessions/s1/events")).json()
    decided = [item for item in events if item["kind"] == "approval_decided"]
    assert len(decided) == 1
    assert decided[0]["detail"]["state"] == "cancelled"
    assert decided[0]["detail"]["decided_by"] == "agentd"


async def test_sweep_records_a_decision_event(ctx, client):
    """The sweep is the restart path: the waiter is gone, so nothing else would
    ever record a decision for a stale approval."""
    await client.post("/v1/events", json=[event(event_id="codex:s1:0")])
    await ctx.db.insert_approval(
        ApprovalRecord(
            approval_id="a-sweep",
            agent_type=AgentType.CODEX,
            session_id="s1",
            workspace_path=WORKSPACE,
            tool=Tool(name="execute_command", kind=ToolKind.COMMAND),
            action=ActionDetail(summary="ls", command="ls"),
            risk=Risk(level=RiskLevel.HIGH, reasons=["shell"]),
            action_hash="deadbeef",
            state=ApprovalState.PENDING,
            created_at=utcnow() - timedelta(minutes=5),
            expires_at=utcnow() - timedelta(seconds=1),
        )
    )

    assert await ctx.approvals.sweep_expired() == 1

    events = (await client.get("/v1/sessions/s1/events")).json()
    decided = [item for item in events if item["kind"] == "approval_decided"]
    assert len(decided) == 1
    assert decided[0]["detail"]["approval_id"] == "a-sweep"
    assert decided[0]["detail"]["state"] == "expired"
    assert decided[0]["detail"]["decided_by"] == "agentd"
