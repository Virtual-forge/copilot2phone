"""Tests for the agent hook adapters."""

from __future__ import annotations

import json

import pytest

from agentd.adapters.base import HookParseError, collect_paths
from agentd.adapters.codex import hook_cli as codex_hook
from agentd.adapters.codex.mapping import parse as parse_codex
from agentd.adapters.codex.mapping import tool_kind_for as codex_kind
from agentd.protocol import AgentType, ToolKind

WORKSPACE = "C:/work/project"


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
    stderr, code = adapter.render_deny(None, "blocked")
    assert code == 2
    assert "blocked" in stderr


def test_codex_render_allow_uses_exit_zero():
    adapter = codex_hook.CodexAdapter()
    stdout, code = adapter.render_allow(None)  # type: ignore[arg-type]
    assert code == 0
    assert stdout == ""


# --- shared helpers -------------------------------------------------------


def test_collect_paths_deduplicates():
    assert collect_paths({"path": "a", "paths": ["a", "b"]}) == ["a", "b"]


def test_collect_paths_handles_missing_keys():
    assert collect_paths({}) == []
