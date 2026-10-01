import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
const clients = [];
async function ask(source, question) {
  const client = new Client({name:source,version:'1'}); clients.push(client);
  await client.connect(new StdioClientTransport({ command:process.execPath,args:[resolve('dist/mcp.js')],env:{...process.env,AIL_SOURCE:source,AIL_CLIENT_ID:`demo-${source}`} }));
  return client.callTool({ name:'ask_user',arguments:{request_key:randomUUID(),wait_seconds:1800,...question} }, undefined, {timeout:1860000});
}
console.log('正在建立兩個示範工作。請在桌面回答框提交，這裡會收到原始結果。');
try {
  await Promise.all([
    ask('OpenCode · 示範',{work_id:'site-demo',work_title:'網站設計',question:'這次首頁，你偏好哪一種呈現方式？',mode:'single',options:[{id:'compact',label:'集中重點',description:'首屏只放核心功能與一個主要操作，適合快速完成任務。',images:[{path:resolve('assets/compact.png'),caption:'集中重點版面示意'}]},{id:'explore',label:'展開探索',description:'用卡片展示不同功能，讓新使用者慢慢了解。',images:[{path:resolve('assets/explore.png'),caption:'展開探索版面示意'}]}]}).then(r=>console.log('網站設計回答：',JSON.stringify(r.structuredContent))),
    ask('Codex · 示範',{work_id:'feature-demo',work_title:'回答中心功能',question:'第一版你最想先保留哪些操作？',mode:'multiple',options:[{id:'paste',label:'貼上圖片',description:'直接貼上截圖，不需要先另存檔案。'},{id:'notes',label:'逐項備註',description:'即使不選這個選項，也能留下意見。'},{id:'pin',label:'視窗置頂',description:'切换程式時仍能看到待答問題。'}]}).then(r=>console.log('回答中心功能：',JSON.stringify(r.structuredContent)))
  ]);
} finally { for(const c of clients) await c.close(); }
