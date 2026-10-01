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
        payload = {
            "decision": "allow",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            },
        }
        return json.dumps(payload), ALLOW_EXIT

    def render_deny(self, action: Action | None, reason: str) -> tuple[str, int]:
        msg = f"AgentLink blocked this action: {reason}"
        payload = {
            "decision": "block",
            "reason": msg,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg,
            },
        }
        return json.dumps(payload), BLOCK_EXIT


def _emit_out(text: str) -> None:
    if text:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()


def _emit_err(text: str) -> None:
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
        out, code = adapter.render_deny(None, "malformed hook payload")
        _emit_out(out)
        _emit_err("malformed hook payload")
        return code

    try:
        action = adapter.parse(payload)
    except HookParseError as exc:
        out, code = adapter.render_deny(None, f"could not parse hook payload: {exc}")
        _emit_out(out)
        _emit_err(f"could not parse hook payload: {exc}")
        return code

    try:
        outcome = call_daemon(action)
    except DaemonUnavailable as exc:
        out, code = adapter.render_deny(action, f"daemon unavailable ({exc})")
        _emit_out(out)
        _emit_err(f"daemon unavailable ({exc})")
        return code

    if outcome.allowed:
        out, code = adapter.render_allow(action)
        _emit_out(out)
        return code
    out, code = adapter.render_deny(action, outcome.reason or "denied")
    _emit_out(out)
    _emit_err(outcome.reason or "denied")
    return code


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
