import { _electron as electron } from 'playwright';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
const resultsDir = resolve('test-results'); mkdirSync(resultsDir,{recursive:true});
const dataDir = mkdtempSync(join(resultsDir,'desktop-'));
const clients = [], pending = [], env = {...process.env, AIL_DATA_DIR:dataDir,AIL_TEST:'1'};
delete env.ELECTRON_RUN_AS_NODE;
let app, page;
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
async function connect(name) {
  const client = new Client({name,version:'1'}); clients.push(client);
  await client.connect(new StdioClientTransport({command:process.execPath,args:[resolve('dist/mcp.js')],env:{...env,AIL_SOURCE:name,AIL_CLIENT_ID:name}})); return client;
}
async function poll(fn) { for(let i=0;i<100;i++){ if(await fn()) return; await new Promise(r=>setTimeout(r,50)); } throw new Error('Condition timed out'); }
try {
  app = await electron.launch({args:[resolve('.')],env,timeout:30000});
  page = await app.firstWindow(); page.setDefaultTimeout(12000);
  const pageErrors = []; page.on('pageerror',e=>pageErrors.push(e.message));
  await page.getByRole('button',{name:'安裝模組',exact:true}).click(); await page.locator('#nav-qa').click();
  await page.getByRole('heading',{name:'等 Agent 問你下一題'}).waitFor();
  const a = await connect('OpenCode'), b = await connect('Claude Code');
  let settledA = false;
  const aPromise = a.callTool({name:'ask_user',arguments:{request_key:'desktop-a',work_id:'design',work_title:'網站設計',question:'首頁應該採用哪一種呈現方式？',mode:'single',options:[{id:'compact',label:'集中重點',description:'首屏只呈現核心內容，讓使用者快速完成操作。'},{id:'explore',label:'展開探索',description:'用卡片展示不同功能，讓使用者依需求深入。'}],wait_seconds:120}},undefined,{timeout:130000}).then(r=>{settledA=true;return r;}); pending.push(aPromise); aPromise.catch(()=>{});
  await page.getByRole('heading',{name:'首頁應該採用哪一種呈現方式？'}).waitFor();
  await page.getByLabel('你的回答／補充').fill('先保留這份草稿。');
  await page.getByLabel('你的回答／補充').focus();
  const bPromise = b.callTool({name:'ask_user',arguments:{request_key:'desktop-b',work_id:'features',work_title:'功能優先順序',question:'第一版需要哪些功能？',mode:'multiple',images:[{data_url:`data:image/png;base64,${png}`,caption:'參考圖片'}],options:[{id:'paste',label:'圖片貼上'},{id:'notes',label:'選項備註'}],wait_seconds:120}},undefined,{timeout:130000}); pending.push(bPromise); bPromise.catch(()=>{});
  await page.locator('.question-link').filter({hasText:'第一版需要哪些功能'}).waitFor();
  assert.equal(await page.locator('#question-panel h1').textContent(),'首頁應該採用哪一種呈現方式？');
  assert.equal(await page.locator('#answer-text').evaluate(e=>e===document.activeElement),true);
  assert.equal(settledA,false);
  await page.getByRole('radio',{name:'集中重點',exact:false}).check();
  await page.getByRole('button',{name:'清除選擇，改用文字回答'}).click();
  assert.equal(await page.getByRole('radio',{name:'集中重點',exact:false}).isChecked(),false);
  await page.getByRole('radio',{name:'集中重點',exact:false}).check();
  await page.locator('.option').filter({hasText:'展開探索'}).locator('summary').click();
  await page.getByLabel('展開探索的備註').fill('雖然不選 B，但可以採用它的配色。');
  await page.locator('#file-picker').setInputFiles({name:'review.txt',mimeType:'text/plain',buffer:Buffer.from('human supplied notes')});
  await page.getByText('review.txt',{exact:true}).waitFor();
  await page.locator('#file-picker').setInputFiles({name:'reference.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
  await page.getByText('reference.png',{exact:true}).waitFor();
  await page.getByText('草稿已保存',{exact:true}).waitFor();
  await page.locator('.question-scroll').evaluate(e=>{e.scrollTop=0;});
  await page.screenshot({path:join(resultsDir,'answer-center.png')});
  // Reload proves drafts persist independently of the renderer.
  await page.reload(); await page.locator('#nav-qa').click(); await page.getByLabel('你的回答／補充').waitFor();
  assert.equal(await page.getByLabel('你的回答／補充').inputValue(),'先保留這份草稿。');
  assert.equal(await page.getByRole('radio',{name:'集中重點',exact:false}).isChecked(),true);
  assert.equal(await page.getByLabel('展開探索的備註').inputValue(),'雖然不選 B，但可以採用它的配色。');
  await page.getByRole('button',{name:'提交回答'}).click();
  const answerA = await aPromise;
  assert.equal(answerA.structuredContent.text,'先保留這份草稿。');
  assert.equal(answerA.structuredContent.option_notes.explore,'雖然不選 B，但可以採用它的配色。');
  assert.equal(answerA.structuredContent.attachments.length,2);
  assert.ok(answerA.content.some(c=>c.type==='image'));
  await page.getByRole('heading',{name:'第一版需要哪些功能？'}).waitFor();
  await page.getByRole('button',{name:'放大圖片：參考圖片'}).click(); await page.locator('#image-dialog').waitFor({state:'visible'});
  await page.getByRole('button',{name:'關閉預覽'}).click();
  await page.getByRole('checkbox',{name:'圖片貼上'}).check(); await page.getByRole('checkbox',{name:'選項備註'}).check();
  await page.getByRole('button',{name:'提交回答'}).click();
  const answerB = await bPromise; assert.equal(answerB.structuredContent.selected_options.length,2);
  await page.getByRole('heading',{name:'等 Agent 問你下一題'}).waitFor();
  const cPromise = a.callTool({name:'ask_user',arguments:{request_key:'desktop-text',work_id:'text',work_title:'文字回答',question:'請補充想法',wait_seconds:60}},undefined,{timeout:70000}); pending.push(cPromise); cPromise.catch(()=>{});
  await page.getByRole('heading',{name:'請補充想法'}).waitFor();
  await page.getByLabel('你的回答',{exact:true}).fill('這是純文字回答。');
  await page.getByRole('button',{name:'提交回答'}).click();
  assert.equal((await cPromise).structuredContent.text,'這是純文字回答。');
  await page.getByRole('button',{name:'已處理',exact:true}).click();
  await poll(async()=>await page.locator('.question-link').count()===3);
  assert.equal(pageErrors.length,0,pageErrors.join('\n'));
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].focus());
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await page.getByRole('button',{name:'置頂',exact:true}).click(); await poll(()=>app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isAlwaysOnTop()));
  await page.getByRole('button',{name:'收起 ↘'}).click(); await poll(async()=>!(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isVisible())));
  console.log('PASS: native desktop, concurrent MCP callers, no focus stealing, durable drafts, notes, files, images, single/multi/text answers, history, pin, hide.');
  console.log('Screenshot: test-results/answer-center.png');
} catch (error) {
  console.error(error);
  if(page && !page.isClosed()) { await page.screenshot({path:join(resultsDir,'failure.png')}).catch(()=>{}); console.error(await page.locator('body').innerText()); }
  process.exitCode = 1;
} finally {
  for(const c of clients) await c.close();
  if(app) await app.close();
  await Promise.allSettled(pending);
}
