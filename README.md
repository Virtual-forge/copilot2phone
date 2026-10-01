# AgentLink

Monitor and approve coding-agent activity (Cline, Codex) from your phone, with
strict per-agent segregation.

The full specification lives in [`docs/SPEC.md`](docs/SPEC.md). Design decisions
are recorded in [`docs/decisions.md`](docs/decisions.md).

## Status

**Slice 1 complete** — the local approval loop works end to end:

```
agent hook  ->  agentd (loopback API)  ->  decision  ->  hook unblocks
```

| Component | State |
|---|---|
| `agentd` daemon (local API, approvals, policy, audit, sessions) | ✅ slice 1 |
| Cline + Codex hook adapters | ✅ slice 1 (contracts pending Phase 0) |
| `agentlink-sim` (terminal phone) | ✅ slice 1 |
| Off-LAN access via ngrok tunnel | ✅ `agentd run --tunnel` |
| Cloud relay + E2E crypto | ⏳ slice 2 |
| Real hook installation (`agentd install`) | ⏳ slice 3 |
| Git diff engine + file browser | ⏳ slice 5 |
| Live activity stream | ⏳ slice 6 |
| PWA | ⏳ slice 7 |

## Layout

```
agentd/     PC daemon (Python) — the single source of truth
sim/        agentlink-sim, a terminal stand-in for the phone
scripts/    Phase 0 probes and the slice 1 demo
docs/       SPEC.md, decisions.md, phase0-findings.md
relay/      cloud relay (slice 2)
pwa/        phone web app (slice 7)
```

## Quick start

```powershell
pip install -e ./agentd
pip install -e ./sim

agentd doctor          # check the setup
agentd run             # start the daemon (leave this running)
```

In a second terminal:

```powershell
agentlink-sim status
agentlink-sim list --state pending
agentlink-sim watch
```

### See the whole loop

With `agentd run` in one terminal:

```powershell
.\scripts\demo-slice1.ps1
```

That fires a synthetic Cline hook (which blocks), shows the pending approval,
approves it, and prints the hook's response.

### Try it by hand

```powershell
# terminal 2 — this blocks until you decide
.\scripts\fake-hook.ps1 -Agent cline -Tool execute_command -Command "rm -rf build/"

# terminal 3
agentlink-sim list --state pending
agentlink-sim approve <id>
```

## Reaching the phone

The daemon always listens on loopback. Two ways to get your phone to it:

| Mode | Command | Works when |
|---|---|---|
| Same Wi-Fi | `agentd run --lan` | the phone is on the same network |
| Anywhere | `agentd run --tunnel` | the phone is on mobile data or another network |

`--lan` binds `0.0.0.0` and prints `http://<your-lan-ip>:47800/#t=<token>`.

`--tunnel` keeps the daemon on `127.0.0.1` and puts an
[ngrok](https://ngrok.com) tunnel in front of it, printing the public URL
instead. Because ngrok forwards to loopback, **`--tunnel` never needs `--lan`**
— the daemon is never exposed on your LAN.

### Setting up ngrok

```powershell
winget install ngrok.ngrok
ngrok config add-authtoken <your-token>   # free account
agentd doctor                             # should now show: ngrok [ok - ...]
```

Then:

```powershell
agentd run --tunnel
```

If you would rather run ngrok yourself, start `ngrok http 47800` in another
terminal and ask agentd for the URL:

```powershell
agentd tunnel
```

Notes:

- The token travels in the URL fragment (`#t=...`), which browsers never send
  to the server, so it stays out of ngrok's access logs.
- ngrok's free tier shows a one-time browser warning page. Tap through it once;
  the app's own requests send `ngrok-skip-browser-warning`, so they are never
  intercepted.
- **A tunnel URL is public.** Anyone holding the token can approve actions.
  Stop the tunnel when you are done.
- If ngrok is missing or fails to start, `agentd run --tunnel` says so and
  serves locally anyway — the tunnel is a convenience, not a gate.

## How a decision is made

1. The agent calls its `PreToolUse` hook; the adapter normalises the payload
   into an `Action` and POSTs it to `http://127.0.0.1:47800/v1/approvals`.
2. The policy engine scores it. Reads inside the workspace are allowed
   outright; everything else is asked.
3. If asked, the daemon records the approval and **blocks the hook**.
4. You decide from the phone (or `agentlink-sim`).
5. The hook returns `{"cancel": false}` (Cline) or exit `0` (Codex) to allow,
   or `{"cancel": true}` / exit `2` to block.

**Everything fails closed.** If the daemon is unreachable, the payload is
malformed, the token is missing, or the approval times out, the action is
denied.

## Tests

```powershell
cd agentd
python -m pytest -q
```

115 tests, including a real end-to-end run (a live daemon plus the real hook
entrypoints), the cross-agent segregation check, and the tunnel wiring (ngrok
is faked, so the suite passes without it installed).

## Phase 0

The exact Cline and Codex hook contracts are unverified. Run the probes on the
machine that has both agents installed and fill in
[`docs/phase0-findings.md`](docs/phase0-findings.md):

```powershell
.\scripts\probe-hook.ps1 -Agent cline
.\scripts\probe-hook.ps1 -Agent codex
```

Until then the adapters use a deliberately tolerant parser that accepts several
plausible payload shapes.
