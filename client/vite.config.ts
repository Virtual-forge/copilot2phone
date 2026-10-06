import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { resolve } from "node:path"

/**
 * Dev proxy: the browser talks same-origin, and Vite forwards /api and /auth
 * to OpenCode's local background service with its credentials — the app needs
 * no token in the browser, and the phone reaches it over the LAN.
 *
 * The service URL + password live in ~/.local/state/opencode/service.json.
 * They change when the service restarts, so they are read at dev-server start.
 */
function opencodeService(): { target: string; password: string } {
  try {
    const file = resolve(homedir(), ".local/state/opencode/service.json")
    const raw = JSON.parse(readFileSync(file, "utf-8"))
    return { target: raw.url, password: raw.password }
  } catch {
    // no service running yet: fall back to the default serve port.
    return { target: "http://127.0.0.1:4096", password: "" }
  }
}

const service = opencodeService()
const authHeaders = service.password
  ? {
      // OpenCode's own client uses HTTP Basic with username "opencode" and
      // the service password (read from the @opencode/client service module).
      authorization: `Basic ${btoa(`opencode:${service.password}`)}`,
    }
  : undefined

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      "/api": { target: service.target, headers: authHeaders, changeOrigin: false },
      "/auth": { target: service.target, headers: authHeaders, changeOrigin: false },
    },
  },
})
