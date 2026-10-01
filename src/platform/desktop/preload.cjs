const { contextBridge, ipcRenderer } = require('electron');
const invoke = name => (...args) => ipcRenderer.invoke(name, ...args);
contextBridge.exposeInMainWorld('answers', {
  list: invoke('qa:list'), saveDraft: invoke('qa:draft'), submit: invoke('qa:answer'), cancel: invoke('qa:cancel'),
  upload: invoke('qa:upload'), removeUpload: invoke('qa:remove-upload'), asset: invoke('qa:asset'),
  hide: invoke('hide'), pin: invoke('pin'),
  onChange: callback => { const listener = () => callback(); ipcRenderer.on('questions-changed', listener); return () => ipcRenderer.removeListener('questions-changed', listener); },
  onFocus: callback => { const listener = (_event, id) => callback(id); ipcRenderer.on('focus-question', listener); return () => ipcRenderer.removeListener('focus-question', listener); }
});
contextBridge.exposeInMainWorld('agentDock', {
  state: invoke('dock:state'), module: invoke('dock:module'), prepare: invoke('dock:prepare'), apply: invoke('dock:apply'),
  addAgent: invoke('dock:agent-add'), removeAgent: invoke('dock:agent-remove'), pickConfig: invoke('dock:pick-config'), download: invoke('dock:download'), hide: invoke('hide'), pin: invoke('pin'),
  onChange: callback => { const listener=()=>callback();ipcRenderer.on('dock-changed',listener);return ()=>ipcRenderer.removeListener('dock-changed',listener); },
  onFocus: callback => { const listener=(_event,id)=>callback(id);ipcRenderer.on('dock-focus-module',listener);return ()=>ipcRenderer.removeListener('dock-focus-module',listener); }
});
