"""A minimal phone UI served by ``agentd`` (preview of the slice 7 PWA).

This is a single self-contained page: no build step, no dependencies, no
bundler. It exists so the approval loop can be exercised from a real phone over
the LAN before the relay and the real PWA exist.

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
<meta name="theme-color" content="#0b0f14">
<title>AgentLink</title>
<style>
  :root {
    --bg: #0b0f14; --card: #151b23; --card-2: #1c242e; --line: #263140;
    --fg: #e6edf3; --dim: #8b98a5; --accent: #4c9aff;
    --allow: #2ea043; --deny: #d1242f; --warn: #d29922;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
  body {
    font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: env(safe-area-inset-top) env(safe-area-inset-right)
             env(safe-area-inset-bottom) env(safe-area-inset-left);
    overscroll-behavior-y: contain;
  }
  header {
    position: sticky; top: 0; z-index: 10; background: rgba(11,15,20,.92);
    backdrop-filter: blur(12px); border-bottom: 1px solid var(--line);
    padding: 12px 14px 10px;
  }
  .row { display: flex; align-items: center; gap: 8px; }
  .brand { font-weight: 650; letter-spacing: -.01em; font-size: 17px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); flex: none; }
  .dot.on { background: var(--allow); box-shadow: 0 0 0 3px rgba(46,160,67,.18); }
  .dot.off { background: var(--deny); }
  .spacer { flex: 1; }
  .muted { color: var(--dim); font-size: 12.5px; }
  .chips { display: flex; gap: 6px; margin-top: 10px; overflow-x: auto; scrollbar-width: none; }
  .chips::-webkit-scrollbar { display: none; }
  .chip {
    flex: none; padding: 5px 12px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--card); color: var(--dim); font-size: 13px; font-weight: 550;
  }
  .chip.sel { background: var(--accent); border-color: var(--accent); color: #fff; }
  .chip .n { opacity: .75; margin-left: 4px; }
  main { padding: 12px 14px 40px; display: flex; flex-direction: column; gap: 10px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 13px 14px; display: flex; flex-direction: column; gap: 9px;
  }
  .card.high { border-color: rgba(209,36,47,.5); }
  .badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .badge {
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    padding: 3px 7px; border-radius: 6px; background: var(--card-2); color: var(--dim);
  }
  .badge.cline { background: rgba(76,154,255,.16); color: #7cb3ff; }
  .badge.codex { background: rgba(163,113,247,.16); color: #b79bff; }
  .badge.low { background: rgba(46,160,67,.16); color: #56d364; }
  .badge.medium { background: rgba(210,153,34,.16); color: #e3b341; }
  .badge.high { background: rgba(209,36,47,.18); color: #ff7b72; }
  .tool { font-weight: 600; font-size: 15px; }
  pre.cmd {
    margin: 0; padding: 9px 10px; background: #0d1117; border: 1px solid var(--line);
    border-radius: 9px; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: pre-wrap; word-break: break-word; color: #c9d1d9; max-height: 30vh; overflow: auto;
  }
  .meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--dim); }
  .meta span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .reasons { font-size: 12px; color: var(--warn); }
  .actions { display: flex; gap: 8px; margin-top: 2px; }
  button {
    flex: 1; position: relative; overflow: hidden; border: 0; border-radius: 11px;
    padding: 13px 10px; font-size: 15px; font-weight: 650; color: #fff;
    font-family: inherit; touch-action: manipulation; user-select: none;
  }
  button:disabled { opacity: .45; }
  .deny { background: var(--deny); }
  .allow { background: var(--allow); }
  .hold-bar {
    position: absolute; left: 0; top: 0; bottom: 0; width: 0%;
    background: rgba(255,255,255,.28); pointer-events: none;
  }
  .hold-hint { font-size: 11px; font-weight: 600; opacity: .85; display: block; }
  .empty { text-align: center; color: var(--dim); padding: 56px 20px; font-size: 14px; }
  .empty .big { font-size: 30px; margin-bottom: 10px; opacity: .5; }
  .banner {
    background: rgba(209,36,47,.14); border: 1px solid rgba(209,36,47,.4);
    color: #ff7b72; border-radius: 11px; padding: 10px 12px; font-size: 13px;
  }
  .banner.away { background: rgba(210,153,34,.14); border-color: rgba(210,153,34,.4); color: #e3b341; }
  .overlay {
    position: fixed; inset: 0; background: rgba(5,8,12,.9); z-index: 50;
    display: flex; align-items: center; justify-content: center; padding: 22px;
  }
  .overlay .card { width: 100%; max-width: 380px; }
  input {
    width: 100%; padding: 12px; border-radius: 10px; border: 1px solid var(--line);
    background: #0d1117; color: var(--fg); font: 14px ui-monospace, Menlo, monospace;
  }
  .hidden { display: none !important; }
  .toast {
    position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%);
    background: var(--card-2); border: 1px solid var(--line); color: var(--fg);
    padding: 10px 16px; border-radius: 999px; font-size: 13.5px; z-index: 60;
    box-shadow: 0 8px 28px rgba(0,0,0,.5);
  }
</style>
</head>
<body>
<header>
  <div class="row">
    <span class="dot" id="dot"></span>
    <span class="brand">AgentLink</span>
    <span class="spacer"></span>
    <span class="muted" id="hdr">connecting…</span>
  </div>
  <div class="chips" id="chips"></div>
</header>
<main id="main"></main>

<div class="overlay hidden" id="gate">
  <div class="card">
    <div class="tool">Connect to agentd</div>
    <div class="muted">Paste the token printed by <code>agentd run</code>.</div>
    <input id="tokenInput" type="password" placeholder="local API token"
           autocomplete="off" autocapitalize="off" spellcheck="false">
    <div class="actions">
      <button class="allow" id="saveToken">Connect</button>
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

let filter = localStorage.getItem(FILTER_KEY) || "all";
let busy = new Set();
let lastPending = null;

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
  const res = await fetch(path, Object.assign({}, opts, { headers }));
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
function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

/* ---------- press-and-hold (D-004) ---------- */
function armHold(btn, ms, done) {
  const bar = btn.querySelector(".hold-bar");
  let raf = null, start = 0, fired = false;
  const stop = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    btn.classList.remove("holding");
    if (bar) bar.style.width = "0%";
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
    lastPending = null;
    await refresh();
  }
}

/* ---------- render ---------- */
function card(a) {
  const risk = (a.risk && a.risk.level) || "medium";
  const reasons = (a.risk && a.risk.reasons) || [];
  const d = a.action || {};
  const body = d.command || d.summary || (d.paths && d.paths.join(", ")) || "(no detail)";
  const left = secsLeft(a.expires_at);
  const isBusy = busy.has(a.approval_id);
  const needsHold = risk === "high";

  const el = document.createElement("div");
  el.className = "card" + (needsHold ? " high" : "");
  el.innerHTML =
    '<div class="badges">' +
      '<span class="badge ' + esc(a.agent_type) + '">' + esc(a.agent_type) + '</span>' +
      '<span class="badge ' + esc(risk) + '">' + esc(risk) + ' risk</span>' +
      '<span class="spacer"></span>' +
      '<span class="muted">' + esc(fmtLeft(left)) + '</span>' +
    '</div>' +
    '<div class="tool">' + esc(a.tool && a.tool.name) + '</div>' +
    '<pre class="cmd">' + esc(body) + '</pre>' +
    (reasons.length ? '<div class="reasons">' + esc(reasons.join(" · ")) + '</div>' : '') +
    '<div class="meta"><span>' + esc(a.workspace_path) + '</span></div>' +
    '<div class="actions">' +
      '<button class="deny" data-act="deny">Deny</button>' +
      '<button class="allow" data-act="allow">' +
        (needsHold ? 'Hold to allow<span class="hold-hint">press and hold 1.5s</span>'
                   : 'Allow') +
        '<span class="hold-bar"></span>' +
      '</button>' +
    '</div>';

  const denyBtn = el.querySelector('[data-act="deny"]');
  const allowBtn = el.querySelector('[data-act="allow"]');
  denyBtn.disabled = isBusy;
  allowBtn.disabled = isBusy;
  denyBtn.addEventListener("click", () => decide(a.approval_id, "deny"));
  if (needsHold) {
    armHold(allowBtn, HOLD_MS, () => decide(a.approval_id, "allow"));
  } else {
    allowBtn.addEventListener("click", () => decide(a.approval_id, "allow"));
  }
  return el;
}

function renderChips(counts) {
  const defs = [["all", "All"], ["cline", "Cline"], ["codex", "Codex"]];
  const box = document.getElementById("chips");
  box.innerHTML = "";
  defs.forEach(([key, label]) => {
    const n = key === "all"
      ? (counts.cline || 0) + (counts.codex || 0)
      : (counts[key] || 0);
    const b = document.createElement("button");
    b.className = "chip" + (filter === key ? " sel" : "");
    b.innerHTML = esc(label) + (n ? '<span class="n">' + n + '</span>' : "");
    b.addEventListener("click", () => {
      filter = key;
      localStorage.setItem(FILTER_KEY, key);
      lastPending = null;
      refresh();
    });
    box.appendChild(b);
  });
}

function renderList(items) {
  const main = document.getElementById("main");
  main.innerHTML = "";
  if (!items.length) {
    main.innerHTML =
      '<div class="empty"><div class="big">&#10003;</div>Nothing waiting for you.</div>';
    return;
  }
  items.forEach(a => main.appendChild(card(a)));
}

/* ---------- poll ---------- */
async function refresh() {
  let status, items;
  try {
    const q = filter === "all" ? "" : "&agent_type=" + filter;
    const both = await Promise.all([
      api("/v1/status"),
      api("/v1/approvals?state=pending&limit=50" + q)
    ]);
    status = both[0];
    items = both[1];
  } catch (err) {
    document.getElementById("dot").className = "dot off";
    document.getElementById("hdr").textContent = "offline";
    return;
  }

  document.getElementById("dot").className = "dot on";
  document.getElementById("hdr").textContent =
    status.waiting + " waiting · v" + status.version;
  renderChips(status.pending || {});

  const main = document.getElementById("main");
  const key = JSON.stringify(items.map(i => [i.approval_id, i.state]));
  if (key !== lastPending) {
    lastPending = key;
    renderList(items);
  }
  const existing = main.querySelector(".banner");
  if (status.away && !existing) {
    main.insertAdjacentHTML("afterbegin",
      '<div class="banner away">Away mode is on — unanswered requests are denied.</div>');
  } else if (!status.away && existing) {
    existing.remove();
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
    lastPending = null;
    refresh();
  } catch (err) {
    showGate("Could not connect: " + err.message);
  }
});

if (!token) showGate("");
refresh();
setInterval(refresh, POLL_MS);
</script>
</body>
</html>
"""


def render() -> str:
    """Return the phone UI as a single HTML document."""
    return PAGE
