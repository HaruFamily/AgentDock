import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { mkdirSync, mkdtempSync, existsSync, readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
mkdirSync('test-results',{recursive:true});
const dir=mkdtempSync(resolve('test-results/startup-')), clients=[], pending=[];
let desktopPid;
async function connect(name) {
  const c=new Client({name,version:'1'}); clients.push(c);
  await c.connect(new StdioClientTransport({command:process.execPath,args:[resolve('dist/mcp.js')],env:{...process.env,AIL_DATA_DIR:dir,AIL_SOURCE:name,AIL_CLIENT_ID:name,AIL_TEST:'1'}}));
  const promise=c.callTool({name:'ask_user',arguments:{request_key:'cold',work_id:'cold',work_title:'自動啟動測試',question:`${name} 自動啟動`,wait_seconds:60}},undefined,{timeout:70000});
  promise.catch(()=>{}); pending.push(promise);
}
try {
  await Promise.all([connect('Cold A'),connect('Cold B')]);
  const deadline=Date.now()+30000;
  let saved=[];
  while(Date.now()<deadline) {
    if(existsSync(join(dir,'questions.json'))) saved=JSON.parse(readFileSync(join(dir,'questions.json'),'utf8'));
    if(saved.length===2) break;
    await new Promise(r=>setTimeout(r,100));
  }
  assert.equal(saved.length,2,'both MCP clients must reach the auto-launched shared desktop');
  const endpoint=JSON.parse(readFileSync(join(dir,'endpoint.json'),'utf8')); desktopPid=endpoint.pid;
  assert.equal(saved.every(q=>q.status==='pending'),true);
  assert.notEqual(saved[0].owner,saved[1].owner);
  const response=await fetch(`http://127.0.0.1:${endpoint.port}/health`,{headers:{Authorization:`Bearer ${endpoint.token}`}});
  assert.equal(response.status,200);
  console.log('PASS: two MCP clients cold-start one shared desktop, both questions remain pending until answered.');
} finally {
  for(const c of clients) await c.close();
  // Only terminate the process recorded by this uniquely-created test fixture.
  if(!desktopPid && existsSync(join(dir,'endpoint.json'))) desktopPid=JSON.parse(readFileSync(join(dir,'endpoint.json'),'utf8')).pid;
  if(desktopPid && desktopPid!==process.pid) { try { process.kill(desktopPid); } catch {} }
  await Promise.allSettled(pending);
}
