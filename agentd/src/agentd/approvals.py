"""Approval state machine with async waiters (SPEC.md §4.2, D10).

The hook blocks here until the phone decides, the approval expires, or the
daemon shuts down. Every path fails closed (D5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from .activity import ActivityManager
from .audit import AuditLog
from .config import Config
from .crypto import action_hash, new_id, redact, redact_text
from .db import Database
from .policy import PolicyEngine
from .protocol import (
    Action,
    ActionDetail,
    ActivityKind,
    AgentType,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalState,
    Decision,
    Effect,
    Risk,
    RiskLevel,
    iso,
    utcnow,
)
from .sessions import SessionManager

logger = logging.getLogger(__name__)



class ApprovalNotFound(Exception):
    """No approval with that id."""


class ApprovalConflict(Exception):
    """The approval is no longer pending (decided, expired or cancelled)."""


class ApprovalManager:
    def __init__(
        self,
        *,
        db: Database,
        config: Config,
        policy: PolicyEngine,
        audit: AuditLog,
        sessions: SessionManager,
        activity: ActivityManager | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._policy = policy
        self._audit = audit
        self._sessions = sessions
        self._activity = activity
        self._waiters: dict[str, asyncio.Event] = {}
        #: Away mode: nobody is at the phone, so asks are denied immediately
        #: instead of parking the agent for the whole timeout.
        self.away: bool = False

    async def _record(
        self,
        action: Action,
        kind: ActivityKind,
        summary: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        """Best-effort activity entry; never let it break the approval path.

        Everything is redacted on the way in (S7): the stream — and therefore
        the phone — shows ``password=***REDACTED***`` rather than the secret
        an agent tried to pass to a tool.
        """
        if self._activity is None:
            return
        try:
            await self._activity.record(
                agent_type=action.agent_type,
                session_id=action.session_id,
                workspace_path=action.workspace_path,
                kind=kind,
                summary=redact_text(summary),
                detail=redact(detail or {}),
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to record activity for %s", action.session_id)

    async def _record_decision(
        self,
        record: ApprovalRecord,
        decision: Decision,
        reason: str | None,
        *,
        state: ApprovalState,
        decided_by: str | None = None,
    ) -> None:
        """Best-effort activity entry for a decision on an existing record.

        ``state`` is the terminal state (allowed/denied/expired/cancelled) and
        is what the phone renders on the inline card, so it is recorded
        explicitly rather than inferred from ``decision``.
        """
        if self._activity is None:
            return
        try:
            await self._activity.record(
                agent_type=record.agent_type,
                session_id=record.session_id,
                workspace_path=record.workspace_path,
                kind=ActivityKind.APPROVAL_DECIDED,
                summary=redact_text(f"{decision.value}: {record.tool.name}"),
                detail=redact({
                    "approval_id": record.approval_id,
                    "decision": decision.value,
                    "state": state.value,
                    "reason": reason,
                    "decided_by": decided_by,
                    "tool": record.tool.name,
                    "risk": record.risk.level.value,
                }),
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to record decision for %s", record.approval_id)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _redacted(detail: ActionDetail) -> ActionDetail:
        """What the phone should see of an action, minus its secrets (S7)."""
        return ActionDetail(
            summary=redact_text(detail.summary),
            command=redact_text(detail.command) if detail.command else None,
            cwd=detail.cwd,
            paths=[redact_text(path) for path in detail.paths],
            diff_preview=(
                redact_text(detail.diff_preview) if detail.diff_preview else None
            ),
        )

    def timeout_for(self, agent_type: AgentType) -> int:
        agent_cfg = self._config.agents.for_agent(agent_type.value)
        return agent_cfg.hook_timeout_seconds or self._config.approvals.timeout_seconds

    @property
    def waiting_count(self) -> int:
        return len(self._waiters)

    # --- request ----------------------------------------------------------

    async def request(self, action: Action) -> ApprovalOutcome:
        """Evaluate an action and, if needed, block until it is decided."""
        started = time.monotonic()

        agent_cfg = self._config.agents.for_agent(action.agent_type.value)
        if not agent_cfg.enabled:
            # A disabled agent is a config promise (D14): its hooks fail
            # closed immediately instead of queueing a decision nobody will
            # answer.
            await self._audit.write(
                actor="agentd",
                action="approval.agent_disabled",
                subject=action.session_id,
                detail={
                    "agent_type": action.agent_type.value,
                    "tool": action.tool.name,
                },
            )
            await self._record(
                action,
                ActivityKind.APPROVAL_DECIDED,
                f"agent disabled: {action.tool.name}",
                {"decision": "deny", "state": "denied", "reason": "agent disabled"},
            )
            return ApprovalOutcome(
                approval_id=new_id(),
                agent_type=action.agent_type,
                state=ApprovalState.DENIED,
                decision=Decision.DENY,
                reason=f"{action.agent_type.value} is disabled in agentd's config",
                risk=Risk(level=RiskLevel.MEDIUM, reasons=["agent disabled"]),
                waited_seconds=time.monotonic() - started,
                policy_effect=Effect.DENY,
            )

        await self._sessions.touch(
            agent_type=action.agent_type,
            session_id=action.session_id,
            workspace_path=action.workspace_path,
        )

        result = self._policy.evaluate(action)
        await self._audit.write(
            actor="agentd",
            action="policy.evaluate",
            subject=action.session_id,
            detail={
                "agent_type": action.agent_type.value,
                "tool": action.tool.name,
                "tool_kind": action.tool.kind.value,
                "effect": result.effect.value,
                "risk": result.risk.level.value,
                "matched_rule": result.matched_rule,
            },
        )

        if result.effect is Effect.ALLOW:
            await self._audit.write(
                actor="agentd",
                action="approval.auto_allow",
                subject=action.session_id,
                detail={"tool": action.tool.name, "rule": result.matched_rule},
            )
            await self._record(
                action,
                ActivityKind.APPROVAL_DECIDED,
                f"auto-allowed {action.tool.name}",
                {"decision": "allow", "rule": result.matched_rule},
            )
            return ApprovalOutcome(
                approval_id=new_id(),
                agent_type=action.agent_type,
                state=ApprovalState.ALLOWED,
                decision=Decision.ALLOW,
                reason="allowed by policy",
                risk=result.risk,
                waited_seconds=time.monotonic() - started,
                policy_effect=result.effect,
            )

        if result.effect is Effect.DENY:
            await self._audit.write(
                actor="agentd",
                action="approval.auto_deny",
                subject=action.session_id,
                detail={"tool": action.tool.name, "rule": result.matched_rule},
            )
            await self._record(
                action,
                ActivityKind.APPROVAL_DECIDED,
                f"auto-denied {action.tool.name}",
                {"decision": "deny", "rule": result.matched_rule},
            )
            return ApprovalOutcome(
                approval_id=new_id(),
                agent_type=action.agent_type,
                state=ApprovalState.DENIED,
                decision=Decision.DENY,
                reason="denied by policy",
                risk=result.risk,
                waited_seconds=time.monotonic() - started,
                policy_effect=result.effect,
            )

        if self.away:
            # Nobody is at the phone: deny now rather than park the agent for
            # the whole timeout. The hook still fails closed either way, this
            # just fails fast.
            await self._audit.write(
                actor="agentd",
                action="approval.away_deny",
                subject=action.session_id,
                detail={"tool": action.tool.name},
            )
            await self._record(
                action,
                ActivityKind.APPROVAL_DECIDED,
                f"denied while away: {action.tool.name}",
                {"decision": "deny", "state": "denied", "reason": "away mode"},
            )
            return ApprovalOutcome(
                approval_id=new_id(),
                agent_type=action.agent_type,
                state=ApprovalState.DENIED,
                decision=Decision.DENY,
                reason="away mode is on",
                risk=result.risk,
                waited_seconds=time.monotonic() - started,
                policy_effect=result.effect,
            )

        return await self._ask(action, result.risk, started)

    async def _ask(self, action: Action, risk: Risk, started: float) -> ApprovalOutcome:
        approval_id = new_id()
        now = utcnow()
        timeout = self.timeout_for(action.agent_type)
        record = ApprovalRecord(
            approval_id=approval_id,
            agent_type=action.agent_type,
            session_id=action.session_id,
            workspace_path=action.workspace_path,
            tool=action.tool,
            # Stored redacted (S7): the phone shows what the agent wanted to
            # do, minus any secret embedded in it. The hash below is computed
            # from the raw action so it stays stable.
            action=self._redacted(action.action),
            risk=risk,
            action_hash=action_hash(action),
            state=ApprovalState.PENDING,
            created_at=now,
            expires_at=now + timedelta(seconds=timeout),
        )
        # Record the request before the approval row is visible to the phone:
        # the row and its inline card are separate commits, so if the row won
        # the race a poll could see a pending approval with no card yet.
        await self._record(
            action,
            ActivityKind.APPROVAL_REQUESTED,
            f"approval requested: {action.tool.name}",
            {
                "approval_id": approval_id,
                "tool": action.tool.name,
                "tool_kind": action.tool.kind.value,
                "risk": risk.level.value,
                "reasons": list(risk.reasons),
                "summary": action.action.summary,
                "command": action.action.command,
                "cwd": action.action.cwd,
                "paths": list(action.action.paths),
                "workspace_path": action.workspace_path,
                "expires_at": iso(record.expires_at),
            },
        )
        try:
            await self._db.insert_approval(record)
        except Exception:
            # The card for this request is already in the stream; with no
            # approval row nothing would ever resolve it, so close it out
            # before failing the request (the hook fails closed either way).
            logger.exception(
                "could not store approval for %s", action.session_id
            )
            await self._record(
                action,
                ActivityKind.APPROVAL_DECIDED,
                "approval failed to register",
                {
                    "approval_id": approval_id,
                    "decision": "deny",
                    "state": "denied",
                    "reason": "approval could not be stored",
                },
            )
            raise
        # Register the waiter before any further awaits: the record is already
        # visible to the phone, so a decision must be able to reach us.
        event = asyncio.Event()
        self._waiters[approval_id] = event
        logger.info(
            "approval created id=%s agent=%s tool=%s risk=%s expires=%s",
            approval_id,
            action.agent_type.value,
            action.tool.name,
            risk.level.value,
            iso(record.expires_at),
        )
        await self._audit.write(
            actor="agentd",
            action="approval.created",
            subject=approval_id,
            detail={
                "agent_type": action.agent_type.value,
                "session_id": action.session_id,
                "tool": action.tool.name,
                "risk": risk.level.value,
                "expires_at": iso(record.expires_at),
            },
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._expire(approval_id)
            return ApprovalOutcome(
                approval_id=approval_id,
                agent_type=action.agent_type,
                state=ApprovalState.EXPIRED,
                decision=Decision.DENY,
                reason="approval timed out",
                risk=risk,
                waited_seconds=time.monotonic() - started,
                policy_effect=Effect.ASK,
            )
        finally:
            self._waiters.pop(approval_id, None)

        final = await self._db.get_approval(approval_id)
        if final is None:  # pragma: no cover - defensive
            return ApprovalOutcome(
                approval_id=approval_id,
                agent_type=action.agent_type,
                state=ApprovalState.CANCELLED,
                decision=Decision.DENY,
                reason="approval record disappeared",
                risk=risk,
                waited_seconds=time.monotonic() - started,
                policy_effect=Effect.ASK,
            )
        return ApprovalOutcome(
            approval_id=final.approval_id,
            agent_type=final.agent_type,
            state=final.state,
            decision=final.decision,
            reason=final.decision_reason,
            risk=final.risk,
            waited_seconds=time.monotonic() - started,
            policy_effect=Effect.ASK,
        )

    # --- decisions --------------------------------------------------------

    async def decide(
        self,
        approval_id: str,
        *,
        decision: Decision,
        reason: str | None = None,
        decided_by: str = "sim",
    ) -> ApprovalRecord:
        record = await self._db.get_approval(approval_id)
        if record is None:
            raise ApprovalNotFound(approval_id)
        if record.state is not ApprovalState.PENDING:
            raise ApprovalConflict(f"approval is already {record.state.value}")
        if utcnow() >= record.expires_at:
            await self._expire(approval_id)
            raise ApprovalConflict("approval has expired")

        state = ApprovalState.ALLOWED if decision is Decision.ALLOW else ApprovalState.DENIED
        await self._db.set_decision(
            approval_id,
            state=state,
            decision=decision,
            reason=reason,
            decided_by=decided_by,
        )
        await self._audit.write(
            actor=decided_by,
            action="approval.decided",
            subject=approval_id,
            detail={
                "agent_type": record.agent_type.value,
                "decision": decision.value,
                "reason": reason,
            },
        )
        await self._record_decision(
            record, decision, reason, state=state, decided_by=decided_by
        )
        event = self._waiters.get(approval_id)
        if event is not None:
            event.set()

        updated = await self._db.get_approval(approval_id)
        assert updated is not None
        logger.info(
            "approval decided id=%s decision=%s by=%s",
            approval_id,
            decision.value,
            decided_by,
        )
        return updated

    async def _expire(self, approval_id: str) -> None:
        record = await self._db.get_approval(approval_id)
        await self._db.set_decision(
            approval_id,
            state=ApprovalState.EXPIRED,
            decision=Decision.DENY,
            reason="expired",
            decided_by="agentd",
        )
        logger.warning("approval expired id=%s (failing closed)", approval_id)
        await self._audit.write(
            actor="agentd",
            action="approval.expired",
            subject=approval_id,
            detail={},
        )
        if record is not None:
            await self._record_decision(
                record,
                Decision.DENY,
                "expired",
                state=ApprovalState.EXPIRED,
                decided_by="agentd",
            )
        event = self._waiters.get(approval_id)
        if event is not None:
            event.set()

    async def sweep_expired(self) -> int:
        """Expire any pending approvals whose deadline has passed.

        Records a decision event per swept approval so the activity stream (and
        therefore the phone's inline card) never shows a request that is stuck
        on "pending" after a restart.
        """
        expired_ids = await self._db.expire_stale(iso(utcnow()))
        if not expired_ids:
            return 0
        await self._audit.write(
            actor="agentd",
            action="approval.sweep",
            subject=None,
            detail={"expired": len(expired_ids)},
        )
        for approval_id in expired_ids:
            record = await self._db.get_approval(approval_id)
            if record is None:  # pragma: no cover - defensive
                continue
            await self._record_decision(
                record,
                Decision.DENY,
                "expired",
                state=ApprovalState.EXPIRED,
                decided_by="agentd",
            )
        return len(expired_ids)

    async def cancel_all(self, reason: str = "daemon shutting down") -> int:
        """Fail closed every in-flight approval (used on shutdown)."""
        pending = await self._db.list_approvals(state=ApprovalState.PENDING, limit=1000)
        for record in pending:
            await self._db.set_decision(
                record.approval_id,
                state=ApprovalState.CANCELLED,
                decision=Decision.DENY,
                reason=reason,
                decided_by="agentd",
            )
            await self._record_decision(
                record,
                Decision.DENY,
                reason,
                state=ApprovalState.CANCELLED,
                decided_by="agentd",
            )
            event = self._waiters.get(record.approval_id)
            if event is not None:
                event.set()
        if pending:
            await self._audit.write(
                actor="agentd",
                action="approval.cancel_all",
                subject=None,
                detail={"count": len(pending), "reason": reason},
            )
        return len(pending)

    # --- queries ----------------------------------------------------------

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return await self._db.get_approval(approval_id)

    async def list(
        self,
        *,
        agent_type: AgentType | None = None,
        session_id: str | None = None,
        state: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ApprovalRecord]:
        return await self._db.list_approvals(
            agent_type=agent_type, session_id=session_id, state=state, limit=limit
        )

    async def pending_counts(self) -> dict[str, int]:
        return {
            agent.value: await self._db.count_pending(agent) for agent in AgentType
        }


