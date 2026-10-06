"""Tests for the loopback HTTP API."""

from __future__ import annotations

import asyncio

WORKSPACE = "C:/work/project"


def command_action(**detail) -> dict:
    return {
        "agent_type": "codex",
        "session_id": "s1",
        "workspace_path": WORKSPACE,
        "tool": {"name": "shell", "kind": "command"},
        "action": {"summary": "ls", "command": "ls", **detail},
    }


def read_action() -> dict:
    return {
        "agent_type": "codex",
        "session_id": "s1",
        "workspace_path": WORKSPACE,
        "tool": {"name": "read_file", "kind": "file_read"},
        "action": {"summary": "read", "paths": [f"{WORKSPACE}/a.py"]},
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


# --- auth -----------------------------------------------------------------


async def test_health_needs_no_auth(client):
    response = await client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_missing_token_is_rejected(client):
    response = await client.get("/v1/approvals", headers={"Authorization": ""})
    assert response.status_code == 401


async def test_wrong_token_is_rejected(client):
    response = await client.get(
        "/v1/approvals", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


async def test_status_requires_a_token(client):
    response = await client.get("/v1/status", headers={"Authorization": ""})
    assert response.status_code == 401


# --- approvals ------------------------------------------------------------


async def test_workspace_read_is_auto_allowed(client):
    response = await client.post("/v1/approvals", json=read_action())
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["state"] == "allowed"


async def test_blocking_approval_round_trip(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)
    approval_id = pending[0]["approval_id"]

    decision = await client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "allow", "reason": "ok", "decided_by": "test"},
    )
    assert decision.status_code == 200
    assert decision.json()["state"] == "allowed"

    outcome = await asyncio.wait_for(task, timeout=5)
    assert outcome.status_code == 200
    assert outcome.json()["decision"] == "allow"


async def test_deny_round_trip(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)
    await client.post(
        f"/v1/approvals/{pending[0]['approval_id']}/decision",
        json={"decision": "deny", "reason": "no", "decided_by": "test"},
    )
    outcome = await asyncio.wait_for(task, timeout=5)
    assert outcome.json()["decision"] == "deny"
    assert outcome.json()["reason"] == "no"


async def test_unknown_approval_is_404(client):
    response = await client.get("/v1/approvals/does-not-exist")
    assert response.status_code == 404


async def test_double_decide_is_409(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)
    url = f"/v1/approvals/{pending[0]['approval_id']}/decision"

    first = await client.post(url, json={"decision": "allow"})
    assert first.status_code == 200
    second = await client.post(url, json={"decision": "deny"})
    assert second.status_code == 409

    await asyncio.wait_for(task, timeout=5)


# --- status / sessions / audit -------------------------------------------


async def test_status_reports_pending_per_agent(client):
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)

    status = (await client.get("/v1/status")).json()
    assert status["pending"] == {"codex": 1}
    assert status["waiting"] == 1
    assert status["agents"]["codex"]["enabled"] is True

    await client.post(
        f"/v1/approvals/{pending[0]['approval_id']}/decision", json={"decision": "deny"}
    )
    await asyncio.wait_for(task, timeout=5)


async def test_away_toggle(client):
    response = await client.post("/v1/away", params={"enabled": True})
    assert response.json() == {"away": True}
    assert (await client.get("/v1/status")).json()["away"] is True

    await client.post("/v1/away", params={"enabled": False})
    assert (await client.get("/v1/status")).json()["away"] is False


async def test_remote_toggle(client):
    """The phone-approvals switch (D-028): native prompt on the desktop
    when off, cards here when on."""
    response = await client.post("/v1/remote", params={"enabled": True})
    assert response.json() == {"remote": True}
    assert (await client.get("/v1/status")).json()["remote"] is True

    off = await client.post("/v1/remote", params={"enabled": False})
    assert off.json() == {"remote": False}
    assert (await client.get("/v1/status")).json()["remote"] is False


async def test_sessions_endpoint(client):
    await client.post("/v1/approvals", json=read_action())
    sessions = (await client.get("/v1/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["agent_type"] == "codex"
    assert sessions[0]["session_id"] == "s1"


async def test_audit_endpoint(client):
    await client.post("/v1/approvals", json=read_action())
    entries = (await client.get("/v1/audit")).json()
    assert any(entry["action"] == "policy.evaluate" for entry in entries)


# --- phone UI -------------------------------------------------------------


async def test_phone_ui_is_served_without_auth(client):
    """The shell is public; it is only markup and asks for the token itself."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<title>AgentLink</title>" in body
    assert "/v1/approvals" in body
    assert "HOLD_MS = 1500" in body  # D-004 press-and-hold
    assert body.count("<script") == body.count("</script>") == 1


async def test_phone_ui_does_not_leak_the_token(client):
    from conftest import TEST_TOKEN

    body = (await client.get("/")).text
    assert TEST_TOKEN not in body


async def test_phone_ui_decision_flow(client):
    """Replay exactly what the page does: list pending, then decide."""
    task = asyncio.create_task(client.post("/v1/approvals", json=command_action()))
    pending = await poll_pending(client)
    approval_id = pending[0]["approval_id"]

    response = await client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "allow", "decided_by": "phone"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "allowed"
    assert response.json()["decided_by"] == "phone"

    outcome = (await asyncio.wait_for(task, timeout=5)).json()
    assert outcome["decision"] == "allow"
