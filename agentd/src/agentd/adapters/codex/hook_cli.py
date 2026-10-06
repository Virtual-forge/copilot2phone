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


def _decision_json(permission: str, reason: str) -> str:
    """Codex's stdout JSON contract (P0-2, read from the codex binary):
    ``permissionDecision`` allow/deny/ask inside ``hookSpecificOutput``.
    Kept alongside the exit codes — whichever executor honors, honors."""
    return (
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": permission,
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )


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

    if outcome.deferred:
        # Remote approvals are off (D-028): agentd created no approval and
        # blocked nobody. Answer with Codex's JSON "ask" — permissionDecision
        # "ask" hands the decision back to the built-in approval prompt, so
        # the desktop gets its native, refined UI exactly as without a hook.
        sys.stdout.write(
            _decision_json("ask", "AgentLink: remote approvals are off — deciding on desktop")
        )
        sys.stdout.flush()
        return ALLOW_EXIT

    if outcome.allowed:
        sys.stdout.write(
            _decision_json("allow", "AgentLink: allowed from the phone")
        )
        sys.stdout.flush()
        return ALLOW_EXIT

    reason = outcome.reason or "denied"
    # Belt and braces (P0-2): the JSON deny on stdout, the exit code 2, and
    # the reason on stderr — so whichever contract the running Codex build
    # honors, a phone-deny means "blocked".
    sys.stdout.write(_decision_json("deny", f"AgentLink: {reason}"))
    sys.stdout.flush()
    _emit(adapter.render_deny(action, reason)[0])
    return BLOCK_EXIT


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
