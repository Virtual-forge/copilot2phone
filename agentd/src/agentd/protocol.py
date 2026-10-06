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
    """Serialise a datetime as RFC3339 UTC.

    Always with microseconds: SQL compares these strings, and
    ``...:00Z`` sorts *after* ``...:00.5Z`` within the same second, which
    would order and expire rows a beat wrong.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


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
    #: Outcome-only (D-028): no approval record is created; the desktop's own
    #: approval UI decides. Never stored in the approvals table.
    DEFERRED = "deferred"


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
    #: True when remote approvals are off (D-028): the daemon created no
    #: approval and blocked for nobody — the hook hands the decision back to
    #: the agent's own approval UI (Codex: ``permissionDecision: "ask"``).
    deferred: bool = False

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


class ActivityKind(str, Enum):
    """What one entry in a session's activity stream represents (SPEC.md §12)."""

    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_PLUMBING = "tool_plumbing"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    FILE_CHANGED = "file_changed"
    ERROR = "error"
    NOTE = "note"


#: Kinds that belong in the chat transcript view (everything else is feed-only).
#:
#: Deliberately absent: the lifecycle markers (``session_*``, ``task_*``) and
#: ``tool_plumbing``. Those are the agent harness talking to itself — a Codex
#: turn boundary, or the ``exec``/``wait`` polling loop behind a single shell
#: command — and rendering them as chat bubbles buries the actual conversation.
#: They stay in the activity feed, which is the unfiltered stream.
CHAT_KINDS: frozenset[ActivityKind] = frozenset(
    {
        ActivityKind.USER_MESSAGE,
        ActivityKind.ASSISTANT_MESSAGE,
        ActivityKind.REASONING,
        ActivityKind.TOOL_CALL,
        ActivityKind.TOOL_RESULT,
        ActivityKind.ERROR,
        ActivityKind.NOTE,
    }
)


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ActivityEvent(BaseModel):
    """One entry in a session's activity stream (SPEC.md §12.1)."""

    event_id: str
    seq: int
    agent_type: AgentType
    session_id: str
    workspace_path: str
    kind: ActivityKind
    role: MessageRole | None = None
    ts: datetime
    summary: str = ""
    text: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class MessageRecord(BaseModel):
    """A chat-shaped view of an activity event."""

    message_id: str
    seq: int
    agent_type: AgentType
    session_id: str
    role: MessageRole
    kind: ActivityKind
    ts: datetime
    text: str = ""
    tool_name: str | None = None
    tool_kind: ToolKind | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    """A session as shown in the phone's session list."""

    session_id: str
    agent_type: AgentType
    workspace_path: str
    title: str = ""
    state: str = "idle"
    source: str = "hook"
    started_at: datetime
    ended_at: datetime | None = None
    last_activity_at: datetime | None = None
    message_count: int = 0
    pending_approvals: int = 0


class SessionDetail(SessionSummary):
    """A session plus its recent transcript and activity."""

    messages: list[MessageRecord] = Field(default_factory=list)
    events: list[ActivityEvent] = Field(default_factory=list)


class ActivityEventIn(BaseModel):
    """An activity event posted to ``POST /v1/events`` by a hook or adapter."""

    agent_type: AgentType
    session_id: str
    workspace_path: str
    kind: ActivityKind
    summary: str = ""
    text: str = ""
    role: MessageRole | None = None
    ts: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None


class PolicyResult(BaseModel):
    effect: Effect
    risk: Risk
    matched_rule: str | None = None
