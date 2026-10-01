"""Tests for the agent hook adapters."""

from __future__ import annotations

import json

import pytest

from agentd.adapters.base import HookParseError, collect_paths
from agentd.adapters.cline import hook_cli as cline_hook
from agentd.adapters.cline.mapping import parse as parse_cline
from agentd.adapters.cline.mapping import tool_kind_for as cline_kind
from agentd.adapters.codex import hook_cli as codex_hook
from agentd.adapters.codex.mapping import parse as parse_codex
from agentd.adapters.codex.mapping import tool_kind_for as codex_kind
from agentd.protocol import AgentType, ToolKind

WORKSPACE = "C:/work/project"


# --- Cline ----------------------------------------------------------------


def test_cline_parses_nested_pretooluse():
    action = parse_cline(
        {
            "hookName": "PreToolUse",
            "taskId": "task-1",
            "workspaceRoots": [WORKSPACE],
            "preToolUse": {
                "toolName": "execute_command",
                "parameters": {"command": "ls -la", "cwd": WORKSPACE},
            },
        }
    )
    assert action.agent_type is AgentType.CLINE
    assert action.session_id == "task-1"
    assert action.workspace_path == WORKSPACE
    assert action.tool.name == "execute_command"
    assert action.tool.kind is ToolKind.COMMAND
    assert action.action.command == "ls -la"
    assert action.action.summary == "ls -la"


def test_cline_parses_flat_payload():
    action = parse_cline(
        {
            "tool_name": "write_to_file",
            "session_id": "s9",
            "workspace_path": WORKSPACE,
            "arguments": {"path": f"{WORKSPACE}/a.py"},
        }
    )
    assert action.session_id == "s9"
    assert action.tool.kind is ToolKind.FILE_EDIT
    assert action.action.paths == [f"{WORKSPACE}/a.py"]


def test_cline_keeps_the_raw_payload():
    payload = {"toolName": "read_file", "parameters": {"path": "a.py"}}
    assert parse_cline(payload).raw == payload


def test_cline_requires_a_tool_name():
    with pytest.raises(HookParseError):
        parse_cline({"taskId": "t"})


def test_cline_rejects_non_objects():
    with pytest.raises(HookParseError):
        parse_cline("not a dict")  # type: ignore[arg-type]


def test_cline_kind_heuristics():
    assert cline_kind("execute_command") is ToolKind.COMMAND
    assert cline_kind("read_file") is ToolKind.FILE_READ
    assert cline_kind("delete_file") is ToolKind.FILE_DELETE
    assert cline_kind("browser_action") is ToolKind.NETWORK
    assert cline_kind("something_unknown") is ToolKind.OTHER


def test_cline_render_allow():
    adapter = cline_hook.ClineAdapter()
    stdout, code = adapter.render_allow(None)  # type: ignore[arg-type]
    assert json.loads(stdout) == {"cancel": False}
    assert code == 0


def test_cline_render_deny():
    adapter = cline_hook.ClineAdapter()
    stdout, code = adapter.render_deny(None, "nope")
    body = json.loads(stdout)
    assert body["cancel"] is True
    assert "nope" in body["errorMessage"]
    assert code == 0


# --- Codex ----------------------------------------------------------------


def test_codex_parses_tool_input_shape():
    action = parse_codex(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "cx-1",
            "cwd": WORKSPACE,
            "tool_name": "shell",
            "tool_input": {"command": "git push --force"},
        }
    )
    assert action.agent_type is AgentType.CODEX
    assert action.session_id == "cx-1"
    assert action.workspace_path == WORKSPACE
    assert action.tool.kind is ToolKind.COMMAND
    assert action.action.command == "git push --force"


def test_codex_parses_apply_patch():
    action = parse_codex(
        {
            "session_id": "cx-2",
            "cwd": WORKSPACE,
            "tool_name": "apply_patch",
            "tool_input": {"patch": "*** Begin Patch\n*** Update File: a.py\n*** End Patch"},
        }
    )
    assert action.tool.kind is ToolKind.FILE_EDIT
    assert "apply_patch" in action.action.summary


def test_codex_requires_a_tool_name():
    with pytest.raises(HookParseError):
        parse_codex({"session_id": "x"})


def test_codex_kind_heuristics():
    assert codex_kind("shell") is ToolKind.COMMAND
    assert codex_kind("apply_patch") is ToolKind.FILE_EDIT
    assert codex_kind("read_file") is ToolKind.FILE_READ
    assert codex_kind("web_search") is ToolKind.NETWORK
    assert codex_kind("mystery") is ToolKind.OTHER


def test_codex_render_deny_uses_exit_two():
    adapter = codex_hook.CodexAdapter()
    body, code = adapter.render_deny(None, "blocked")
    assert code == 2
    assert "blocked" in body
    assert "deny" in body


def test_codex_render_allow_uses_exit_zero():
    adapter = codex_hook.CodexAdapter()
    body, code = adapter.render_allow(None)  # type: ignore[arg-type]
    assert code == 0
    assert "allow" in body


# --- shared helpers -------------------------------------------------------


def test_collect_paths_deduplicates():
    assert collect_paths({"path": "a", "paths": ["a", "b"]}) == ["a", "b"]


def test_collect_paths_handles_missing_keys():
    assert collect_paths({}) == []
