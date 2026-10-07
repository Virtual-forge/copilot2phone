import { useEffect, useRef, useState } from "react"
import { client } from "./api"
import type { McpServer, ModelRef } from "@opencode/client"

/* ---------- primitives ---------- */

function useOutside(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [onClose])
  return ref
}

function StatusDot({ status }: { status: string }) {
  const cls =
    status === "connected"
      ? "ok"
      : status === "failed"
        ? "bad"
        : status === "needs_auth"
          ? "warn"
          : ""
  return <span className={"tool-dot " + cls} />
}

/* ---------- the ⋮ menu ---------- */

export type Panel = "mcp" | "skills" | null

export function SettingsMenu({
  onOpen,
}: {
  onOpen: (panel: Exclude<Panel, null>) => void
}) {
  // Internal open state: deliberately NOT tied to the panel — tying them
  // meant the menu stayed open behind the panel and every click inside the
  // panel hit this menu's outside-handler, closing the panel instantly.
  const [open, setOpen] = useState(false)
  const ref = useOutside(() => setOpen(false))
  return (
    <div className="picker" ref={ref}>
      <button
        className="icon-btn"
        aria-label="Settings"
        onClick={() => setOpen(!open)}
      >
        ⋮
      </button>
      {open && (
        <div className="picker-menu menu-right">
          <button
            className="picker-item"
            onClick={() => {
              setOpen(false)
              onOpen("mcp")
            }}
          >
            <span className="picker-item-name">MCP servers</span>
            <span className="picker-item-provider">
              add, edit, remove, connect, resources
            </span>
          </button>
          <button
            className="picker-item"
            onClick={() => {
              setOpen(false)
              onOpen("skills")
            }}
          >
            <span className="picker-item-name">Skills</span>
            <span className="picker-item-provider">
              view, add, remove SKILL.md files
            </span>
          </button>
        </div>
      )}
    </div>
  )
}

/* ---------- MCP panel ---------- */

type McpConfigShape = {
  type?: string
  url?: string
  command?: string[]
  cwd?: string
  environment?: Record<string, string>
}

function McpForm({
  initialName,
  initialConfig,
  onSubmit,
  onClose,
}: {
  initialName?: string
  initialConfig?: McpConfigShape | null
  onSubmit: (name: string, config: Record<string, unknown>) => void
  onClose: () => void
}) {
  const editing = Boolean(initialName)
  const initial: McpConfigShape = initialConfig ?? {}
  const [name, setName] = useState(initialName ?? "")
  const [type, setType] = useState<"local" | "remote">(
    initial.type === "local" ? "local" : "remote",
  )
  const [url, setUrl] = useState(initial.url ?? "")
  const [command, setCommand] = useState((initial.command ?? []).join(" "))
  const [cwd, setCwd] = useState(initial.cwd ?? "")
  const [env, setEnv] = useState(
    Object.entries(initial.environment ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
  )

  const submit = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    const config: Record<string, unknown> = { type, enabled: true }
    if (type === "remote") {
      const parsed = url.trim()
      if (!parsed) return
      config.url = parsed
    } else {
      const argv = command.trim().split(/\s+/)
      if (!argv.length) return
      config.command = argv
      if (cwd.trim()) config.cwd = cwd.trim()
    }
    const envLines = env
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
    if (envLines.length) {
      const environment: Record<string, string> = {}
      for (const line of envLines) {
        const eq = line.indexOf("=")
        if (eq > 0) environment[line.slice(0, eq)] = line.slice(eq + 1)
      }
      if (Object.keys(environment).length) config.environment = environment
    }
    onSubmit(trimmed, config)
    onClose()
  }

  return (
    <div className="panel-form">
      <input
        className="field"
        placeholder="name (e.g. github)"
        value={name}
        disabled={editing}
        onChange={(e) => setName(e.target.value)}
      />
      <div className="seg">
        <button
          className={"seg-btn" + (type === "remote" ? " sel" : "")}
          onClick={() => setType("remote")}
        >
          Remote (URL)
        </button>
        <button
          className={"seg-btn" + (type === "local" ? " sel" : "")}
          onClick={() => setType("local")}
        >
          Local (command)
        </button>
      </div>
      {type === "remote" ? (
        <input
          className="field"
          placeholder="https://example.com/mcp"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      ) : (
        <>
          <input
            className="field"
            placeholder="command + args (e.g. npx -y @modelcontextprotocol/server-github)"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
          />
          <input
            className="field"
            placeholder="working directory (optional)"
            value={cwd}
            onChange={(e) => setCwd(e.target.value)}
          />
        </>
      )}
      <textarea
        className="field"
        rows={2}
        placeholder={"environment variables, KEY=value per line (optional)"}
        value={env}
        onChange={(e) => setEnv(e.target.value)}
      />
      <div className="form-actions">
        <button className="btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button className="btn-primary" onClick={submit}>
          {editing ? "Save changes" : "Add server"}
        </button>
      </div>
    </div>
  )
}

export function McpPanel({
  sessionDirectory,
  onClose,
}: {
  sessionDirectory?: string
  onClose: () => void
}) {
  const [servers, setServers] = useState<McpServer[]>([])
  const [configs, setConfigs] = useState<Record<string, McpConfigShape>>({})
  const [resources, setResources] = useState<Record<string, { name: string; uri: string }[]>>({})
  const [showResources, setShowResources] = useState(false)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const location = sessionDirectory
    ? { location: { directory: sessionDirectory } }
    : {}

  const load = async () => {
    try {
      const res = await client.mcp.list(location)
      setServers(res.data)
      // pre-fill source for Edit: the config layers carry mcp.servers
      try {
        const cfg = await client.config.get()
        const layers = (
          Array.isArray(cfg) ? cfg : ((cfg as { data?: unknown }).data ?? [])
        ) as Array<{ info?: { mcp?: { servers?: Record<string, McpConfigShape> } } }>
        const merged: Record<string, McpConfigShape> = {}
        for (const layer of layers) {
          const servers = layer?.info?.mcp?.servers
          if (servers && typeof servers === "object") Object.assign(merged, servers)
        }
        setConfigs(merged)
      } catch {
        /* pre-fill is best-effort */
      }
      setError(null)
    } catch (err) {
      setError(String(err))
    }
  }

  useEffect(() => {
    load()
  }, [sessionDirectory])

  const upsert = async (name: string, config: Record<string, unknown>) => {
    setBusy(name)
    try {
      await client.mcp.add({ server: name, config, ...location } as never)
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (name: string) => {
    setBusy(name)
    try {
      await client.mcp.remove({ server: name, ...location })
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  const toggleConnection = async (server: McpServer) => {
    const connected = server.status.status === "connected"
    setBusy(server.name)
    try {
      if (connected) {
        await client.mcp.disconnect({ server: server.name, ...location })
      } else {
        await client.mcp.connect({ server: server.name, ...location })
      }
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  const loadResources = async () => {
    if (showResources) {
      setShowResources(false)
      return
    }
    try {
      const res = await client.mcp.resource.catalog(location)
      const byServer: Record<string, { name: string; uri: string }[]> = {}
      for (const r of res.data.resources) {
        byServer[r.server] = [...(byServer[r.server] ?? []), { name: r.name, uri: r.uri }]
      }
      setResources(byServer)
      setShowResources(true)
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>MCP servers</h3>
        <div className="panel-actions">
          <button className="btn-ghost" onClick={loadResources}>
            {showResources ? "Hide resources" : "Resources"}
          </button>
          <button
            className="btn-ghost"
            onClick={() => {
              setAdding(!adding)
              setEditing(null)
            }}
          >
            {adding ? "Close" : "Add"}
          </button>
          <button className="btn-ghost" onClick={onClose}>
            ✕
          </button>
        </div>
      </div>

      {error && <div className="panel-error">{error}</div>}

      {adding && (
        <McpForm onSubmit={upsert} onClose={() => setAdding(false)} />
      )}
      {editing && (
        <McpForm
          initialName={editing}
          initialConfig={configs[editing] ?? null}
          onSubmit={upsert}
          onClose={() => setEditing(null)}
        />
      )}

      <div className="panel-list">
        {servers.map((s) => (
          <div key={s.name} className="panel-row">
            <StatusDot status={s.status.status} />
            <div className="panel-row-main">
              <span className="panel-row-name">{s.name}</span>
              <span className="panel-row-sub">
                {s.status.status === "failed" || s.status.status === "needs_auth"
                  ? (s.status as { error?: string }).error || s.status.status
                  : s.status.status}
              </span>
            </div>
            <div className="panel-row-actions">
              <button
                className="btn-ghost"
                disabled={busy === s.name}
                onClick={() => {
                  setEditing(s.name)
                  setAdding(false)
                }}
              >
                Edit
              </button>
              {s.status.status !== "pending" && (
                <button
                  className="btn-ghost"
                  disabled={busy === s.name}
                  onClick={() => toggleConnection(s)}
                >
                  {s.status.status === "connected" ? "Disconnect" : "Connect"}
                </button>
              )}
              <button
                className="btn-ghost danger"
                disabled={busy === s.name}
                onClick={() => remove(s.name)}
              >
                Remove
              </button>
            </div>
            {showResources && resources[s.name] && (
              <div className="panel-resources">
                {resources[s.name].map((r) => (
                  <div key={r.uri} className="panel-resource">
                    <span className="panel-row-name">{r.name}</span>
                    <span className="panel-row-sub">{r.uri}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {servers.length === 0 && (
          <div className="panel-empty">No MCP servers configured</div>
        )}
      </div>
    </div>
  )
}

/* ---------- Skills panel ---------- */

type SkillEntry = {
  id: string
  name: string
  description?: string
  path: string
  content: string
}

function SkillAddForm({
  onAdd,
  onClose,
}: {
  onAdd: (name: string, description: string, instructions: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")

  const submit = () => {
    const trimmed = name.trim()
    if (!trimmed || !description.trim()) return
    onAdd(trimmed, description.trim(), instructions)
    onClose()
  }

  return (
    <div className="panel-form">
      <input
        className="field"
        placeholder="skill id (e.g. triage-ci)"
        value={name}
        onChange={(e) => setName(e.target.value.replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase())}
      />
      <input
        className="field"
        placeholder="when to use it (description — the agent reads this)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <textarea
        className="field"
        rows={5}
        placeholder="instructions (the SKILL.md body)"
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />
      <div className="form-actions">
        <button className="btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button className="btn-primary" onClick={submit}>
          Create skill
        </button>
      </div>
    </div>
  )
}

export function SkillsPanel({
  sessionDirectory,
  onClose,
}: {
  sessionDirectory?: string
  onClose: () => void
}) {
  const [skills, setSkills] = useState<SkillEntry[]>([])
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const location = sessionDirectory
    ? { location: { directory: sessionDirectory } }
    : undefined

  const load = async () => {
    try {
      const res = await client.skill.list(location)
      setSkills(res.data as SkillEntry[])
      setError(null)
    } catch (err) {
      setError(String(err))
    }
  }

  useEffect(() => {
    load()
  }, [sessionDirectory])

  const skillDirFor = (path?: string): string => {
    if (sessionDirectory) return sessionDirectory
    if (path) {
      // derive the project root from an existing skill path: <root>/.opencode/skills/...
      const marker = path.replace(/\\/g, "/").indexOf("/.opencode/skills/")
      if (marker > 0) return path.slice(0, marker)
    }
    return ""
  }

  const add = async (name: string, description: string, instructions: string) => {
    setBusy(name)
    try {
      const dir = skillDirFor(skills[0]?.path) || sessionDirectory || ""
      const target = `${dir.replace(/\\/g, "/").replace(/\/$/, "")}/.opencode/skills/${name}/SKILL.md`
      const content = `---\nname: ${name}\ndescription: ${description}\n---\n\n${instructions || description}\n`
      const bytes = new TextEncoder().encode(content)
      await client.file.write({ location: { directory: dir }, path: target, payload: bytes })
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (skill: SkillEntry) => {
    setBusy(skill.name)
    try {
      // no fs/delete in the API: run the removal through the shell API
      const dir = skillDirFor(skill.path)
      const winPath = skill.path.replace(/\//g, "\\")
      const command = `Remove-Item -Recurse -Force '${winPath}'`
      await client.shell.create(
        { command, ...(dir ? { location: { directory: dir } } : {}) } as never,
      )
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Skills</h3>
        <div className="panel-actions">
          <button className="btn-ghost" onClick={() => setAdding(!adding)}>
            {adding ? "Close" : "Add"}
          </button>
          <button className="btn-ghost" onClick={onClose}>
            ✕
          </button>
        </div>
      </div>

      {error && <div className="panel-error">{error}</div>}
      {sessionDirectory === undefined && (
        <div className="panel-note">
          Open a session to scope skills to its project.
        </div>
      )}

      {adding && (
        <SkillAddForm onAdd={add} onClose={() => setAdding(false)} />
      )}

      <div className="panel-list">
        {skills.map((s) => {
          const isBuiltin = s.path.startsWith("/builtin")
          return (
            <div key={s.id} className="panel-row">
              <div className="panel-row-main">
                <span className="panel-row-name">{s.name}</span>
                <span className="panel-row-sub">
                  {s.description || (isBuiltin ? "built-in" : s.path)}
                </span>
              </div>
              <div className="panel-row-actions">
                {!isBuiltin && (
                  <>
                    <button
                      className="btn-ghost"
                      onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                    >
                      {expanded === s.id ? "Hide" : "View"}
                    </button>
                    <button
                      className="btn-ghost danger"
                      disabled={busy === s.name}
                      onClick={() => remove(s)}
                    >
                      Remove
                    </button>
                  </>
                )}
              </div>
              {expanded === s.id && (
                <div className="panel-resource">
                  <pre className="tool-io">{s.content}</pre>
                </div>
              )}
            </div>
          )
        })}
        {skills.length === 0 && (
          <div className="panel-empty">No skills discovered</div>
        )}
      </div>
    </div>
  )
}

/* ---------- the panel host ---------- */

export function SettingsPanel({
  panel,
  sessionDirectory,
  onClose,
}: {
  panel: Panel
  sessionDirectory?: string
  onClose: () => void
}) {
  if (!panel) return null
  return (
    <>
      <div className="scrim-high" onClick={onClose} />
      <div className="panel-host">
        {panel === "mcp" ? (
          <McpPanel sessionDirectory={sessionDirectory} onClose={onClose} />
        ) : (
          <SkillsPanel sessionDirectory={sessionDirectory} onClose={onClose} />
        )}
      </div>
    </>
  )
}

export type { ModelRef }
