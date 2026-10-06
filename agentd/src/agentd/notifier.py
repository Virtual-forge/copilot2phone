"""Best-effort webhook notifications (work plan P1-a).

The point of the remote is that you do not have to watch it: when a Codex
action is waiting on you, agentd POSTs a notification to your webhook —
ntfy, Pushover, a private Discord webhook, anything that accepts JSON.

Rules of the road:

* **Never blocks the caller.** ``notify_soon`` schedules the POST and
  forgets; a slow or dead webhook can cost at most its own timeout, never a
  decision or an ingest.
* **Never leaks secrets.** Bodies are redacted (S7) — the webhook is a
  third party, and a ``password=...`` inside a command must not reach it.
* **Never raises.** Failures are logged, nothing more; a broken webhook
  must not break the app.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Config
from .crypto import redact_text
from .protocol import iso, utcnow

logger = logging.getLogger(__name__)

#: Priority for ntfy publishes; "high" makes approvals buzz loudly.
_URGENT_EVENTS = {"approval_requested"}


class Notifier:
    """Posts small JSON notifications to the configured webhook."""

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config.notifications
        self._transport = transport
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._config.webhook_url.strip())

    # --- delivery ----------------------------------------------------------

    def notify_soon(self, **fields: Any) -> None:
        """Schedule a notification; returns immediately, never raises."""
        if not self.enabled:
            return
        task = asyncio.create_task(self._deliver(fields))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, fields: dict[str, Any]) -> None:
        try:
            url, payload = self._request(fields)
            async with httpx.AsyncClient(
                timeout=5.0, transport=self._transport
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            logger.info("webhook notified: %s", fields.get("event"))
        except Exception:  # noqa: BLE001 - deliberately best-effort
            logger.warning(
                "webhook notification failed (%s)", fields.get("event"), exc_info=True
            )

    # --- payload -----------------------------------------------------------

    def _request(
        self, fields: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        event = str(fields.get("event") or "update")
        title = redact_text(str(fields.get("title") or "AgentLink"))
        body = redact_text(str(fields.get("body") or ""))

        if self._config.webhook_format == "ntfy":
            # webhook_url points at the topic: https://ntfy.sh/<topic>.
            # Publishing goes to the root with the topic inside the JSON.
            parts = urlsplit(self._config.webhook_url.strip())
            topic = parts.path.strip("/")
            if not topic:
                logger.warning(
                    "notifications.webhook_format=ntfy but the URL has no "
                    "topic path (use https://ntfy.sh/<topic>)"
                )
            payload = {
                "topic": topic,
                "title": title,
                "message": body or title,
                "priority": "high" if event in _URGENT_EVENTS else "default",
            }
            return f"{parts.scheme}://{parts.netloc}", payload

        payload: dict[str, Any] = {
            "event": event,
            "title": title,
            "body": body,
            "at": iso(utcnow()),
        }
        for key in ("agent", "session_id"):
            if fields.get(key) is not None:
                payload[key] = fields[key]
        return self._config.webhook_url.strip(), payload
