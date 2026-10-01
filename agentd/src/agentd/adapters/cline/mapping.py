"""Cline hook payload mapping.

The exact Cline hook contract is a Phase 0 unknown (P0-1). This parser is
deliberately tolerant: it accepts several plausible shapes and keeps the raw
payload so real captures can be folded into ``docs/phase0-findings.md``.
"""

from __future__ import annotations

import os
from typing import Any

from ...protocol import Action, ActionDetail, AgentType, Tool, ToolKind
from ..base import HookParseError, as_dict, collect_paths, first

CLINE_TOOL_KINDS: dict[str, ToolKind] = {
    "execute_command": ToolKind.COMMAND,
    "run_command": ToolKind.COMMAND,
    "bash": ToolKind.COMMAND,
    "terminal": ToolKind.COMMAND,
    "write_to_file": ToolKind.FILE_EDIT,
    "replace_in_file": ToolKind.FILE_EDIT,
    "apply_diff": ToolKind.FILE_EDIT,
    "edit_file": ToolKind.FILE_EDIT,
    "insert_content": ToolKind.FILE_EDIT,
    "read_file": ToolKind.FILE_READ,
    "list_files": ToolKind.FILE_READ,
    "search_files": ToolKind.FILE_READ,
    "delete_file": ToolKind.FILE_DELETE,
    "browser_action": ToolKind.NETWORK,
    "web_fetch": ToolKind.NETWORK,
    "fetch": ToolKind.NETWORK,
}

_TOOL_BLOCK_KEYS = ("preToolUse", "pre_tool_use", "toolUse", "tool_use", "tool")
_PARAM_KEYS = ("parameters", "params", "arguments", "input", "args")
_SESSION_KEYS = ("taskId", "task_id", "sessionId", "session_id", "conversationId")
_WORKSPACE_KEYS = (
    "workspaceRoots",
    "workspace_roots",
    "workspaceRoot",
    "workspace_path",
    "cwd",
)


def tool_kind_for(name: str) -> ToolKind:
    """Map a Cline tool name onto a :class:`ToolKind`."""
    lowered = name.strip().lower()
    if lowered in CLINE_TOOL_KINDS:
        return CLINE_TOOL_KINDS[lowered]
    if any(token in lowered for token in ("command", "shell", "terminal", "exec")):
        return ToolKind.COMMAND
    if any(token in lowered for token in ("delete", "remove", "unlink")):
        return ToolKind.FILE_DELETE
    if any(token in lowered for token in ("read", "list", "search", "glob", "grep")):
        return ToolKind.FILE_READ
    if any(token in lowered for token in ("write", "edit", "diff", "replace", "patch")):
        return ToolKind.FILE_EDIT
    if any(token in lowered for token in ("browser", "fetch", "http", "web", "url")):
        return ToolKind.NETWORK
    return ToolKind.OTHER


def _tool_block(payload: dict[str, Any]) -> dict[str, Any]:
    for key in _TOOL_BLOCK_KEYS:
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload


def _summarize(tool_name: str, params: dict[str, Any], kind: ToolKind) -> str:
    if kind is ToolKind.COMMAND:
        command = params.get("command")
        if isinstance(command, str) and command.strip():
            return command.strip().splitlines()[0][:200]
    paths = collect_paths(params)
    if paths:
        return f"{tool_name}: {', '.join(paths[:3])}"
    return tool_name


def parse(payload: dict[str, Any]) -> Action:
    """Normalise a Cline hook payload into an :class:`Action`."""
    if not isinstance(payload, dict):
        raise HookParseError("hook payload is not a JSON object")

    block = _tool_block(payload)

    tool_name = first(block, "toolName", "tool_name", "name")
    if not tool_name:
        tool_name = first(payload, "toolName", "tool_name", "name")
    if not tool_name:
        raise HookParseError("no tool name found in hook payload")
    tool_name = str(tool_name)

    params = as_dict(first(block, *_PARAM_KEYS, default={}))

    session_id = first(payload, *_SESSION_KEYS) or first(block, *_SESSION_KEYS)
    session_id = str(session_id) if session_id else "cline-unknown"

    workspace = first(payload, *_WORKSPACE_KEYS)
    if isinstance(workspace, list):
        workspace = workspace[0] if workspace else None
    if not workspace:
        workspace = first(params, "cwd", "workspace")
    workspace = str(workspace) if workspace else os.getcwd()

    kind = tool_kind_for(tool_name)
    command = params.get("command")
    cwd = params.get("cwd")

    detail = ActionDetail(
        summary=_summarize(tool_name, params, kind),
        command=command if isinstance(command, str) else None,
        cwd=str(cwd) if isinstance(cwd, str) else None,
        paths=collect_paths(params),
    )

    return Action(
        agent_type=AgentType.CLINE,
        session_id=session_id,
        workspace_path=workspace,
        tool=Tool(name=tool_name, kind=kind),
        action=detail,
        raw=payload,
    )
