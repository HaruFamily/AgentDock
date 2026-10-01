import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ModuleRegistry } from '../src/platform/core/modules.js';
import { installCodex, disconnectCodex, inspectCodex, configPreview } from '../src/platform/core/connections.js';
import { parse } from 'smol-toml';
function fixture(t:any){const dir=mkdtempSync(join(tmpdir(),'agentdock-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));return dir;}
test('standalone platform, idempotent module install, disabled and removed stay disabled across restart',t=>{
  const dir=fixture(t),registry=new ModuleRegistry(dir);
  assert.equal(registry.enabled('qainteract'),false);
  assert.equal(existsSync(join(dir,'questions.json')),false);
  registry.ensure('qainteract');registry.ensure('qainteract');
  assert.equal(new ModuleRegistry(dir).enabled('qainteract'),true);
  registry.set('qainteract','disable');assert.throws(()=>registry.ensure('qainteract'),/停用/);
  registry.set('qainteract','remove');assert.throws(()=>new ModuleRegistry(dir).ensure('qainteract'),/移除/);
  registry.set('qainteract','install');assert.equal(registry.enabled('qainteract'),true);
  assert.throws(()=>registry.set('usage-monitor','install'),/尚未/);
});
test('existing answer data migrates to enabled QAInteract without being rewritten',t=>{
  const dir=fixture(t);writeFileSync(join(dir,'questions.json'),'[]');
  assert.equal(new ModuleRegistry(dir).enabled('qainteract'),true);
  assert.equal(readFileSync(join(dir,'questions.json'),'utf8'),'[]');
});
test('Codex configuration preserves other servers, backs up, installs idempotently and disconnects only owned block',t=>{
  const dir=fixture(t),file=join(dir,'config.toml');
  const original='# personal comment\nmodel = "example"\n[mcp_servers.existing]\ncommand = "keep"\n';writeFileSync(file,original);
  const settings={command:'node',entry:'D:\\tools\\AgentDock\\dist\\mcp.js',directory:'D:\\資料'};
  const backup=installCodex(file,settings);assert.ok(backup);assert.equal(readFileSync(backup,'utf8'),original);
  const installed=readFileSync(file,'utf8');assert.ok(installed.startsWith(original));
  assert.equal((parse(installed) as any).mcp_servers.existing.command,'keep');
  assert.equal(inspectCodex(file,settings).status,'configured');
  assert.equal(installCodex(file,settings),undefined);
  disconnectCodex(file);assert.equal(inspectCodex(file).status,'absent');assert.ok(readFileSync(file,'utf8').startsWith(original));
  assert.equal((parse(configPreview({...settings,desktopExe:'D:\\AgentDock.exe'})) as any).mcp_servers.agentdock_qa.env.ELECTRON_RUN_AS_NODE,'1');
});
test('malformed, legacy, foreign and mixed managed settings are never overwritten',t=>{
  const file=join(fixture(t),'config.toml'),settings={command:'node',entry:'test',directory:'data'};
  for(const content of ['broken = [','[mcp_servers.agent_interaction]\ncommand="old"','[mcp_servers.agentdock_qa]\ncommand="someone else"',configPreview(settings).replace('# END AgentDock QAInteract','[mcp_servers.personal]\ncommand="keep"\n# END AgentDock QAInteract')]){
    writeFileSync(file,content);assert.throws(()=>installCodex(file,settings));assert.equal(readFileSync(file,'utf8'),content);
  }
});
