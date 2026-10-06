"""Tests for webhook notifications (work plan P1-a, P1-c)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agentd.activity import IncomingEvent
from agentd.config import Config
from agentd.notifier import Notifier
from agentd.protocol import ActivityKind, ApprovalState, Decision

WORKSPACE = "C:/work/project"


class Recorder:
    """A stand-in notifier: records fields, never posts."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def enabled(self) -> bool:
        return True

    def notify_soon(self, **fields) -> None:
        self.sent.append(fields)


def make_notifier(url: str, fmt: str = "generic") -> tuple[Notifier, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    config = Config()
    config.notifications.webhook_url = url
    config.notifications.webhook_format = fmt
    return Notifier(config, transport=httpx.MockTransport(handler)), captured


# --- payloads ---------------------------------------------------------------


async def test_generic_payload_shape():
    notifier, captured = make_notifier("https://example.test/hook")
    await notifier._deliver(
        {
            "event": "approval_requested",
            "title": "codex: shell needs a decision",
            "body": "rm -rf build/",
            "agent": "codex",
            "session_id": "s1",
        }
    )
    assert len(captured) == 1
    assert str(captured[0].url) == "https://example.test/hook"
    body = json.loads(captured[0].read())
    assert body["event"] == "approval_requested"
    assert body["title"] == "codex: shell needs a decision"
    assert body["body"] == "rm -rf build/"
    assert body["agent"] == "codex"
    assert body["session_id"] == "s1"
    assert body["at"]


async def test_ntfy_payload_publishes_to_the_root():
    notifier, captured = make_notifier("https://ntfy.sh/my-topic", fmt="ntfy")
    await notifier._deliver({"event": "approval_requested", "title": "decision needed", "body": "ls"})
    assert str(captured[0].url) == "https://ntfy.sh"
    body = json.loads(captured[0].read())
    assert body["topic"] == "my-topic"
    assert body["title"] == "decision needed"
    assert body["message"] == "ls"
    assert body["priority"] == "high"  # approvals buzz

    notifier2, captured2 = make_notifier("https://ntfy.sh/my-topic", fmt="ntfy")
    await notifier2._deliver({"event": "turn_completed", "title": "done", "body": ""})
    body2 = json.loads(captured2[0].read())
    assert body2["priority"] == "default"
    assert body2["message"] == "done"  # falls back to the title


async def test_bodies_are_redacted():
    notifier, captured = make_notifier("https://example.test/hook")
    secret = "sk-abcdefghijklmnopqrst"
    await notifier._deliver(
        {"event": "approval_requested", "title": "t", "body": f"curl -H 'Authorization: Bearer {secret}' x"}
    )
    body = json.loads(captured[0].read())
    assert secret not in body["body"]
    assert "***REDACTED***" in body["body"]


async def test_disabled_notifier_posts_nothing():
    notifier, captured = make_notifier("   ")
    assert not notifier.enabled
    notifier.notify_soon(event="approval_requested", title="t", body="b")
    await asyncio.sleep(0.05)
    assert captured == []


async def test_a_dead_webhook_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    config = Config()
    config.notifications.webhook_url = "https://example.test/hook"
    notifier = Notifier(config, transport=httpx.MockTransport(handler))
    notifier.notify_soon(event="approval_requested", title="t", body="b")
    await asyncio.sleep(0.05)  # the failure is logged, not raised


def test_unknown_webhook_format_is_rejected():
    config = Config()
    config.notifications.webhook_format = "pigeon"
    with pytest.raises(Exception):
        Config.model_validate(config.model_dump())


# --- triggers ---------------------------------------------------------------


async def test_approval_request_summons_the_phone(ctx, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(ctx.approvals, "_notifier", recorder)

    task = asyncio.create_task(
        ctx.approvals.request(
            _codex_action(command="ls -la")
        )
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline and not recorder.sent:
        await asyncio.sleep(0.01)
    pending = await ctx.approvals.list(state=ApprovalState.PENDING)
    assert pending

    assert recorder.sent[0]["event"] == "approval_requested"
    assert "shell" in recorder.sent[0]["title"]
    assert recorder.sent[0]["body"] == "ls -la"

    await ctx.approvals.decide(pending[0].approval_id, decision=Decision.DENY)
    outcome = await asyncio.wait_for(task, timeout=5)
    assert not outcome.allowed


# --- helpers ---------------------------------------------------------------


def _codex_action(**detail):
    from agentd.protocol import Action, ActionDetail, AgentType, Tool, ToolKind

    return Action(
        agent_type=AgentType.CODEX,
        session_id="s1",
        workspace_path=WORKSPACE,
        tool=Tool(name="shell", kind=ToolKind.COMMAND),
        action=ActionDetail(summary="ls", **detail),
    )


def _codex_agent():
    from agentd.protocol import AgentType

    return AgentType.CODEX
