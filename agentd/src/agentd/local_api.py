"""Local HTTP API on 127.0.0.1 (SPEC.md §7.2).

Only the hook adapters and the simulator talk to this. It is bound to loopback
and requires a bearer token stored in ``%USERPROFILE%\\.agentlink\\local_api_token``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import __version__, paths, webui
from .activity import ActivityManager, IncomingEvent
from .approvals import ApprovalConflict, ApprovalManager, ApprovalNotFound
from .audit import AuditLog
from .config import Config, load_config
from .crypto import new_token
from .db import Database
from .policy import PolicyEngine
from .protocol import (
    Action,
    ActivityEvent,
    ActivityEventIn,
    AgentType,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalState,
    DecisionRequest,
    MessageRecord,
    MessageRole,
    SessionDetail,
    SessionSummary,
    iso,
    utcnow,
)
from .sessions import SessionManager
from .watcher import TranscriptWatcher

SWEEP_INTERVAL_SECONDS = 15
HOUSEKEEPING_INTERVAL_SECONDS = 3600
#: A session whose transcripts have been quiet for this long stops showing the
#: "active" badge (and stops pretending to be running).
SESSION_IDLE_AFTER_MINUTES = 15

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
    activity: ActivityManager
    watcher: TranscriptWatcher | None = None
    started_at: datetime = field(default_factory=utcnow)


def _token_matches(presented: str, expected: str) -> bool:
    """Constant-time token comparison that survives non-ASCII input.

    Header values arrive latin-1-decoded, so ``compare_digest`` on the str
    form would raise ``TypeError`` and turn a bad token into a 500. Bytes
    never do.
    """
    return secrets.compare_digest(
        presented.encode("utf-8", "surrogatepass"),
        expected.encode("utf-8"),
    )


def load_or_create_token(path: Path | None = None) -> str:
    """Read the local API token, generating it on first run."""
    target = path or paths.token_path()
    if target.exists():
        existing = target.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = new_token()
    paths.ensure_dir(target.parent)
    # 0600: on POSIX the token must not be world-readable. Windows ignores the
    # mode and relies on the user profile's ACLs.
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
    except AttributeError:  # pragma: no cover - exotic platform
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
    activity = ActivityManager(db=database, sessions=sessions)
    approvals = ApprovalManager(
        db=database,
        config=cfg,
        policy=policy,
        audit=audit,
        sessions=sessions,
        activity=activity,
    )
    watcher = TranscriptWatcher(activity=activity, sessions=sessions, db=database)
    return AppContext(
        config=cfg,
        db=database,
        token=token or load_or_create_token(),
        policy=policy,
        audit=audit,
        sessions=sessions,
        approvals=approvals,
        activity=activity,
        watcher=watcher,
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


async def _housekeeping_loop(ctx: AppContext) -> None:
    """Slow periodic cleanup: idle badges and ``retention_days`` pruning."""
    while True:
        await asyncio.sleep(HOUSEKEEPING_INTERVAL_SECONDS)
        with contextlib.suppress(Exception):
            await _housekeeping(ctx)


async def _housekeeping(ctx: AppContext) -> None:
    idle_after = iso(utcnow() - timedelta(minutes=SESSION_IDLE_AFTER_MINUTES))
    await ctx.db.mark_stale_sessions_idle(idle_after)

    retention_days = ctx.config.activity.retention_days
    if retention_days > 0:
        cutoff = iso(utcnow() - timedelta(days=retention_days))
        stats = await ctx.db.prune_before(cutoff)
        if any(stats.values()):
            logger.info(
                "retention pruned %s", ", ".join(f"{k}={v}" for k, v in stats.items())
            )


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
        housekeeper = asyncio.create_task(_housekeeping_loop(context))
        watcher_task: asyncio.Task[None] | None = None
        if context.watcher is not None:
            # The first scan runs inside the watcher task, not here: startup
            # must not block the API on re-reading months of transcripts, and
            # thanks to the persisted high-water marks a restart only reads
            # the tails anyway.
            watcher_task = asyncio.create_task(context.watcher.run())
        try:
            yield
        finally:
            sweeper.cancel()
            housekeeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
                await housekeeper
            if watcher_task is not None:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task
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
        if not _token_matches(presented, context.token):
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
        session_id: str | None = Query(default=None),
        state: ApprovalState | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[ApprovalRecord]:
        return await ctx.approvals.list(
            agent_type=agent_type, session_id=session_id, state=state, limit=limit
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

    @app.get("/v1/sessions", response_model=list[SessionSummary], dependencies=auth)
    async def list_sessions(
        agent_type: AgentType | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[SessionSummary]:
        summaries = await ctx.sessions.summaries(agent_type, limit)
        # One query for every session's pending count, not one per session —
        # the home screen polls this every two seconds.
        counts = await ctx.db.pending_counts_by_session()
        for summary in summaries:
            summary.pending_approvals = counts.get(summary.session_id, 0)
        return summaries

    @app.get(
        "/v1/sessions/{session_id}", response_model=SessionDetail, dependencies=auth
    )
    async def get_session(
        session_id: str,
        limit: int = Query(default=300, ge=1, le=2000),
        tail: bool = Query(default=False, help="Return the newest rows, not the oldest"),
        ctx: AppContext = Depends(get_ctx),
    ) -> SessionDetail:
        summary = await ctx.sessions.summary(session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="session not found")
        summary.pending_approvals = await ctx.db.count_pending_for_session(session_id)
        messages = await ctx.activity.messages(
            agent_type=summary.agent_type, session_id=session_id,
            limit=limit, tail=tail,
        )
        events = await ctx.activity.events(
            agent_type=summary.agent_type, session_id=session_id,
            limit=limit, tail=tail,
        )
        return SessionDetail(**summary.model_dump(), messages=messages, events=events)

    @app.get(
        "/v1/sessions/{session_id}/messages",
        response_model=list[MessageRecord],
        dependencies=auth,
    )
    async def list_session_messages(
        session_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=2000),
        tail: bool = Query(default=False, help="Return the newest rows, not the oldest"),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[MessageRecord]:
        summary = await ctx.sessions.summary(session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="session not found")
        return await ctx.activity.messages(
            agent_type=summary.agent_type,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            tail=tail,
        )

    @app.get(
        "/v1/sessions/{session_id}/events",
        response_model=list[ActivityEvent],
        dependencies=auth,
    )
    async def list_session_events(
        session_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=2000),
        tail: bool = Query(default=False, help="Return the newest rows, not the oldest"),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[ActivityEvent]:
        summary = await ctx.sessions.summary(session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="session not found")
        return await ctx.activity.events(
            agent_type=summary.agent_type,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            tail=tail,
        )

    # --- activity ingest (hooks / adapters) -------------------------------

    @app.post("/v1/events", dependencies=auth)
    async def ingest_events(
        events: list[ActivityEventIn],
        ctx: AppContext = Depends(get_ctx),
    ) -> dict[str, Any]:
        """Record activity posted by a hook or adapter. Returns the count."""
        grouped: dict[tuple[AgentType, str, str], list[IncomingEvent]] = {}
        for item in events:
            key = (item.agent_type, item.session_id, item.workspace_path)
            grouped.setdefault(key, []).append(
                IncomingEvent(
                    kind=item.kind,
                    summary=item.summary,
                    text=item.text,
                    role=item.role,
                    ts=item.ts,
                    detail=item.detail,
                    event_id=item.event_id,
                )
            )
        inserted = 0
        for (agent_type, session_id, workspace_path), batch in grouped.items():
            inserted += await ctx.activity.ingest(
                agent_type=agent_type,
                session_id=session_id,
                workspace_path=workspace_path,
                events=batch,
            )
        return {"received": len(events), "inserted": inserted}

    # --- status / mode ----------------------------------------------------

    @app.get("/v1/status", dependencies=auth)
    async def status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
        now = utcnow()
        return {
            "version": __version__,
            "started_at": iso(ctx.started_at),
            "uptime_seconds": (now - ctx.started_at).total_seconds(),
            "away": ctx.approvals.away,
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
        """Away mode denies asks immediately instead of blocking for the
        timeout — the hook still fails closed, it just fails fast."""
        ctx.approvals.away = enabled
        await ctx.audit.write(
            actor="sim",
            action="mode.away",
            subject=None,
            detail={"enabled": enabled},
        )
        return {"away": ctx.approvals.away}

    # --- audit ------------------------------------------------------------

    @app.get("/v1/audit", dependencies=auth)
    async def list_audit(
        limit: int = Query(default=100, ge=1, le=1000),
        ctx: AppContext = Depends(get_ctx),
    ) -> list[dict[str, Any]]:
        return await ctx.audit.recent(limit=limit)

    return app

