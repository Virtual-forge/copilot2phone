"""CLI-level tests for ``agentd run --tunnel`` and ``agentd tunnel``.

These never touch the network or spawn ngrok: the tunnel module and uvicorn are
both stubbed, so the tests assert on what the CLI *decides* to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn
from typer.testing import CliRunner

from agentd import cli as cli_mod
from agentd import tunnel as tunnel_mod
from agentd.config import Config

runner = CliRunner()


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _session(url: str = "https://abc.ngrok-free.app") -> tunnel_mod.TunnelSession:
    return tunnel_mod.TunnelSession(
        tunnel=tunnel_mod.Tunnel(public_url=url), process=FakeProcess()
    )


def _config_with_provider(provider: str) -> Config:
    config = Config()
    config.tunnel.provider = provider
    return config


@pytest.fixture
def no_uvicorn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the uvicorn.run call instead of actually serving."""
    captured: dict[str, Any] = {}

    def fake_run(app: Any, host: str, port: int, **kwargs: Any) -> None:
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return captured


# --------------------------------------------------------------------------
# agentd run --tunnel
# --------------------------------------------------------------------------


def test_run_with_tunnel_prints_the_public_phone_url(
    home: Path, no_uvicorn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tunnel_mod, "start", lambda *a, **k: _session())

    result = runner.invoke(cli_mod.app, ["run", "--tunnel"])

    assert result.exit_code == 0
    assert "https://abc.ngrok-free.app/#t=" in result.output
    assert "from anywhere" in result.output


def test_run_with_tunnel_keeps_the_daemon_on_loopback(
    home: Path, no_uvicorn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """ngrok forwards to 127.0.0.1, so --tunnel must not open the LAN."""
    monkeypatch.setattr(tunnel_mod, "start", lambda *a, **k: _session())

    result = runner.invoke(cli_mod.app, ["run", "--tunnel"])

    assert result.exit_code == 0
    assert no_uvicorn["host"] == "127.0.0.1"


def test_run_with_tunnel_stops_the_tunnel_on_exit(
    home: Path, no_uvicorn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    session = _session()
    monkeypatch.setattr(tunnel_mod, "start", lambda *a, **k: session)

    runner.invoke(cli_mod.app, ["run", "--tunnel"])

    assert session.process is not None
    assert session.process.terminated is True


def test_run_with_tunnel_still_serves_when_ngrok_is_missing(
    home: Path, no_uvicorn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    def boom(*args: Any, **kwargs: Any) -> None:
        raise tunnel_mod.TunnelError("ngrok is not installed or not on PATH.")

    monkeypatch.setattr(tunnel_mod, "start", boom)

    result = runner.invoke(cli_mod.app, ["run", "--tunnel"])

    assert result.exit_code == 0
    assert "not installed" in result.output
    # The daemon must still come up: the tunnel is a convenience, not a gate.
    assert no_uvicorn["host"] == "127.0.0.1"


def test_run_with_tunnel_rejects_an_unknown_provider(
    home: Path, no_uvicorn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: _config_with_provider("cloudflared")
    )

    result = runner.invoke(cli_mod.app, ["run", "--tunnel"])

    assert result.exit_code == 0
    assert "unknown provider" in result.output


# --------------------------------------------------------------------------
# agentd tunnel
# --------------------------------------------------------------------------


def test_tunnel_command_prints_the_phone_url(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        tunnel_mod,
        "read_tunnel",
        lambda *a, **k: tunnel_mod.Tunnel(
            public_url="https://abc.ngrok-free.app", local_url="http://localhost:47800"
        ),
    )

    result = runner.invoke(cli_mod.app, ["tunnel"])

    assert result.exit_code == 0
    assert "https://abc.ngrok-free.app" in result.output
    assert "http://localhost:47800" in result.output
    assert "/#t=" in result.output


def test_tunnel_command_token_only_prints_just_the_token(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        tunnel_mod,
        "read_tunnel",
        lambda *a, **k: tunnel_mod.Tunnel(public_url="https://abc.ngrok-free.app"),
    )

    result = runner.invoke(cli_mod.app, ["tunnel", "--token-only"])

    assert result.exit_code == 0
    assert "ngrok" not in result.output
    assert result.output.strip()


def test_tunnel_command_explains_when_ngrok_is_missing(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: False)

    result = runner.invoke(cli_mod.app, ["tunnel"])

    assert result.exit_code == 1
    assert "no ngrok tunnel found" in result.output
    assert "ngrok.com/download" in result.output


def test_tunnel_command_suggests_how_to_start_one(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)

    result = runner.invoke(cli_mod.app, ["tunnel"])

    assert result.exit_code == 1
    assert "agentd run --tunnel" in result.output
