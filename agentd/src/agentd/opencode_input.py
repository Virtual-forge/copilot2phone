"""Inject prompts into desktop OpenCode sessions.

OpenCode's background service — the same server the desktop TUI talks to —
accepts a prompt for an existing session via
``opencode run --session <id> --format json <message>`` (D-026). The CLI is a
thin client: the turn is orchestrated by the service and completes there even
if the caller goes away, so agentd fires the subprocess and returns. The
prompt and the agent's reply land in ``opencode.db`` like any other turn, so
the transcript reader picks them up on the next scan — the phone sees what it
sent and the streaming answer without any extra plumbing.

Deliberately *not* used here: the server's HTTP API. It exists (`opencode
serve`, pairing links, an OpenAPI document), but its auth handshake is
undocumented and the CLI already solves discovery and authentication by
reading the same service state the TUI uses. Shelling out keeps agentd out
of the token business; swapping in a native HTTP client later is a local
change behind this same interface.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from . import paths

logger = logging.getLogger(__name__)

DEFAULT_BINARY = "opencode"


class InputUnavailable(Exception):
    """Prompts cannot be delivered to this machine's OpenCode."""


class _Process(Protocol):
    def poll(self) -> int | None: ...


class OpencodeInput:
    """Fire-and-forget prompt delivery with one in-flight prompt per session."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        spawn: Callable[..., Any] | None = None,
    ) -> None:
        self._binary = binary or os.environ.get("OPENCODE_BIN") or DEFAULT_BINARY
        # Tests inject a fake spawn; production forks the real CLI.
        self._spawn = spawn or subprocess.Popen
        self._running: dict[str, Any] = {}

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    def in_flight(self, session_id: str) -> bool:
        """True while a prompt for this session is still being delivered.

        OpenCode runs one turn per session; a second prompt would queue
        behind the first anyway, so the endpoint refuses it with a clear
        error instead of letting the user wonder which one landed.
        """
        process = self._running.get(session_id)
        if process is None:
            return False
        if process.poll() is not None:
            self._running.pop(session_id, None)
            return False
        return True

    def submit(self, session_id: str, text: str) -> None:
        """Deliver a prompt; returns immediately, never waits for the turn."""
        if text.lstrip().startswith("-"):
            # The CLI would read it as a flag and silently do nothing.
            raise InputUnavailable(
                "prompts starting with '-' are not supported yet"
            )
        resolved = shutil.which(self._binary)
        if resolved is None:
            raise InputUnavailable(
                f"'{self._binary}' is not on PATH — session input needs the "
                "OpenCode CLI"
            )
        # NB: Popen needs the *resolved* path — on Windows the CLI is an npm
        # shim (opencode.cmd) that CreateProcess cannot find by bare name.
        argv = [
            resolved, "run", "--session", session_id, "--format", "json", text
        ]
        # The CLI streams the turn as JSON events; keep them for debugging.
        # Appending per session keeps the number of files bounded.
        target = (
            paths.log_dir() / f"opencode-input-{_slug(session_id)}.log"
        )
        paths.ensure_dir(target.parent)
        process = self._spawn(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=target.open("ab"),
            stderr=subprocess.STDOUT,
        )
        self._running[session_id] = process
        logger.info("prompt queued for opencode session %s (pid %s)",
                    session_id, getattr(process, "pid", "?"))


def _slug(session_id: str) -> str:
    keep = "".join(
        character if character.isalnum() else "-" for character in session_id
    )
    return keep.strip("-")[:48] or "session"
