"""End-to-end slice 1: hook -> daemon -> decision -> hook unblocks.

These tests run a real agentd over HTTP and drive the real hook entrypoints,
so they cover the whole walking skeleton including the fail-closed paths.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
from pathlib import Path

import httpx

from agentd.adapters.cline import hook_cli as cline_hook
from agentd.adapters.codex import hook_cli as codex_hook
from conftest import (
    TEST_TOKEN,
    auth_headers,
    decide,
    wait_for_pending,
    wait_for_pending_many,
)

WORKSPACE = "C:/work/project"


def cline_payload(command: str = "rm -rf build/", tool: str = "execute_command") -> dict:
    return {
        "hookName": "PreToolUse",
        "taskId": "e2e-cline",
        "workspaceRoots": [WORKSPACE],
        "preToolUse": {
            "toolName": tool,
            "parameters": {"command": command, "cwd": WORKSPACE},
        },
    }


def codex_payload(command: str = "rm -rf build/") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "e2e-codex",
        "cwd": WORKSPACE,
        "tool_name": "shell",
        "tool_input": {"command": command},
    }


def point_at_dead_daemon(home: Path) -> None:
    """Write a config + token that aim at a port nothing is listening on."""
    (home / "config.toml").write_text(
        '[server]\nhost = "127.0.0.1"\nport = 1\n', encoding="utf-8"
    )
    (home / "local_api_token").write_text(TEST_TOKEN, encoding="utf-8")


# --- the happy path -------------------------------------------------------


def test_cline_hook_blocks_until_approved(live_daemon):
    port = live_daemon
    buffer = io.StringIO()
    result: dict = {}

    def target() -> None:
        result["code"] = cline_hook.run(json.dumps(cline_payload()))

    with contextlib.redirect_stdout(buffer):
        thread = threading.Thread(target=target)
        thread.start()
        approval_id = wait_for_pending(port)
        decide(port, approval_id, "allow", "e2e allow")
        thread.join(timeout=15)

    assert not thread.is_alive()
    assert result["code"] == 0
    assert json.loads(buffer.getvalue().strip()) == {"cancel": False}


def test_cline_hook_blocks_until_denied(live_daemon):
    port = live_daemon
    buffer = io.StringIO()
    result: dict = {}

    def target() -> None:
        result["code"] = cline_hook.run(json.dumps(cline_payload()))

    with contextlib.redirect_stdout(buffer):
        thread = threading.Thread(target=target)
        thread.start()
        approval_id = wait_for_pending(port)
        decide(port, approval_id, "deny", "e2e deny")
        thread.join(timeout=15)

    assert result["code"] == 0
    body = json.loads(buffer.getvalue().strip())
    assert body["cancel"] is True
    assert "e2e deny" in body["errorMessage"]


def test_codex_hook_allows_when_approved(live_daemon):
    port = live_daemon
    result: dict = {}

    def target() -> None:
        result["code"] = codex_hook.run(json.dumps(codex_payload()))

    thread = threading.Thread(target=target)
    thread.start()
    approval_id = wait_for_pending(port)
    decide(port, approval_id, "allow", "e2e allow")
    thread.join(timeout=15)

    assert result["code"] == 0


def test_codex_hook_blocks_when_denied(live_daemon):
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
    payload = cline_payload(tool="read_file")
    payload["preToolUse"]["parameters"] = {"path": f"{WORKSPACE}/a.py"}

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cline_hook.run(json.dumps(payload))

    assert code == 0
    assert json.loads(buffer.getvalue().strip()) == {"cancel": False}

    pending = httpx.get(
        f"http://127.0.0.1:{port}/v1/approvals",
        params={"state": "pending"},
        headers=auth_headers(),
        timeout=5.0,
    ).json()
    assert pending == []


# --- segregation ----------------------------------------------------------


def test_agents_do_not_cross_contaminate(live_daemon):
    """Cline and Codex decisions must never be applied to each other."""
    port = live_daemon
    cline_result: dict = {}
    codex_result: dict = {}

    def run_cline() -> None:
        cline_result["code"] = cline_hook.run(json.dumps(cline_payload("echo cline")))

    def run_codex() -> None:
        codex_result["code"] = codex_hook.run(json.dumps(codex_payload("echo codex")))

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cline_thread = threading.Thread(target=run_cline)
        codex_thread = threading.Thread(target=run_codex)
        cline_thread.start()
        codex_thread.start()

        pending = wait_for_pending_many(port, 2)
        by_agent = {item["agent_type"]: item for item in pending}
        assert set(by_agent) == {"cline", "codex"}

        decide(port, by_agent["cline"]["approval_id"], "allow")
        decide(port, by_agent["codex"]["approval_id"], "deny")

        cline_thread.join(timeout=15)
        codex_thread.join(timeout=15)

    assert cline_result["code"] == 0
    assert codex_result["code"] == 2


# --- fail closed ----------------------------------------------------------


def test_cline_hook_fails_closed_when_daemon_is_down(home):
    point_at_dead_daemon(home)
    buffer = io.StringIO()

    with contextlib.redirect_stdout(buffer):
        code = cline_hook.run(json.dumps(cline_payload()))

    assert code == 0
    body = json.loads(buffer.getvalue().strip())
    assert body["cancel"] is True
    assert "daemon" in body["errorMessage"].lower()


def test_codex_hook_fails_closed_when_daemon_is_down(home):
    point_at_dead_daemon(home)
    buffer = io.StringIO()

    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run(json.dumps(codex_payload()))

    assert code == 2
    assert "daemon" in buffer.getvalue().lower()


def test_cline_hook_fails_closed_on_malformed_json(home):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cline_hook.run("{not json")
    assert code == 0
    assert json.loads(buffer.getvalue().strip())["cancel"] is True


def test_codex_hook_fails_closed_on_malformed_json(home):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = codex_hook.run("{not json")
    assert code == 2


def test_cline_hook_fails_closed_when_payload_has_no_tool(home):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cline_hook.run(json.dumps({"taskId": "x"}))
    assert code == 0
    body = json.loads(buffer.getvalue().strip())
    assert body["cancel"] is True
    assert "parse" in body["errorMessage"].lower()


def test_cline_hook_fails_closed_without_a_token(home):
    """No token file means the daemon has never run: deny."""
    (home / "config.toml").write_text(
        '[server]\nhost = "127.0.0.1"\nport = 1\n', encoding="utf-8"
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cline_hook.run(json.dumps(cline_payload()))
    assert code == 0
    assert json.loads(buffer.getvalue().strip())["cancel"] is True

