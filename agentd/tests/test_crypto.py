"""Tests for hashing, redaction and id helpers."""

from __future__ import annotations

from agentd.crypto import action_hash, canonical_json, new_id, new_token, redact, redact_text
from agentd.protocol import Action, ActionDetail, AgentType, Tool, ToolKind


def make_action(**overrides) -> Action:
    base = {
        "agent_type": AgentType.CODEX,
        "session_id": "s1",
        "workspace_path": "C:/work",
        "tool": Tool(name="shell", kind=ToolKind.COMMAND),
        "action": ActionDetail(summary="rm -rf build/", command="rm -rf build/"),
    }
    base.update(overrides)
    return Action(**base)


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_action_hash_is_stable():
    assert action_hash(make_action()) == action_hash(make_action())


def test_action_hash_changes_with_command():
    other = make_action(action=ActionDetail(summary="ls", command="ls"))
    assert action_hash(make_action()) != action_hash(other)


def test_action_hash_ignores_raw_payload():
    with_raw = make_action(raw={"noise": [1, 2, 3]})
    assert action_hash(make_action()) == action_hash(with_raw)


def test_action_hash_is_prefixed():
    assert action_hash(make_action()).startswith("sha256:")


def test_new_id_is_unique():
    assert new_id() != new_id()


def test_new_token_is_long_enough():
    assert len(new_token()) >= 32


def test_redact_text_masks_api_keys():
    assert "sk-abcdefghijklmnopqrst" not in redact_text("key=sk-abcdefghijklmnopqrst")


def test_redact_text_masks_password_assignments():
    assert "hunter2" not in redact_text("password=hunter2")


def test_redact_masks_secret_keys_in_dicts():
    result = redact({"api_key": "abc", "nested": {"token": "xyz"}, "safe": "ok"})
    assert result["api_key"] == "***REDACTED***"
    assert result["nested"]["token"] == "***REDACTED***"
    assert result["safe"] == "ok"


def test_redact_walks_lists():
    result = redact([{"secret": "a"}, "plain"])
    assert result[0]["secret"] == "***REDACTED***"
    assert result[1] == "plain"
