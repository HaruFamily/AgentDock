import { BrowserWindow, Tray, Notification, dialog } from 'electron';
import { QuestionStore } from './storage.js';
import { questionRoutes } from './routes.js';
export function activate(context: { directory: string; window: BrowserWindow; tray: Tray; show: (id?: string)=>void; handle: (name: string, fn: (...args: any[])=>unknown)=>void; changed: ()=>void }) {
 const { directory, window, tray, show, handle }=context;
 const store=new QuestionStore(directory);
  function broadcast(): void {
    if (!window.isDestroyed()) window.webContents.send('questions-changed');
    const count = store.list().filter(q => q.status === 'pending').length;
    tray.setToolTip(count ? `AgentDock · ${count} 題待回答` : 'AgentDock');
  }
  store.on('change', broadcast);
  store.on('new-question', q => {
    if (!window.isVisible()) window.showInactive();
    window.flashFrame(true);
    if (!window.isFocused() && Notification.isSupported()) {
      const notification = new Notification({ title: `${q.source} 正在等你回答`, body: `${q.work_title}：${q.question.slice(0, 100)}` });
      notification.on('click', () => show(q.id)); notification.show();
    }
  });

  handle('qa:list', () => store.list().map(q => ({ ...q, connected: Date.now() - (store.lastSeen.get(q.id) ?? 0) < 5000 })));
  handle('qa:draft', (id, draft) => store.saveDraft(id, draft));
  handle('qa:answer', (id, answer) => {
    const result = store.answer(id, answer);
    context.changed();
    if (!store.list().some(q => q.status === 'pending') && process.env.AIL_TEST !== '1') window.hide();
    return result;
  });
  handle('qa:cancel', async id => {
    const choice = await dialog.showMessageBox(window, { type: 'question', buttons: ['返回回答', '取消這題'], defaultId: 0, cancelId: 0, message: '取消這個問題？', detail: 'Agent 會收到取消結果，不會收到默認答案。' });
    if(choice.response!==1) return null;
    const result=store.cancel(id);context.changed();return result;
  });
  handle('qa:upload', (id, name, url) => {
    if (typeof name !== 'string' || typeof url !== 'string' || url.length > 7_000_000) throw new Error('附件無效或超過 5 MB。');
    return store.addUpload(id, name, url);
  });
  handle('qa:remove-upload', (id, asset) => store.removeUpload(id, asset));
  handle('qa:asset', (id, asset) => {
    const data = store.asset(id, asset);
    if (!data.meta.mime.startsWith('image/')) throw new Error('此附件不能預覽。');
    return `data:${data.meta.mime};base64,${data.bytes.toString('base64')}`;
  });

 const route=questionRoutes(store,show);
 const timer=setInterval(broadcast,4000);timer.unref();
 store.on('new-question',context.changed);
 return { route, pending:()=>store.list().some(q=>q.status==='pending'), received:()=>store.list().map(q=>({source:q.source,at:q.created_at})), dispose:()=>{clearInterval(timer);store.removeAllListeners();} };
}
