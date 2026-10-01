"""Protocol models shared by the daemon, the adapters and the simulator.

Mirrors SPEC.md §7.4. Slice 1 uses the local subset; the relay envelope types
are added in slice 2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Serialise a datetime as RFC3339 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse an RFC3339 timestamp produced by :func:`iso`."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class AgentType(str, Enum):
    CLINE = "cline"
    CODEX = "codex"


class ToolKind(str, Enum):
    COMMAND = "command"
    FILE_EDIT = "file_edit"
    FILE_READ = "file_read"
    FILE_DELETE = "file_delete"
    NETWORK = "network"
    OTHER = "other"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalState(str, Enum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class Tool(BaseModel):
    name: str
    kind: ToolKind = ToolKind.OTHER


class ActionDetail(BaseModel):
    summary: str = ""
    command: str | None = None
    cwd: str | None = None
    paths: list[str] = Field(default_factory=list)
    diff_preview: str | None = None


class Risk(BaseModel):
    level: RiskLevel = RiskLevel.MEDIUM
    reasons: list[str] = Field(default_factory=list)


class Action(BaseModel):
    """A normalised agent action awaiting a decision."""

    agent_type: AgentType
    session_id: str
    workspace_path: str
    tool: Tool
    action: ActionDetail = Field(default_factory=ActionDetail)
    raw: dict[str, Any] | None = None


class ApprovalRecord(BaseModel):
    approval_id: str
    agent_type: AgentType
    session_id: str
    workspace_path: str
    tool: Tool
    action: ActionDetail
    risk: Risk
    action_hash: str
    state: ApprovalState
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decision: Decision | None = None
    decision_reason: str | None = None
    decided_by: str | None = None


class ApprovalOutcome(BaseModel):
    """What the hook receives back from the daemon."""

    approval_id: str
    agent_type: AgentType
    state: ApprovalState
    decision: Decision | None
    reason: str | None = None
    risk: Risk
    waited_seconds: float = 0.0
    policy_effect: Effect | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class DecisionRequest(BaseModel):
    decision: Decision
    reason: str | None = None
    decided_by: str = "sim"


class SessionRecord(BaseModel):
    session_id: str
    agent_type: AgentType
    workspace_path: str
    started_at: datetime
    ended_at: datetime | None = None
    state: str = "idle"


class PolicyResult(BaseModel):
    effect: Effect
    risk: Risk
    matched_rule: str | None = None
