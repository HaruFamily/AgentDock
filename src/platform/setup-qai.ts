// Invoked by an authorized Agent or the QAI bootstrap, while the desktop is closed.
import {resolve,join} from 'node:path';
import {existsSync,readFileSync} from 'node:fs';
import {AgentManager,kindSchema} from './core/agents.js';
import {ModulePackages} from './core/packages.js';
import {ModuleRegistry} from './core/modules.js';
const [platform,archive,kind,config,name='My Agent']=process.argv.slice(2);
if(!platform||!archive||!kind||!config)throw new Error('Usage: setup-qai <platform-dir> <module.admod> <codex|opencode|claude-code|claude-desktop> <config-path> [name]');
const root=resolve(platform),directory=join(root,'data'),exe=join(root,'AgentDock.exe');
if(!existsSync(exe))throw new Error('AgentDock.exe not found.');
// Avoid stale in-memory profile/module registries overwriting this update.
const endpoint=join(directory,'endpoint.json');
if(existsSync(endpoint)){
 const info=JSON.parse(readFileSync(endpoint,'utf8'));
 let running=false;
 try{const response=await fetch(`http://127.0.0.1:${Number(info.port)}/health`,{headers:{Authorization:`Bearer ${info.token}`},signal:AbortSignal.timeout(1500)});running=response.ok;}catch{}
 if(running)throw new Error('請先從系統匣結束 AgentDock，再執行自動設定；或直接使用桌面內的 MCP 管理。');
}
const manager=new AgentManager(directory),path=resolve(config),type=kindSchema.parse(kind);
let profile=manager.list().find(p=>p.path.toLowerCase()===path.toLowerCase());
if(profile&&profile.kind!==type)throw new Error('此設定檔已登錄為不同類型。');
const packages=new ModulePackages(join(root,'modules'));packages.install(resolve(archive));
profile??=manager.add({kind:type,path,name});
const plan=manager.prepare([],{command:exe,entry:join(packages.directory(),'mcp.js'),directory,desktopExe:exe},[profile.id],undefined,[profile.id]);
const result=manager.apply(plan);new ModuleRegistry(directory).set('qainteract','install');
console.log(JSON.stringify({configured:profile.name,results:result,reloadClient:true}));
