"""Shared pytest fixtures."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from httpx import ASGITransport, AsyncClient

from agentd.config import Config
from agentd.local_api import AppContext, build_context, create_app

TEST_TOKEN = "test-token"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate all AgentLink state in a temp directory."""
    monkeypatch.setenv("AGENTLINK_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def ctx(home: Path) -> AppContext:
    context = await build_context(
        Config(), db_path=home / "agentd.db", token=TEST_TOKEN
    )
    # Manager-level tests describe the phone-gating behaviour; the
    # remote-off defer path (D-028) is exercised explicitly per test.
    context.approvals.remote = True
    try:
        yield context
    finally:
        await context.db.close()


@pytest_asyncio.fixture
async def client(ctx: AppContext) -> AsyncClient:
    app = create_app(ctx)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://agentd",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    ) as http:
        yield http


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_daemon(home: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Run a real agentd over HTTP so the hook CLI can be tested end to end."""
    port = free_port()
    config = Config()
    config.server.port = port
    config.agents.codex.hook_timeout_seconds = 10

    # Isolate the daemon's transcript readers from the *real* agents on this
    # machine: the default watcher would otherwise ingest the user's live
    # Codex/OpenCode sessions into the test daemon, and their stray change
    # notices race the tests that listen to the stream.
    monkeypatch.setenv("CODEX_HOME", str(home / "codex-home"))
    monkeypatch.setenv("OPENCODE_DB", str(home / "opencode.db"))

    # The hook CLI reads config and token from AGENTLINK_HOME.
    (home / "config.toml").write_text(
        f'[server]\nhost = "127.0.0.1"\nport = {port}\n', encoding="utf-8"
    )
    (home / "local_api_token").write_text(TEST_TOKEN, encoding="utf-8")

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config=config),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("agentd did not start in time")

    # The e2e suite describes phone gating; a fresh daemon defaults to
    # desktop-native approvals (D-028), so switch it on explicitly.
    httpx.post(
        f"http://127.0.0.1:{port}/v1/remote",
        params={"enabled": "true"},
        headers=auth_headers(),
        timeout=5.0,
    )

    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def wait_for_pending(port: int, timeout: float = 10.0) -> str:
    """Block until a pending approval shows up; return its id."""
    return str(wait_for_pending_many(port, 1, timeout)[0]["approval_id"])


def wait_for_pending_many(port: int, count: int, timeout: float = 10.0) -> list[dict]:
    """Block until ``count`` pending approvals exist; return them."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = httpx.get(
            f"http://127.0.0.1:{port}/v1/approvals",
            params={"state": "pending"},
            headers=auth_headers(),
            timeout=5.0,
        )
        response.raise_for_status()
        items = response.json()
        if len(items) >= count:
            return list(items)
        time.sleep(0.05)
    raise AssertionError(f"expected {count} pending approval(s)")



def decide(port: int, approval_id: str, decision: str, reason: str | None = None) -> dict:
    response = httpx.post(
        f"http://127.0.0.1:{port}/v1/approvals/{approval_id}/decision",
        json={"decision": decision, "reason": reason, "decided_by": "test"},
        headers=auth_headers(),
        timeout=5.0,
    )
    response.raise_for_status()
    return dict(response.json())
