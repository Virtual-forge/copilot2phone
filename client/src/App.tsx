import { useCallback, useEffect, useRef, useState } from "react"
import { client } from "./api"
import type {
  AgentInfo,
  ModelInfo,
  ModelRef,
  SessionInfo,
  SessionMessageInfo,
  V2Event,
} from "@opencode/client"
import { ChatView, EmptyState, Sidebar } from "./components"
import type { AgentOption, ChatItem } from "./types"
import { Composer } from "./composer"
import { toChatItems } from "./types"

const DEFAULT_DIRECTORY = ""

type Running = Record<string, boolean> // sessionID -> busy

export function App() {
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [activeId, setActiveId] = useState<string | undefined>(undefined)
  const [items, setItems] = useState<ChatItem[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [running, setRunning] = useState<Running>({})
  const [connected, setConnected] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const activeSession = sessions.find((s) => s.id === activeId)
  const activeRef = useRef<string | undefined>(undefined)
  activeRef.current = activeId

  /* ---------- data loading ---------- */

  const loadSessions = useCallback(async () => {
    try {
      const res = await client.session.list()
      setSessions(res.data)
      setConnected(true)
      return res.data
    } catch {
      setConnected(false)
      return []
    }
  }, [])

  const loadMessages = useCallback(async (sessionID: string) => {
    try {
      const res = await client.message.list({ sessionID, order: "asc", limit: 200 })
      setItems(toChatItems(res.data.reverse()))
    } catch {
      setItems([])
    }
  }, [])

  const loadMeta = useCallback(async () => {
    try {
      const [m, a, active] = await Promise.all([
        client.model.list(),
        client.agent.list(),
        client.session.active(),
      ])
      const usable = m.data.filter((x) => x.enabled && x.status !== "deprecated")
      setModels(usable)
      setAgents(
        a.data
          .filter((x: AgentInfo) => x.mode !== "subagent" && !x.hidden)
          .map((x) => ({ id: x.id, name: x.name, description: x.description })),
      )
      const busy: Running = {}
      for (const id of Object.keys(active)) {
        busy[id] = true
      }
      setRunning(busy)
    } catch {
      /* the server will retry on next event */
    }
  }, [])

  /* ---------- event pump: the live sync ---------- */

  useEffect(() => {
    let cancelled = false
    let iterator: AsyncIterator<V2Event> | undefined

    const refreshSessions = debounce(async () => {
      await loadSessions()
    }, 300)

    const refreshActive = debounce(async () => {
      const id = activeRef.current
      if (id) await loadMessages(id)
    }, 250)

    const start = async () => {
      try {
        for await (const event of client.event.subscribe()) {
          if (cancelled) return
          handleEvent(event)
        }
      } catch {
        // live-only, no auto-reconnect: back off and resubscribe
        if (!cancelled) setTimeout(start, 2000)
      }
    }

    const handleEvent = (event: V2Event) => {
      const type = event.type as string
      const data = (event as { data?: Record<string, unknown> }).data ?? {}
      const sessionID = data.sessionID as string | undefined
      const active = activeRef.current

      if (type.startsWith("session.created") || type.startsWith("session.deleted")) {
        refreshSessions()
        return
      }

      if (type === "session.execution.started" && sessionID) {
        setRunning((r) => ({ ...r, [sessionID]: true }))
        refreshSessions()
        return
      }
      if (
        (type === "session.execution.succeeded" ||
          type === "session.execution.failed" ||
          type === "session.execution.interrupted") &&
        sessionID
      ) {
        setRunning((r) => ({ ...r, [sessionID]: false }))
        refreshSessions()
        if (sessionID === active) refreshActive()
        return
      }

      // anything content-shaped in the open session: re-render it live
      if (sessionID && sessionID === active) {
        if (
          type.startsWith("session.text.") ||
          type.startsWith("session.reasoning.") ||
          type.startsWith("session.tool.") ||
          type.startsWith("session.step.") ||
          type === "session.usage.updated" ||
          type === "session.inbox.delivered"
        ) {
          refreshActive()
        }
      }
    }

    start()
    return () => {
      cancelled = true
    }
  }, [loadSessions, loadMessages])

  /* ---------- boot ---------- */

  useEffect(() => {
    loadSessions().then((list) => {
      if (list.length > 0) {
        const mostRecent = [...list].sort(
          (a, b) => b.time.updated - a.time.updated,
        )[0]
        setActiveId(mostRecent.id)
      }
    })
    loadMeta()
  }, [loadSessions, loadMeta])

  useEffect(() => {
    if (activeId) {
      setItems([])
      loadMessages(activeId)
    } else {
      setItems([])
    }
  }, [activeId, loadMessages])

  /* ---------- actions ---------- */

  const createSession = async (): Promise<string | undefined> => {
    try {
      const session = await client.session.create()
      await loadSessions()
      setActiveId(session.id)
      setSidebarOpen(false)
      return session.id
    } catch (err) {
      console.error(err)
      return undefined
    }
  }

  const send = async (text: string) => {
    const id = activeId ?? (await createSession())
    if (!id) return
    // optimistic user bubble
    const tempId = `temp-${Date.now()}`
    setItems((prev) => [...prev, { kind: "user", id: tempId, text, time: Date.now() }])
    setRunning((r) => ({ ...r, [id]: true }))
    try {
      await client.session.prompt({ sessionID: id, text, delivery: "steer" })
    } catch (err) {
      console.error(err)
    }
  }

  const stop = async () => {
    if (!activeId) return
    try {
      await client.session.interrupt({ sessionID: activeId })
    } catch (err) {
      console.error(err)
    }
  }

  const removeSession = async (id: string) => {
    try {
      await client.session.remove({ sessionID: id })
      if (id === activeId) setActiveId(undefined)
      await loadSessions()
    } catch (err) {
      console.error(err)
    }
  }

  const pickModel = async (m: ModelInfo) => {
    if (!activeId) return
    try {
      await client.session.switchModel({
        sessionID: activeId,
        model: { id: m.id, providerID: m.providerID, variant: m.variants[0]?.id },
      })
      await loadSessions()
    } catch (err) {
      console.error(err)
    }
  }

  const pickAgent = async (a: AgentOption) => {
    if (!activeId) return
    try {
      await client.session.switchAgent({ sessionID: activeId, agent: a.id })
      await loadSessions()
    } catch (err) {
      console.error(err)
    }
  }

  /* ---------- render ---------- */

  const busy = activeId ? Boolean(running[activeId]) : false

  return (
    <div className={"app" + (sidebarOpen ? " shifted" : "")}>
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id)
          setSidebarOpen(false)
        }}
        onCreate={createSession}
        onDelete={removeSession}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="main">
        <header className="header">
          <button
            className="menu-btn"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            aria-label="Menu"
          >
            ☰
          </button>
          <div className="header-title">
            {activeSession?.title || "New session"}
          </div>
          <div className="header-meta">
            {busy && <span className="running-dot" title="Working" />}
            <span className={"conn " + (connected ? "on" : "off")}>
              {connected ? "live" : "offline"}
            </span>
          </div>
        </header>

        {activeId ? (
          <>
            <ChatView items={items} streaming={busy} />
            <Composer
              disabled={!connected}
              running={busy}
              onSend={send}
              onStop={stop}
              model={activeSession?.model as ModelRef | undefined}
              models={models}
              onModelPick={pickModel}
              agent={activeSession?.agent}
              agents={agents}
              onAgentPick={pickAgent}
            />
          </>
        ) : (
          <EmptyState onCreate={createSession} />
        )}
      </main>
    </div>
  )
}

/* ---------- tiny debounce ---------- */

function debounce<T extends () => Promise<void>>(fn: T, ms: number): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  return () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void fn(), ms)
  }
}
