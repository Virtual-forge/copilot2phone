"""``agentd`` command line (SPEC.md §15)."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import httpx
import typer

from . import __version__, netinfo, paths, tunnel as tunnel_mod
from .config import Config, load_config, write_default_config
from .local_api import create_app, load_or_create_token


app = typer.Typer(
    help="AgentLink PC daemon: remote approvals for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)


def _configure_logging(level: str) -> None:
    """Mirror daemon logs to ``%USERPROFILE%\\.agentlink\\logs\\agentd.log``."""
    paths.ensure_dir(paths.log_dir())
    handler = logging.FileHandler(paths.log_path(), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addHandler(handler)



def _client(config: Config, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=config.base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )


def _probe(config: Config) -> bool:
    try:
        response = httpx.get(f"{config.base_url}/v1/health", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _print_lan_hint(config: Config, token: str) -> None:
    """Explain how to reach the daemon from a phone on the same Wi-Fi."""
    url = netinfo.phone_url(config.server.port, token)
    if url:
        typer.secho(f"  phone     : {url}", fg="green", bold=True)
        typer.echo("              open that on your phone (same Wi-Fi)")
    else:
        typer.secho("  phone     : could not detect a LAN address", fg="yellow")
    typer.secho(
        "  warning   : bound to all interfaces - anyone on your LAN with the "
        "token can approve actions",
        fg="yellow",
    )
    typer.echo(
        "              if the phone cannot connect, allow the port once:\n"
        f'              netsh advfirewall firewall add rule name="AgentLink" '
        f"dir=in action=allow protocol=TCP localport={config.server.port}"
    )


def _start_tunnel(config: Config, token: str) -> tunnel_mod.TunnelSession | None:
    """Start the public tunnel, or explain why we could not."""
    cfg = config.tunnel
    if cfg.provider != "ngrok":
        typer.secho(
            f"  tunnel    : unknown provider '{cfg.provider}' (only ngrok is "
            "supported)",
            fg="red",
        )
        return None

    typer.echo("  tunnel    : starting ngrok...")
    try:
        session = tunnel_mod.start(
            config.server.port,
            authtoken=cfg.authtoken,
            region=cfg.region,
            domain=cfg.domain,
            api_url=cfg.api_url,
        )
    except tunnel_mod.TunnelError as exc:
        typer.secho(f"  tunnel    : {exc}", fg="red")
        return None

    if session.reused:
        typer.echo("  tunnel    : reusing the ngrok agent that is already running")
    typer.secho(
        f"  phone     : {session.tunnel.phone_url(token)}", fg="green", bold=True
    )
    typer.echo("              open that on your phone, from anywhere")
    typer.secho(
        "  warning   : this URL is on the public internet - anyone with the "
        "token can approve actions",
        fg="yellow",
    )
    typer.echo(
        "              ngrok's free tier shows a one-time browser warning page;\n"
        "              tap through it once and the app loads."
    )
    return session


@app.command()
def run(
    host: str = typer.Option(None, help="Bind address (default: from config)"),
    port: int = typer.Option(None, help="Bind port (default: from config)"),
    log_level: str = typer.Option(None, help="uvicorn log level"),
    lan: bool = typer.Option(
        False, "--lan", help="Bind all interfaces so your phone can reach the UI"
    ),
    tunnel: bool = typer.Option(
        False, "--tunnel", help="Expose the daemon through ngrok (works off-LAN)"
    ),
) -> None:
    """Run the daemon in the foreground."""
    import uvicorn

    config = load_config()
    if lan:
        config.server.host = "0.0.0.0"
    if host:
        config.server.host = host
    if port:
        config.server.port = port

    paths.ensure_home()
    write_default_config()
    token = load_or_create_token()
    _configure_logging(log_level or config.logging.level)

    typer.echo(f"agentd {__version__}")
    typer.echo(f"  state dir : {paths.agentlink_home()}")
    typer.echo(f"  listening : {config.base_url}")
    typer.echo(f"  token     : {token}")
    typer.echo(f"  log       : {paths.log_path()}")

    if config.server.host == "0.0.0.0":
        _print_lan_hint(config, token)

    # ngrok forwards to 127.0.0.1, so --tunnel never needs --lan.
    session: tunnel_mod.TunnelSession | None = None
    if tunnel:
        session = _start_tunnel(config, token)

    typer.echo("  press Ctrl+C to stop")

    try:
        uvicorn.run(
            create_app(config=config),
            host=config.server.host,
            port=config.server.port,
            log_level=log_level or config.logging.level,
            log_config=None,
        )
    finally:
        if session is not None and session.owned:
            typer.echo("  stopping tunnel")
            session.stop()


@app.command()
def tunnel(
    token_only: bool = typer.Option(
        False, "--token-only", help="Print just the token, for scripting"
    ),
) -> None:
    """Show the public URL of a running ngrok tunnel.

    Use this when you started ngrok yourself in another terminal; ``agentd run
    --tunnel`` starts one for you instead.
    """
    config = load_config()
    token = load_or_create_token()
    found = tunnel_mod.read_tunnel(config.tunnel.api_url)

    if found is None:
        typer.secho("no ngrok tunnel found", fg="yellow")
        if not tunnel_mod.ngrok_installed():
            typer.echo(tunnel_mod.INSTALL_HINT)
        else:
            typer.echo(f"  start one with : ngrok http {config.server.port}")
            typer.echo("  or just run    : agentd run --tunnel")
        raise typer.Exit(code=1)

    if token_only:
        typer.echo(token)
        return

    local = found.local_url or f"127.0.0.1:{config.server.port}"
    typer.echo(f"  public    : {found.public_url}")
    typer.echo(f"  forwards  : {local}")
    typer.secho(f"  phone     : {found.phone_url(token)}", fg="green", bold=True)


@app.command()
def status() -> None:
    """Show daemon status as JSON."""
    config = load_config()
    token = load_or_create_token()
    try:
        with _client(config, token) as client:
            response = client.get("/v1/status")
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
    except httpx.HTTPError as exc:
        typer.secho(f"agentd is not reachable at {config.base_url}: {exc}", fg="red")
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(payload, indent=2))


@app.command()
def doctor() -> None:
    """Check the local setup and report anything missing."""
    config = load_config()
    home = paths.agentlink_home()

    typer.echo(f"agentd {__version__} on Python {sys.version.split()[0]}")
    typer.echo(f"  state dir : {home} [{'ok' if home.exists() else 'missing'}]")

    config_file = paths.config_path()
    typer.echo(
        f"  config    : {config_file} "
        f"[{'ok' if config_file.exists() else 'using built-in defaults'}]"
    )

    token_file = paths.token_path()
    typer.echo(
        f"  token     : {token_file} "
        f"[{'ok' if token_file.exists() else 'not created yet'}]"
    )

    db_file = paths.db_path()
    typer.echo(
        f"  database  : {db_file} [{'ok' if db_file.exists() else 'not created yet'}]"
    )

    reachable = _probe(config)
    typer.echo(
        f"  daemon    : {config.base_url} "
        f"[{'running' if reachable else 'not running'}]"
    )

    if tunnel_mod.ngrok_installed():
        live = tunnel_mod.read_tunnel(config.tunnel.api_url)
        detail = f"tunnel up at {live.public_url}" if live else "installed, no tunnel"
        typer.echo(f"  ngrok     : [ok - {detail}]")
    else:
        typer.echo("  ngrok     : [not installed - needed for --tunnel]")

    for agent in ("cline", "codex"):
        agent_cfg = config.agents.for_agent(agent)
        typer.echo(
            f"  {agent:<9} : enabled={agent_cfg.enabled} "
            f"hook_timeout={agent_cfg.hook_timeout_seconds}s"
        )

    if not reachable:
        typer.secho("start it with: agentd run", fg="yellow")


@app.command()
def away(
    off: bool = typer.Option(False, "--off", help="Leave away mode"),
) -> None:
    """Toggle away mode (approvals still fail closed)."""
    config = load_config()
    token = load_or_create_token()
    enabled = not off
    try:
        with _client(config, token) as client:
            response = client.post("/v1/away", params={"enabled": enabled})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        typer.secho(f"agentd is not reachable at {config.base_url}: {exc}", fg="red")
        raise typer.Exit(code=1) from None
    typer.echo(f"away mode: {'on' if enabled else 'off'}")


@app.command()
def logs(
    lines: int = typer.Option(50, help="How many lines to show"),
) -> None:
    """Show the tail of the daemon log."""
    target = paths.log_path()
    if not target.exists():
        typer.secho(f"no log file at {target}", fg="yellow")
        raise typer.Exit(code=1)
    content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        typer.echo(line)


@app.command()
def pair() -> None:
    """Pair a phone with this PC (arrives with the relay in slice 2)."""
    typer.secho("pairing is not implemented yet (slice 2: relay + crypto)", fg="yellow")
    raise typer.Exit(code=1)


@app.command()
def install() -> None:
    """Install the agent hooks (arrives with the adapters in slice 3)."""
    typer.secho("hook installation is not implemented yet (slice 3)", fg="yellow")
    raise typer.Exit(code=1)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
