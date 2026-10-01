const api=window.agentDock,$=id=>document.getElementById(id);
let state,active='modules',mounted=false,refreshPromise,qaLoading,unmount,plan,qaiSelection,remoteSelection=new Set();
const changes=new Map(),kinds={codex:'Codex',opencode:'OpenCode','claude-code':'Claude Code','claude-desktop':'Claude Desktop'};
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function notice(e){$('notice').textContent=String(e?.message??e).replace(/^Error invoking remote method '[^']+': Error: /,'');$('notice').hidden=false;}
function button(text,fn,cls='quiet'){const b=el('button',cls,text);b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){notice(e);}finally{b.disabled=false;}};return b;}
function bind(id,fn){$(id).onclick=async()=>{$(id).disabled=true;try{await fn();}catch(e){notice(e);}finally{$(id).disabled=false;}};}
function check(text,checked,fn,disabled=false){const label=el('label','check-row'),input=el('input');input.type='checkbox';input.checked=checked;input.disabled=disabled;input.onchange=()=>fn(input.checked);label.append(input,el('span','',text));return label;}
function navigate(page){if(page==='qa'&&!mounted)page='modules';active=page;for(const id of ['modules','agents','connections'])$(id+'-page').hidden=page!==id;$('module-host').hidden=page!=='qa';for(const id of ['modules','agents','connections','qa'])$('nav-'+id).setAttribute('aria-current',page===id?'page':'false');}
async function preview(request){plan=await api.prepare(request);if(!plan.summary.length){notice('沒有需要變更的設定。');return;}const host=$('plan-summary');host.replaceChildren();for(const item of plan.summary){const card=el('article','plan-item');card.append(el('h3','',item.agent),el('p','path',item.path));for(const line of item.changes)card.append(el('p','',line));if(item.reformats)card.append(el('p','muted','TOML 會重新排版，原有註解不保留；完整原檔會備份。'));host.append(card);}$('plan-dialog').showModal();}
async function refresh(){
 if(refreshPromise)return refreshPromise;
 refreshPromise=(async()=>{
  const wasPending=state?.pending;state=await api.state();const enabled=state.modules.some(m=>m.id==='qainteract'&&m.state==='enabled');
  $('nav-qa').hidden=!enabled;$('qa-dot').hidden=!state.pending;
  if(enabled&&!mounted){qaLoading??=(async()=>{if(!$('qa-style')){const css=el('link');css.id='qa-style';css.rel='stylesheet';css.href='agentdock-module://qainteract/ui/styles.css';document.head.prepend(css);}const {mount}=await import('agentdock-module://qainteract/ui/app.js');unmount=await mount($('module-host'));mounted=true;})();await qaLoading;}
  if(!enabled&&mounted){unmount?.();mounted=false;qaLoading=undefined;if(active==='qa')navigate('modules');}
  if(mounted&&state.pending&&!wasPending&&!document.activeElement?.matches('input,textarea,select')&&!$('plan-dialog').open)navigate('qa');
  const cards=$('module-cards');cards.replaceChildren();for(const m of state.modules){const card=el('article','module-card'+(!m.available?' future':'')),head=el('div','module-heading');head.append(el('h2','',m.title),el('span','module-status',!m.available?'尚未推出':({enabled:'已啟用',disabled:'已停用',removed:'已移除',absent:'可安裝',missing:'需要模組包'}[m.state])));card.append(head,el('p','module-name',m.name+(m.available?' · '+m.version:'')),el('p','',m.description));if(m.available){const actions=el('div','module-actions');if(m.state==='enabled')actions.append(button('開啟回答中心',()=>navigate('qa'),'primary'),button('停用',async()=>{await api.module(m.id,'disable');await refresh();}),button('移除模組',async()=>{await api.module(m.id,'remove');await refresh();}));else actions.append(button(m.state==='disabled'?'啟用模組':'安裝模組',async()=>{if(await api.module(m.id,'install')){await refresh();notice('QAI 已啟用。請在下方選擇要支援的 Agent。');}},'primary'));card.append(actions);}cards.append(card);}
  if(!$('agent-path').value)$('agent-path').value=state.defaults[$('agent-kind').value];updateHelp();
  const agents=$('agent-list'),mcps=$('mcp-list'),targets=$('qai-targets'),remotes=$('remote-targets');for(const host of [agents,mcps,targets,remotes])host.replaceChildren();
  if(!qaiSelection)qaiSelection=new Set(state.agents.filter(p=>p.servers.some(s=>s.qai&&s.enabled)).map(p=>p.id));
  if(!state.agents.length){for(const host of [agents,mcps,targets])host.append(el('p','muted','尚未登錄 Agent。請先到「Agent 清單」新增。'));}
  for(const p of state.agents){
   const card=el('article','module-card');card.append(el('h2','',p.name),el('p','',kinds[p.kind]),el('p','path',p.path),el('p','muted',p.error||(p.exists?'設定檔已找到':'尚無設定檔；套用時會建立')),button('移除登錄',async()=>{await api.removeAgent(p.id);qaiSelection.delete(p.id);await refresh();}));agents.append(card);
   targets.append(check(p.name+(p.error?'（設定檔需要修正）':''),qaiSelection.has(p.id),yes=>yes?qaiSelection.add(p.id):qaiSelection.delete(p.id),Boolean(p.error)));
   if(p.kind!=='claude-desktop')remotes.append(check(p.name,remoteSelection.has(p.id),yes=>yes?remoteSelection.add(p.id):remoteSelection.delete(p.id),Boolean(p.error)));
   const m=el('article','module-card');m.append(el('h2','',p.name),el('p','path',p.path));if(p.error)m.append(el('p','',p.error));else if(!p.servers.length)m.append(el('p','muted','這份檔案沒有 MCP。可至擴充頁連接 QAI。'));
   for(const server of p.servers){const key=p.id+':'+server.name;const row=check(server.name,changes.has(key)?changes.get(key).enabled:server.enabled,yes=>{if(yes===server.enabled)changes.delete(key);else changes.set(key,{profile:p.id,server:server.name,enabled:yes});});row.append(el('small','muted',(server.enabled?'檔案設定：啟用':'檔案設定：停用')+' · 連線尚未驗證'));m.append(row);}mcps.append(m);
  }
  $('prepare-qai').disabled=!enabled||!state.agents.length;
  const received=$('received-list');received.replaceChildren();const latest=new Map();for(const r of state.received)if(!latest.has(r.source)||latest.get(r.source)<r.at)latest.set(r.source,r.at);if(!latest.size)received.append(el('p','muted','尚未收到問答呼叫。'));for(const [source,at]of latest){const row=el('div','received-row');row.append(el('span','',source),el('time','',new Date(at).toLocaleString()));received.append(row);}
 })().finally(()=>{refreshPromise=undefined;});return refreshPromise;
}
function updateHelp(){$('path-help').textContent=$('agent-kind').value==='claude-code'?'預設為使用者設定。專案請改選該專案的 .mcp.json。':$('agent-kind').value==='opencode'?'可改選專案中的 opencode.json 或 opencode.jsonc。':'請確認此路徑是客戶端實際讀取的設定檔。';}
for(const id of ['modules','agents','connections','qa'])$('nav-'+id).onclick=()=>navigate(id);
$('agent-kind').onchange=()=>{$('agent-path').value=state.defaults[$('agent-kind').value];updateHelp();};
$('agent-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await api.addAgent({name:$('agent-name').value,kind:$('agent-kind').value,path:$('agent-path').value});$('agent-name').value='';await refresh();notice('已記住 Agent。接著到擴充頁選擇 QAI 的支援對象。');}catch(e){notice(e);}finally{b.disabled=false;}};
bind('pick-config',async()=>{const p=await api.pickConfig();if(p)$('agent-path').value=p;});
bind('download-module',async()=>{if(await api.download($('module-url').value)){await refresh();notice('QAI 已安裝。請選擇要支援的 Agent。');}});
bind('prepare-qai',()=>preview({qaiTargets:[...qaiSelection]}));
bind('prepare-changes',()=>preview({changes:[...changes.values()]}));
bind('prepare-remote',()=>preview({remote:{name:$('remote-name').value,url:$('remote-url').value,targets:[...remoteSelection]}}));
bind('refresh-config',async()=>{changes.clear();qaiSelection=undefined;await refresh();});
$('cancel-plan').onclick=()=>$('plan-dialog').close();
bind('apply-plan',async()=>{const result=await api.apply(plan.id);changes.clear();qaiSelection=undefined;remoteSelection.clear();$('plan-dialog').close();await refresh();notice('已更新 '+result.length+' 份設定。請重新載入客戶端，呼叫 ask_user 確認。備份保存在各原設定檔旁。');});
bind('pin',async()=>$('pin').setAttribute('aria-pressed',String(await api.pin($('pin').getAttribute('aria-pressed')!=='true'))));bind('hide',()=>api.hide());$('notice').onclick=()=>{$('notice').hidden=true;};
api.onChange(()=>refresh().catch(notice));api.onFocus(()=>void refresh().then(()=>navigate('qa')).catch(notice));void refresh().catch(notice);
