import { useEffect, useRef, useState } from "react"
import type { ModelInfo, ModelRef, ChatItem, AgentOption } from "./types"
import { ModelPicker, AgentPicker } from "./components"

export type { ModelInfo, ModelRef, ChatItem, AgentOption }

export function Composer({
  disabled,
  running,
  onSend,
  onStop,
  model,
  models,
  onModelPick,
  agent,
  agents,
  onAgentPick,
}: {
  disabled: boolean
  running: boolean
  onSend: (text: string) => void
  onStop: () => void
  model?: { id: string; providerID: string; variant?: string }
  models: ModelInfo[]
  onModelPick: (m: ModelInfo) => void
  agent?: string
  agents: AgentOption[]
  onAgentPick: (a: AgentOption) => void
}) {
  const [text, setText] = useState("")
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px"
  }, [text])

  const send = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText("")
  }

  return (
    <div className="composer-wrap">
      <div className="composer-row">
        <AgentPicker agent={agent} agents={agents} onPick={onAgentPick} />
        <ModelPicker model={model} models={models} onPick={onModelPick} />
      </div>
      <div className="composer">
        <textarea
          ref={ref}
          value={text}
          rows={1}
          placeholder={
            disabled ? "Connecting to OpenCode…" : "Send a message…"
          }
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        {running ? (
          <button className="send stop" title="Stop" onClick={onStop}>
            ■
          </button>
        ) : (
          <button
            className="send"
            title="Send"
            disabled={!text.trim() || disabled}
            onClick={send}
          >
            ↑
          </button>
        )}
      </div>
      <div className="composer-note">
        OpenCode runs on your PC — this session is shared with the desktop.
      </div>
    </div>
  )
}
