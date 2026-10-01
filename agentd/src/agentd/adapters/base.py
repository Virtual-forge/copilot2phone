"""Adapter interface (SPEC.md §18).

An adapter translates one agent's hook contract into the AgentLink ``Action``
model and renders the decision back in whatever shape that agent expects.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..protocol import Action, AgentType


class HookParseError(Exception):
    """The hook payload could not be understood.

    Callers must fail closed: deny the action rather than let it through.
    """


@runtime_checkable
class HookAdapter(Protocol):
    """What every agent adapter must provide."""

    agent_type: AgentType

    def parse(self, payload: dict[str, Any]) -> Action:
        """Normalise a raw hook payload into an :class:`Action`."""

    def render_allow(self, action: Action) -> tuple[str, int]:
        """Return ``(stdout, exit_code)`` for an allowed action."""

    def render_deny(self, action: Action | None, reason: str) -> tuple[str, int]:
        """Return ``(stdout, exit_code)`` for a denied action.

        ``action`` is ``None`` when the payload could not even be parsed.
        """



def first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present, non-None value among ``keys``."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def as_dict(value: Any) -> dict[str, Any]:
    """Coerce a value to a dict, wrapping scalars."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def collect_paths(params: dict[str, Any]) -> list[str]:
    """Pull file paths out of a tool's parameter bag."""
    paths: list[str] = []
    for key in ("path", "file_path", "filePath", "file", "target_file", "targetFile"):
        value = params.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    for key in ("paths", "files", "file_paths"):
        value = params.get(key)
        if isinstance(value, list):
            paths.extend(item for item in value if isinstance(item, str) and item)
    # de-duplicate, preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique
