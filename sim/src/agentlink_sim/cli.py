"""``agentlink-sim``: a terminal stand-in for the phone.

Slice 1 talks straight to the daemon's loopback API. Slice 2 swaps the
transport for the relay without changing the commands.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import typer

from agentd.config import Config, load_config
from agentd.local_api import load_or_create_token
from agentd.protocol import ApprovalRecord, Decision

app = typer.Typer(
    help="AgentLink simulator: approve agent actions from a terminal.",
    no_args_is_help=True,
    add_completion=False,
)

_STATE_COLOURS = {
    "pending": "yellow",
    "allowed": "green",
    "denied": "red",
    "expired": "magenta",
    "cancelled": "bright_black",
}
_HEADER = f"{'id':<8}  {'agent':<6} {'risk':<6} {'state':<9} {'tool':<18} summary"


def _client(config: Config) -> httpx.Client:
    return httpx.Client(
        base_url=config.base_url,
        headers={"Authorization": f"Bearer {load_or_create_token()}"},
        timeout=30.0,
    )


def _fail(exc: Exception, config: Config) -> None:
    typer.secho(f"cannot reach agentd at {config.base_url}: {exc}", fg="red")
    raise typer.Exit(code=1)


def _print_row(record: ApprovalRecord) -> None:
    colour = _STATE_COLOURS.get(record.state.value, "white")
    typer.secho(
        f"{record.approval_id[:8]}  "
        f"{record.agent_type.value:<6} "
        f"{record.risk.level.value:<6} "
        f"{record.state.value:<9} "
        f"{record.tool.name:<18} "
        f"{record.action.summary[:60]}",
        fg=colour,
    )


def _fetch(config: Config, path: str, params: dict[str, Any] | None = None) -> Any:
    with _client(config) as client:
        response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()


def _resolve(config: Config, prefix: str) -> str:
    """Allow short ids on the command line."""
    if len(prefix) >= 36:
        return prefix
    try:
        records = _fetch(config, "/v1/approvals", {"limit": 200})
    except httpx.HTTPError:
        return prefix
    matches = [r["approval_id"] for r in records if r["approval_id"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        typer.secho(f"no approval matching '{prefix}'", fg="red")
        raise typer.Exit(code=1)
    typer.secho(f"'{prefix}' is ambiguous ({len(matches)} matches)", fg="red")
    raise typer.Exit(code=1)


def _decide(prefix: str, decision: Decision, reason: str | None) -> None:
    config = load_config()
    approval_id = _resolve(config, prefix)
    try:
        with _client(config) as client:
            response = client.post(
                f"/v1/approvals/{approval_id}/decision",
                json={
                    "decision": decision.value,
                    "reason": reason,
                    "decided_by": "sim",
                },
            )
            if response.status_code == 409:
                typer.secho(f"cannot decide: {response.json().get('detail')}", fg="red")
                raise typer.Exit(code=1)
            response.raise_for_status()
            record = ApprovalRecord.model_validate(response.json())
    except httpx.HTTPError as exc:
        _fail(exc, config)
        return
    colour = "green" if decision is Decision.ALLOW else "red"
    typer.secho(f"{record.state.value}: {record.approval_id}", fg=colour)


@app.command("list")
def list_approvals(
    agent: str = typer.Option(None, "--agent", "-a", help="codex"),
    state: str = typer.Option(None, "--state", "-s", help="pending/allowed/denied/expired"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum rows"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """List approvals, newest first."""
    config = load_config()
    params: dict[str, Any] = {"limit": limit}
    if agent:
        params["agent_type"] = agent
    if state:
        params["state"] = state
    try:
        records = _fetch(config, "/v1/approvals", params)
    except httpx.HTTPError as exc:
        _fail(exc, config)
        return

    if as_json:
        typer.echo(json.dumps(records, indent=2))
        return
    if not records:
        typer.echo("no approvals")
        return
    typer.secho(_HEADER, fg="bright_black")
    for item in records:
        _print_row(ApprovalRecord.model_validate(item))


@app.command()
def show(
    approval_id: str = typer.Argument(..., help="Approval id (or unique prefix)"),
) -> None:
    """Show one approval in full."""
    config = load_config()
    try:
        record = ApprovalRecord.model_validate(
            _fetch(config, f"/v1/approvals/{_resolve(config, approval_id)}")
        )
    except httpx.HTTPError as exc:
        _fail(exc, config)
        return
    typer.echo(json.dumps(record.model_dump(mode="json"), indent=2))


@app.command()
def approve(
    approval_id: str = typer.Argument(..., help="Approval id (or unique prefix)"),
    reason: str = typer.Option(None, "--reason", "-r", help="Optional note"),
) -> None:
    """Allow a pending action."""
    _decide(approval_id, Decision.ALLOW, reason)


@app.command()
def deny(
    approval_id: str = typer.Argument(..., help="Approval id (or unique prefix)"),
    reason: str = typer.Option(None, "--reason", "-r", help="Optional note"),
) -> None:
    """Deny a pending action."""
    _decide(approval_id, Decision.DENY, reason)


@app.command()
def watch(
    interval: float = typer.Option(2.0, "--interval", help="Poll interval in seconds"),
    once: bool = typer.Option(False, "--once", help="Poll once and exit"),
) -> None:
    """Poll for new pending approvals and print them."""
    config = load_config()
    seen: set[str] = set()
    typer.secho(f"watching {config.base_url} (Ctrl+C to stop)", fg="bright_black")
    try:
        while True:
            try:
                payload = _fetch(config, "/v1/approvals", {"state": "pending", "limit": 100})
                records = [ApprovalRecord.model_validate(item) for item in payload]
            except httpx.HTTPError as exc:
                typer.secho(f"  ! {exc}", fg="red")
                records = []
            for record in records:
                if record.approval_id in seen:
                    continue
                seen.add(record.approval_id)
                typer.echo("")
                typer.secho(f"NEW APPROVAL  {record.approval_id}", fg="cyan")
                _print_row(record)
                if record.action.command:
                    typer.secho(f"    $ {record.action.command}", fg="white")
                typer.secho(
                    f"    agentlink-sim approve {record.approval_id[:8]}"
                    f"   |   agentlink-sim deny {record.approval_id[:8]}",
                    fg="bright_black",
                )
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("")


@app.command()
def status() -> None:
    """Show daemon status."""
    config = load_config()
    try:
        payload = _fetch(config, "/v1/status")
    except httpx.HTTPError as exc:
        _fail(exc, config)
        return
    typer.echo(json.dumps(payload, indent=2))


@app.command()
def away(
    off: bool = typer.Option(False, "--off", help="Leave away mode"),
) -> None:
    """Toggle away mode."""
    config = load_config()
    enabled = not off
    try:
        with _client(config) as client:
            response = client.post("/v1/away", params={"enabled": enabled})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        _fail(exc, config)
        return
    typer.echo(f"away mode: {'on' if enabled else 'off'}")


def main() -> int:
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

