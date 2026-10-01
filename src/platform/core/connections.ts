import { existsSync, readFileSync, writeFileSync, renameSync, mkdirSync, copyFileSync, unlinkSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { parse } from 'smol-toml';
export interface ConnectionSettings {command:string;entry:string;directory:string;desktopExe?:string}
const begin='# BEGIN AgentDock QAInteract',end='# END AgentDock QAInteract',key='agentdock_qa';
const quote=(s:string)=>JSON.stringify(s);
export function configPreview(s:ConnectionSettings):string {
  return `${begin}\n[mcp_servers.${key}]\ncommand = ${quote(s.command)}\nargs = [${quote(s.entry)}]\nenabled = true\ntool_timeout_sec = 1860\n\n[mcp_servers.${key}.env]\nAIL_SOURCE = "Codex"\nAIL_CLIENT_ID = "codex"\nAIL_DATA_DIR = ${quote(s.directory)}\n${s.desktopExe?`ELECTRON_RUN_AS_NODE = "1"\nAIL_DESKTOP_EXE = ${quote(s.desktopExe)}\n`:''}${end}\n`;
}
function stripManaged(text:string):string {
  const start=text.indexOf(begin),finish=text.indexOf(end);
  if(start<0&&finish<0) return text;
  if(start<0||finish<start||text.indexOf(begin,start+begin.length)>=0||text.indexOf(end,finish+end.length)>=0) throw new Error('AgentDock 設定標記不完整，請先檢查設定檔。');
  const parsed=parse(text.slice(start+begin.length,finish)) as any;
  if(Object.keys(parsed).some(k=>k!=='mcp_servers') || Object.keys(parsed.mcp_servers??{}).some(k=>k!==key)) throw new Error('管理區塊內有其他設定，請手動移出後再試。');
  return text.slice(0,start)+text.slice(finish+end.length).replace(/^\r?\n/,'');
}
function read(path:string):string {return existsSync(path)?readFileSync(path,'utf8').replace(/^\uFEFF/,''):'';}
function servers(text:string):Record<string,any> {return (parse(text) as any).mcp_servers??{};}
export function inspectCodex(path:string,s?:ConnectionSettings) {
  try {const all=servers(read(path)),config=all[key];return {status:config?(s && (config.command!==s.command||config.args?.[0]!==s.entry||config.env?.AIL_DATA_DIR!==s.directory||config.enabled===false)?'different':'configured'):all.agent_interaction?'legacy':'absent',error:''};}
  catch(e) {return {status:'invalid',error:e instanceof Error?e.message:String(e)};}
}
function commit(path:string,original:string,next:string):string|undefined {
  parse(next);
  if(read(path)!==original) throw new Error('設定檔剛被其他程式修改，請重新操作。');
  if(original===next) return;
  mkdirSync(dirname(path),{recursive:true});
  const backup=existsSync(path)?`${path}.agentdock-${Date.now()}-${randomUUID()}.bak`:undefined;
  if(backup) copyFileSync(path,backup);
  const temp=`${path}.${randomUUID()}.tmp`;
  try {writeFileSync(temp,next,{mode:0o600});renameSync(temp,path);} finally {if(existsSync(temp))unlinkSync(temp);}
  return backup;
}
export function installCodex(path:string,s:ConnectionSettings):string|undefined {
  const original=read(path);servers(original);
  const rest=stripManaged(original),all=servers(rest);
  if(all[key]) throw new Error('已有同名 MCP 設定且不由 AgentDock 管理，請先手動檢查，避免覆寫。');
  if(all.agent_interaction) throw new Error('偵測到舊版 agent_interaction 設定。請先移除舊版區塊，避免重複連接；回答資料不受影響。');
  return commit(path,original,`${rest.trimEnd()}${rest.trim()?'\n\n':''}${configPreview(s)}`);
}
export function disconnectCodex(path:string):string|undefined {const original=read(path);servers(original);return commit(path,original,stripManaged(original));}
