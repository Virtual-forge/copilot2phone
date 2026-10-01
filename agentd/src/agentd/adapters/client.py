"""Shared HTTP client used by the hook adapters to reach the daemon."""

from __future__ import annotations

from pathlib import Path

import httpx

from .. import paths
from ..config import Config, load_config
from ..protocol import Action, ApprovalOutcome


class DaemonUnavailable(Exception):
    """The daemon could not be reached or refused the request."""


def read_token(path: Path | None = None) -> str:
    """Read the local API token. Raises if the daemon has never run."""
    target = path or paths.token_path()
    if not target.exists():
        raise DaemonUnavailable(
            f"local API token not found at {target}; run 'agentd run' first"
        )
    token = target.read_text(encoding="utf-8").strip()
    if not token:
        raise DaemonUnavailable(f"local API token at {target} is empty")
    return token


def call_daemon(
    action: Action,
    *,
    config: Config | None = None,
    token: str | None = None,
    timeout: float | None = None,
) -> ApprovalOutcome:
    """POST an action to the daemon and block for the decision."""
    cfg = config or load_config()
    bearer = token or read_token()
    budget = timeout if timeout is not None else cfg.approvals.timeout_seconds
    url = f"{cfg.base_url}/v1/approvals"

    try:
        with httpx.Client(timeout=budget + 15.0) as client:
            response = client.post(
                url,
                json=action.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {bearer}"},
            )
    except httpx.HTTPError as exc:
        raise DaemonUnavailable(f"cannot reach agentd at {url}: {exc}") from exc

    if response.status_code == 401:
        raise DaemonUnavailable("agentd rejected the local API token")
    if response.status_code >= 400:
        raise DaemonUnavailable(
            f"agentd returned HTTP {response.status_code}: {response.text[:200]}"
        )
    return ApprovalOutcome.model_validate(response.json())
