# OpenCode client

A standalone web app that is a genuine remote for the OpenCode sessions on
your PC: list sessions, watch one stream live (the same one the desktop is
in), send prompts, switch model and agent (Build/Plan), interrupt a running
turn — the same session, two screens.

Built on `@opencode/client`, OpenCode's official TypeScript client, speaking
its HTTP API directly — no scraping, no shims.

## Run it

```sh
cd client
npm install
npm run dev
```

Vite serves on all interfaces and proxies `/api` to OpenCode's background
service (URL + auth read from `~/.local/state/opencode/service.json`), so:

- **On the PC**: open `http://localhost:5173`
- **On the phone** (same Wi-Fi): open `http://<pc-ip>:5173`

The service URL changes when the background service restarts — Vite reads it
at dev-server start, so restart `npm run dev` after restarting OpenCode.

## What it covers

| Feature | API |
|---|---|
| Session list (grouped, live) | `session.list` + `session.created/deleted` events |
| Session sync | `event.subscribe` — live-only, refetch on reconnect |
| Session switch / delete | `session.list`, `session.remove` |
| New session | `session.create` |
| Send a message (steer) | `session.prompt({ delivery: "steer" })` |
| Stop a running turn | `session.interrupt` |
| Model switch | `session.switchModel` + `model.list` |
| Mode switch (Build/Plan) | `session.switchAgent` + `agent.list` |
| Live text | `session.text.delta` & friends → debounced refetch |

## Production use

The dev proxy is fine for personal use. For anywhere-access, put the built
app behind the same origin as an `opencode serve --hostname 0.0.0.0 --port
4096` (Tailscale recommended — see `docs/mobile-app-plan.md` M3), or add the
`--cors` flag and connect the app directly with a pairing token.

## Design notes

Deliberately its own thing — restrained graphite surfaces, one green accent,
16/24px radii, no gradients, no pill shapes. Hand-rolled CSS (~400 lines),
zero UI dependencies beyond React.
