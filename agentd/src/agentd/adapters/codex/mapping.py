"""Codex hook payload mapping.

The exact Codex hook contract is a Phase 0 unknown (P0-2). This parser accepts
both the ``tool_name``/``tool_input`` shape and the ``toolName``/``parameters``
shape, and keeps the raw payload for the findings doc.
"""

from __future__ import annotations

import os
from typing import Any

from ...protocol import Action, ActionDetail, AgentType, Tool, ToolKind
from ..base import HookParseError, as_dict, collect_paths, first

CODEX_TOOL_KINDS: dict[str, ToolKind] = {
    "shell": ToolKind.COMMAND,
    "bash": ToolKind.COMMAND,
    "exec": ToolKind.COMMAND,
    "exec_command": ToolKind.COMMAND,
    "apply_patch": ToolKind.FILE_EDIT,
    "write_file": ToolKind.FILE_EDIT,
    "edit_file": ToolKind.FILE_EDIT,
    "read_file": ToolKind.FILE_READ,
    "list_dir": ToolKind.FILE_READ,
    "view_image": ToolKind.FILE_READ,
    "delete_file": ToolKind.FILE_DELETE,
    "web_search": ToolKind.NETWORK,
    "fetch": ToolKind.NETWORK,
}

_TOOL_BLOCK_KEYS = ("preToolUse", "pre_tool_use", "toolUse", "tool_use", "tool")
_PARAM_KEYS = ("tool_input", "toolInput", "input", "arguments", "parameters", "params", "args")
_SESSION_KEYS = ("session_id", "sessionId", "conversation_id", "conversationId", "thread_id")
_WORKSPACE_KEYS = ("cwd", "workspace_path", "workspace", "workspaceRoot", "workspace_roots")


def tool_kind_for(name: str) -> ToolKind:
    """Map a Codex tool name onto a :class:`ToolKind`."""
    lowered = name.strip().lower()
    if lowered in CODEX_TOOL_KINDS:
        return CODEX_TOOL_KINDS[lowered]
    if any(token in lowered for token in ("shell", "command", "exec", "bash", "terminal")):
        return ToolKind.COMMAND
    if any(token in lowered for token in ("delete", "remove", "unlink")):
        return ToolKind.FILE_DELETE
    if any(token in lowered for token in ("read", "list", "view", "search", "glob", "grep")):
        return ToolKind.FILE_READ
    if any(token in lowered for token in ("patch", "write", "edit", "diff", "replace")):
        return ToolKind.FILE_EDIT
    if any(token in lowered for token in ("web", "fetch", "http", "url", "search")):
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
        command = first(params, "command", "cmd", "script")
        if isinstance(command, str) and command.strip():
            return command.strip().splitlines()[0][:200]
    if kind is ToolKind.FILE_EDIT:
        patch = first(params, "patch", "diff")
        if isinstance(patch, str) and patch.strip():
            return f"{tool_name}: {patch.strip().splitlines()[0][:160]}"
    paths = collect_paths(params)
    if paths:
        return f"{tool_name}: {', '.join(paths[:3])}"
    return tool_name


def parse(payload: dict[str, Any]) -> Action:
    """Normalise a Codex hook payload into an :class:`Action`."""
    if not isinstance(payload, dict):
        raise HookParseError("hook payload is not a JSON object")

    block = _tool_block(payload)

    tool_name = first(block, "tool_name", "toolName", "name")
    if not tool_name:
        tool_name = first(payload, "tool_name", "toolName", "name")
    if not tool_name:
        raise HookParseError("no tool name found in hook payload")
    tool_name = str(tool_name)

    params = as_dict(first(block, *_PARAM_KEYS, default={}))

    session_id = first(payload, *_SESSION_KEYS) or first(block, *_SESSION_KEYS)
    session_id = str(session_id) if session_id else "codex-unknown"

    workspace = first(payload, *_WORKSPACE_KEYS)
    if isinstance(workspace, list):
        workspace = workspace[0] if workspace else None
    if not workspace:
        workspace = first(params, "cwd", "workdir")
    workspace = str(workspace) if workspace else os.getcwd()

    kind = tool_kind_for(tool_name)
    command = first(params, "command", "cmd", "script")
    cwd = first(params, "cwd", "workdir")

    detail = ActionDetail(
        summary=_summarize(tool_name, params, kind),
        command=command if isinstance(command, str) else None,
        cwd=str(cwd) if isinstance(cwd, str) else None,
        paths=collect_paths(params),
    )

    return Action(
        agent_type=AgentType.CODEX,
        session_id=session_id,
        workspace_path=workspace,
        tool=Tool(name=tool_name, kind=kind),
        action=detail,
        raw=payload,
    )
