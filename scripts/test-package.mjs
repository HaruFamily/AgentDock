import {_electron as electron} from 'playwright';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {mkdtempSync,mkdirSync,readFileSync,existsSync,statSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {listPackage} from '@electron/asar';
import {parse} from 'smol-toml';
import assert from 'node:assert/strict';
mkdirSync('test-results',{recursive:true});const dir=mkdtempSync(resolve('test-results/portable-')),destination=join(dir,'我的 AgentDock'),release=resolve('output');
const zip=join(release,'AgentDock-0.5.0-win-x64.zip'),archive=join(release,'QAInteract/QAInteract-0.5.0.admod'),setup=join(release,'QAInteract/Setup-QAI.ps1');
const ps=args=>execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass',...args],{windowsHide:true,encoding:'utf8',timeout:120000});
const quote=s=>"'"+s.replaceAll("'","''")+"'";
ps(['-Command','Expand-Archive -LiteralPath '+quote(zip)+' -DestinationPath '+quote(destination)]);
const exe=join(destination,'AgentDock.exe'),modified=statSync(exe).mtimeMs;
assert.ok(existsSync(join(destination,'Start.cmd')));assert.equal(existsSync(join(destination,'modules')),false);
const entries=listPackage(join(destination,'resources/app.asar'));assert.equal(entries.some(p=>/modules[\\/]|QAInteract|node_modules[\\/]@modelcontextprotocol/.test(p)),false);
let app,client,page,coldPid;
try{
 const env={...process.env,AIL_TEST:'1'};delete env.ELECTRON_RUN_AS_NODE;delete env.AIL_DATA_DIR;delete env.AGENTDOCK_MODULE_DIR;
 app=await electron.launch({executablePath:exe,args:[],env});page=await app.firstWindow();page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.getByRole('heading',{name:'你的工作台，由你選配。'}).waitFor();assert.ok(existsSync(join(destination,'data/modules.json')));
 await app.evaluate(({dialog},path)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[path]});dialog.showMessageBox=async()=>({response:1,checkboxChecked:false});},archive);
 await page.getByRole('button',{name:'安裝模組',exact:true}).click();await page.getByRole('button',{name:'開啟回答中心',exact:true}).waitFor();
 assert.ok(existsSync(join(destination,'modules/qainteract/0.5.0/mcp.js')));
 await page.locator('#nav-agents').click();await page.locator('#agent-name').fill('Portable Codex');await page.locator('#agent-path').fill(join(dir,'config.toml'));await page.getByRole('button',{name:'記住這個 Agent'}).click();await page.locator('#agent-list').getByRole('heading',{name:'Portable Codex'}).waitFor();
 await page.locator('#nav-modules').click();await page.locator('#qai-targets').getByRole('checkbox',{name:'Portable Codex'}).check();await page.locator('#prepare-qai').click();await page.locator('#apply-plan').click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
 const config=parse(readFileSync(join(dir,'config.toml'),'utf8')).mcp_servers.agentdock_qa;
 assert.equal(config.command,exe);assert.equal(config.env.AIL_DATA_DIR,join(destination,'data'));assert.ok(!config.args[0].includes('app.asar'));
 client=new Client({name:'Packaged fixture',version:'1'});await client.connect(new StdioClientTransport({command:config.command,args:config.args,env:{...env,...config.env}}));
 assert.ok((await client.listTools()).tools.some(t=>t.name==='ask_user'));
 const pending=client.callTool({name:'ask_user',arguments:{work_id:'portable',work_title:'免安裝驗收',request_key:'one',question:'免安裝版可以正常回答嗎？',wait_seconds:60}});pending.catch(()=>{});
 await page.locator('#nav-qa').click();await page.getByRole('heading',{name:'免安裝版可以正常回答嗎？'}).waitFor();await page.getByLabel('你的回答',{exact:true}).fill('可以');await page.getByRole('button',{name:'提交回答'}).click();assert.equal((await pending).structuredContent.text,'可以');
 await page.locator('#nav-modules').click();await page.screenshot({path:resolve('test-results/agentdock-portable.png')});assert.deepEqual(errors,[]);
 await client.close();client=undefined;await app.close();app=undefined;
 // Validate the independent source installer against the release assets.
 console.log(ps(['-File',resolve('extensions/QAInteract/Install.ps1'),'-ReleaseDirectory',release,'-PlatformDirectory',destination,'-Kind','codex','-ConfigPath',join(dir,'standalone.toml'),'-AgentName','Standalone QAI']));
 assert.ok(parse(readFileSync(join(dir,'standalone.toml'),'utf8')).mcp_servers.agentdock_qa);
 // Reuse the same executable and configure a second client without disturbing the first.
 console.log(ps(['-File',setup,'-PlatformDirectory',destination,'-Kind','opencode','-ConfigPath',join(dir,'opencode.json'),'-AgentName','Portable OpenCode']));
 assert.equal(statSync(exe).mtimeMs,modified);assert.equal(parse(readFileSync(join(dir,'config.toml'),'utf8')).mcp_servers.agentdock_qa.enabled,true);
 assert.equal(JSON.parse(readFileSync(join(dir,'opencode.json'),'utf8')).mcp.agentdock_qa.enabled,true);
 // Bootstrap absent platform from the separately downloaded ZIP and configure a client.
 const fresh=join(dir,'bootstrap'),freshConfig=join(dir,'claude.json');
 console.log(ps(['-File',setup,'-PlatformDirectory',fresh,'-PlatformArchive',zip,'-Kind','claude-code','-ConfigPath',freshConfig,'-AgentName','Bootstrap Claude']));
 assert.ok(existsSync(join(fresh,'AgentDock.exe')));assert.ok(existsSync(join(fresh,'modules/qainteract/0.5.0/manifest.json')));assert.ok(JSON.parse(readFileSync(freshConfig,'utf8')).mcpServers.agentdock_qa);
 const coldConfig=JSON.parse(readFileSync(freshConfig,'utf8')).mcpServers.agentdock_qa;
 client=new Client({name:'Cold packaged fixture',version:'1'});await client.connect(new StdioClientTransport({command:coldConfig.command,args:coldConfig.args,env:{...env,...coldConfig.env}}));
 const cold=client.callTool({name:'ask_user',arguments:{work_id:'cold-package',work_title:'免安裝自動啟動',request_key:'cold',question:'自動啟動測試',wait_seconds:10}},undefined,{timeout:40000});
 const coldResult=await cold;assert.equal(coldResult.isError,true,'unanswered timeout must not imply agreement');assert.equal(JSON.parse(coldResult.content[0].text).status,'pending');
 const coldData=join(fresh,'data');assert.equal(JSON.parse(readFileSync(join(coldData,'questions.json'),'utf8'))[0].status,'pending');coldPid=JSON.parse(readFileSync(join(coldData,'endpoint.json'),'utf8')).pid;
 console.log('PASS: extracted portable ZIP runs without install; core excludes QAI; native import; packaged MCP answer; warm bootstrap reuses executable; cold bootstrap verifies ZIP and configures client; MCP auto-launches packaged desktop.');
}catch(error){if(page&&!page.isClosed()){console.error(await page.locator('body').innerText());await page.screenshot({path:resolve('test-results/portable-failure.png')});}throw error;}
finally{await client?.close();await app?.close();if(coldPid&&coldPid!==process.pid){try{process.kill(coldPid);}catch{}}}
