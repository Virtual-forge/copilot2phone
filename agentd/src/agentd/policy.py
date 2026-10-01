"""Policy rules and risk heuristics (SPEC.md §9).

Slice 1 ships the built-in defaults plus the risk heuristics. DB-backed rules
are layered on in a later slice; the interface does not change.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .protocol import Action, Effect, PolicyResult, Risk, RiskLevel, ToolKind

_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}

DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-rf\b",
    r"\brm\s+-fr\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
    r"\bRemove-Item\b[^\n]*-Recurse\b",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bgit\s+push\b[^\n]*--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bcurl\b[^\n]*\|\s*(ba)?sh\b",
    r"\bwget\b[^\n]*\|\s*(ba)?sh\b",
    r"\bshutdown\b",
    r"\breg\s+delete\b",
    r"\bdiskpart\b",
)

SECRET_ARG_PATTERNS: tuple[str, ...] = (
    r"\bsk-[A-Za-z0-9]{16,}\b",
    r"\bghp_[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)

_KIND_RISK: dict[ToolKind, tuple[RiskLevel, str]] = {
    ToolKind.FILE_READ: (RiskLevel.LOW, "file read"),
    ToolKind.FILE_EDIT: (RiskLevel.MEDIUM, "file edit"),
    ToolKind.COMMAND: (RiskLevel.MEDIUM, "shell command"),
    ToolKind.FILE_DELETE: (RiskLevel.HIGH, "file deletion"),
    ToolKind.NETWORK: (RiskLevel.MEDIUM, "network access"),
    ToolKind.OTHER: (RiskLevel.MEDIUM, "unclassified tool"),
}


def path_inside_workspace(path: str, workspace: str) -> bool:
    """True when ``path`` resolves to ``workspace`` or something under it."""
    if not path:
        return True
    try:
        candidate = Path(path).expanduser().resolve()
        root = Path(workspace).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return candidate == root or root in candidate.parents


class PolicyEngine:
    """Evaluates an action into an effect plus a risk assessment."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def evaluate(self, action: Action) -> PolicyResult:
        reasons: list[str] = []
        level = RiskLevel.LOW

        def bump(candidate: RiskLevel) -> None:
            nonlocal level
            if _ORDER[candidate] > _ORDER[level]:
                level = candidate

        kind = action.tool.kind
        base_level, base_reason = _KIND_RISK.get(kind, _KIND_RISK[ToolKind.OTHER])
        bump(base_level)
        reasons.append(base_reason)

        command = action.action.command or ""
        if command:
            for pattern in DESTRUCTIVE_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    bump(RiskLevel.HIGH)
                    reasons.append("destructive command pattern")
                    break
            for pattern in SECRET_ARG_PATTERNS:
                if re.search(pattern, command):
                    bump(RiskLevel.HIGH)
                    reasons.append("secret-looking argument")
                    break

        outside = [
            p for p in action.action.paths if not path_inside_workspace(p, action.workspace_path)
        ]
        if outside:
            bump(RiskLevel.HIGH)
            reasons.append("path outside workspace")

        # Defaults (§9.3): reads inside the workspace are allowed outright;
        # everything else is asked. A high-risk action is still *asked* so the
        # phone can press-and-hold (D12) — but it fails closed if unanswered.
        if kind is ToolKind.FILE_READ and not outside:
            effect = Effect.ALLOW
            matched = "default:allow-workspace-read"
        else:
            effect = Effect.ASK
            matched = "default:ask"

        return PolicyResult(
            effect=effect,
            risk=Risk(level=level, reasons=reasons),
            matched_rule=matched,
        )
