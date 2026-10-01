"""Filesystem locations for AgentLink state.

Everything lives under ``%USERPROFILE%\\.agentlink`` unless ``AGENTLINK_HOME``
is set (used by tests and by ``agentd doctor``).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = ".agentlink"
ENV_HOME = "AGENTLINK_HOME"


def agentlink_home() -> Path:
    """Return the AgentLink state directory."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / APP_DIR_NAME


def config_path() -> Path:
    return agentlink_home() / "config.toml"


def db_path() -> Path:
    return agentlink_home() / "agentd.db"


def token_path() -> Path:
    return agentlink_home() / "local_api_token"


def keys_dir() -> Path:
    return agentlink_home() / "keys"


def log_dir() -> Path:
    return agentlink_home() / "logs"


def log_path() -> Path:
    return log_dir() / "agentd.log"


def probe_dir() -> Path:
    return agentlink_home() / "probe"


def ensure_home() -> Path:
    """Create the state directory if needed and return it."""
    home = agentlink_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
