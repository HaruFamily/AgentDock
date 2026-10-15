import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';

let reply = { stdout: JSON.stringify({ hookSpecificOutput: { additionalContext: 'graph context' } }) };
const calls = [];
const child = new vm.SyntheticModule(['execFile'], function () {
  this.setExport('execFile', (exe, args, options, callback) => {
    const stdin = new EventEmitter();
    stdin.end = (payload) => {
      calls.push({exe, args, options, payload: JSON.parse(payload)});
      if (reply.pipeError) { stdin.emit('error', new Error('EPIPE')); return; }
      callback(reply.error, reply.stdout);
    };
    return {stdin};
  });
});
const source = fs.readFileSync(process.argv[2], 'utf8').replace('__CBM_LAUNCH__', JSON.stringify({command:'isolated.exe',env:{CBM_CACHE_DIR:'isolated'}}));
const module = new vm.SourceTextModule(source);
await module.link(name => { assert.equal(name, 'node:child_process'); return child; });
await module.evaluate();
const definition = module.namespace.default;
assert.equal(definition.id, 'codebase-memory-augment');
let hook;
await definition.setup({location:{directory:'test-project'},tool:{hook:async(name, callback)=>{assert.equal(name,'execute.after');hook=callback;}}});
for (const content of ['original', [{type:'file',uri:'x',mime:'text/plain'}], undefined]) {
  const result = Object.freeze({content,output:{keep:true},metadata:{keep:true}});
  const event={tool:'grep',status:'completed',input:{pattern:'Foo'},result};
  await hook(event);
  assert.equal(event.result.output, result.output);
  assert.equal(event.result.metadata, result.metadata);
  assert.equal(typeof content==='string' ? event.result.content : event.result.content.at(-1).text, typeof content==='string' ? 'original\ngraph context':'graph context');
  if (Array.isArray(content)) assert.equal(event.result.content[0], content[0]);
}
assert.deepEqual(calls[0].payload.tool_input,{pattern:'Foo'});
assert.equal(calls[0].payload.cwd,'test-project');
assert.equal(calls[0].options.cwd,'test-project');
assert.equal(calls[0].options.env.CBM_CACHE_DIR,'isolated');
assert.equal(calls[0].options.timeout,3000);
assert.equal(calls[0].options.shell,undefined);
const n=calls.length;
await hook({tool:'bash',status:'completed',result:{content:'keep'}});
await hook({tool:'grep',status:'error'});
assert.equal(calls.length,n);
const success = reply;
for (const failure of [{error:new Error('ENOENT')}, {error:{killed:true},stdout:'partial'}, {stdout:'invalid'}, {stdout:'{}'}, {pipeError:true}]) {
  reply=failure;
  const result={content:'keep'};
  const event={tool:'glob',status:'completed',input:{pattern:'*.py'},result};
  await hook(event);
  assert.equal(event.result,result);
}
reply=success;
const hooks=await definition.server({directory:'v1-project'});
const output={output:'original'};
await hooks['tool.execute.after']({tool:'glob',args:{pattern:'SomeClass*'}},output);
assert.equal(output.output,'original\ngraph context');
assert.deepEqual(calls.at(-1).payload.tool_input,{pattern:'SomeClass*'});
assert.equal(calls.at(-1).options.cwd,'v1-project');
console.log('PASS: CBM V1/V2 hook contracts and failure isolation');
