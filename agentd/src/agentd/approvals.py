"""Approval state machine with async waiters (SPEC.md §4.2, D10).

The hook blocks here until the phone decides, the approval expires, or the
daemon shuts down. Every path fails closed (D5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from .audit import AuditLog
from .config import Config
from .crypto import action_hash, new_id
from .db import Database
from .policy import PolicyEngine
from .protocol import (
    Action,
    AgentType,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalState,
    Decision,
    Effect,
    Risk,
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
    ) -> None:
        self._db = db
        self._config = config
        self._policy = policy
        self._audit = audit
        self._sessions = sessions
        self._waiters: dict[str, asyncio.Event] = {}

    # --- helpers ----------------------------------------------------------

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
            action=action.action,
            risk=risk,
            action_hash=action_hash(action),
            state=ApprovalState.PENDING,
            created_at=now,
            expires_at=now + timedelta(seconds=timeout),
        )
        await self._db.insert_approval(record)
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

        event = asyncio.Event()
        self._waiters[approval_id] = event
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
        event = self._waiters.get(approval_id)
        if event is not None:
            event.set()

    async def sweep_expired(self) -> int:
        """Expire any pending approvals whose deadline has passed."""
        changed = await self._db.expire_stale(iso(utcnow()))
        if changed:
            await self._audit.write(
                actor="agentd",
                action="approval.sweep",
                subject=None,
                detail={"expired": changed},
            )
        return changed

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
        state: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ApprovalRecord]:
        return await self._db.list_approvals(
            agent_type=agent_type, state=state, limit=limit
        )

    async def pending_counts(self) -> dict[str, int]:
        return {
            agent.value: await self._db.count_pending(agent) for agent in AgentType
        }


