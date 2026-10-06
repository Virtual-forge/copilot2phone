import { useEffect, useRef, useState } from "react"
import type {
  ModelInfo,
  ModelRef,
  SessionInfo,
  ChatItem,
  Block,
  AgentOption,
} from "./types"
import { Markdown } from "./markdown"

/* ---------- helpers ---------- */

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

/* ---------- model picker ---------- */

export function ModelPicker({
  model,
  models,
  onPick,
}: {
  model?: ModelRef
  models: ModelInfo[]
  onPick: (m: ModelInfo) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useOutside(() => setOpen(false))
  const current = models.find(
    (m) => m.id === model?.id && m.providerID === model?.providerID,
  )
  const label = current?.name ?? model?.id ?? "Model"

  return (
    <div className="picker" ref={ref}>
      <button className="picker-btn" onClick={() => setOpen(!open)}>
        {label}
        <span className="picker-caret">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          {models.map((m) => (
            <button
              key={`${m.providerID}/${m.id}`}
              className={
                "picker-item" +
                (m.id === model?.id && m.providerID === model?.providerID
                  ? " selected"
                  : "")
              }
              onClick={() => {
                onPick(m)
                setOpen(false)
              }}
            >
              <span className="picker-item-name">{m.name}</span>
              <span className="picker-item-provider">{m.providerID}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------- agent (mode) picker ---------- */

export type { AgentOption }

export function AgentPicker({
  agent,
  agents,
  onPick,
}: {
  agent?: string
  agents: AgentOption[]
  onPick: (a: AgentOption) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useOutside(() => setOpen(false))
  const current = agents.find((a) => a.id === agent)
  const label = current?.name ?? agent ?? "Mode"

  return (
    <div className="picker" ref={ref}>
      <button className="picker-btn" onClick={() => setOpen(!open)}>
        {label}
        <span className="picker-caret">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          {agents.map((a) => (
            <button
              key={a.id}
              className={"picker-item" + (a.id === agent ? " selected" : "")}
              onClick={() => {
                onPick(a)
                setOpen(false)
              }}
            >
              <span className="picker-item-name">{a.name}</span>
              {a.description && (
                <span className="picker-item-provider">{a.description}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------- tool call block ---------- */

type ToolBlockData = Extract<Block, { type: "tool" }>

function ToolBlock({ block }: { block: ToolBlockData }) {
  const [open, setOpen] = useState(false)
  const done = block.state === "completed"
  const failed = block.state === "error"
  return (
    <div className={"tool" + (failed ? " failed" : "")}>
      <button className="tool-head" onClick={() => setOpen(!open)}>
        <span className={"tool-dot" + (done ? " ok" : failed ? " bad" : " run")} />
        <span className="tool-name">{block.name}</span>
        <span className="tool-state">{failed ? "error" : done ? "done" : block.state}</span>
        <span className="tool-caret">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="tool-body">
          <pre className="tool-io">{block.input}</pre>
          {block.output && <pre className="tool-io out">{block.output}</pre>}
        </div>
      )}
    </div>
  )
}

/* ---------- reasoning block ---------- */

function ReasoningBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="reasoning">
      <button className="reasoning-head" onClick={() => setOpen(!open)}>
        <span className="reasoning-icon">◇</span> Thinking
        <span className="tool-caret">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="reasoning-body">{text}</div>}
    </div>
  )
}

/* ---------- chat message list ---------- */

export function ChatView({ items, streaming }: { items: ChatItem[]; streaming: boolean }) {
  const endRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener("scroll", onScroll)
    return () => el.removeEventListener("scroll", onScroll)
  }, [])

  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ behavior: "auto" })
  }, [items])

  return (
    <div className="chat-scroll" ref={scrollRef}>
      <div className="chat-inner">
        {items.map((item) => {
          if (item.kind === "user") {
            return (
              <div key={item.id} className="row user">
                <div className="bubble-user">{item.text}</div>
              </div>
            )
          }
          if (item.kind === "system") {
            return (
              <div key={item.id} className="row system">
                <div className="bubble-system">{item.text}</div>
              </div>
            )
          }
          return (
            <div key={item.id} className="row assistant">
              {item.error && <div className="assistant-error">{item.error}</div>}
              {item.blocks.map((block: Block, i: number) => {
                const key = `${item.id}-${i}`
                if (block.type === "text")
                  return <Markdown key={key} text={block.text} />
                if (block.type === "reasoning")
                  return <ReasoningBlock key={key} text={block.text} />
                return <ToolBlock key={key} block={block} />
              })}
              {!item.done && streaming && <span className="cursor" />}
            </div>
          )
        })}
        <div ref={endRef} />
      </div>
    </div>
  )
}

/* ---------- empty state ---------- */

export function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="empty">
      <div className="empty-mark">◧</div>
      <h2>What should we work on?</h2>
      <p>Your OpenCode sessions, live from the desktop.</p>
      <button className="btn-primary" onClick={onCreate}>
        Start a session
      </button>
    </div>
  )
}

/* ---------- sidebar ---------- */

export function Sidebar({
  sessions,
  activeId,
  onSelect,
  onCreate,
  onDelete,
  open,
  onClose,
}: {
  sessions: SessionInfo[]
  activeId?: string
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  open: boolean
  onClose: () => void
}) {
  const groups: { label: string; sessions: SessionInfo[] }[] = []
  for (const session of sessions) {
    const label = new Date(session.time.updated).toISOString().slice(0, 10) ===
      new Date().toISOString().slice(0, 10)
      ? "Today"
      : new Date(session.time.updated).toLocaleDateString([], {
          month: "long",
          day: "numeric",
        })
    const last = groups[groups.length - 1]
    if (last && last.label === label) last.sessions.push(session)
    else groups.push({ label, sessions: [session] })
  }

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={"sidebar" + (open ? " open" : "")}>
        <div className="sidebar-top">
          <button className="new-session" onClick={onCreate}>
            <span className="new-icon">＋</span> New session
          </button>
        </div>
        <nav className="session-list">
          {groups.map((group) => (
            <div key={group.label}>
              <div className="group-label">{group.label}</div>
              {group.sessions.map((session) => (
                <div
                  key={session.id}
                  className={
                    "session-item" + (session.id === activeId ? " active" : "")
                  }
                  onClick={() => onSelect(session.id)}
                >
                  <span className="session-title">
                    {session.title || "Untitled"}
                  </span>
                  <button
                    className="session-delete"
                    title="Delete"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(session.id)
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          ))}
          {sessions.length === 0 && (
            <div className="sidebar-empty">No sessions yet</div>
          )}
        </nav>
      </aside>
    </>
  )
}
