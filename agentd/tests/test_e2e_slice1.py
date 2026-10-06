"""End-to-end slice 1: hook -> daemon -> decision -> hook unblocks.

These tests run a real agentd over HTTP and drive the real hook entrypoint,
so they cover the whole walking skeleton including the fail-closed paths.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
from pathlib import Path

import httpx

from agentd.adapters.codex import hook_cli as codex_hook
from conftest import (
    TEST_TOKEN,
    auth_headers,
    decide,
    wait_for_pending,
    wait_for_pending_many,
)

WORKSPACE = "C:/work/project"


def codex_payload(
    command: str = "rm -rf build/",
    session_id: str = "e2e-codex",
    tool: str = "shell",
    tool_input: dict | None = None,
) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "cwd": WORKSPACE,
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"command": command},
    }


def point_at_dead_daemon(home: Path) -> None:
    """Write a config + token that aim at a port nothing is listening on."""
    (home / "config.toml").write_text(
        '[server]\nhost = "127.0.0.1"\nport = 1\n', encoding="utf-8"
    )
    (home / "local_api_token").write_text(TEST_TOKEN, encoding="utf-8")


# --- the happy path -------------------------------------------------------


def test_codex_hook_blocks_until_allowed(live_daemon):
    port = live_daemon
    result: dict = {}

    def target() -> None:
        result["code"] = codex_hook.run(json.dumps(codex_payload()))

    thread = threading.Thread(target=target)
    thread.start()
    approval_id = wait_for_pending(port)
    decide(port, approval_id, "allow", "e2e allow")
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert result["code"] == 0


def test_codex_hook_blocks_until_denied(live_daemon):
    port = live_daemon
    buffer = io.StringIO()
    result: dict = {}

    def target() -> None:
        result["code"] = codex_hook.run(json.dumps(codex_payload()))

    with contextlib.redirect_stderr(buffer):
        thread = threading.Thread(target=target)
        thread.start()
        approval_id = wait_for_pending(port)
        decide(port, approval_id, "deny", "e2e deny")
        thread.join(timeout=15)

    assert result["code"] == 2
    assert "e2e deny" in buffer.getvalue()


def test_workspace_read_does_not_ask(live_daemon):
    port = live_daemon
    payload = codex_payload(
        tool="read_file", tool_input={"path": f"{WORKSPACE}/a.py"}
    )

    code = codex_hook.run(json.dumps(payload))

    assert code == 0
    pending = httpx.get(
        f"http://127.0.0.1:{port}/v1/approvals",
        params={"state": "pending"},
        headers=auth_headers(),
        timeout=5.0,
    ).json()
    assert pending == []


# --- segregation ----------------------------------------------------------


def test_sessions_do_not_cross_contaminate(live_daemon):
    """Two concurrent sessions: a decision for one never answers the other."""
    port = live_daemon
    allowed: dict = {}
    denied: dict = {}

    def run_allowed() -> None:
        allowed["code"] = codex_hook.run(
            json.dumps(codex_payload("echo a", session_id="e2e-a"))
        )

    def run_denied() -> None:
        denied["code"] = codex_hook.run(
            json.dumps(codex_payload("echo b", session_id="e2e-b"))
        )

    with contextlib.redirect_stderr(io.StringIO()):
        allowed_thread = threading.Thread(target=run_allowed)
        denied_thread = threading.Thread(target=run_denied)
        allowed_thread.start()
        denied_thread.start()

        pending = wait_for_pending_many(port, 2)
        by_session = {item["session_id"]: item for item in pending}
        assert set(by_session) == {"e2e-a", "e2e-b"}

        decide(port, by_session["e2e-a"]["approval_id"], "allow")
        decide(port, by_session["e2e-b"]["approval_id"], "deny")

        allowed_thread.join(timeout=15)
        denied_thread.join(timeout=15)

    assert allowed["code"] == 0
    assert denied["code"] == 2


# --- live stream (D-026) ---------------------------------------------------


def test_stream_pushes_change_notices(live_daemon):
    """SSE over a real socket: an ingest anywhere wakes the stream with a
    tiny notice, and the data itself still comes from the normal endpoints."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}"

    def post_event() -> None:
        httpx.post(
            f"{base}/v1/events",
            headers=auth_headers(),
            json=[
                {
                    "agent_type": "codex",
                    "session_id": "stream-e2e",
                    "workspace_path": WORKSPACE,
                    "kind": "user_message",
                    "summary": "hi",
                    "text": "hi",
                    "role": "user",
                }
            ],
            timeout=5.0,
        )

    with httpx.Client(
        base_url=base, headers=auth_headers(), timeout=15.0
    ) as client:
        with client.stream(
            "GET", "/v1/stream", params={"session_id": "stream-e2e"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            text = ""
            posted = False
            for chunk in response.iter_raw():
                text += chunk.decode("utf-8", errors="replace")
                # the stream is subscribed once the greeting arrives: only
                # then can a notice be guaranteed to reach it
                if not posted and ": connected" in text:
                    posted = True
                    threading.Thread(target=post_event).start()
                if "event: change" in text:
                    break
            assert posted, "the stream never greeted"

    assert "event: change" in text
    assert '"session_id": "stream-e2e"' in text


# --- remote approvals gate (D-028) -----------------------------------------


def test_remote_off_hands_the_decision_back_to_codex(live_daemon):
    """With phone gating off, the hook answers Codex's JSON 'ask': no
    approval is parked, and the desktop's own approval prompt decides."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}"
    httpx.post(
        f"{base}/v1/remote",
        params={"enabled": "false"},
        headers=auth_headers(),
        timeout=5.0,
    )

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = codex_hook.run(json.dumps(codex_payload()))

    assert code == 0
    payload = json.loads(buffer.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert payload["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert payload["hookSpecificOutput"]["permissionDecisionReason"]

    pending = httpx.get(
        f"{base}/v1/approvals",
        params={"state": "pending"},
        headers=auth_headers(),
        timeout=5.0,
    ).json()
    assert pending == []


# --- fail closed ----------------------------------------------------------


def test_codex_hook_fails_closed_when_daemon_is_down(home):
    point_at_dead_daemon(home)
    buffer = io.StringIO()

    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run(json.dumps(codex_payload()))

    assert code == 2
    assert "daemon" in buffer.getvalue().lower()


def test_codex_hook_fails_closed_on_malformed_json(home):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run("{not json")
    assert code == 2


def test_codex_hook_fails_closed_when_payload_has_no_tool(home):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run(json.dumps({"session_id": "x"}))
    assert code == 2
    assert "parse" in buffer.getvalue().lower()


def test_codex_hook_fails_closed_without_a_token(home):
    """No token file means the daemon has never run: deny."""
    (home / "config.toml").write_text(
        '[server]\nhost = "127.0.0.1"\nport = 1\n', encoding="utf-8"
    )
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run(json.dumps(codex_payload()))
    assert code == 2
