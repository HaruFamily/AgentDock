// AgentDock adapter for rtk-ai/rtk (Apache-2.0).
// Command rules remain in `rtk rewrite`; this file only bridges OpenCode APIs.
// No npm runtime dependency or Unix-only `which` command is required.
// Supports OpenCode V2 and V1 >= 1.18.29 (object entrypoints).
import { execFile } from "node:child_process"

function rewrite(command, directory) {
  return new Promise((resolve) => {
    execFile("rtk", ["rewrite", command], {
      cwd: directory,
      encoding: "utf8",
      timeout: 2000,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
    }, (error, stdout) => {
      // RTK uses exit 3 for a successful rewrite. Never use partial output from
      // a timeout, signal, buffer overflow, or another process failure.
      if (error && (error.code !== 3 || error.killed || error.signal)) return resolve(undefined)
      const result = stdout.trim()
      resolve(result && result !== command ? result : undefined)
    })
  }).catch(() => undefined)
}

function commandOf(tool, args) {
  if (!["bash", "shell"].includes(String(tool ?? "").toLowerCase())) return
  if (!args || typeof args !== "object") return
  if (typeof args.command === "string" && args.command.trim()) return args.command
}

// V1's server hook contract.
const RtkOpenCodePlugin = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    const command = commandOf(input?.tool, output?.args)
    if (!command) return
    const result = await rewrite(command, directory)
    if (result) output.args.command = result
  },
})

// V2 requires a default definition and replacement of event.input.
export default {
  id: "rtk",
  server: RtkOpenCodePlugin,
  async setup(ctx) {
    await ctx.tool.hook("execute.before", async (event) => {
      const command = commandOf(event?.tool, event?.input)
      if (!command) return
      const result = await rewrite(command, ctx.location?.directory)
      if (result) event.input = { ...event.input, command: result }
    })
  },
}
