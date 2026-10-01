"""Codex ``PreToolUse`` hook entrypoint.

Contract (per Codex CLI docs, to be confirmed by Phase 0 probe P0-2):
  * JSON payload arrives on stdin.
  * Exit code ``0`` allows the tool call.
  * Exit code ``2`` blocks it; stderr is surfaced to the model.
  * Any other exit code is treated as an error by Codex.

Every failure path exits ``2`` (fail closed, D5).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ...protocol import Action, AgentType
from ..base import HookParseError
from ..client import DaemonUnavailable, call_daemon
from .mapping import parse

ALLOW_EXIT = 0
BLOCK_EXIT = 2


class CodexAdapter:
    agent_type = AgentType.CODEX

    def parse(self, payload: dict[str, Any]) -> Action:
        return parse(payload)

    def render_allow(self, action: Action) -> tuple[str, int]:
        return "", ALLOW_EXIT

    def render_deny(self, action: Action | None, reason: str) -> tuple[str, int]:
        return f"AgentLink blocked this action: {reason}", BLOCK_EXIT


def _emit(text: str) -> None:
    if text:
        sys.stderr.write(text)
        sys.stderr.write("\n")
        sys.stderr.flush()


def run(stdin_text: str) -> int:
    """Process one hook invocation. Returns the exit code Codex expects."""
    adapter = CodexAdapter()

    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        _emit(adapter.render_deny(None, "malformed hook payload")[0])
        return BLOCK_EXIT

    try:
        action = adapter.parse(payload)
    except HookParseError as exc:
        _emit(adapter.render_deny(None, f"could not parse hook payload: {exc}")[0])
        return BLOCK_EXIT

    try:
        outcome = call_daemon(action)
    except DaemonUnavailable as exc:
        _emit(adapter.render_deny(action, f"daemon unavailable ({exc})")[0])
        return BLOCK_EXIT

    if outcome.allowed:
        return ALLOW_EXIT
    _emit(adapter.render_deny(action, outcome.reason or "denied")[0])
    return BLOCK_EXIT


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
