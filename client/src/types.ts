import type {
  SessionMessageInfo,
  SessionMessageAssistant,
  SessionMessageAssistantText,
  SessionMessageAssistantReasoning,
  SessionMessageAssistantTool,
  SessionInfo,
  ModelInfo,
  AgentInfo,
  ModelRef,
  V2Event,
} from "@opencode/client"

export type { SessionInfo, ModelInfo, AgentInfo, ModelRef, V2Event }

export type AgentOption = { id: string; name: string; description?: string }

export type ChatItem =
  | { kind: "user"; id: string; text: string; time: number }
  | { kind: "system"; id: string; text: string; time: number }
  | {
      kind: "assistant"
      id: string
      time: number
      agent: string
      model?: string
      blocks: Block[]
      error?: string
      done: boolean
    }

export type Block =
  | { type: "text"; ordinal: number; text: string }
  | { type: "reasoning"; ordinal: number; text: string }
  | {
      type: "tool"
      ordinal: number
      toolId: string
      name: string
      state: string
      input: string
      output?: string
    }

const ordinal = (part: unknown, i: number): number =>
  (part as { ordinal?: number })?.ordinal ?? i

/** Flatten an assistant message's content parts into ordered blocks. */
function blocksOf(message: SessionMessageAssistant): Block[] {
  const blocks: Block[] = []
  message.content.forEach((part, i) => {
    if (part.type === "text") {
      const p = part as SessionMessageAssistantText
      if (p.text)
        blocks.push({ type: "text", ordinal: ordinal(part, i), text: p.text })
    } else if (part.type === "reasoning") {
      const p = part as SessionMessageAssistantReasoning
      if (p.text)
        blocks.push({ type: "reasoning", ordinal: ordinal(part, i), text: p.text })
    } else if (part.type === "tool") {
      blocks.push(toolBlock(part as SessionMessageAssistantTool, ordinal(part, i)))
    }
  })
  return blocks.sort((a, b) => a.ordinal - b.ordinal)
}

/**
 * Extract what the tool dropdown shows: the call's input and its output.
 * The state carries both — `input` is the arguments object, `content` the
 * result's text parts, and an error state carries the failure instead.
 */
function toolBlock(part: SessionMessageAssistantTool, ord: number): Block {
  const state = (
    part as {
      state?: {
        status?: string
        input?: unknown
        content?: unknown
        error?: { message?: string; title?: string }
      }
    }
  ).state
  const status = state?.status ?? "running"

  let input = ""
  if (state?.input && typeof state.input === "object") {
    input = JSON.stringify(state.input, null, 2)
  } else if (state?.input != null) {
    input = String(state.input)
  }

  let output: string | undefined
  if (status === "error") {
    output = state?.error?.message || state?.error?.title || "error"
  } else if (Array.isArray(state?.content)) {
    const texts = (state.content as Array<{ type?: string; text?: string }>)
      .filter((c) => c.type === "text" && c.text)
      .map((c) => c.text as string)
    if (texts.length) output = texts.join("\n")
  }

  return {
    type: "tool",
    ordinal: ord,
    toolId: part.id,
    name: part.name,
    state: status,
    input,
    output,
  }
}

export function modelLabel(message: SessionMessageAssistant): string | undefined {
  const m = message.model
  return m ? `${m.providerID}/${m.id}` : undefined
}

/** Convert the API message stream into the chat items the view renders. */
export function toChatItems(messages: SessionMessageInfo[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const m of messages) {
    if (m.type === "user") {
      const text = (m as { text?: string }).text ?? ""
      if (text.trim())
        items.push({ kind: "user", id: m.id, text, time: m.time.created })
    } else if (m.type === "assistant") {
      const error = (m as { error?: { title?: string; message?: string } }).error
      items.push({
        kind: "assistant",
        id: m.id,
        time: m.time.created,
        agent: m.agent,
        model: modelLabel(m),
        blocks: blocksOf(m),
        error: error ? `${error.title || "error"}: ${error.message || ""}` : undefined,
        done: Boolean(m.time.completed),
      })
    } else if (m.type === "system" || m.type === "synthetic") {
      const text = (m as { text?: string }).text
      const description = (m as { description?: string }).description
      const line = text || description
      if (line?.trim())
        items.push({ kind: "system", id: m.id, text: line, time: m.time.created })
    }
  }
  return items
}

export function timeAgo(ms: number): string {
  const s = Math.max(1, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return "now"
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.round(h / 24)}d`
}
