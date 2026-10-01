import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,existsSync,rmSync,mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {parse} from 'smol-toml';
import {parse as jsonc} from 'jsonc-parser';
import {AgentManager} from '../src/platform/core/agents.js';
import {downloadModule} from '../src/platform/core/download.js';
function fixture(t:any){const dir=mkdtempSync(join(tmpdir(),'dock-agents-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));return {dir,manager:new AgentManager(join(dir,'data'))};}
const settings={command:'C:\\Dock\\AgentDock.exe',entry:'C:\\Dock\\modules\\qainteract\\mcp.js',directory:'C:\\Dock\\data',desktopExe:'C:\\Dock\\AgentDock.exe'};
test('four client formats: preview is read-only, apply backs up and preserves unrelated settings',t=>{
 const {dir,manager}=fixture(t);const kinds=['codex','opencode','claude-code','claude-desktop'] as const;
 const profiles=kinds.map(kind=>{const path=join(dir,kind+'.config');writeFileSync(path,kind==='codex'?'model="keep"\n[mcp_servers.other]\ncommand="existing"\n':'{\n// preserve root comment\n"theme":"keep", "'+(kind==='opencode'?'mcp':'mcpServers')+'":{"other":{"command":"existing"}}}');return manager.add({kind,name:kind,path});});
 const originals=profiles.map(p=>readFileSync(p.path,'utf8'));
 const plan=manager.prepare([],settings,profiles.map(p=>p.id));assert.equal(plan.summary.length,4);profiles.forEach((p,i)=>assert.equal(readFileSync(p.path,'utf8'),originals[i]));
 const results=manager.apply(plan);results.forEach((r,i)=>assert.equal(readFileSync(r.backup!,'utf8'),originals[i]));
 profiles.forEach(p=>{const text=readFileSync(p.path,'utf8'),data=p.kind==='codex'?parse(text):jsonc(text);const servers=(data as any)[p.kind==='codex'?'mcp_servers':p.kind==='opencode'?'mcp':'mcpServers'];assert.equal(servers.other.command,'existing');assert.ok(servers.agentdock_qa);assert.equal(p.kind==='codex'?data.model:data.theme,'keep');});
 assert.equal(new AgentManager(join(dir,'data')).list().length,4);
 assert.throws(()=>manager.add({kind:'codex',name:'duplicate',path:profiles[0].path}),/已登錄/);
});
test('Claude disabled entries survive restart and restore exact values; removing profile cannot orphan them',t=>{
 const {dir,manager}=fixture(t),path=join(dir,'claude.json'),server={command:'tool',args:['a'],env:{TOKEN:'secret'}};
 writeFileSync(path,JSON.stringify({mcpServers:{tool:server}}));const p=manager.add({kind:'claude-code',path,name:'Claude'});
 manager.apply(manager.prepare([{profile:p.id,server:'tool',enabled:false}]));assert.deepEqual(JSON.parse(readFileSync(path,'utf8')).mcpServers,{});
 const next=new AgentManager(join(dir,'data'));assert.equal(next.inspect()[0].servers[0].enabled,false);assert.equal(JSON.stringify(next.inspect()).includes('secret'),false);assert.throws(()=>next.remove(p.id),/暫存/);
 next.apply(next.prepare([{profile:p.id,server:'tool',enabled:true}]));assert.deepEqual(JSON.parse(readFileSync(path,'utf8')).mcpServers.tool,server);next.remove(p.id);assert.ok(existsSync(path));
});
test('concurrent edits invalidate preview; failed multi-file apply rolls back earlier files',t=>{
 const {dir,manager}=fixture(t),path=join(dir,'one.toml');writeFileSync(path,'model="a"');const one=manager.add({kind:'codex',name:'one',path});
 let plan=manager.prepare([],settings,[one.id]);writeFileSync(path,'model="changed"');assert.throws(()=>manager.apply(plan),/改變/);assert.equal(readFileSync(path,'utf8'),'model="changed"');
 const blocked=join(dir,'blocked');const two=manager.add({kind:'codex',name:'two',path:join(blocked,'two.toml')});plan=manager.prepare([],settings,[one.id,two.id]);writeFileSync(blocked,'not a directory');assert.throws(()=>manager.apply(plan),/復原/);assert.equal(readFileSync(path,'utf8'),'model="changed"');
});
test('scoped CLI setup leaves other agents unchanged; invalid unrelated config does not block targeted toggles',t=>{
 const {dir,manager}=fixture(t),p=manager.add({name:'one',kind:'codex',path:join(dir,'one.toml')}),other=manager.add({name:'two',kind:'codex',path:join(dir,'two.toml')});
 manager.apply(manager.prepare([],settings,[p.id,other.id]));const original=readFileSync(other.path,'utf8');manager.apply(manager.prepare([],settings,[p.id],undefined,[p.id]));assert.equal(readFileSync(other.path,'utf8'),original);
 writeFileSync(other.path,'invalid = [');manager.apply(manager.prepare([{profile:p.id,server:'agentdock_qa',enabled:false}]));assert.equal(manager.inspect()[0].servers[0].enabled,false);
 assert.throws(()=>manager.prepare([],undefined,undefined,{name:'__proto__',url:'https://example.com/mcp',targets:[p.id]}));assert.throws(()=>manager.prepare([],undefined,undefined,{name:'test',url:'http://example.com',targets:[p.id]}));
});
test('module URL download validates redirects, size and HTTP failure',async t=>{
 const {dir}=fixture(t);const fake=(response:Response)=>async()=>response;
 const path=await downloadModule('https://example.com/module',dir,fake(new Response('module')) as typeof fetch);assert.equal(readFileSync(path,'utf8'),'module');
 await assert.rejects(downloadModule('http://example.com/module',dir),/HTTPS/);
 await assert.rejects(downloadModule('https://example.com/module',dir,fake(new Response(null,{status:302,headers:{location:'http://example.com/no'}})) as typeof fetch),/重新導向/);
 await assert.rejects(downloadModule('https://example.com/module',dir,fake(new Response('error',{status:404})) as typeof fetch),/404/);
 await assert.rejects(downloadModule('https://example.com/module',dir,fake(new Response(new Uint8Array(26*1024*1024))) as typeof fetch),/25 MB/);
});
