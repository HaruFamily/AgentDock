import {readFileSync,existsSync,mkdirSync,writeFileSync,renameSync,copyFileSync,unlinkSync,realpathSync} from 'node:fs';
import {join,dirname,resolve,isAbsolute} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {parse as toml,stringify as tomlText} from 'smol-toml';
import {parse as jsonc,modify,applyEdits,type ParseError} from 'jsonc-parser';
import {z} from 'zod';
import {atomicJson} from '../../shared/files.js';
import type {ConnectionSettings} from './connections.js';
export const kindSchema=z.enum(['codex','opencode','claude-code','claude-desktop']);
export const profileSchema=z.object({id:z.string().uuid(),name:z.string().trim().min(1).max(100),kind:kindSchema,path:z.string().min(1).max(4096)});
export type AgentProfile=z.infer<typeof profileSchema>;
const changeSchema=z.object({profile:z.string().uuid(),server:z.string().min(1).max(200),enabled:z.boolean()});
export type McpChange=z.infer<typeof changeSchema>;
const hash=(text:string)=>createHash('sha256').update(text).digest('hex');
const record=(v:any):Record<string,any>=>{if(v===undefined)return Object.create(null);if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('設定格式必須是物件。');return Object.assign(Object.create(null),v);};
function load(path:string):string{return existsSync(path)?readFileSync(path,'utf8').replace(/^\uFEFF/,''):'';}
function parse(p:AgentProfile,text:string):Record<string,any>{
 if(p.kind==='codex')return record(toml(text));
 const errors:ParseError[]=[];const value=jsonc(text||'{}',errors,{allowTrailingComma:true});if(errors.length)throw new Error('設定檔有 JSON/JSONC 語法錯誤，請先修正。');return record(value);
}
const section=(p:AgentProfile)=>p.kind==='codex'?'mcp_servers':p.kind==='opencode'?'mcp':'mcpServers';
const directDisable=(p:AgentProfile)=>['codex','opencode'].includes(p.kind);
function edit(p:AgentProfile,text:string,data:Record<string,any>):string{
 if(p.kind==='codex')return tomlText(data as any);
 return applyEdits(text||'{}',modify(text||'{}',[section(p)],data[section(p)],{formattingOptions:{insertSpaces:true,tabSize:2,eol:'\n'}}));
}
type PlanFile={profile:AgentProfile;original:string;next:string;parked:Record<string,any>;parkedOriginal:string};
export type AgentPlan={id:string;files:PlanFile[];summary:Array<{agent:string;path:string;changes:string[];reformats:boolean}>};
export class AgentManager{
 private file:string;private profiles:AgentProfile[];
 constructor(readonly directory:string){mkdirSync(directory,{recursive:true});this.file=join(directory,'agents.json');this.profiles=existsSync(this.file)?z.array(profileSchema).parse(JSON.parse(load(this.file))):[];}
 list():AgentProfile[]{return structuredClone(this.profiles);}
 add(raw:unknown):AgentProfile{
  const p=profileSchema.parse({...record(raw),id:randomUUID()});if(!isAbsolute(p.path))throw new Error('請選擇設定檔的完整路徑。');p.path=resolve(p.path);
  const canonical=(path:string)=>(existsSync(path)?realpathSync(path):resolve(path)).toLowerCase();
  if(this.profiles.some(a=>canonical(a.path)===canonical(p.path)))throw new Error('這份設定檔已登錄，請使用既有 Agent 項目。');
  if(existsSync(p.path))parse(p,load(p.path));
  const next=[...this.profiles,p];atomicJson(this.file,next);this.profiles=next;return p;
 }
 remove(id:string):void{const p=this.profiles.find(p=>p.id===id);if(p&&Object.keys(this.parked(p)).length)throw new Error('請先啟用由 AgentDock 暫存的 MCP，再移除登錄，避免遺失還原入口。');const next=this.profiles.filter(p=>p.id!==id);atomicJson(this.file,next);this.profiles=next;}
 private parkedPath(p:AgentProfile){return join(this.directory,`disabled-${p.id}.json`);}
 private parked(p:AgentProfile){return record(JSON.parse(load(this.parkedPath(p))||'{}'));}
 inspect(){return this.profiles.map(p=>{try{
  const text=load(p.path),data=parse(p,text),servers=record(data[section(p)]),parked=this.parked(p);
  return {...p,error:'',exists:existsSync(p.path),revision:hash(text),servers:[...new Set([...Object.keys(servers),...Object.keys(parked)])].map(name=>({name,enabled:name in servers&&(directDisable(p)?servers[name]?.enabled!==false:true),transport:servers[name]?.url||parked[name]?.url?'remote':'local',qai:['agentdock_qa','agent_interaction'].includes(name),verification:'尚未驗證'}))};
 }catch(e){return {...p,error:e instanceof Error?e.message:String(e),exists:existsSync(p.path),revision:'',servers:[]};}});}
 prepare(changes:unknown,settings?:ConnectionSettings,qaiTargets?:string[],remote?:{name:string;url:string;targets:string[]},scope?:string[]):AgentPlan{
  if(remote){if(!/^[a-zA-Z0-9_-]{1,100}$/.test(remote.name)||['__proto__','constructor','prototype'].includes(remote.name))throw new Error('MCP 名稱僅可使用英文、數字、底線及連字號。');const url=new URL(remote.url);if(url.protocol!=='https:'||url.username||url.password)throw new Error('遠端 MCP 請使用 HTTPS 網址。');}
  const desired=z.array(changeSchema).max(500).parse(changes);const allIds=new Set(this.profiles.map(p=>p.id));
  for(const id of [...desired.map(c=>c.profile),...(qaiTargets??[]),...(remote?.targets??[])])if(!allIds.has(id))throw new Error('Agent 清單已變更，請重新整理。');
  const files:PlanFile[]=[],summary:AgentPlan['summary']=[];
  for(const p of this.profiles){
   if(scope&&!scope.includes(p.id))continue;
   if(!qaiTargets&&!desired.some(c=>c.profile===p.id)&&!remote?.targets.includes(p.id))continue;
   const original=load(p.path),data=parse(p,original),servers=record(data[section(p)]),parked=this.parked(p),parkedOriginal=load(this.parkedPath(p));
   data[section(p)]=servers;const descriptions:string[]=[];
   const toggle=(name:string,enabled:boolean)=>{
    if(!(name in servers)&&!(name in parked))throw new Error(`${p.name} 的 ${name} 已不存在，請重新整理。`);
    if(directDisable(p))servers[name]={...record(servers[name]),enabled};
    else if(!enabled){if(name in servers){parked[name]=servers[name];delete servers[name];}}
    else if(!(name in servers)){servers[name]=parked[name];delete parked[name];}
    descriptions.push(`${name} → ${enabled?'啟用':'停用'}${!directDisable(p)&&!enabled?'（移出設定並保存在 AgentDock）':''}`);
   };
   for(const c of desired.filter(c=>c.profile===p.id))toggle(c.server,c.enabled);
   if(settings&&qaiTargets){
    const existing='agentdock_qa' in servers||'agentdock_qa' in parked?'agentdock_qa':('agent_interaction' in servers||'agent_interaction' in parked?'agent_interaction':'agentdock_qa');
    if(qaiTargets.includes(p.id)){
      const previous=servers[existing]??parked[existing];
      if(previous&&!previous.env?.AIL_DATA_DIR&&!previous.environment?.AIL_DATA_DIR)throw new Error(`${p.name} 有同名但非 QAI 的設定，請先確認。`);
      const env={AIL_SOURCE:p.name,AIL_CLIENT_ID:`agentdock-${p.id}`,AIL_DATA_DIR:settings.directory,...(settings.desktopExe?{ELECTRON_RUN_AS_NODE:'1',AIL_DESKTOP_EXE:settings.desktopExe}:{})};
      servers[existing]=p.kind==='opencode'?{type:'local',command:[settings.command,settings.entry],environment:env,enabled:true}: {command:settings.command,args:[settings.entry],env,...(p.kind==='codex'?{enabled:true,tool_timeout_sec:1860}:p.kind==='claude-code'?{timeout:1860000}:{})};
      delete parked[existing];descriptions.push('QAI → 連接／更新路徑');
    }else if(existing in servers){if(!servers[existing]?.env?.AIL_DATA_DIR&&!servers[existing]?.environment?.AIL_DATA_DIR)throw new Error(`${p.name} 有同名但非 QAI 的設定，請先確認。`);toggle(existing,false);}
   }
   if(remote?.targets.includes(p.id)){
    if(p.kind==='claude-desktop')throw new Error('Claude Desktop 的遠端 MCP 請使用客戶端的連接器介面。');
    if(remote.name in servers||remote.name in parked)throw new Error(`${p.name} 已有同名 MCP。`);
    servers[remote.name]=p.kind==='codex'?{url:remote.url,enabled:true}:p.kind==='opencode'?{type:'remote',url:remote.url,enabled:true}:{type:'http',url:remote.url};
    descriptions.push(`${remote.name} → 新增遠端 MCP`);
   }
   if(!descriptions.length)continue;
   const next=edit(p,original,data);parse(p,next);
   files.push({profile:p,original,next,parked,parkedOriginal});summary.push({agent:p.name,path:p.path,changes:descriptions,reformats:p.kind==='codex'});
  }
  return {id:randomUUID(),files,summary};
 }
 apply(plan:AgentPlan):Array<{agent:string;backup?:string}>{
  for(const f of plan.files){if(!this.profiles.some(p=>p.id===f.profile.id&&p.path===f.profile.path)||load(f.profile.path)!==f.original||load(this.parkedPath(f.profile))!==f.parkedOriginal)throw new Error('設定或 Agent 清單在預覽後改變，請重新預覽。');}
  const committed:Array<{path:string;before:string;after:string;existed:boolean}>=[],results:Array<{agent:string;backup?:string}>=[];
  const write=(path:string,text:string)=>{mkdirSync(dirname(path),{recursive:true});const temp=`${path}.${randomUUID()}.tmp`;try{writeFileSync(temp,text,{mode:0o600});renameSync(temp,path);}finally{if(existsSync(temp))unlinkSync(temp);}};
  try{for(const f of plan.files){
    const path=f.profile.path,backup=existsSync(path)?`${path}.agentdock-${Date.now()}-${randomUUID()}.bak`:undefined;
    if(backup)copyFileSync(path,backup);
    // Persist disabled entries first so a failed config write never loses their original values.
    for(const [target,next]of [[this.parkedPath(f.profile),JSON.stringify(f.parked)],[path,f.next]]){
      const existed=existsSync(target),before=load(target);write(target,next);committed.push({path:target,before,after:next,existed});
    }
    results.push({agent:f.profile.name,backup});
   }return results;
  }catch(e){const failed:string[]=[];for(const f of committed.reverse()){try{if(load(f.path)!==f.after){failed.push(f.path);continue;}if(f.existed)write(f.path,f.before);else unlinkSync(f.path);}catch{failed.push(f.path);}}
   throw new Error(`${String(e)}；${failed.length?'部分復原失敗，請使用備份檢查：'+failed.join('、'):'本次已寫入的檔案已復原。'}`);
  }
 }
}
