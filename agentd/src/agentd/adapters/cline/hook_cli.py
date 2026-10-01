"""Cline ``PreToolUse`` hook entrypoint.

Contract (per Cline docs, to be confirmed by Phase 0 probe P0-1):
  * JSON payload arrives on stdin.
  * ``{"cancel": false}`` on stdout allows the tool call.
  * ``{"cancel": true, "errorMessage": "..."}`` blocks it.
  * Exit code is always 0; the JSON carries the decision.

Every failure path emits ``cancel: true`` (fail closed, D5).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ...protocol import Action, AgentType
from ..base import HookParseError
from ..client import DaemonUnavailable, call_daemon
from .mapping import parse


class ClineAdapter:
    agent_type = AgentType.CLINE

    def parse(self, payload: dict[str, Any]) -> Action:
        return parse(payload)

    def render_allow(self, action: Action) -> tuple[str, int]:
        return json.dumps({"cancel": False}), 0

    def render_deny(self, action: Action | None, reason: str) -> tuple[str, int]:
        return json.dumps({"cancel": True, "errorMessage": f"AgentLink: {reason}"}), 0


def _emit(text: str) -> None:
    if text:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()


def run(stdin_text: str) -> int:
    """Process one hook invocation. Always returns 0 for Cline."""
    adapter = ClineAdapter()

    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        _emit(adapter.render_deny(None, "malformed hook payload")[0])
        return 0

    try:
        action = adapter.parse(payload)
    except HookParseError as exc:
        _emit(adapter.render_deny(None, f"could not parse hook payload: {exc}")[0])
        return 0

    try:
        outcome = call_daemon(action)
    except DaemonUnavailable as exc:
        _emit(adapter.render_deny(action, f"daemon unavailable ({exc})")[0])
        return 0

    if outcome.allowed:
        _emit(adapter.render_allow(action)[0])
    else:
        _emit(adapter.render_deny(action, outcome.reason or "denied")[0])
    return 0


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
