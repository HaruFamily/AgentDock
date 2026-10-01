import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { QuestionStore } from '../extensions/QAInteract/src/storage.js';
import { startBroker } from './helpers/broker.js';
import { emptyDraft } from '../extensions/QAInteract/src/schema.js';

const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
async function until(fn: () => boolean, timeout = 10000) { const end = Date.now() + timeout; while (!fn()) { if (Date.now() > end) throw new Error('Condition timed out'); await new Promise(r => setTimeout(r, 30)); } }
test('real stdio MCP blocks, routes concurrent answers, returns images/resources and cancellation', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'ail-mcp-'));
  const store = new QuestionStore(dir), broker = await startBroker(store, () => {});
  const clients: Client[] = [];
  t.after(async () => { for (const c of clients) await c.close(); await broker.close(); rmSync(dir, { recursive: true, force: true }); });
  async function connect(name: string) {
    const client = new Client({ name, version: '1' });
    const transport = new StdioClientTransport({ command: process.execPath, args: [resolve('dist/mcp.js')], env: { ...Object.fromEntries(Object.entries(process.env).filter((e): e is [string, string] => typeof e[1] === 'string')), AIL_DATA_DIR: dir, AIL_NO_LAUNCH: '1', AIL_CLIENT_ID: name, AIL_SOURCE: name } });
    clients.push(client); await client.connect(transport); return client;
  }
  const a = await connect('Agent A'), b = await connect('Agent B');
  const tools = await a.listTools(); assert.ok(tools.tools.some(t => t.name === 'ask_user'));
  const args = { request_key: 'same-key', work_id: 'same-work', work_title: '隔離測試', question: '請選擇', mode: 'single', options: [{id:'a',label:'A'},{id:'b',label:'B'}], images: [{data_url:`data:image/png;base64,${png}`, caption:'參考圖'}], wait_seconds: 30 };
  let resolved = false;
  const first = a.callTool({name:'ask_user',arguments:args}).then(v => { resolved = true; return v; });
  const second = b.callTool({name:'ask_user',arguments:args});
  await until(() => store.list().length === 2);
  await new Promise(r => setTimeout(r, 600)); assert.equal(resolved, false);
  const qa = store.list().find(q => q.source === 'Agent A')!, qb = store.list().find(q => q.source === 'Agent B')!;
  const attachment = store.addUpload(qa.id, 'answer.png', `data:image/png;base64,${png}`);
  const textFile = store.addUpload(qa.id, 'answer.txt', 'data:text/plain;base64,aGVsbG8=');
  store.answer(qa.id, { ...emptyDraft(), selected:['a'], text:'採用 A', notes:{b:'B 的配色不錯'}, attachment_ids:[attachment.id,textFile.id] });
  const response = await first;
  assert.equal(response.isError, false); assert.ok((response.content as any[]).some(c => c.type === 'image' && c.data === png));
  assert.ok((response.content as any[]).some(c => c.type === 'resource_link'));
  assert.equal((response.structuredContent as any).option_notes.b, 'B 的配色不錯');
  const denied = await b.callTool({name:'get_user_answer',arguments:{request_id:qa.id,wait_seconds:1}}); assert.equal(denied.isError, true);
  const resource = await a.readResource({uri:`ail://answers/${qa.id}/${textFile.id}`}); assert.equal((resource.contents[0] as any).blob, 'aGVsbG8=');
  store.cancel(qb.id); const cancelled = await second; assert.equal((cancelled.structuredContent as any).status, 'cancelled');
  const again = await a.callTool({name:'ask_user',arguments:args}); assert.equal((again.structuredContent as any).request_id, qa.id); assert.equal(store.list().length, 2);
});
test('local bridge rejects unauthorized and browser-origin requests; it exposes no answer endpoint', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'ail-auth-')), store = new QuestionStore(dir), broker = await startBroker(store, () => {});
  t.after(async () => { await broker.close(); rmSync(dir, { recursive: true, force: true }); });
  const url = `http://127.0.0.1:${broker.endpoint.port}`;
  assert.equal((await fetch(url+'/health')).status,403);
  assert.equal((await fetch(url+'/health',{headers:{Authorization:`Bearer ${broker.endpoint.token}`,Origin:'https://example.com'}})).status,403);
  assert.equal((await fetch(url+'/health',{headers:{Authorization:`Bearer ${broker.endpoint.token}`}})).status,200);
});
test('host cancellation and timeout preserve a question; reconnect can recover the answer', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'ail-recover-')), store = new QuestionStore(dir), broker = await startBroker(store, () => {});
  const clients: Client[] = [];
  t.after(async () => { for(const c of clients) await c.close(); await broker.close(); rmSync(dir,{recursive:true,force:true}); });
  async function connect() {
    const c = new Client({name:'Recovery',version:'1'}); clients.push(c);
    await c.connect(new StdioClientTransport({command:process.execPath,args:[resolve('dist/mcp.js')],env:{...Object.fromEntries(Object.entries(process.env).filter((e): e is [string,string]=>typeof e[1]==='string')),AIL_DATA_DIR:dir,AIL_NO_LAUNCH:'1',AIL_CLIENT_ID:'recovery',AIL_SOURCE:'Recovery'}})); return c;
  }
  const c = await connect(), abort = new AbortController();
  const request = c.callTool({name:'ask_user',arguments:{request_key:'recover',work_id:'w',work_title:'恢復測試',question:'請等待',wait_seconds:30}},undefined,{signal:abort.signal});
  const rejected = assert.rejects(request);
  await until(()=>store.list().length===1); const q = store.list()[0];
  abort.abort(); await rejected;
  await until(()=>!store.lastSeen.has(q.id));
  assert.equal(store.get(q.id).status,'pending');
  await c.close();
  const resumed = await connect();
  const pending = await resumed.callTool({name:'get_user_answer',arguments:{request_id:q.id,wait_seconds:1}});
  assert.equal(pending.isError,true); assert.equal(JSON.parse((pending.content as any[])[0].text).status,'pending');
  assert.equal(store.get(q.id).answer,undefined);
  store.answer(q.id,{...emptyDraft(),text:'稍後提交的答案'});
  const result = await resumed.callTool({name:'get_user_answer',arguments:{request_id:q.id,wait_seconds:1}});
  assert.equal((result.structuredContent as any).text,'稍後提交的答案');
});
