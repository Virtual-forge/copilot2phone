"""Hashing, redaction and id/token helpers.

Slice 1 needs deterministic action hashing (D10) and secret redaction (S7).
The libsodium ``crypto_box`` layer is added in slice 2.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from typing import Any

from .protocol import Action

# --- ids and tokens -------------------------------------------------------


def new_id() -> str:
    """A fresh opaque identifier."""
    return str(uuid.uuid4())


def new_token() -> str:
    """A fresh local-API bearer token."""
    return secrets.token_urlsafe(32)


# --- canonical hashing ----------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 safe."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def action_hash(action: Action) -> str:
    """Hash the security-relevant parts of an action (D10).

    Deliberately excludes ``raw`` so that cosmetic differences in the agent's
    hook payload do not change the hash.
    """
    payload = {
        "agent_type": action.agent_type.value,
        "session_id": action.session_id,
        "workspace_path": action.workspace_path,
        "tool": action.tool.model_dump(mode="json"),
        "action": action.action.model_dump(mode="json"),
    }
    return "sha256:" + sha256_hex(canonical_json(payload).encode("utf-8"))


# --- redaction (S7) -------------------------------------------------------

_REDACTED = "***REDACTED***"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bauthorization\s*:\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "private_key",
        "client_secret",
    }
)


def redact_text(text: str) -> str:
    """Replace anything that looks like a secret."""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def redact(obj: Any) -> Any:
    """Recursively redact secrets from a JSON-like structure."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEYS:
                result[key] = _REDACTED
            else:
                result[key] = redact(value)
        return result
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj
