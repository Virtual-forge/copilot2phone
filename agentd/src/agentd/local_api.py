"""Local HTTP API on 127.0.0.1 (SPEC.md §7.2).

Only the hook adapters and the simulator talk to this. It is bound to loopback
and requires a bearer token stored in ``%USERPROFILE%\\.agentlink\\local_api_token``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import __version__, paths, webui
from .approvals import ApprovalConflict, ApprovalManager, ApprovalNotFound
from .audit import AuditLog
from .config import Config, load_config
from .crypto import new_token
from .db import Database
from .policy import PolicyEngine
from .protocol import (
    Action,
    AgentType,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalState,
    DecisionRequest,
    SessionRecord,
    iso,
    utcnow,
)
from .sessions import SessionManager

SWEEP_INTERVAL_SECONDS = 15

logger = logging.getLogger(__name__)



@dataclass
class AppContext:
    """Everything the API needs, wired once at startup."""

    config: Config
    db: Database
    token: str
    policy: PolicyEngine
    audit: AuditLog
    sessions: SessionManager
    approvals: ApprovalManager
    started_at: datetime = field(default_factory=utcnow)
    away: bool = False


def load_or_create_token(path: Path | None = None) -> str:
    """Read the local API token, generating it on first run."""
    target = path or paths.token_path()
    if target.exists():
        existing = target.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = new_token()
    paths.ensure_dir(target.parent)
    target.write_text(token, encoding="utf-8")
    return token


async def build_context(
    config: Config | None = None,
    *,
    db_path: Path | None = None,
    token: str | None = None,
) -> AppContext:
    """Connect the database and assemble the application context."""
    cfg = config or load_config()
    database = Database(db_path or paths.db_path())
    await database.connect()
    await database.migrate()

    policy = PolicyEngine(cfg)
    audit = AuditLog(database)
    sessions = SessionManager(database)
    approvals = ApprovalManager(
        db=database,
        config=cfg,
        policy=policy,
        audit=audit,
        sessions=sessions,
    )
    return AppContext(
        config=cfg,
        db=database,
        token=token or load_or_create_token(),
        policy=policy,
        audit=audit,
        sessions=sessions,
        approvals=approvals,
    )


def get_ctx(request: Request) -> AppContext:
    """Resolve the context attached to the running app."""
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="daemon is still starting")
    return ctx



async def _sweep_loop(ctx: AppContext) -> None:
    """Periodically expire approvals whose deadline has passed."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        with contextlib.suppress(Exception):
            await ctx.approvals.sweep_expired()


def create_app(
    ctx: AppContext | None = None,
    *,
    config: Config | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Pass ``ctx`` for tests (an already-built context) or ``config`` to have the
    context built inside the server's own event loop (the CLI path).
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: AppContext | None = None
        if getattr(app.state, "ctx", None) is None:
            owned = await build_context(config)
            app.state.ctx = owned
        context: AppContext = app.state.ctx
        logger.info(
            "agentd %s listening on %s (db=%s)",
            __version__,
            context.config.base_url,
            context.db.path,
        )
        # Any approval still in state=pending belongs to a previous process.
        # The hooks that created them had their HTTP connections severed when
        # agentd was last stopped (gracefully or not), so they already exited
        # fail-closed. Leaving those rows as 'pending' causes the phone to
        # show ghost approvals that can never unblock any agent.
        stale = await context.approvals.cancel_all(
            "agentd restarted — previous session's pending approvals cancelled"
        )
        if stale:
            logger.warning(
                "startup: cancelled %d stale pending approval(s) from previous session",
                stale,
            )
        sweeper = asyncio.create_task(_sweep_loop(context))
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            if owned is not None:
                cancelled = await owned.approvals.cancel_all()
                if cancelled:
                    logger.warning(
                        "shutdown: failed closed %d in-flight approval(s)", cancelled
                    )
                await owned.db.close()
                logger.info("agentd stopped")


    app = FastAPI(
        title="agentd",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if ctx is not None:
        app.state.ctx = ctx

    async def require_token(
        authorization: str | None = Header(default=None),
        context: AppContext = Depends(get_ctx),
    ) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        presented = authorization[len("Bearer ") :].strip()
        if not secrets.compare_digest(presented, context.token):
            raise HTTPException(status_code=401, detail="invalid token")

    auth = [Depends(require_token)]


    # --- phone UI (static shell; the API below still needs the token) -----

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def phone_ui() -> HTMLResponse:
        return HTMLResponse(webui.render())

    # --- health (unauthenticated, loopback only) --------------------------

    @app.get("/v1/health")
    async def health(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "started_at": iso(ctx.started_at),
        }

    # --- approvals --------------------------------------------------------

    @app.post("/v1/approvals", response_model=ApprovalOutcome, dependencies=auth)
    async def create_approval(
        action: Action, ctx: AppContext = Depends(get_ctx)
    ) -> ApprovalOutcome:
        """Called by the hook adapters. Blocks until decided or expired."""
        return await ctx.approvals.request(action)

    @app.get(
        "/v1/approvals", response_model=list[ApprovalRecord], dependencies=auth
    )
    async def list_approvals(
        agent_type: AgentType | None = Query(default=None),
        state: ApprovalState | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[ApprovalRecord]:
        return await ctx.approvals.list(
            agent_type=agent_type, state=state, limit=limit
        )

    @app.get(
        "/v1/approvals/{approval_id}", response_model=ApprovalRecord, dependencies=auth
    )
    async def get_approval(
        approval_id: str, ctx: AppContext = Depends(get_ctx)
    ) -> ApprovalRecord:
        record = await ctx.approvals.get(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail="approval not found")
        return record

    @app.post(
        "/v1/approvals/{approval_id}/decision",
        response_model=ApprovalRecord,
        dependencies=auth,
    )
    async def decide_approval(
        approval_id: str,
        body: DecisionRequest,
        ctx: AppContext = Depends(get_ctx),
    ) -> ApprovalRecord:
        try:
            return await ctx.approvals.decide(
                approval_id,
                decision=body.decision,
                reason=body.reason,
                decided_by=body.decided_by,
            )
        except ApprovalNotFound:
            raise HTTPException(status_code=404, detail="approval not found") from None
        except ApprovalConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    # --- sessions ---------------------------------------------------------

    @app.get("/v1/sessions", response_model=list[SessionRecord], dependencies=auth)
    async def list_sessions(
        agent_type: AgentType | None = Query(default=None),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[SessionRecord]:
        return await ctx.sessions.list(agent_type)

    # --- status / mode ----------------------------------------------------

    @app.get("/v1/status", dependencies=auth)
    async def status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
        now = utcnow()
        return {
            "version": __version__,
            "started_at": iso(ctx.started_at),
            "uptime_seconds": (now - ctx.started_at).total_seconds(),
            "away": ctx.away,
            "waiting": ctx.approvals.waiting_count,
            "pending": await ctx.approvals.pending_counts(),
            "db_path": str(ctx.db.path),
            "agents": {
                agent.value: {
                    "enabled": ctx.config.agents.for_agent(agent.value).enabled,
                    "hook_timeout_seconds": ctx.approvals.timeout_for(agent),
                }
                for agent in AgentType
            },
        }

    @app.post("/v1/away", dependencies=auth)
    async def set_away(
        enabled: bool = Query(...), ctx: AppContext = Depends(get_ctx)
    ) -> dict[str, Any]:
        ctx.away = enabled
        await ctx.audit.write(
            actor="sim",
            action="mode.away",
            subject=None,
            detail={"enabled": enabled},
        )
        return {"away": ctx.away}

    # --- audit ------------------------------------------------------------

    @app.get("/v1/audit", dependencies=auth)
    async def list_audit(
        limit: int = Query(default=100, ge=1, le=1000),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[dict[str, Any]]:
        return await ctx.audit.recent(limit=limit)

    return app

