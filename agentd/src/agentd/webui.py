"""The phone UI served by ``agentd`` (preview of the slice 7 PWA).

A single self-contained page: no build step, no dependencies, no bundler. It is
session-centric — the home screen lists sessions, and tapping one opens its chat,
activity feed and pending approvals.

Security notes:

* The page itself contains no secrets. It asks for the local API token and
  keeps it in ``localStorage``.
* The token can also be passed once as a URL fragment (``/#t=<token>``), which
  is never sent to the server and is stripped from the address bar on load.
* Serving this page does **not** widen the API surface: every ``/v1`` route
  still requires the bearer token.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0d1017">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<title>AgentLink</title>
<style>
  :root {
    --bg: #0d1017; --panel: #12161e; --card: #151a23; --card-2: #1b212d;
    --line: #242c3a; --line-soft: #1c2430;
    --fg: #dde4ee; --dim: #93a1b3; --faint: #61707f;
    --accent: #539bf5; --accent-soft: rgba(83,155,245,.13);
    --accent-line: rgba(83,155,245,.38);
    --allow: #3fb950; --deny: #f47067; --warn: #e0a458;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
  body {
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", sans-serif;
    min-height: 100dvh; display: flex; flex-direction: column;
    padding: env(safe-area-inset-top) env(safe-area-inset-right)
             env(safe-area-inset-bottom) env(safe-area-inset-left);
    overscroll-behavior-y: contain;
    -webkit-font-smoothing: antialiased;
  }
  header {
    position: sticky; top: 0; z-index: 10; background: var(--bg);
    border-bottom: 1px solid var(--line);
    padding: 10px 16px 8px;
  }
  .row { display: flex; align-items: center; gap: 8px; min-height: 28px; }
  .brand { font-weight: 700; font-size: 14px; letter-spacing: .01em; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--faint); flex: none; }
  .dot.on { background: var(--allow); }
  .dot.off { background: var(--deny); }
  .spacer { flex: 1; }
  .muted { color: var(--dim); font-size: 12.5px; }
  .faint { color: var(--faint); font-size: 11.5px; }
  .title {
    font-weight: 600; font-size: 15px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
  }
  .back {
    flex: none; width: 30px; height: 30px; border-radius: 6px; border: 1px solid var(--line);
    background: var(--card); color: var(--dim); font-size: 16px; line-height: 1;
    display: flex; align-items: center; justify-content: center; padding: 0;
  }
  .chips, .tabs { display: flex; gap: 6px; margin-top: 8px; overflow-x: auto; scrollbar-width: none; }
  .chips::-webkit-scrollbar, .tabs::-webkit-scrollbar { display: none; }
  .chip {
    flex: none; padding: 4px 10px; border-radius: 5px; border: 1px solid var(--line);
    background: transparent; color: var(--dim); font-size: 12.5px; font-weight: 550;
    font-family: inherit;
  }
  .chip.sel { background: var(--accent-soft); border-color: var(--accent-line); color: var(--accent); }
  .chip .n { opacity: .8; margin-left: 4px; font-weight: 700; }
  .tab {
    flex: none; padding: 5px 1px 7px; margin-right: 16px; border: 0;
    border-bottom: 2px solid transparent; border-radius: 0;
    background: transparent; color: var(--dim); font-size: 13px; font-weight: 600;
    font-family: inherit; white-space: nowrap;
  }
  .tab.sel { color: var(--fg); border-bottom-color: var(--accent); }
  .tab .n { opacity: 1; margin-left: 5px; font-weight: 700; color: var(--warn); }
  main { flex: 1; padding: 12px 16px 52px; display: flex; flex-direction: column; gap: 10px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
  }
  .card.tap { cursor: pointer; }
  .card.tap:active { background: var(--card-2); }
  .card.high { border-color: rgba(244,112,103,.5); }
  .badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .badge {
    font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
    padding: 2px 7px; border-radius: 4px; background: var(--card-2); color: var(--dim);
    border: 1px solid transparent;
  }
  .badge.codex { color: #c3a9f7; background: rgba(179,146,240,.1); border-color: rgba(179,146,240,.3); }
  .badge.opencode { color: #58d6e0; background: rgba(88,214,224,.1); border-color: rgba(88,214,224,.3); }
  .badge.low { color: #6fdd8b; background: rgba(63,185,80,.1); border-color: rgba(63,185,80,.3); }
  .badge.medium { color: #ecb46b; background: rgba(224,164,88,.1); border-color: rgba(224,164,88,.3); }
  .badge.high { color: #ff918a; background: rgba(244,112,103,.12); border-color: rgba(244,112,103,.35); }
  .tool { font-weight: 600; font-size: 15px; }
  pre.cmd {
    margin: 0; padding: 10px 12px; background: var(--bg); border: 1px solid var(--line-soft);
    border-radius: 6px; font: 12.5px/1.55 var(--mono);
    white-space: pre-wrap; word-break: break-word; color: #c9d4e0;
    max-height: 30vh; overflow: auto;
  }
  .meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--faint); }
  .meta span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .reasons { font-size: 12px; color: var(--warn); }
  .actions { display: flex; gap: 8px; margin-top: 2px; }
  button {
    flex: 1; position: relative; overflow: hidden; border: 0; border-radius: 7px;
    padding: 13px 10px; font-size: 15px; font-weight: 650; color: #fff;
    font-family: inherit; touch-action: manipulation; user-select: none;
  }
  button:disabled { opacity: .45; }
  .deny { background: transparent; border: 1px solid rgba(244,112,103,.55); color: var(--deny); }
  .allow { background: var(--allow); color: #07130a; }
  .primary { background: var(--accent); }
  .hold-bar {
    position: absolute; left: 0; top: 0; bottom: 0; width: 0%;
    background: rgba(255,255,255,.32); pointer-events: none;
  }
  .hold-hint { font-size: 11px; font-weight: 600; opacity: .8; display: block; }
  .empty {
    text-align: center; color: var(--faint); padding: 56px 20px; font-size: 13.5px;
    border: 1px dashed var(--line); border-radius: 8px; line-height: 1.7;
  }
  .banner {
    background: rgba(244,112,103,.1); border: 1px solid rgba(244,112,103,.4);
    color: #ff918a; border-radius: 7px; padding: 10px 12px; font-size: 13px;
  }
  .banner.away { background: rgba(224,164,88,.1); border-color: rgba(224,164,88,.4); color: var(--warn); }
  .overlay {
    position: fixed; inset: 0; background: rgba(5,8,12,.92); z-index: 50;
    display: flex; align-items: center; justify-content: center; padding: 22px;
  }
  .overlay .card { width: 100%; max-width: 380px; }
  input {
    width: 100%; padding: 12px; border-radius: 7px; border: 1px solid var(--line);
    background: var(--bg); color: var(--fg); font: 13.5px var(--mono);
  }
  .hidden { display: none !important; }
  .toast {
    position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%);
    background: var(--card-2); border: 1px solid var(--line); color: var(--fg);
    padding: 10px 16px; border-radius: 6px; font-size: 13.5px; z-index: 60;
    box-shadow: 0 10px 32px rgba(0,0,0,.55);
  }
  /* session input (D-026): a composer under the chat, opencode sessions only */
  .composer {
    display: flex; gap: 8px; align-items: flex-end;
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
    background: var(--bg); border-top: 1px solid var(--line);
  }
  .composer textarea {
    flex: 1; resize: none; border-radius: 7px; border: 1px solid var(--line);
    background: var(--card); color: var(--fg); font: 14.5px/1.45 inherit;
    font-family: inherit; padding: 11px 12px; min-height: 43px; max-height: 32vh;
  }
  .composer textarea:focus { outline: none; border-color: var(--accent-line); }
  .composer button {
    flex: none; width: auto; padding: 12px 20px;
  }
  /* chat */
  .chat { display: flex; flex-direction: column; gap: 12px; }
  .msg { display: flex; flex-direction: column; gap: 4px; max-width: 100%; }
  .msg .who {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--faint);
  }
  .bubble {
    border-radius: 8px; padding: 10px 12px; font-size: 14.5px; line-height: 1.55;
    white-space: pre-wrap; word-break: break-word;
    border: 1px solid var(--line); background: var(--card);
  }
  .msg.user { align-items: flex-end; }
  .msg.user .who { text-align: right; }
  .msg.user .bubble {
    background: var(--accent-soft); border-color: var(--accent-line);
    border-bottom-right-radius: 2px;
  }
  .msg.assistant .bubble { border-bottom-left-radius: 2px; }
  .msg.reasoning .bubble {
    background: transparent; border-style: dashed; color: var(--dim); font-style: italic;
  }
  .msg.tool .bubble {
    font-family: var(--mono); font-size: 12px; background: var(--bg); color: #c9d4e0;
    border-color: var(--line-soft);
    max-height: 40vh; overflow: auto;
  }
  .msg.tool .who { color: var(--dim); }
  .msg.note { align-items: center; }
  .note-tag {
    font-size: 11.5px; color: var(--faint); background: var(--panel);
    border: 1px solid var(--line-soft); border-radius: 4px; padding: 3px 10px;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  details.bubble { padding: 0; overflow: hidden; }
  details.bubble > summary {
    padding: 10px 12px; cursor: pointer; list-style: none; color: var(--dim);
    font-size: 13px;
  }
  details.bubble > summary::-webkit-details-marker { display: none; }
  details.bubble > pre {
    margin: 0; padding: 0 12px 12px; font: 12px/1.55 var(--mono);
    white-space: pre-wrap; word-break: break-word; color: #c9d4e0;
  }
  /* inline approval card, interleaved into the chat timeline */
  .card.approval { border-left: 2px solid var(--warn); }
  .card.approval.high { border-left-color: var(--deny); }
  .card.approval.done { border-left-color: var(--line-soft); }
  .card.approval.done.ok { border-left-color: var(--allow); }
  .card.approval.done.no { border-left-color: var(--deny); }
  .card.approval.done.warn { border-left-color: var(--warn); }
  .badge.ok { color: #6fdd8b; background: rgba(63,185,80,.1); border-color: rgba(63,185,80,.3); }
  .badge.no { color: #ff918a; background: rgba(244,112,103,.12); border-color: rgba(244,112,103,.35); }
  .badge.warn { color: #ecb46b; background: rgba(224,164,88,.1); border-color: rgba(224,164,88,.3); }
  .outcome {
    font-size: 12.5px; font-weight: 600; border-radius: 6px; padding: 8px 10px;
    background: var(--card-2); color: var(--dim);
  }
  .outcome.ok { background: rgba(63,185,80,.1); color: #6fdd8b; }
  .outcome.no { background: rgba(244,112,103,.1); color: #ff918a; }
  .outcome.warn { background: rgba(224,164,88,.1); color: #ecb46b; }
  /* activity feed */
  .feed { display: flex; flex-direction: column; }
  .feed-row {
    display: flex; align-items: baseline; gap: 10px; padding: 8px 2px;
    border-bottom: 1px solid var(--line-soft); font-size: 13px;
  }
  .feed-row:last-child { border-bottom: 0; }
  .feed-kind {
    flex: none; font-family: var(--mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: .03em; text-transform: uppercase; color: var(--faint); width: 118px;
  }
  .feed-kind.k-tool_call, .feed-kind.k-tool_result { color: #7cb8ff; }
  .feed-kind.k-approval_requested, .feed-kind.k-approval_decided { color: #ecb46b; }
  .feed-kind.k-error { color: #ff918a; }
  .feed-kind.k-user_message { color: #9ecbff; }
  .feed-kind.k-assistant_message { color: #b8c4d4; }
  .feed-text {
    flex: 1; min-width: 0; color: var(--fg);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  @media (min-width: 720px) {
    main { max-width: 760px; width: 100%; margin: 0 auto; }
    header { padding-left: max(16px, calc((100% - 760px) / 2)); padding-right: max(16px, calc((100% - 760px) / 2)); }
    .msg { max-width: 80%; }
    .msg.user { align-self: flex-end; }
  }
</style>
</head>
<body>
<header>
  <div class="row" id="hrow"></div>
  <div class="chips" id="chips"></div>
  <div class="tabs hidden" id="tabs"></div>
</header>
<main id="main"></main>

<div class="composer hidden" id="composer">
  <textarea id="promptText" rows="1" placeholder="Send a prompt to this session"
            autocapitalize="sentences" autocomplete="off" spellcheck="false"></textarea>
  <button class="primary" id="promptSend">Send</button>
</div>

<div class="overlay hidden" id="gate">
  <div class="card">
    <div class="tool">Connect to agentd</div>
    <div class="muted">Paste the token printed by <code>agentd run</code>.</div>
    <input id="tokenInput" type="password" placeholder="local API token"
           autocomplete="off" autocapitalize="off" spellcheck="false">
    <div class="actions">
      <button class="primary" id="saveToken">Connect</button>
    </div>
    <div class="muted" id="gateErr"></div>
  </div>
</div>

<script>
"use strict";
const TOKEN_KEY = "agentlink.token";
const FILTER_KEY = "agentlink.filter";
const POLL_MS = 2000;
const HOLD_MS = 1500;          // D-004: high risk needs a sustained press

/* Mirrors agentd.protocol.CHAT_KINDS: the kinds that render as chat bubbles.
   Approvals are handled separately - they fold into a single card. The
   lifecycle markers (session_*, task_*) and tool_plumbing are feed-only: they
   are the agent harness talking to itself, not the conversation. */
const CHAT_KINDS = new Set([
  "user_message", "assistant_message", "reasoning",
  "tool_call", "tool_result", "error", "note"
]);

let filter = localStorage.getItem(FILTER_KEY) || "all";
let busy = new Set();
let lastSessions = null;
let sessionInfo = null;   // summary of the open session (header)
let chatEvents = [];      // the open session's events, ascending
let chatSeq = 0;          // highest seq ingested into chatEvents
let chatLoaded = null;    // session the chat state belongs to
let lastRenderedSeq = -1; // seq the chat last rendered at
let lastApprovalsKey = null;
let lastTabKey = null;
let refreshing = false;
let tab = "chat";

/* ---------- token ---------- */
function readToken() {
  const frag = new URLSearchParams(location.hash.slice(1));
  const fromUrl = frag.get("t");
  if (fromUrl) {
    localStorage.setItem(TOKEN_KEY, fromUrl);
    history.replaceState(null, "", location.pathname + location.search);
    return fromUrl;
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}
let token = readToken();

function showGate(msg) {
  document.getElementById("gate").classList.remove("hidden");
  document.getElementById("gateErr").textContent = msg || "";
}

/* ---------- api ---------- */
const FETCH_TIMEOUT_MS = 15000;

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign(
    {
      "Authorization": "Bearer " + token,
      /* ngrok's free tier otherwise answers with an HTML warning page
         instead of JSON. Harmless when we are not behind a tunnel. */
      "ngrok-skip-browser-warning": "true"
    },
    opts.body ? { "Content-Type": "application/json" } : {},
    opts.headers || {}
  );
  /* A hung tunnel must not stack polls forever: every call gives up on its
     own, and refresh() refuses to run concurrently with itself. */
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS) : null;
  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, {
      headers: headers,
      signal: ctrl ? ctrl.signal : undefined
    }));
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (res.status === 401) { showGate("Token rejected."); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error(res.status + " " + (await res.text()).slice(0, 200));
  return res.status === 204 ? null : res.json();
}

/* ---------- helpers ---------- */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function secsLeft(iso) {
  return Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 1000));
}
function fmtLeft(s) {
  if (s <= 0) return "expired";
  const m = Math.floor(s / 60);
  return m > 0 ? m + "m " + (s % 60) + "s left" : s + "s left";
}
function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return s + "s ago";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  return Math.floor(h / 24) + "d ago";
}
function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

/* ---------- routing ---------- */
function route() {
  const m = location.hash.match(/^#\/s\/(.+)$/);
  return m ? { view: "session", id: decodeURIComponent(m[1]) } : { view: "list" };
}
function openSession(id) { location.hash = "#/s/" + encodeURIComponent(id); }
function goHome() { location.hash = ""; }

/* ---------- press-and-hold (D-004) ---------- */
let holdActive = false;        // a hold is in progress: don't rip the DOM apart
let deferredRender = null;     // a render waiting for the hold to finish

function deferRender(fn) {
  deferredRender = fn;
}

function flushDeferredRender() {
  holdActive = false;
  if (deferredRender) {
    const pending = deferredRender;
    deferredRender = null;
    pending();
  }
}

function armHold(btn, ms, done) {
  const bar = btn.querySelector(".hold-bar");
  let raf = null, start = 0, fired = false;
  const stop = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    btn.classList.remove("holding");
    if (bar) bar.style.width = "0%";
    /* A poll must never destroy the button mid-press: the render it asked
       for happens once the hold is over (or fired). */
    flushDeferredRender();
  };
  const tick = () => {
    const p = Math.min(1, (Date.now() - start) / ms);
    if (bar) bar.style.width = (p * 100) + "%";
    if (p >= 1) { fired = true; stop(); done(); return; }
    raf = requestAnimationFrame(tick);
  };
  btn.addEventListener("pointerdown", e => {
    e.preventDefault();
    if (btn.disabled || fired) return;
    holdActive = true;
    start = Date.now();
    btn.classList.add("holding");
    raf = requestAnimationFrame(tick);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach(ev =>
    btn.addEventListener(ev, stop));
}

/* ---------- decide ---------- */
async function decide(id, decision) {
  if (busy.has(id)) return;
  busy.add(id);
  try {
    await api("/v1/approvals/" + encodeURIComponent(id) + "/decision", {
      method: "POST",
      body: JSON.stringify({ decision: decision, decided_by: "phone" })
    });
    toast(decision === "allow" ? "Allowed" : "Denied");
  } catch (err) {
    toast("Failed: " + err.message);
  } finally {
    busy.delete(id);
    await refresh();
  }
}

/* ---------- approval card ---------- */
const OUTCOME = {
  allowed:   { label: "Allowed",   cls: "ok" },
  denied:    { label: "Denied",    cls: "no" },
  expired:   { label: "Expired",   cls: "warn" },
  cancelled: { label: "Cancelled", cls: "warn" }
};

/* Normalise an ApprovalRecord or an approval_requested activity event into one
   shape, so the same card renders in the chat timeline and the Approvals tab. */
function approvalView(source) {
  if (source.kind === "approval_requested") {
    const d = source.detail || {};
    return {
      approval_id: d.approval_id,
      agent_type: source.agent_type,
      workspace_path: d.workspace_path || source.workspace_path,
      tool: d.tool,
      risk: d.risk || "medium",
      reasons: d.reasons || [],
      summary: d.summary || "",
      command: d.command || null,
      paths: d.paths || [],
      expires_at: d.expires_at || null,
      state: "pending",
      decision: null,
      decided_by: null,
      reason: null,
      ts: source.ts
    };
  }
  const a = source.action || {};
  return {
    approval_id: source.approval_id,
    agent_type: source.agent_type,
    workspace_path: source.workspace_path,
    tool: source.tool && source.tool.name,
    risk: (source.risk && source.risk.level) || "medium",
    reasons: (source.risk && source.risk.reasons) || [],
    summary: a.summary || "",
    command: a.command || null,
    paths: a.paths || [],
    expires_at: source.expires_at || null,
    state: source.state || "pending",
    decision: source.decision || null,
    decided_by: source.decided_by || null,
    reason: source.decision_reason || null,
    ts: source.created_at
  };
}

function outcomeText(view) {
  const o = OUTCOME[view.state] || { label: view.state };
  let text = o.label;
  if (view.state === "expired") text += " \u2014 failed closed";
  if (view.decided_by) text += " \u00b7 by " + view.decided_by;
  if (view.reason && view.reason !== "expired") text += " \u00b7 " + view.reason;
  return text;
}

function approvalCard(view) {
  const pending = view.state === "pending";
  const needsHold = view.risk === "high";
  const body = view.command || view.summary ||
    (view.paths && view.paths.length ? view.paths.join(", ") : "(no detail)");
  const outcome = OUTCOME[view.state] || { label: view.state, cls: "" };
  let html =
    '<div class="badges">' +
      '<span class="badge ' + esc(view.agent_type) + '">' + esc(view.agent_type) + '</span>' +
      '<span class="badge ' + esc(view.risk) + '">' + esc(view.risk) + ' risk</span>' +
      '<span class="spacer"></span>' +
      (pending
        ? '<span class="muted exp" data-expires="' + esc(view.expires_at || "") + '">' +
            esc(view.expires_at ? fmtLeft(secsLeft(view.expires_at)) : "waiting") +
          '</span>'
        : '<span class="badge ' + esc(outcome.cls) + '">' + esc(outcome.label) + '</span>') +
    '</div>' +
    '<div class="tool">' + esc(view.tool) + '</div>' +
    '<pre class="cmd">' + esc(body) + '</pre>' +
    (view.reasons && view.reasons.length
      ? '<div class="reasons">' + esc(view.reasons.join(" \u00b7 ")) + '</div>' : '') +
    '<div class="meta"><span>' + esc(view.workspace_path) + '</span></div>';

  if (pending) {
    html +=
      '<div class="actions">' +
        '<button class="deny" data-act="deny">Deny</button>' +
        '<button class="allow" data-act="allow">' +
          (needsHold ? 'Hold to allow<span class="hold-hint">press and hold 1.5s</span>'
                     : 'Allow') +
          '<span class="hold-bar"></span>' +
        '</button>' +
      '</div>';
  } else {
    html +=
      '<div class="outcome ' + esc(outcome.cls) + '">' + esc(outcomeText(view)) + '</div>';
  }

  const el = document.createElement("div");
  el.className = "card approval" +
    (pending ? (needsHold ? " high" : "") : " done " + (outcome.cls || ""));
  el.innerHTML = html;

  if (!pending) return el;

  const isBusy = busy.has(view.approval_id);
  const denyBtn = el.querySelector('[data-act="deny"]');
  const allowBtn = el.querySelector('[data-act="allow"]');
  denyBtn.disabled = isBusy;
  allowBtn.disabled = isBusy;
  denyBtn.addEventListener("click", () => decide(view.approval_id, "deny"));
  if (needsHold) {
    armHold(allowBtn, HOLD_MS, () => decide(view.approval_id, "allow"));
  } else {
    allowBtn.addEventListener("click", () => decide(view.approval_id, "allow"));
  }
  return el;
}

/* ---------- session list ---------- */
function sessionCard(s) {
  const el = document.createElement("div");
  el.className = "card tap";
  const title = s.title || s.session_id;
  const pending = s.pending_approvals || 0;
  el.innerHTML =
    '<div class="badges">' +
      '<span class="badge ' + esc(s.agent_type) + '">' + esc(s.agent_type) + '</span>' +
      (s.state === "running" ? '<span class="badge low">active</span>' : '') +
      (pending ? '<span class="badge high">' + pending + ' waiting</span>' : '') +
      '<span class="spacer"></span>' +
      '<span class="muted">' + esc(ago(s.last_activity_at || s.started_at)) + '</span>' +
    '</div>' +
    '<div class="tool">' + esc(title) + '</div>' +
    (s.workspace_path
      ? '<div class="meta"><span>' + esc(s.workspace_path) + '</span></div>' : '') +
    '<div class="meta">' +
      '<span>' + (s.message_count || 0) + ' messages</span>' +
      '<span>' + esc(s.source === "transcript" ? "from transcript" : "from hooks") + '</span>' +
    '</div>';
  el.addEventListener("click", () => openSession(s.session_id));
  return el;
}

function renderSessions(items) {
  const main = document.getElementById("main");
  main.innerHTML = "";
  if (!items.length) {
    main.innerHTML =
      '<div class="empty">No sessions yet.<br>Start Codex or OpenCode and they will show up here.</div>';
    return;
  }
  items.forEach(s => main.appendChild(sessionCard(s)));
}

/* ---------- chat ---------- */
function bubbleClass(m) {
  if (m.kind === "user_message") return "user";
  if (m.kind === "reasoning") return "reasoning";
  if (m.kind === "tool_call" || m.kind === "tool_result") return "tool";
  if (m.kind === "note" || m.kind === "error") return "note";
  return "assistant";
}

function messageEl(m) {
  const kind = bubbleClass(m);
  const el = document.createElement("div");
  el.className = "msg " + kind;

  if (kind === "note") {
    el.innerHTML =
      '<div class="note-tag">' + esc(m.text || m.summary || m.kind) + '</div>';
    return el;
  }

  const who = kind === "user" ? "you"
    : kind === "tool" ? (m.tool_name || (m.detail && m.detail.tool_name) || "tool")
    : kind === "reasoning" ? "thinking"
    : "assistant";
  const body = m.text || m.summary || "";
  const long = body.length > 600;

  let inner = '<div class="who">' + esc(who) + '</div>';
  if (long) {
    inner += '<details class="bubble"><summary>' + esc(body.slice(0, 200)) + '…</summary>' +
             '<pre>' + esc(body) + '</pre></details>';
  } else {
    inner += '<div class="bubble">' + esc(body) + '</div>';
  }
  el.innerHTML = inner;
  return el;
}

/* ---------- chat timeline (messages + approvals, interleaved) ---------- */

/* Fold the activity stream into an ordered render list. Two events describe one
   approval (requested + decided); they collapse into a single card sitting at
   the position of the request. A lone approval_decided is an auto-allow or
   auto-deny from policy, so it stays feed-only and never becomes a chat card. */
function buildTimeline(events, approvals) {
  const sorted = events.slice().sort((a, b) =>
    (Date.parse(a.ts) - Date.parse(b.ts)) || (a.seq - b.seq));

  const cards = new Map();   // approval_id -> view
  const items = [];          // { type, at, seq, event | view }

  for (const e of sorted) {
    if (e.kind === "approval_requested") {
      const view = approvalView(e);
      cards.set(view.approval_id, view);
      items.push({ type: "approval", view: view, at: Date.parse(e.ts) || 0, seq: e.seq });
    } else if (e.kind === "approval_decided") {
      const d = e.detail || {};
      const view = cards.get(d.approval_id);
      if (view) {
        view.state = d.state || (d.decision === "allow" ? "allowed" : "denied");
        view.decision = d.decision || null;
        view.decided_by = d.decided_by || null;
        view.reason = d.reason || null;
      }
    } else if (CHAT_KINDS.has(e.kind)) {
      items.push({ type: "msg", event: e, at: Date.parse(e.ts) || 0, seq: e.seq });
    }
  }

  /* The approval record is authoritative for state: it survives a restart, a
     sweep and a truncated window, none of which the event stream guarantees.
     It is also the *only* trace when the request event is missing — the
     decision was taken by a daemon that predates activity recording, across a
     restart, or outside the fetched window — so every record becomes a card,
     sitting at the moment its request happened. */
  for (const a of approvals) {
    const view = cards.get(a.approval_id);
    if (view) {
      view.state = a.state;
      view.decision = a.decision;
      view.decided_by = a.decided_by;
      view.reason = a.decision_reason;
    } else {
      const fresh = approvalView(a);
      cards.set(fresh.approval_id, fresh);
      items.push({
        type: "approval", view: fresh,
        at: Date.parse(fresh.ts) || 0,
        seq: fresh.state === "pending" ? Number.MAX_SAFE_INTEGER : 0
      });
    }
  }

  /* A pending card is pinned to the bottom: an approval that still needs an
     answer is the one thing the user must not have to scroll to find. Decided
     cards stay where their request happened, so the conversation still reads
     in order. */
  items.sort((a, b) => {
    const ap = a.type === "approval" && a.view.state === "pending" ? 1 : 0;
    const bp = b.type === "approval" && b.view.state === "pending" ? 1 : 0;
    return (ap - bp) || (a.at - b.at) || (a.seq - b.seq);
  });
  return items;
}

/* The chat reads oldest-first, so opening a session should land on the newest
   turn rather than the top of the transcript. */
function scrollToNewest() {
  requestAnimationFrame(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
  });
}

function nearBottom() {
  return window.innerHeight + window.scrollY >=
    document.documentElement.scrollHeight - 120;
}

function renderTimeline(items, stickToBottom) {
  if (holdActive) { deferRender(() => renderTimeline(items, stickToBottom)); return; }
  const main = document.getElementById("main");
  main.innerHTML = "";
  if (!items.length) {
    main.innerHTML = '<div class="empty">No transcript yet for this session.</div>';
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "chat";
  items.forEach(item => wrap.appendChild(
    item.type === "approval" ? approvalCard(item.view) : messageEl(item.event)));
  main.appendChild(wrap);
  if (stickToBottom) scrollToNewest();
}

/* ---------- activity ---------- */
function renderActivity(events) {
  const main = document.getElementById("main");
  main.innerHTML = "";
  if (!events.length) {
    main.innerHTML = '<div class="empty">No activity recorded yet.</div>';
    return;
  }
  const list = document.createElement("div");
  list.className = "feed";
  events.slice().reverse().forEach(e => {
    const row = document.createElement("div");
    row.className = "feed-row";
    row.innerHTML =
      '<span class="feed-kind k-' + esc(e.kind) + '">' +
        esc(e.kind.replace(/_/g, " ")) + '</span>' +
      '<span class="feed-text">' + esc(e.summary || e.text || "") + '</span>' +
      '<span class="faint">' + esc(clock(e.ts)) + '</span>';
    list.appendChild(row);
  });
  main.appendChild(list);
}

/* ---------- approvals ---------- */
function renderApprovals(items) {
  if (holdActive) { deferRender(() => renderApprovals(items)); return; }
  const main = document.getElementById("main");
  main.innerHTML = "";
  if (!items.length) {
    main.innerHTML = '<div class="empty">Nothing waiting for a decision.</div>';
    return;
  }
  items.forEach(a => main.appendChild(approvalCard(approvalView(a))));
}

/* ---------- header ---------- */
function setHeader(view, detail) {
  const hrow = document.getElementById("hrow");
  const chips = document.getElementById("chips");
  const tabs = document.getElementById("tabs");
  hrow.innerHTML = "";

  if (view === "list") {
    chips.classList.remove("hidden");
    tabs.classList.add("hidden");
    hrow.innerHTML =
      '<span class="dot" id="dot"></span>' +
      '<span class="brand">AgentLink</span>' +
      '<span class="spacer"></span>' +
      '<span class="muted" id="hdr">connecting…</span>';
    return;
  }

  chips.classList.add("hidden");
  tabs.classList.remove("hidden");
  const title = (detail && (detail.title || detail.session_id)) || "session";
  hrow.innerHTML =
    '<button class="back" id="back" aria-label="Back">&#8592;</button>' +
    '<span class="title">' + esc(title) + '</span>' +
    '<span class="spacer"></span>' +
    '<span class="dot on" id="dot"></span>';
  document.getElementById("back").addEventListener("click", goHome);

  const pending = (detail && detail.pending_approvals) || 0;
  const defs = [["chat", "Chat"], ["activity", "Activity"], ["approvals", "Approvals"]];
  tabs.innerHTML = "";
  defs.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.className = "tab" + (tab === key ? " sel" : "");
    b.innerHTML = esc(label) +
      (key === "approvals" && pending ? '<span class="n">' + pending + '</span>' : "");
    b.addEventListener("click", () => {
      tab = key;
      lastTabKey = null;
      refresh();
    });
    tabs.appendChild(b);
  });
}

/* ---------- filter chips ---------- */
function renderChips(counts) {
  const defs = [["all", "All"], ["codex", "Codex"], ["opencode", "OpenCode"]];
  const box = document.getElementById("chips");
  box.innerHTML = "";
  defs.forEach(([key, label]) => {
    const n = key === "all"
      ? (counts.codex || 0) + (counts.opencode || 0)
      : (counts[key] || 0);
    const b = document.createElement("button");
    b.className = "chip" + (filter === key ? " sel" : "");
    b.innerHTML = esc(label) + (n ? '<span class="n">' + n + '</span>' : "");
    b.addEventListener("click", () => {
      filter = key;
      localStorage.setItem(FILTER_KEY, key);
      lastSessions = null;
      refresh();
    });
    box.appendChild(b);
  });
}

/* ---------- poll ---------- */
async function refresh() {
  if (refreshing) return;   // a slow tunnel must not stack concurrent polls
  refreshing = true;
  try {
    const r = route();
    if (r.view === "list") {
      await refreshList();
    } else {
      await refreshSession(r.id);
    }
  } catch (err) {
    const dot = document.getElementById("dot");
    if (dot) dot.className = "dot off";
    const hdr = document.getElementById("hdr");
    if (hdr) hdr.textContent = "offline";
  } finally {
    refreshing = false;
    updateComposer();
  }
}

async function refreshList() {
  const q = filter === "all" ? "" : "?agent_type=" + filter;
  const both = await Promise.all([api("/v1/status"), api("/v1/sessions" + q)]);
  const status = both[0];
  const sessions = both[1];

  setHeader("list", null);
  const dot = document.getElementById("dot");
  if (dot) dot.className = "dot on";
  const hdr = document.getElementById("hdr");
  if (hdr) hdr.textContent = status.waiting + " waiting · v" + status.version;

  renderChips(status.pending || {});

  const key = JSON.stringify(sessions.map(s =>
    [s.session_id, s.message_count, s.pending_approvals, s.last_activity_at]));
  if (key !== lastSessions) {
    lastSessions = key;
    renderSessions(sessions);
  }
}

async function refreshSession(id) {
  const base = "/v1/sessions/" + encodeURIComponent(id);
  const opening = chatLoaded !== id;

  if (opening) {
    /* One heavier call on open: the summary plus the *newest* events (the
       oldest-first window used to freeze the chat once a session passed
       its limit and hid the live end entirely). */
    const detail = await api(base + "?tail=1&limit=300");
    sessionInfo = detail;
    chatEvents = detail.events || [];
    chatSeq = chatEvents.length ? chatEvents[chatEvents.length - 1].seq : 0;
    chatLoaded = id;
  } else {
    /* Polls are incremental: only rows newer than what we hold. */
    const fresh = await api(base + "/events?after_seq=" + chatSeq + "&limit=200");
    if (fresh.length) {
      chatEvents = chatEvents.concat(fresh).slice(-900);
      chatSeq = Math.max(chatSeq, fresh[fresh.length - 1].seq);
    }
  }

  /* The approval records are authoritative for a card's state (D-020). */
  const approvals = await api(
    "/v1/approvals?session_id=" + encodeURIComponent(id) + "&limit=200");
  const aKey = JSON.stringify(approvals.map(a => [a.approval_id, a.state]));
  const changed = opening || chatSeq !== lastRenderedSeq || aKey !== lastApprovalsKey;
  lastApprovalsKey = aKey;
  if (sessionInfo) sessionInfo.pending_approvals =
    approvals.filter(a => a.state === "pending").length;
  setHeader("session", sessionInfo);

  if (!changed) return;
  lastRenderedSeq = chatSeq;

  if (tab === "chat") {
    /* Land on the newest turn when the session is opened; afterwards only
       follow along if the user was already at the bottom, so a poll never
       yanks them away from what they are reading. */
    const stick = opening || nearBottom();
    renderTimeline(buildTimeline(chatEvents, approvals), stick);
    return;
  }

  if (tab === "activity") {
    const key = "act|" + chatSeq;
    if (key !== lastTabKey) {
      lastTabKey = key;
      renderActivity(chatEvents);
    }
    return;
  }

  const key = "appr|" + aKey;
  if (key !== lastTabKey) {
    lastTabKey = key;
    renderApprovals(approvals.filter(a => a.state === "pending"));
  }
}

/* The "5m 30s left" on a pending card is a live countdown, not a snapshot
   from whenever the last render happened. */
function tickCountdowns() {
  document.querySelectorAll(".exp").forEach(el => {
    if (!el.dataset.expires) return;
    el.textContent = fmtLeft(secsLeft(el.dataset.expires));
  });
}
setInterval(tickCountdowns, 1000);

/* ---------- session input (D-026) ---------- */

async function sendPrompt() {
  const box = document.getElementById("promptText");
  const text = box.value.trim();
  if (!text) return;
  const id = route().id;
  if (!id) return;
  try {
    await api("/v1/sessions/" + encodeURIComponent(id) + "/input", {
      method: "POST",
      body: JSON.stringify({ text: text })
    });
    box.value = "";
    box.style.height = "auto";
    toast("Prompt sent — the reply will stream into the chat");
  } catch (err) {
    toast("Could not send: " + err.message);
  }
}

function updateComposer() {
  const box = document.getElementById("composer");
  if (!box) return;
  const mine = sessionInfo && sessionInfo.agent_type === "opencode" &&
               route().view === "session";
  box.classList.toggle("hidden", !mine);
  if (!mine) {
    const field = document.getElementById("promptText");
    if (field) field.value = "";
  }
}

/* ---------- live stream (D-026) ----------
   The SSE endpoint pushes a tiny "something changed" notice per ingest; the
   fetch-based reader (EventSource cannot send the Authorization header)
   then triggers a normal incremental refresh. If the stream drops — tunnel
   hiccup, daemon restart — the 2s poll keeps working and the stream retries. */
let streamAbort = null;
let streamRetry = null;

async function startStream() {
  if (streamAbort || !token) return;
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  streamAbort = ctrl;
  try {
    const res = await fetch("/v1/stream", {
      headers: {
        "Authorization": "Bearer " + token,
        "ngrok-skip-browser-warning": "true"
      },
      signal: ctrl ? ctrl.signal : undefined
    });
    if (!res.ok || !res.body) return;
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        if (frame.indexOf("event: change") === 0) refresh();
      }
    }
  } catch (err) { /* offline or aborted: the poll is the fallback */ }
  finally {
    if (streamAbort === ctrl) streamAbort = null;
    if (!streamRetry) {
      streamRetry = setTimeout(() => { streamRetry = null; startStream(); }, 4000);
    }
  }
}

/* ---------- boot ---------- */
document.getElementById("saveToken").addEventListener("click", async () => {
  const v = document.getElementById("tokenInput").value.trim();
  if (!v) return;
  token = v;
  localStorage.setItem(TOKEN_KEY, v);
  try {
    await api("/v1/status");
    document.getElementById("gate").classList.add("hidden");
    lastSessions = null;
    chatLoaded = null;
    sessionInfo = null;
    chatEvents = [];
    chatSeq = 0;
    lastRenderedSeq = -1;
    lastApprovalsKey = null;
    refresh();
  } catch (err) {
    showGate("Could not connect: " + err.message);
  }
});

window.addEventListener("hashchange", () => {
  chatLoaded = null;
  sessionInfo = null;
  chatEvents = [];
  chatSeq = 0;
  lastRenderedSeq = -1;
  lastApprovalsKey = null;
  lastTabKey = null;
  tab = "chat";
  refresh();
});

if (!token) showGate("");
refresh();
setInterval(refresh, POLL_MS);

document.getElementById("promptSend").addEventListener("click", sendPrompt);
document.getElementById("promptText").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
});
document.getElementById("promptText").addEventListener("input", e => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, window.innerHeight * 0.32) + "px";
});

startStream();

/* Installable as an app where the context is secure (the tunnel, or
   localhost); plain-LAN http just stays a tab. */
try {
  if (window.isSecureContext && navigator && navigator.serviceWorker) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
} catch (err) { /* no service worker in this context */ }
</script>
</body>
</html>
"""


def render() -> str:
    """Return the phone UI as a single HTML document."""
    return PAGE
