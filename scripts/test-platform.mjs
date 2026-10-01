import {_electron as electron} from 'playwright';
import {mkdtempSync,mkdirSync,readFileSync,existsSync,writeFileSync,readdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import assert from 'node:assert/strict';
import {parse} from 'smol-toml';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
mkdirSync('test-results',{recursive:true});const directory=mkdtempSync(resolve('test-results/platform-')),config=join(directory,'config.toml'),json=join(directory,'opencode.jsonc');
writeFileSync(config,'model="fixture"\n[mcp_servers.existing]\ncommand="keep"\n');writeFileSync(json,'{\n// preserve comment\n"theme":"system"}');
const env={...process.env,AIL_DATA_DIR:directory,AIL_TEST:'1'};delete env.ELECTRON_RUN_AS_NODE;
let app,client,page;const errors=[];
try {
 app=await electron.launch({args:[resolve('.')],env});page=await app.firstWindow();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await page.getByRole('heading',{name:'你的工作台，由你選配。'}).waitFor();assert.equal(existsSync(join(directory,'questions.json')),false);
 await page.locator('#nav-agents').click();
 for(const [kind,name,path]of [['codex','我的 Codex',config],['opencode','專案 OpenCode',json]]){
  await page.locator('#agent-kind').selectOption(kind);await page.locator('#agent-name').fill(name);await page.locator('#agent-path').fill(path);await page.getByRole('button',{name:'記住這個 Agent'}).click();await page.locator('#agent-list').getByRole('heading',{name}).waitFor();
 }
 await page.screenshot({path:resolve('test-results/agentdock-agents.png')});
 await page.locator('#nav-modules').click();await page.getByRole('button',{name:'安裝模組',exact:true}).click();await page.getByRole('button',{name:'開啟回答中心',exact:true}).waitFor();
 await page.locator('#qai-targets').getByRole('checkbox',{name:'我的 Codex',exact:true}).check();await page.locator('#qai-targets').getByRole('checkbox',{name:'專案 OpenCode',exact:true}).check();
 await page.locator('#prepare-qai').click();await page.locator('#plan-dialog').waitFor({state:'visible'});assert.equal(readFileSync(config,'utf8').includes('agentdock_qa'),false);
 await page.screenshot({path:resolve('test-results/agentdock-confirm.png')});await page.locator('#apply-plan').click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
 assert.ok(parse(readFileSync(config,'utf8')).mcp_servers.agentdock_qa);assert.ok(readFileSync(json,'utf8').includes('// preserve comment'));assert.ok(readdirSync(directory).some(x=>x.includes('.bak')));
 await page.locator('#nav-connections').click();const codexCard=page.locator('#mcp-list .module-card').filter({has:page.getByRole('heading',{name:'我的 Codex',exact:true})});
 await codexCard.getByRole('checkbox',{name:'existing',exact:false}).uncheck();assert.equal(parse(readFileSync(config,'utf8')).mcp_servers.existing.enabled,undefined);
 await page.locator('#prepare-changes').click();await page.locator('#apply-plan').click();await page.locator('#plan-dialog').waitFor({state:'hidden'});assert.equal(parse(readFileSync(config,'utf8')).mcp_servers.existing.enabled,false);
 await page.screenshot({path:resolve('test-results/agentdock-mcp.png')});
 await page.reload();await page.locator('#nav-agents').click();await page.locator('#agent-list').getByRole('heading',{name:'我的 Codex',exact:true}).waitFor();
 await page.locator('#nav-modules').click();await page.getByRole('button',{name:'停用',exact:true}).click();await page.getByText('已停用',{exact:true}).waitFor();
 client=new Client({name:'Platform fixture',version:'1'});await client.connect(new StdioClientTransport({command:process.execPath,args:[resolve('dist/mcp.js')],env}));
 const result=await client.callTool({name:'ask_user',arguments:{work_id:'w',work_title:'平台測試',request_key:'disabled',question:'不能偷偷啟用',wait_seconds:10}});assert.equal(result.isError,true);
 await page.getByRole('button',{name:'啟用模組',exact:true}).click();await page.locator('#nav-qa').click();await page.getByRole('heading',{name:'等 Agent 問你下一題'}).waitFor();
 const pending=client.callTool({name:'ask_user',arguments:{work_id:'w',work_title:'平台測試',request_key:'active',question:'平台與模組整合測試',wait_seconds:60}});pending.catch(()=>{});
 await page.getByRole('heading',{name:'平台與模組整合測試'}).waitFor();
 await page.locator('#nav-modules').click();await page.getByRole('button',{name:'停用',exact:true}).click();await page.getByText(/仍有待回答問題，請先回答/).waitFor();
 await page.locator('#nav-qa').click();await page.getByLabel('你的回答',{exact:true}).fill('完成');await page.getByRole('button',{name:'提交回答'}).click();assert.equal((await pending).structuredContent.text,'完成');
 await page.locator('#qa-dot').waitFor({state:'hidden'});
 await page.locator('#nav-modules').click();await page.getByRole('button',{name:'移除模組',exact:true}).click();await page.getByText('已移除',{exact:true}).waitFor();
 assert.equal(JSON.parse(readFileSync(join(directory,'questions.json'),'utf8'))[0].answer.text,'完成');assert.deepEqual(errors,[]);
 console.log('PASS: profiles persist; QAI selection, preview, backup, multi-client apply and generic MCP toggle; QAI lifecycle and actual stdio answer.');
}catch(error){if(page&&!page.isClosed()){console.error(await page.locator('body').innerText());await page.screenshot({path:resolve('test-results/platform-failure.png')});}throw error;}
finally{await client?.close();await app?.close();}
