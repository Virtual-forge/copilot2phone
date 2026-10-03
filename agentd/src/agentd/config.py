"""TOML configuration (SPEC.md §16.1)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from . import paths


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 47800


class RelayConfig(BaseModel):
    url: str = "wss://relay.example.com/v1/ws"
    proxy: str = "system"
    ca_bundle: str = ""


class ApprovalsConfig(BaseModel):
    timeout_seconds: float = 600
    default_expiry_seconds: float = 600
    diff_preview_max_lines: int = 200


class TunnelConfig(BaseModel):
    """Public tunnel settings, used by ``agentd run --tunnel``."""

    provider: str = "ngrok"
    authtoken: str = ""  # blank = use ngrok's own config file
    region: str = ""  # e.g. "eu", "ap"
    domain: str = ""  # reserved domain, if you have one
    api_url: str = "http://127.0.0.1:4040/api/tunnels"


class ActivityConfig(BaseModel):
    heartbeat_seconds: float = 60
    retention_days: int = 7


class NotificationsConfig(BaseModel):
    """Webhook push (P1-a). Empty URL = notifications off."""

    webhook_url: str = ""
    webhook_format: str = "generic"

    @field_validator("webhook_format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        allowed = {"generic", "ntfy"}
        if value not in allowed:
            raise ValueError(f"webhook_format must be one of {sorted(allowed)}")
        return value


class PolicyConfig(BaseModel):
    #: Effect for actions no rule matched: "ask" (default), "deny" or "allow".
    default_effect: str = "ask"

    @field_validator("default_effect")
    @classmethod
    def _known_effect(cls, value: str) -> str:
        allowed = {"ask", "deny", "allow"}
        if value not in allowed:
            raise ValueError(f"default_effect must be one of {sorted(allowed)}")
        return value


class AgentConfig(BaseModel):
    enabled: bool = True
    hook_timeout_seconds: float = 600


class AgentsConfig(BaseModel):
    codex: AgentConfig = Field(default_factory=AgentConfig)
    #: OpenCode is monitor-only (D-025): it has no AgentLink hook, so the
    #: timeout fields are meaningless for it — it is here so `enabled`
    #: shows up in status/doctor like any other agent.
    opencode: AgentConfig = Field(default_factory=AgentConfig)

    def for_agent(self, agent_type: str) -> AgentConfig:
        # Never a raw getattr(): ``for_agent("for_agent")`` must not return
        # this method itself.
        if agent_type not in ("codex", "opencode"):
            return AgentConfig()
        return getattr(self, agent_type)


class LoggingConfig(BaseModel):
    level: str = "info"


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    relay: RelayConfig = Field(default_factory=RelayConfig)
    tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def base_url(self) -> str:
        return f"http://{self.server.host}:{self.server.port}"


DEFAULT_TOML = """\
# AgentLink daemon configuration (SPEC.md §16.1)

[server]
host = "127.0.0.1"
port = 47800

[relay]
url = "wss://relay.example.com/v1/ws"
proxy = "system"            # or an explicit URL
ca_bundle = ""              # optional custom CA for TLS inspection

[tunnel]
provider = "ngrok"          # only ngrok is supported today
authtoken = ""              # blank = use ngrok's own config file
region = ""                 # e.g. "eu", "ap"
domain = ""                 # reserved domain, if you have one
api_url = "http://127.0.0.1:4040/api/tunnels"

[approvals]
timeout_seconds = 600       # aligned with hook timeout (D10)
default_expiry_seconds = 600
diff_preview_max_lines = 200

[notifications]
webhook_url = ""            # empty = off. gets POSTs when things need you
webhook_format = "generic"  # "generic" JSON, or "ntfy" (topic url in webhook_url)

[activity]
heartbeat_seconds = 60
retention_days = 7

[policy]
default_effect = "ask"      # fail closed

[agents.codex]
enabled = true
hook_timeout_seconds = 600

[agents.opencode]
enabled = true                 # monitor-only: no hook, timeouts do not apply

[logging]
level = "info"
"""


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, falling back to defaults when absent."""
    target = path or paths.config_path()
    if not target.exists():
        return Config()
    with target.open("rb") as handle:
        data = tomllib.load(handle)
    return Config.model_validate(data)


def write_default_config(path: Path | None = None, *, overwrite: bool = False) -> Path:
    """Write the default config file. Returns the path written."""
    target = path or paths.config_path()
    paths.ensure_dir(target.parent)
    if target.exists() and not overwrite:
        return target
    target.write_text(DEFAULT_TOML, encoding="utf-8")
    return target
