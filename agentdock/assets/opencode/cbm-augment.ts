// AgentDock-maintained Codebase Memory adapter. V2 and V1 >= 1.18.29.
// Upstream reinstall may replace this file; AgentDock detects the difference.
import { execFile } from 'node:child_process';

const launch = __CBM_LAUNCH__;

function augment(tool, input, directory) {
  return new Promise((resolve) => {
    const child = execFile(launch.command, ['hook-augment'], {
      cwd: directory, encoding: 'utf8', timeout: 3000,
      maxBuffer: 1024 * 1024, windowsHide: true,
      env: { ...process.env, ...launch.env, CBM_LOG_LEVEL: 'error' },
    }, (error, stdout) => {
      if (error) return resolve('');
      try {
        const extra = JSON.parse(stdout).hookSpecificOutput?.additionalContext;
        resolve(typeof extra === 'string' ? extra.trim() : '');
      } catch { resolve(''); }
    });
    child.stdin?.on('error', () => resolve(''));
    child.stdin?.end(JSON.stringify({
      hook_event_name: 'PreToolUse', tool_name: tool, tool_input: input ?? {}, cwd: directory,
    }));
  }).catch(() => '');
}

function toolName(name) {
  return name === 'grep' ? 'Grep' : name === 'glob' ? 'Glob' : null;
}

export default {
  id: 'codebase-memory-augment',
  async setup(ctx) {
    await ctx.tool.hook('execute.after', async (event) => {
      const tool = toolName(event.tool);
      if (!tool || event.status !== 'completed' || !event.result) return;
      const extra = await augment(tool, event.input, ctx.location?.directory);
      if (!extra) return;
      const content = event.result.content;
      if (content !== undefined && typeof content !== 'string' && !Array.isArray(content)) return;
      event.result = { ...event.result, content: typeof content === 'string'
        ? content + '\n' + extra
        : [...(content ?? []), { type: 'text', text: extra }] };
    });
  },
  async server({ directory }) {
    return { 'tool.execute.after': async (input, output) => {
      const tool = toolName(input?.tool);
      if (!tool || typeof output?.output !== 'string') return;
      const extra = await augment(tool, input.args, directory);
      if (extra) output.output += '\n' + extra;
    } };
  },
};
