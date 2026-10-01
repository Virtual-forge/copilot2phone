"""Tests for the ngrok tunnel wrapper.

Nothing here needs ngrok installed: the process and the ngrok local API are
both faked, so the whole module is exercised on a plain localhost machine.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentd import tunnel as tunnel_mod


class FakeProcess:
    """Stand-in for ``subprocess.Popen`` that never actually runs anything."""

    def __init__(self, *, exit_code: int | None = None) -> None:
        self.returncode = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return int(self.returncode or 0)


def _payload(*urls: str, addr: str = "http://localhost:47800") -> dict[str, Any]:
    return {"tunnels": [{"public_url": url, "config": {"addr": addr}} for url in urls]}


# --------------------------------------------------------------------------
# _pick_tunnel / read_tunnel
# --------------------------------------------------------------------------


def test_pick_tunnel_prefers_https_over_http():
    picked = tunnel_mod._pick_tunnel(
        _payload("http://a.ngrok.io", "https://b.ngrok.io")
    )
    assert picked is not None
    assert picked.public_url == "https://b.ngrok.io"


def test_pick_tunnel_falls_back_to_http_when_only_option():
    picked = tunnel_mod._pick_tunnel(_payload("http://a.ngrok.io"))
    assert picked is not None
    assert picked.public_url == "http://a.ngrok.io"
    assert picked.local_url == "http://localhost:47800"


def test_pick_tunnel_returns_none_when_no_tunnels():
    assert tunnel_mod._pick_tunnel({"tunnels": []}) is None
    assert tunnel_mod._pick_tunnel({}) is None


def test_pick_tunnel_skips_entries_without_a_public_url():
    payload = {"tunnels": [{"config": {"addr": "x"}}, {"public_url": "https://ok"}]}
    picked = tunnel_mod._pick_tunnel(payload)
    assert picked is not None
    assert picked.public_url == "https://ok"


def test_read_tunnel_parses_a_live_api_response(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, timeout: float = 2.0) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload("https://abc.ngrok-free.app"),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(tunnel_mod.httpx, "get", fake_get)
    found = tunnel_mod.read_tunnel()
    assert found is not None
    assert found.public_url == "https://abc.ngrok-free.app"


def test_read_tunnel_returns_none_when_agent_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_get(url: str, timeout: float = 2.0) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tunnel_mod.httpx, "get", fake_get)
    assert tunnel_mod.read_tunnel() is None


def test_read_tunnel_returns_none_on_non_json_body(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, timeout: float = 2.0) -> httpx.Response:
        return httpx.Response(
            200, text="<html>not json</html>", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(tunnel_mod.httpx, "get", fake_get)
    assert tunnel_mod.read_tunnel() is None


def test_read_tunnel_returns_none_on_error_status(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, timeout: float = 2.0) -> httpx.Response:
        return httpx.Response(500, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(tunnel_mod.httpx, "get", fake_get)
    assert tunnel_mod.read_tunnel() is None


# --------------------------------------------------------------------------
# Tunnel.phone_url
# --------------------------------------------------------------------------


def test_phone_url_puts_the_token_in_the_fragment():
    t = tunnel_mod.Tunnel(public_url="https://abc.ngrok-free.app")
    assert t.phone_url("secret") == "https://abc.ngrok-free.app/#t=secret"


def test_phone_url_tolerates_a_trailing_slash():
    t = tunnel_mod.Tunnel(public_url="https://abc.ngrok-free.app/")
    assert t.phone_url("secret") == "https://abc.ngrok-free.app/#t=secret"


# --------------------------------------------------------------------------
# ngrok_installed
# --------------------------------------------------------------------------


def test_ngrok_installed_true_when_on_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: "C:/ngrok.exe")
    assert tunnel_mod.ngrok_installed() is True


def test_ngrok_installed_false_when_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
    assert tunnel_mod.ngrok_installed() is False


# --------------------------------------------------------------------------
# start
# --------------------------------------------------------------------------


def test_start_reuses_an_already_running_agent(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    existing = tunnel_mod.Tunnel(public_url="https://already.ngrok-free.app")
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: existing)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("should not spawn a second ngrok agent")

    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", explode)

    session = tunnel_mod.start(47800)
    assert session.reused is True
    assert session.owned is False
    assert session.tunnel.public_url == "https://already.ngrok-free.app"


def test_start_raises_a_helpful_error_when_ngrok_is_missing(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: False)

    with pytest.raises(tunnel_mod.TunnelError) as excinfo:
        tunnel_mod.start(47800)

    message = str(excinfo.value)
    assert "not installed" in message
    assert "ngrok.com/download" in message


def test_start_spawns_ngrok_and_waits_for_the_public_url(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    process = FakeProcess()
    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        calls.append(args)
        return process

    # First call (pre-flight) finds nothing; the poll then finds the tunnel.
    responses: list[tunnel_mod.Tunnel | None] = [
        None,
        tunnel_mod.Tunnel(public_url="https://new.ngrok-free.app"),
    ]
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", fake_popen)

    session = tunnel_mod.start(47800, region="eu", domain="my.example.com")

    assert session.owned is True
    assert session.reused is False
    assert session.tunnel.public_url == "https://new.ngrok-free.app"
    assert calls == [
        ["ngrok", "http", "47800", "--region", "eu", "--domain", "my.example.com"]
    ]


def test_start_passes_an_explicit_authtoken(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        calls.append(args)
        return FakeProcess()

    responses: list[tunnel_mod.Tunnel | None] = [
        None,
        tunnel_mod.Tunnel(public_url="https://new.ngrok-free.app"),
    ]
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", fake_popen)

    tunnel_mod.start(47800, authtoken="tok_123")
    assert calls[0] == ["ngrok", "http", "47800", "--authtoken", "tok_123"]


def test_start_raises_when_ngrok_exits_immediately(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(
        tunnel_mod.subprocess, "Popen", lambda *a, **k: FakeProcess(exit_code=1)
    )

    with pytest.raises(tunnel_mod.TunnelError) as excinfo:
        tunnel_mod.start(47800)

    assert "exited immediately" in str(excinfo.value)


def test_start_times_out_and_cleans_up_the_process(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    process = FakeProcess()
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", lambda *a, **k: process)

    with pytest.raises(tunnel_mod.TunnelError) as excinfo:
        tunnel_mod.start(47800, timeout=0.3)

    assert "did not report a public URL" in str(excinfo.value)
    assert process.terminated is True


def test_start_writes_ngrok_output_to_the_log_dir(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    captured: dict[str, Any] = {}

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        captured.update(kwargs)
        return FakeProcess()

    responses: list[tunnel_mod.Tunnel | None] = [
        None,
        tunnel_mod.Tunnel(public_url="https://new.ngrok-free.app"),
    ]
    monkeypatch.setattr(tunnel_mod, "read_tunnel", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", fake_popen)

    tunnel_mod.start(47800)

    assert captured["stderr"] == subprocess.STDOUT
    assert captured["stdin"] == subprocess.DEVNULL
    assert (home / "logs" / "ngrok.log").exists()


# --------------------------------------------------------------------------
# TunnelSession.stop
# --------------------------------------------------------------------------


def test_stop_terminates_a_process_we_own():
    process = FakeProcess()
    session = tunnel_mod.TunnelSession(
        tunnel=tunnel_mod.Tunnel(public_url="https://x"), process=process
    )
    session.stop()
    assert process.terminated is True


def test_stop_is_a_noop_for_a_reused_tunnel():
    session = tunnel_mod.TunnelSession(
        tunnel=tunnel_mod.Tunnel(public_url="https://x"), reused=True
    )
    session.stop()  # must not raise


def test_stop_is_a_noop_when_the_process_already_exited():
    process = FakeProcess(exit_code=0)
    session = tunnel_mod.TunnelSession(
        tunnel=tunnel_mod.Tunnel(public_url="https://x"), process=process
    )
    session.stop()
    assert process.terminated is False


# --------------------------------------------------------------------------
# Integration: real HTTP against a stand-in for ngrok's local API
# --------------------------------------------------------------------------


class _NgrokApi:
    """A real HTTP server that mimics ngrok's ``/api/tunnels`` endpoint.

    Answers 404 (as ngrok does before a tunnel exists) until :meth:`go_live`
    is called, so tests never depend on wall-clock timing.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        import http.server
        import json
        import threading

        self.ready = False
        self._payload = payload

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(inner) -> None:  # noqa: N805 - http.server signature
                if not self.ready:
                    inner.send_response(404)
                    inner.end_headers()
                    return
                body = json.dumps(self._payload).encode()
                inner.send_response(200)
                inner.send_header("Content-Type", "application/json")
                inner.send_header("Content-Length", str(len(body)))
                inner.end_headers()
                inner.wfile.write(body)

            def log_message(inner, *args: Any) -> None:  # noqa: N805
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def go_live(self) -> None:
        """Start reporting the tunnel, like ngrok once it is connected."""
        self.ready = True

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/api/tunnels"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_read_tunnel_over_real_http():
    api = _NgrokApi(_payload("https://real.ngrok-free.app"))
    try:
        # ngrok answers 404 until its tunnel is up.
        assert tunnel_mod.read_tunnel(api.api_url) is None

        api.go_live()
        found = tunnel_mod.read_tunnel(api.api_url)
        assert found is not None
        assert found.public_url == "https://real.ngrok-free.app"
        assert found.local_url == "http://localhost:47800"
    finally:
        api.close()


def test_start_polls_a_real_api_until_the_tunnel_appears(
    monkeypatch: pytest.MonkeyPatch, home: Path
):
    """The poll loop speaks real HTTP; only the ngrok process is faked."""
    api = _NgrokApi(_payload("https://polled.ngrok-free.app"))

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        # The "agent" publishes its tunnel shortly after launch. The pre-flight
        # check already ran, so start() must poll until this fires.
        threading.Timer(0.3, api.go_live).start()
        return FakeProcess()

    monkeypatch.setattr(tunnel_mod, "ngrok_installed", lambda: True)
    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", fake_popen)

    try:
        session = tunnel_mod.start(47800, api_url=api.api_url, timeout=10.0)
        assert session.owned is True
        assert session.reused is False
        assert session.tunnel.public_url == "https://polled.ngrok-free.app"
        assert session.tunnel.phone_url("tok") == "https://polled.ngrok-free.app/#t=tok"
    finally:
        api.close()
