import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

let reply = { error: { code: 3 }, stdout: 'rtk git status\n' }
const calls = []
const child = new vm.SyntheticModule(['execFile'], function () {
  this.setExport('execFile', (exe, args, options, callback) => {
    calls.push({ exe, args, options })
    callback(reply.error, reply.stdout)
  })
})
const module = new vm.SourceTextModule(fs.readFileSync(process.argv[2], 'utf8'))
await module.link((name) => {
  assert.equal(name, 'node:child_process')
  return child
})
await module.evaluate()
const definition = module.namespace.default
assert.equal(definition.id, 'rtk')
assert.equal(typeof definition.setup, 'function')
assert.equal(typeof definition.server, 'function')
let hook
await definition.setup({ location: { directory: 'worktree' }, tool: {
  async hook(name, callback) { assert.equal(name, 'execute.before'); hook = callback },
}})
const original = Object.freeze({ command: 'git status', timeout: 123 })
const event = { tool: 'bash', input: original }
await hook(event)
assert.deepEqual(event.input, { command: 'rtk git status', timeout: 123 })
assert.notEqual(event.input, original)
assert.deepEqual(calls[0].args, ['rewrite', 'git status'])
assert.equal(calls[0].exe, 'rtk')
assert.equal(calls[0].options.cwd, 'worktree')
assert.equal(calls[0].options.windowsHide, true)
assert.equal(calls[0].options.timeout, 2000)
assert.equal(calls[0].options.shell, undefined)
for (const error of [{ code: 'ENOENT' }, { code: 1 }, { code: 2 }, { killed: true }, { code: 3, killed: true }, { code: 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' }]) {
  reply = { error, stdout: 'partial output' }
  const e = { tool: 'shell', input: original }
  await hook(e)
  assert.equal(e.input, original)
}
for (const stdout of ['', 'git status']) {
  reply = { error: null, stdout }
  const e = { tool: 'bash', input: original }
  await hook(e)
  assert.equal(e.input, original)
}
const count = calls.length
for (const e of [{ tool: 'read', input: original }, { tool: 'bash', input: null }, { tool: 'bash', input: { command: 5 } }]) await hook(e)
assert.equal(calls.length, count)
reply = { error: null, stdout: 'rtk git status' }
const hooks = await definition.server({ directory: 'v1-worktree' })
const output = { args: { command: 'git status', other: true } }
await hooks['tool.execute.before']({ tool: 'Bash' }, output)
assert.deepEqual(output.args, { command: 'rtk git status', other: true })
assert.equal(calls.at(-1).options.cwd, 'v1-worktree')
// A shell-looking string is one argument to RTK, never executed by this adapter.
const dangerous = 'git status; echo "$(test)"'
await hook({ tool: 'bash', input: { command: dangerous } })
assert.deepEqual(calls.at(-1).args, ['rewrite', dangerous])
console.log('RTK V1/V2 adapter contracts passed')
