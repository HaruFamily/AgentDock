import { readFile } from "node:fs/promises"
import { createHash } from "node:crypto"

// Replaced with JSON literals by AgentDock's preview/backup installer.
const endpointPath = __INBOX_ENDPOINT__
const identity = __INBOX_IDENTITY__
const owner = createHash("sha256").update(identity).digest("hex")

async function linkConversation(tool, session, args) {
  if (!/^(?:mcp__)?(?:inbox|agentchat)(?:__|_)(?:ask_user|report_to_user)$/.test(tool ?? "") ||
      typeof session !== "string" || !session || typeof args?.work_id !== "string") return
  try {
    const endpoint = JSON.parse(await readFile(endpointPath, "utf8"))
    if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 ||
        !/^[a-f0-9]{64}$/.test(endpoint.token)) return
    await fetch(`http://127.0.0.1:${endpoint.port}/conversation-links`, {
      method: "POST", signal: AbortSignal.timeout(700),
      headers: { Authorization: `Bearer ${endpoint.token}`, "X-AgentDock-Owner": owner,
                 "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: args.work_id, conversation_id: `session:opencode:${session}` })
    })
  } catch { /* Linking never blocks the client or grants approval. */ }
}

// OpenCode V1 >= 1.18.29 and V2 share forwarding, not initialization APIs.
const InboxActivity = async ({ directory = "" }) => ({
  "tool.execute.before": async (input, output) => {
    await linkConversation(input.tool, input.sessionID, output.args)
  },
  event: async ({ event }) => {
    const p = event.properties ?? {}
    const session = p.sessionID ?? p.info?.id
    if (!session) return
    let state
    if (event.type === "permission.asked") state = "approval"
    if (event.type === "permission.replied") state = "approval_done"
    if (event.type === "session.idle") state = "idle"
    if (event.type === "session.error") state = "error"
    if (event.type === "session.deleted") state = "ended"
    if (event.type === "session.status") {
      state = p.status?.type === "idle" ? "idle" :
        ["busy", "retry"].includes(p.status?.type) ? "running" : undefined
    }
    if (!state) return
    try {
      const endpoint = JSON.parse(await readFile(endpointPath, "utf8"))
      if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 ||
          !/^[a-f0-9]{64}$/.test(endpoint.token)) return
      await fetch(`http://127.0.0.1:${endpoint.port}/activity`, {
        method: "POST",
        signal: AbortSignal.timeout(700),
        headers: { Authorization: `Bearer ${endpoint.token}`, "X-AgentDock-Owner": owner,
                   "Content-Type": "application/json" },
        body: JSON.stringify({ client: "opencode", session_id: session,
          title: `${directory.replaceAll("\\", "/").split("/").pop()} · ${session.slice(0, 8)}`.slice(0, 160),
          state, detail: state === "approval" ? "請回 OpenCode 批准" : "",
          request_id: p.permissionID ?? (event.type === "permission.asked" ? p.id : "") ?? "" })
      })
    } catch { /* Notification failures never change permissions or stop the client. */ }
  }
})

export default {
  id: "agentdock.inbox-activity",
  server: InboxActivity,
  async setup(ctx) {
    const hooks = await InboxActivity({ directory: ctx.location?.directory })
    let closed = false
    const registration = ctx.tool?.hook && await ctx.tool.hook("execute.before", async (event) => {
      if (closed) return
      await linkConversation(event.tool, event.sessionID, event.input)
    })
    const controller = new AbortController()
    const task = (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          if (controller.signal.aborted) break
          await hooks.event({ event })
        }
      } catch { /* A disconnected notification stream must not stop OpenCode. */ }
    })()
    return async () => {
      closed = true
      controller.abort()
      await registration?.dispose?.()
      await task
    }
  },
}
