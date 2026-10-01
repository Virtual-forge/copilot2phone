"""Append-only audit log (S10)."""

from __future__ import annotations

from typing import Any

from .db import Database


class AuditLog:
    """Thin wrapper so callers do not touch the DB directly."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def write(
        self,
        *,
        actor: str,
        action: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        await self._db.append_audit(
            actor=actor,
            action=action,
            subject=subject,
            detail=detail or {},
        )

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._db.list_audit(limit=limit)
