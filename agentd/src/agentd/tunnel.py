"""Public tunnel support so the phone can reach ``agentd`` off-LAN.

Today this wraps `ngrok <https://ngrok.com>`_. The daemon keeps listening on
loopback; the tunnel agent runs on the same machine and forwards to it, so
exposing the daemon publicly never requires binding it to ``0.0.0.0``.

Everything here degrades gracefully when ngrok is not installed: callers get a
:class:`TunnelError` with an actionable message rather than a traceback.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import paths

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:4040/api/tunnels"
DEFAULT_START_TIMEOUT = 25.0
INSTALL_HINT = (
    "ngrok is not installed or not on PATH.\n"
    "  install:  winget install ngrok.ngrok\n"
    "  or grab:  https://ngrok.com/download\n"
    "  then:     ngrok config add-authtoken <your-token>"
)


class TunnelError(RuntimeError):
    """Raised when a public tunnel cannot be established."""


@dataclass
class Tunnel:
    """A public URL that forwards to the local daemon."""

    public_url: str
    local_url: str = ""
    provider: str = "ngrok"

    def phone_url(self, token: str) -> str:
        """The URL to open on the phone, with the token in the fragment."""
        return f"{self.public_url.rstrip('/')}/#t={token}"


@dataclass
class TunnelSession:
    """A live tunnel plus the process backing it, if we started one."""

    tunnel: Tunnel
    process: subprocess.Popen[bytes] | None = None
    reused: bool = False

    @property
    def owned(self) -> bool:
        """True when we started the tunnel and are responsible for stopping it."""
        return self.process is not None

    def stop(self) -> None:
        """Terminate the tunnel process, if we own it."""
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - slow shutdown
            self.process.kill()


def ngrok_installed() -> bool:
    """True when an ``ngrok`` executable is on PATH."""
    return shutil.which("ngrok") is not None


def _pick_tunnel(payload: dict[str, Any]) -> Tunnel | None:
    """Choose the best tunnel from an ngrok ``/api/tunnels`` response.

    Prefers ``https`` so the bearer token never crosses the network in clear.
    """
    fallback: Tunnel | None = None
    for entry in payload.get("tunnels") or []:
        url = str(entry.get("public_url") or "")
        if not url:
            continue
        config = entry.get("config") or {}
        candidate = Tunnel(
            public_url=url,
            local_url=str(config.get("addr") or ""),
            provider="ngrok",
        )
        if url.startswith("https://"):
            return candidate
        fallback = fallback or candidate
    return fallback


def read_tunnel(api_url: str = DEFAULT_API_URL, timeout: float = 2.0) -> Tunnel | None:
    """Read the public URL from an already-running ngrok agent.

    Returns ``None`` when no agent is running or it has no tunnels yet.
    """
    try:
        response = httpx.get(api_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return _pick_tunnel(payload)


def start(
    port: int,
    *,
    authtoken: str = "",
    region: str = "",
    domain: str = "",
    api_url: str = DEFAULT_API_URL,
    timeout: float = DEFAULT_START_TIMEOUT,
    log_path: Path | None = None,
) -> TunnelSession:
    """Start an ngrok tunnel to ``127.0.0.1:port`` and wait for its public URL.

    If an ngrok agent is already running, its tunnel is reused instead of
    starting a second one (ngrok only allows one agent per API port).
    """
    existing = read_tunnel(api_url)
    if existing is not None:
        logger.info("reusing running ngrok tunnel %s", existing.public_url)
        return TunnelSession(tunnel=existing, reused=True)

    if not ngrok_installed():
        raise TunnelError(INSTALL_HINT)

    args = ["ngrok", "http", str(port)]
    if authtoken:
        args += ["--authtoken", authtoken]
    if region:
        args += ["--region", region]
    if domain:
        args += ["--domain", domain]

    target = log_path or (paths.log_dir() / "ngrok.log")
    paths.ensure_dir(target.parent)
    handle = target.open("ab")
    logger.info("starting ngrok: %s", " ".join(args[:3]))

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            args,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:  # pragma: no cover - depends on the host
        handle.close()
        raise TunnelError(f"could not start ngrok: {exc}") from exc
    # The child inherited its own copy; the parent's is no longer needed and
    # must not leak for the lifetime of the daemon.
    handle.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise TunnelError(
                f"ngrok exited immediately (code {process.returncode}); "
                f"see {target}"
            )
        tunnel = read_tunnel(api_url)
        if tunnel is not None:
            logger.info("ngrok tunnel ready: %s", tunnel.public_url)
            return TunnelSession(tunnel=tunnel, process=process)
        time.sleep(0.25)

    process.terminate()
    raise TunnelError(f"ngrok did not report a public URL within {timeout:.0f}s")
