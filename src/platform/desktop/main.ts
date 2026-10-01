import { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, dialog, session, clipboard, protocol } from 'electron';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdirSync, appendFileSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { ModuleRegistry, platformVersion } from '../core/modules.js';
import { startPlatformBroker, send } from '../../shared/broker.js';
import type { ModuleRuntime, ModuleContext } from '../../shared/module-api.js';
import { ModulePackages, contained } from '../core/packages.js';
import { AgentManager, type AgentPlan } from '../core/agents.js';
import { downloadModule } from '../core/download.js';
import { z } from 'zod';
const here=dirname(fileURLToPath(import.meta.url)), root=resolve(here,'../..');
protocol.registerSchemesAsPrivileged([{scheme:'agentdock-module',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true}}]);
// Preserve v0.1 data and client identity paths.
const directory=resolve(process.env.AIL_DATA_DIR ?? (app.isPackaged ? join(dirname(process.execPath),'data') : resolve(root,'.local/agentdock')));
mkdirSync(directory,{recursive:true});
app.setPath('userData',join(directory,'desktop'));
app.setAppUserModelId('local.agentinteractionlayer.desktop');
let window:BrowserWindow, tray:Tray, quitting=false;
let closeBroker:(()=>Promise<void>)|undefined;
let qa:ModuleRuntime|undefined;
let loading:Promise<void>|undefined;
const registry=new ModuleRegistry(directory), qaChannels=new Set<string>();
const packages=new ModulePackages(process.env.AGENTDOCK_MODULE_DIR ?? (app.isPackaged?join(dirname(process.execPath),'modules'):join(directory,'modules')),app.isPackaged?undefined:join(root,'dist/packages/QAInteract-0.5.0.admod'));
const agents=new AgentManager(directory);let pendingPlan:AgentPlan|undefined;
const defaults={codex:join(process.env.CODEX_HOME ?? join(app.getPath('home'),'.codex'),'config.toml'),opencode:join(app.getPath('home'),'.config/opencode/opencode.json'),'claude-code':join(app.getPath('home'),'.claude.json'),'claude-desktop':join(app.getPath('appData'),'Claude/claude_desktop_config.json')};
const settings={command:app.isPackaged?process.execPath:(process.env.AGENTDOCK_NODE??'node'),entry:join(packages.directory(),'mcp.js'),directory,desktopExe:app.isPackaged?process.execPath:undefined};
function show(id?:string):void {
  if(!window || window.isDestroyed()) return;
  if(window.isMinimized()) window.restore(); window.show();window.focus();
  if(id) {window.webContents.send('dock-focus-module','qainteract');window.webContents.send('focus-question',id);}
}
function changed():void {if(window&&!window.isDestroyed()) window.webContents.send('dock-changed');}
function handle(name:string,fn:(...args:any[])=>unknown):void {
  ipcMain.handle(name,(event,...args)=>{
    if(event.sender!==window.webContents || event.senderFrame!==window.webContents.mainFrame) throw new Error('Invalid sender');
    return fn(...args);
  });
}
async function activateQa():Promise<void> {
  if(qa) return;
  loading??=(async()=>{
    packages.ensurePresent();
    const module=await import(pathToFileURL(join(packages.directory(),'desktop.js')).href) as {activate:(context:ModuleContext)=>ModuleRuntime};
    qa=module.activate({directory,window,tray,show,changed,handle:(name,fn)=>{qaChannels.add(name);handle(name,fn);}});
  })().finally(()=>{loading=undefined;});
  await loading;
}
async function ensureModule(id:string):Promise<void> {
  if(id!=='qainteract')throw new Error('Unknown module');
  if(registry.list().some(m=>m.id===id&&['disabled','removed'].includes(m.state)))registry.ensure(id);
  packages.ensurePresent();registry.ensure(id);await activateQa();changed();
}
async function installModule():Promise<boolean> {
  if(!packages.available()&&!packages.developmentPackage){
    const pick=await dialog.showOpenDialog(window,{title:'選擇 QAInteract 模組包',properties:['openFile'],filters:[{name:'AgentDock 模組',extensions:['admod']}]});
    if(pick.canceled)return false;
    const confirm=await dialog.showMessageBox(window,{type:'question',buttons:['取消','安裝模組'],defaultId:0,cancelId:0,message:'安裝 QAInteract 模組包？',detail:`${pick.filePaths[0]}\n模組包含可執行程式，請使用你信任的發行來源。`});
    if(confirm.response!==1)return false;
    packages.install(pick.filePaths[0]);
  }
  packages.ensurePresent();registry.set('qainteract','install');await activateQa();changed();return true;
}
if(!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance',(_event,argv)=>{void (async()=>{await app.whenReady();if(argv.includes('--install-qainteract'))await installModule();if(!argv.includes('--background'))show();})().catch(e=>dialog.showErrorBox('AgentDock',String(e)));});
  app.on('before-quit',()=>{quitting=true;qa?.dispose();void closeBroker?.();});
  app.on('window-all-closed',()=>{});
  void app.whenReady().then(start).catch(error=>{appendFileSync(join(directory,'desktop-error.log'),`${new Date().toISOString()} ${error.stack??error}\n`);dialog.showErrorBox('AgentDock 啟動失敗',String(error));app.quit();});
}
async function start():Promise<void> {
  if(app.isPackaged&&process.env.AIL_TEST!=='1'){
    try{const location=join(process.env.LOCALAPPDATA??app.getPath('appData'),'AgentDock');mkdirSync(location,{recursive:true});writeFileSync(join(location,'portable.json'),JSON.stringify({directory:dirname(process.execPath)}));}catch{/* Optional discovery hint; portable startup still works. */}
  }
  protocol.handle('agentdock-module',request=>{
    const url=new URL(request.url),path=decodeURIComponent(url.pathname).replace(/^\//,'');
    if(url.hostname!=='qainteract'||!registry.enabled('qainteract')||!['ui/app.js','ui/view.js','ui/styles.css'].includes(path))return new Response('Not found',{status:404});
    try{return new Response(readFileSync(contained(packages.directory(),path)),{headers:{'Content-Type':path.endsWith('.css')?'text/css':'text/javascript','Access-Control-Allow-Origin':'*'}});}catch{return new Response('Not found',{status:404});}
  });
  window=new BrowserWindow({width:1040,height:780,minWidth:700,minHeight:560,show:false,title:'AgentDock',backgroundColor:'#f5f7fb',autoHideMenuBar:true,webPreferences:{preload:join(here,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  window.setMenu(null);
  window.on('close',event=>{if(!quitting){event.preventDefault();window.hide();}});
  window.on('focus',()=>window.flashFrame(false));
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',event=>event.preventDefault());
  session.defaultSession.setPermissionRequestHandler((_c,_p,callback)=>callback(false));
  const icon=nativeImage.createFromPath(join(here,'ui/icon.png')).resize({width:16,height:16});window.setIcon(icon);
  tray=new Tray(icon);tray.setToolTip('AgentDock');tray.on('click',()=>show());
  tray.setContextMenu(Menu.buildFromTemplate([{label:'開啟 AgentDock',click:()=>show()},{label:'視窗置頂',type:'checkbox',click:item=>window.setAlwaysOnTop(item.checked)},{type:'separator'},{label:'結束 AgentDock',click:async()=>{
    if(qa?.pending()) {const result=await dialog.showMessageBox(window,{type:'question',buttons:['繼續等待','結束程式'],defaultId:0,cancelId:0,message:'仍有待回答的問題',detail:'問題與草稿會保留，但 Agent 的等待連線可能中斷。'});if(result.response!==1) return;}app.quit();
  }}]));
  handle('hide',()=>window.hide());
  handle('pin',value=>{window.setAlwaysOnTop(Boolean(value));return window.isAlwaysOnTop();});
  handle('dock:state',()=>({version:platformVersion,modules:registry.list().map(m=>({...m,state:m.id==='qainteract'&&m.state==='enabled'&&!qa?'missing':m.state})),pending:qa?.pending()??false,received:qa?.received()??[],agents:agents.inspect(),defaults}));
  handle('dock:module',async(id,action)=>{
    if(id!=='qainteract'||!['install','disable','remove'].includes(action)) throw new Error('Unsupported module action');
    await loading;
    if(action!=='install' && qa?.pending()) throw new Error('仍有待回答問題，請先回答或取消，再停用模組。');
    if(action==='install') return await installModule();
    registry.set(id,action);
    qa?.dispose();qa=undefined;for(const channel of qaChannels) ipcMain.removeHandler(channel);qaChannels.clear();tray.setToolTip('AgentDock');
    changed();return true;
  });
  handle('dock:agent-add',raw=>{const p=agents.add(raw);pendingPlan=undefined;changed();return p;});
  handle('dock:agent-remove',id=>{agents.remove(z.string().uuid().parse(id));pendingPlan=undefined;changed();});
  handle('dock:pick-config',async()=>{const result=await dialog.showOpenDialog(window,{title:'選擇 Agent 的設定檔',properties:['openFile'],filters:[{name:'設定檔',extensions:['toml','json','jsonc']}]});return result.canceled?null:result.filePaths[0];});
  handle('dock:prepare',raw=>{
    const request=z.object({changes:z.array(z.unknown()).default([]),qaiTargets:z.array(z.string().uuid()).optional(),remote:z.object({name:z.string(),url:z.string(),targets:z.array(z.string().uuid()).min(1)}).optional()}).strict().parse(raw);
    if(request.qaiTargets&&!qa)throw new Error('請先安裝並啟用 QAI。');
    pendingPlan=agents.prepare(request.changes,request.qaiTargets?settings:undefined,request.qaiTargets,request.remote);
    return {id:pendingPlan.id,summary:pendingPlan.summary};
  });
  handle('dock:apply',id=>{
    if(!pendingPlan||id!==pendingPlan.id)throw new Error('預覽已失效，請重新預覽。');
    const plan=pendingPlan;pendingPlan=undefined;const results=agents.apply(plan);changed();return results;
  });
  handle('dock:download',async raw=>{
    const url=z.string().url().max(4096).parse(raw);
    const result=await dialog.showMessageBox(window,{type:'question',buttons:['取消','下載並安裝'],defaultId:0,cancelId:0,message:'從此來源安裝 QAI？',detail:`${url}\n模組含可執行程式，請確認是你信任的發行者。`});
    if(result.response!==1)return false;
    const file=await downloadModule(url,join(directory,'downloads'));
    try{packages.install(file);}finally{unlinkSync(file);}
    registry.set('qainteract','install');await activateQa();changed();return true;
  });
  if(process.argv.includes('--install-qainteract')) await installModule();
  if(registry.enabled('qainteract')&&(packages.available()||packages.developmentPackage)) await activateQa();
  const broker=await startPlatformBroker(directory,{show,ensureModule,route:async(req,res,owner)=>{
    if(!registry.enabled('qainteract')||!qa) return send(res,404,{error:'QAInteract is not enabled. Install it in AgentDock.'});
    await qa.route(req,res,owner);
  }});closeBroker=broker.close;
  await window.loadFile(join(here,'ui/index.html'));
  if(!process.argv.includes('--background')) show();
}
