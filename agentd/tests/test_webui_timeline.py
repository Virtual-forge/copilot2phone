"""The chat timeline fold, exercised under node.

Folding the activity stream into an ordered render list (messages + inline
approval cards) is presentation logic that lives in the page's JavaScript, so it
is tested by running that JavaScript rather than by reimplementing it in Python.
Skipped when node is not installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agentd.webui import PAGE

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract_script(page: str) -> str:
    match = re.search(r"<script>(.*?)</script>", page, re.S)
    assert match is not None, "the page has no <script> block"
    return match.group(1)


#: Runs the page's script under node with DOM stubs, then exercises the fold.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(process.argv[2], "utf8");
const noop = () => {};
const stubEl = () => ({
  classList: { add: noop, remove: noop, contains: () => false, toggle: noop },
  addEventListener: noop, appendChild: noop, querySelector: () => null,
  style: {}, innerHTML: "", textContent: "", className: "", disabled: false,
  value: "", dataset: {}
});

const sandbox = {
  console,
  document: {
    getElementById: stubEl, createElement: stubEl,
    body: stubEl(), addEventListener: noop,
    documentElement: { scrollHeight: 0 }
  },
  localStorage: { getItem: () => null, setItem: noop },
  location: { hash: "", pathname: "/", search: "" },
  history: { replaceState: noop },
  window: { addEventListener: noop, scrollTo: noop, innerHeight: 800, scrollY: 0 },
  setInterval: noop, setTimeout: noop,
  requestAnimationFrame: noop, cancelAnimationFrame: noop,
  fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  URLSearchParams
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

let failures = 0;
function check(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { console.log("  ok   " + name); }
  else { failures++; console.log("  FAIL " + name + "\n       got " + a + "\n       want " + e); }
}

const T = (s) => "2026-01-01T10:00:" + String(s).padStart(2, "0") + "Z";

/* The shape the phone should show: user, agent, agent, approval card, exec. */
const events = [
  { kind: "user_message", seq: 1, ts: T(0), text: "do the thing", summary: "do the thing" },
  { kind: "assistant_message", seq: 2, ts: T(5), text: "on it", summary: "on it" },
  { kind: "tool_call", seq: 3, ts: T(6), text: "rm -rf /", summary: "execute_command",
    detail: { tool_name: "execute_command" } },
  { kind: "approval_requested", seq: 4, ts: T(7),
    detail: { approval_id: "a1", tool: "execute_command", risk: "high",
              command: "rm -rf /", expires_at: "2026-01-01T10:10:00Z" } },
  { kind: "approval_decided", seq: 5, ts: T(20),
    detail: { approval_id: "a1", decision: "allow", state: "allowed", decided_by: "phone" } },
  { kind: "tool_result", seq: 6, ts: T(21), text: "done", summary: "done" },
  /* auto-allow from policy: decided with no request -> feed only */
  { kind: "approval_decided", seq: 7, ts: T(22),
    detail: { approval_id: "auto1", decision: "allow", state: "allowed" } },
  /* feed-only kinds must not become chat bubbles */
  { kind: "session_started", seq: 8, ts: T(23), summary: "started" },
  { kind: "file_changed", seq: 9, ts: T(24), summary: "a.py" }
];

const items = sandbox.buildTimeline(events, []);
check("item count (auto-decision + feed kinds dropped)", items.length, 5);
check("order", items.map(i => i.type === "approval" ? "approval" : i.event.kind),
  ["user_message", "assistant_message", "tool_call", "approval", "tool_result"]);
check("card folded to allowed", items[3].view.state, "allowed");
check("card kept the command", items[3].view.command, "rm -rf /");
check("card kept decided_by", items[3].view.decided_by, "phone");
check("outcome text", sandbox.outcomeText(items[3].view), "Allowed \u00b7 by phone");

/* Pending: a request with no decision yet. */
check("pending card", sandbox.buildTimeline([events[3]], [])[0].view.state, "pending");

/* ts ordering wins over seq: the approval was recorded before the batch of
   tool calls that actually preceded it. */
const outOfOrder = [
  { kind: "approval_requested", seq: 1, ts: T(2),
    detail: { approval_id: "a2", tool: "execute_command", risk: "medium" } },
  { kind: "tool_call", seq: 2, ts: T(0), summary: "first", detail: { tool_name: "t" } },
  { kind: "tool_call", seq: 3, ts: T(1), summary: "second", detail: { tool_name: "t" } }
];
check("ts beats seq", sandbox.buildTimeline(outOfOrder, []).map(
  i => i.type === "approval" ? "approval" : i.event.summary),
  ["first", "second", "approval"]);

/* Authoritative state: the record says allowed even though the window only
   holds the request (the decision event was truncated away). */
const truncated = sandbox.buildTimeline([events[3]],
  [{ approval_id: "a1", state: "allowed", decision: "allow",
     decided_by: "phone", decision_reason: null, created_at: T(7) }]);
check("record overrides missing decision event", truncated[0].view.state, "allowed");

/* Safety net: a pending approval with no request event in the window. */
const orphan = sandbox.buildTimeline([events[0]],
  [{ approval_id: "a9", state: "pending", action: { command: "curl x" },
     tool: { name: "execute_command" }, risk: { level: "high", reasons: [] },
     workspace_path: "C:/w", expires_at: "2026-01-01T10:10:00Z", created_at: T(9) }]);
check("orphan pending appended", orphan.length, 2);
check("orphan is a card", orphan[1].type, "approval");
check("orphan command", orphan[1].view.command, "curl x");

/* A *decided* approval with no request event anywhere: the record is the
   only trace (the decision predates activity recording, or fell outside the
   fetched window) — it still renders, at its request time. */
const recordOnly = sandbox.buildTimeline([events[0]],
  [{ approval_id: "a8", state: "allowed", decision: "allow",
     decided_by: "phone", decision_reason: null, created_at: T(10),
     action: { command: "mkdir agent_files" },
     tool: { name: "Bash" }, risk: { level: "high", reasons: [] },
     workspace_path: "C:/w", expires_at: "2026-01-01T10:10:00Z" }]);
check("decided record renders a card", recordOnly.length, 2);
check("record card sits after earlier messages",
  recordOnly[1].type, "approval");
check("record card outcome", recordOnly[1].view.state, "allowed");
check("record card kept the command", recordOnly[1].view.command, "mkdir agent_files");
check("record card outcome text",
  sandbox.outcomeText(recordOnly[1].view), "Allowed \u00b7 by phone");

/* Expired reads as failed-closed. */
check("expired text",
  sandbox.outcomeText({ state: "expired", decided_by: "agentd", reason: "expired" }),
  "Expired \u2014 failed closed \u00b7 by agentd");

/* Harness mechanics never become chat bubbles: a Codex turn boundary and the
   exec/wait polling loop behind one shell command are feed-only. */
const mechanics = [
  { kind: "task_started", seq: 1, ts: T(0), summary: "task started" },
  { kind: "task_finished", seq: 2, ts: T(1), summary: "task complete" },
  { kind: "tool_plumbing", seq: 3, ts: T(2), summary: "exec" },
  { kind: "tool_plumbing", seq: 4, ts: T(3), summary: "wait" },
  { kind: "user_message", seq: 5, ts: T(4), text: "hi", summary: "hi" }
];
check("harness mechanics are feed-only",
  sandbox.buildTimeline(mechanics, []).map(i => i.event.kind), ["user_message"]);

/* A pending card is pinned to the bottom even though its request happened
   mid-conversation, so the thing that needs an answer is never off-screen. */
const pinned = sandbox.buildTimeline([
  { kind: "user_message", seq: 1, ts: T(0), text: "hi", summary: "hi" },
  { kind: "approval_requested", seq: 2, ts: T(1),
    detail: { approval_id: "p1", tool: "execute_command", risk: "medium", command: "ls" } },
  { kind: "assistant_message", seq: 3, ts: T(2), text: "waiting", summary: "waiting" }
], []);
check("pending card pinned last",
  pinned.map(i => i.type === "approval" ? "approval" : i.event.kind),
  ["user_message", "assistant_message", "approval"]);
check("pinned card keeps its command", pinned[2].view.command, "ls");

/* A decided card stays where its request happened, so the conversation reads
   in order. */
const decided = sandbox.buildTimeline([
  { kind: "user_message", seq: 1, ts: T(0), text: "hi", summary: "hi" },
  { kind: "approval_requested", seq: 2, ts: T(1),
    detail: { approval_id: "p2", tool: "execute_command", risk: "medium", command: "ls" } },
  { kind: "approval_decided", seq: 3, ts: T(2),
    detail: { approval_id: "p2", decision: "allow", state: "allowed", decided_by: "phone" } },
  { kind: "assistant_message", seq: 4, ts: T(3), text: "done", summary: "done" }
], []);
check("decided card stays in place",
  decided.map(i => i.type === "approval" ? "approval" : i.event.kind),
  ["user_message", "approval", "assistant_message"]);

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nall checks passed");
process.exit(failures ? 1 : 0);
"""


def test_chat_timeline_fold(tmp_path: Path) -> None:
    """The fold interleaves approvals into the chat in conversation order."""
    (tmp_path / "page.js").write_text(_extract_script(PAGE), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(tmp_path / "page.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
