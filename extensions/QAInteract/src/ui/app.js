import { template } from './view.js';
export async function mount(host) {
host.innerHTML = template;
const api = window.answers;
const $ = id => document.getElementById(id);
let questions = [], activeId, history = false, busy = false, renderingKey = '';
const drafts = new Map(), saveQueues = new Map(), imageCache = new Map();
let refreshRunning = false, refreshAgain = false, noticeTimer;
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function error(err) { $('notice').textContent = String(err?.message ?? err).replace(/^Error invoking remote method '[^']+': Error: /, ''); $('notice').hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 9000); }
function draftFor(q) { if (!drafts.has(q.id)) drafts.set(q.id, structuredClone(q.answer ?? q.draft)); return drafts.get(q.id); }
function save(q) {
  const snapshot = structuredClone(draftFor(q));
  const previous = saveQueues.get(q.id) ?? Promise.resolve();
  const task = previous.catch(() => {}).then(() => api.saveDraft(q.id, snapshot));
  saveQueues.set(q.id, task);
  task.then(() => { if (activeId === q.id && $('save-status')) $('save-status').textContent = '草稿已保存'; }).catch(error);
  if ($('save-status')) $('save-status').textContent = '儲存中…';
  return task;
}
function assetUrl(q, a) { const key = `${q.id}/${a.id}`; if (!imageCache.has(key)) imageCache.set(key, api.asset(q.id, a.id).catch(e => { imageCache.delete(key); throw e; })); return imageCache.get(key); }
async function preview(q, a) { try { $('large-image').src = await assetUrl(q, a); $('large-image').alt = a.caption || a.name; $('image-caption').textContent = a.caption || a.name; $('image-dialog').showModal(); } catch (e) { error(e); } }
function pictures(q, images) {
  const row = el('div', 'images');
  for (const a of images) {
    const button = el('button', 'image-button'); button.type = 'button'; button.setAttribute('aria-label', `放大圖片：${a.caption || a.name}`);
    const img = el('img'); img.alt = a.caption || a.name;
    assetUrl(q, a).then(url => { img.src = url; }).catch(error);
    button.append(img); if (a.caption) button.append(el('span', '', a.caption)); button.onclick = () => preview(q, a); row.append(button);
  }
  return row;
}
function showList() {
  const pending = questions.filter(q => q.status === 'pending'); $('pending-count').textContent = pending.length;
  const items = questions.filter(q => history ? q.status !== 'pending' : q.status === 'pending');
  const list = $('question-list'); list.replaceChildren();
  const groups = new Map();
  for (const q of items) { const key = `${q.owner}/${q.work_id}`; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(q); }
  for (const group of groups.values()) {
    list.append(el('div', 'group-label', `${group[0].work_title} · ${group.length}`));
    for (const q of group) {
      const button = el('button', `question-link${q.id === activeId ? ' active' : ''}`); button.dataset.questionId = q.id;
      button.setAttribute('aria-current', q.id === activeId ? 'true' : 'false');
      const row = el('span', 'row'); row.append(el('span', '', q.source));
      if (q.status === 'pending') row.append(el('span', 'status-dot')); else row.append(el('span', '', q.status === 'answered' ? '已回答' : '已取消'));
      button.append(row, el('span', 'preview', q.question)); button.onclick = () => { if (!busy) { activeId = q.id; render(); showList(); } }; list.append(button);
    }
  }
  if (!items.length) list.append(el('div', 'list-empty', history ? '還沒有已處理的問題' : '目前沒有待回答的問題'));
}
function updateSelection(q) { document.querySelectorAll('.option').forEach(card => card.classList.toggle('selected', draftFor(q).selected.includes(card.dataset.optionId))); }
function render() {
  const panel = $('question-panel'), q = questions.find(q => q.id === activeId);
  panel.replaceChildren(); renderingKey = q ? `${q.id}/${q.status}` : '';
  if (!q) {
    const empty = el('div', 'empty'); empty.append(el('div', 'empty-symbol', '↔'), el('h1', '', history ? '回答都留在這裡' : '等 Agent 問你下一題'), el('p', '', history ? '已提交的選擇、備註與附件，可以隨時回來查看。' : '需要你決定時，問題會自動出現在這裡。現在可以繼續手上的工作。'), el('small', '', '多個工作 · 同一個回答中心')); panel.append(empty); return;
  }
  const draft = draftFor(q), readonly = q.status !== 'pending';
  const scroll = el('div', 'question-scroll');
  const meta = el('div', 'question-meta'); meta.append(el('span', 'source-chip', q.source), el('span', 'work-chip', q.work_title));
  const waiting = el('span', `waiting${q.connected ? '' : ' detached'}`, readonly ? (q.status === 'answered' ? '已提交' : '已取消') : q.connected ? 'Agent 等待中' : '等待重新連線'); waiting.id = 'waiting-state'; meta.append(waiting); scroll.append(meta);
  scroll.append(el('h1', '', q.question));
  if (q.images.length) scroll.append(pictures(q, q.images));
  if (readonly) scroll.append(el('div', 'history-banner', q.status === 'answered' ? '這是已提交的回答。' : '這題已取消，沒有傳送默認答案。'));
  const hints = { text: '用文字回答，也可以貼上圖片或加入檔案。', single: '選一項，或直接在下方輸入自己的答案。', multiple: '可選多項，也可以在下方補充自己的想法。' };
  scroll.append(el('p', 'mode-hint', hints[q.mode]));
  const options = el('div', 'options');
  for (const option of q.options) {
    const card = el('div', `option${draft.selected.includes(option.id) ? ' selected' : ''}`); card.dataset.optionId = option.id;
    const label = el('label', 'option-label');
    const input = el('input'); input.type = q.mode === 'single' ? 'radio' : 'checkbox'; input.name = `choice-${q.id}`; input.value = option.id; input.checked = draft.selected.includes(option.id); input.disabled = readonly;
    input.onchange = () => { draft.selected = q.mode === 'single' ? [option.id] : [...options.querySelectorAll('input:checked')].map(i => i.value); updateSelection(q); save(q); };
    const copy = el('span', 'option-copy'); copy.append(el('strong', '', option.label)); if (option.description) copy.append(el('span', 'option-description', option.description)); label.append(input, copy); card.append(label);
    if (option.images.length) card.append(pictures(q, option.images));
    const details = el('details'); details.open = Boolean(draft.notes[option.id]); details.append(el('summary', '', draft.notes[option.id] ? '選項備註' : '新增備註'));
    const notes = el('textarea'); notes.value = draft.notes[option.id] ?? ''; notes.maxLength = 4000; notes.placeholder = '對這個選項的想法（不代表選取）'; notes.setAttribute('aria-label', `${option.label}的備註`); notes.readOnly = readonly;
    notes.oninput = () => { draft.notes[option.id] = notes.value; save(q); }; details.append(notes); card.append(details); options.append(card);
  }
  if (q.options.length) {
    scroll.append(options);
    if (!readonly) {
      const clear = el('button', 'clear-choice', '清除選擇，改用文字回答');
      clear.onclick = () => { draft.selected = []; options.querySelectorAll('input').forEach(i => { i.checked = false; }); updateSelection(q); save(q); $('answer-text').focus(); };
      scroll.append(clear);
    }
  }
  const answerArea = el('div', 'answer-area');
  const label = el('label', 'answer-label', q.mode === 'text' ? '你的回答' : '你的回答／補充'); label.htmlFor = 'answer-text';
  if (q.mode !== 'text') label.append(el('span', 'optional', '自由輸入，不受選項限制'));
  const text = el('textarea'); text.id = 'answer-text'; text.value = draft.text; text.maxLength = 20000; text.readOnly = readonly; text.placeholder = q.mode === 'text' ? '寫下你的回答…' : '補充原因，或提出其他做法…';
  text.oninput = () => { draft.text = text.value; save(q); }; answerArea.append(label, text);
  const files = el('div', 'files'); files.id = 'answer-files';
  const renderFiles = () => {
    files.replaceChildren();
    for (const a of q.uploads.filter(a => draft.attachment_ids.includes(a.id))) {
      const chip = el('div', 'file-chip');
      if (a.mime.startsWith('image/')) { const img = el('img', 'file-thumb'); img.alt = a.name; assetUrl(q, a).then(url => { img.src = url; }).catch(error); img.onclick = () => preview(q, a); chip.append(img); }
      chip.append(el('span', '', a.name));
      if (!readonly) { const remove = el('button', '', '×'); remove.title = `移除 ${a.name}`; remove.onclick = async () => { try { await saveQueues.get(q.id); await api.removeUpload(q.id, a.id); draft.attachment_ids = draft.attachment_ids.filter(id => id !== a.id); q.uploads = q.uploads.filter(file => file.id !== a.id); renderFiles(); } catch (e) { error(e); } }; chip.append(remove); }
      files.append(chip);
    }
  };
  async function uploadFiles(fileList) {
    if (readonly || busy) return;
    const pickedFiles = Array.from(fileList);
    busy = true; if ($('submit')) $('submit').disabled = true;
    try {
      await save(q);
      for (const file of pickedFiles) {
        if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name} 超過 5 MB，請選擇較小的檔案。`);
        const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
        const a = await api.upload(q.id, file.name, dataUrl); q.uploads.push(a); draft.attachment_ids.push(a.id); renderFiles();
      }
    } catch (e) { error(e); }
    finally { await save(q).catch(error); busy = false; if ($('submit')) $('submit').disabled = false; }
  }
  if (!readonly) {
    const row = el('div', 'upload-row'), add = el('button', 'upload-button', '＋ 圖片或檔案');
    const picker = el('input'); picker.type = 'file'; picker.multiple = true; picker.hidden = true; picker.id = 'file-picker'; picker.onchange = () => { void uploadFiles(picker.files); picker.value = ''; }; add.onclick = () => picker.click();
    row.append(add, el('span', '', '可拖曳或貼上圖片 · 每檔 5 MB')); answerArea.append(row, picker);
    scroll.ondragover = event => { event.preventDefault(); scroll.classList.add('dragging'); };
    scroll.ondragleave = () => scroll.classList.remove('dragging');
    scroll.ondrop = event => { event.preventDefault(); scroll.classList.remove('dragging'); void uploadFiles(event.dataTransfer.files); };
    scroll.onpaste = event => { const images = [...event.clipboardData.files].filter(f => f.type.startsWith('image/')); if (images.length) { event.preventDefault(); void uploadFiles(images); } };
  }
  renderFiles(); answerArea.append(files); scroll.append(answerArea); panel.append(scroll);
  const footer = el('footer', 'question-footer'), status = el('span', 'footer-status', readonly ? '回答紀錄保存在本機' : '草稿已保存'); status.id = 'save-status'; footer.append(status);
  if (!readonly) {
    const actions = el('div', 'footer-actions'), cancel = el('button', 'cancel', '取消這題'), submit = el('button', 'submit', '提交回答'); submit.id = 'submit'; submit.append(el('kbd', '', 'Ctrl ↵'));
    cancel.onclick = async () => { if (busy) return; busy = true; try { await save(q); await api.cancel(q.id); } catch (e) { error(e); } finally { busy = false; await refresh(); } };
    submit.onclick = async () => { if (busy) return; busy = true; submit.disabled = true; try { await save(q); await api.submit(q.id, structuredClone(draft)); $('notice').hidden = true; } catch (e) { error(e); } finally { busy = false; submit.disabled = false; await refresh(); } };
    actions.append(cancel, submit); footer.append(actions);
  }
  panel.append(footer);
}
async function refresh() {
  if (refreshRunning) { refreshAgain = true; return; }
  refreshRunning = true;
  try {
    questions = await api.list();
    const current = questions.find(q => q.id === activeId);
    if (!current || (!history && current.status !== 'pending')) activeId = questions.find(q => history ? q.status !== 'pending' : q.status === 'pending')?.id;
    showList();
    const q = questions.find(q => q.id === activeId), key = q ? `${q.id}/${q.status}` : '';
    if (key !== renderingKey || !$('question-panel').childElementCount) render();
    else if (q && q.status === 'pending' && $('waiting-state')) { $('waiting-state').textContent = q.connected ? 'Agent 等待中' : '等待重新連線'; $('waiting-state').className = `waiting${q.connected ? '' : ' detached'}`; }
  } catch (e) { error(e); }
  finally { refreshRunning = false; if (refreshAgain) { refreshAgain = false; void refresh(); } }
}
for (const [id, value] of [['pending-tab', false], ['history-tab', true]]) $(id).onclick = () => {
  if (busy) return; history = value; activeId = undefined;
  $('pending-tab').classList.toggle('active', !history); $('pending-tab').setAttribute('aria-pressed', String(!history));
  $('history-tab').classList.toggle('active', history); $('history-tab').setAttribute('aria-pressed', String(history)); void refresh();
};
$('close-image').onclick = () => $('image-dialog').close();
const keydown = event => { if (!host.hidden && ((event.ctrlKey || event.metaKey) && event.key === 'Enter')) { event.preventDefault(); $('submit')?.click(); } };
document.addEventListener('keydown', keydown);
const offChange = api.onChange(refresh);
const offFocus = api.onFocus(async id => { if (busy) return; history = false; activeId = id; $('pending-tab').classList.add('active'); $('history-tab').classList.remove('active'); await refresh(); });
await refresh();
return () => { offChange(); offFocus(); document.removeEventListener('keydown',keydown); clearTimeout(noticeTimer); host.replaceChildren(); };

}
