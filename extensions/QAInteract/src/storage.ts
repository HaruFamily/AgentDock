import { atomicJson } from '../../../src/shared/files.js';
export { atomicJson } from '../../../src/shared/files.js';
import { mkdirSync, readFileSync, writeFileSync, renameSync, existsSync, unlinkSync } from 'node:fs';
import { join, basename } from 'node:path';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { draftSchema, emptyDraft, type Attachment, type Draft, type PreparedAsk, type StoredQuestion } from './schema.js';

export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
export function imageMime(data: Buffer): string | undefined {
  if (data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'image/png';
  if (data[0] === 255 && data[1] === 216 && data[2] === 255) return 'image/jpeg';
  if (['GIF87a', 'GIF89a'].includes(data.subarray(0, 6).toString())) return 'image/gif';
  if (data.subarray(0, 4).toString() === 'RIFF' && data.subarray(8, 12).toString() === 'WEBP') return 'image/webp';
}
export function decodeDataUrl(url: string): { bytes: Buffer; mime: string } {
  const match = /^data:([a-zA-Z0-9.+/-]+);base64,([A-Za-z0-9+/]*={0,2})$/.exec(url);
  if (!match) throw new Error('附件格式無效，請重新加入檔案。');
  const bytes = Buffer.from(match[2], 'base64');
  if (bytes.length > MAX_FILE_BYTES) throw new Error('每個附件上限為 5 MB。');
  return { bytes, mime: imageMime(bytes) ?? (match[1].startsWith('image/') ? 'application/octet-stream' : match[1]) };
}

export class QuestionStore extends EventEmitter {
  private questions: StoredQuestion[];
  private file: string;
  private assets: string;
  readonly lastSeen = new Map<string, number>();
  constructor(readonly directory: string) {
    super();
    mkdirSync(directory, { recursive: true });
    this.assets = join(directory, 'attachments');
    mkdirSync(this.assets, { recursive: true });
    this.file = join(directory, 'questions.json');
    this.questions = existsSync(this.file) ? JSON.parse(readFileSync(this.file, 'utf8')) : [];
    if (!Array.isArray(this.questions)) throw new Error('Question history is invalid. Preserve questions.json and repair it before restarting.');
  }
  list(): StoredQuestion[] { return structuredClone(this.questions); }
  get(id: string, owner?: string): StoredQuestion {
    const q = this.questions.find(q => q.id === id && (owner === undefined || q.owner === owner));
    if (!q) throw new Error('找不到這個工作的問題。');
    return structuredClone(q);
  }
  private persist(next: StoredQuestion[]): void {
    atomicJson(this.file, next);
    this.questions = next;
    this.emit('change');
  }
  private replace(q: StoredQuestion): void { this.persist(this.questions.map(old => old.id === q.id ? q : old)); }
  private saveAsset(name: string, dataUrl: string, caption?: string): Attachment {
    const { bytes, mime } = decodeDataUrl(dataUrl);
    const id = randomUUID();
    writeFileSync(join(this.assets, id), bytes, { mode: 0o600 });
    return { id, name: basename(name).slice(0, 200) || 'attachment', mime, size: bytes.length, caption };
  }
  create(owner: string, source: string, input: PreparedAsk): StoredQuestion {
    const existing = this.questions.find(q => q.owner === owner && q.work_id === input.work_id && q.request_key === input.request_key);
    if (existing) {
      const labels = (options: Array<{id: string; label: string; description: string}>) => options.map(({id,label,description})=>({id,label,description}));
      if (existing.question !== input.question || existing.mode !== input.mode || JSON.stringify(labels(existing.options)) !== JSON.stringify(labels(input.options))) {
        throw new Error('這個 request_key 已用於不同內容。新問題請使用新的 request_key。');
      }
      return structuredClone(existing);
    }
    // Validate all assets before writing, and never fetch remote URLs.
    const all = [...input.images, ...input.options.flatMap(o => o.images)];
    let total = 0;
    for (const image of all) {
      const { bytes, mime } = decodeDataUrl(image.data_url);
      if (!mime.startsWith('image/')) throw new Error('問題圖片僅接受 PNG、JPEG、GIF 或 WebP。');
      total += bytes.length;
    }
    if (total > MAX_TOTAL_BYTES) throw new Error('一題的圖片總量上限為 20 MB。');
    const saveImages = (images: PreparedAsk['images']) => images.map(i => this.saveAsset(i.name, i.data_url, i.caption));
    const q: StoredQuestion = {
      id: randomUUID(), owner, source, request_key: input.request_key, work_id: input.work_id,
      work_title: input.work_title, question: input.question, mode: input.mode,
      images: saveImages(input.images), options: input.options.map(o => ({ ...o, images: saveImages(o.images) })),
      created_at: new Date().toISOString(), status: 'pending', uploads: [], draft: emptyDraft()
    };
    this.persist([...this.questions, q]);
    this.emit('new-question', structuredClone(q));
    return structuredClone(q);
  }
  private validated(q: StoredQuestion, raw: unknown, submit: boolean): Draft {
    const answer = draftSchema.parse(raw);
    const ids = new Set(q.options.map(o => o.id));
    if (new Set(answer.selected).size !== answer.selected.length || answer.selected.some(id => !ids.has(id))) throw new Error('選項無效。');
    if (q.mode === 'single' && answer.selected.length > 1) throw new Error('這題只能選一項。');
    if (q.mode === 'text' && answer.selected.length) throw new Error('文字題不能選擇選項。');
    if (Object.keys(answer.notes).some(id => !ids.has(id))) throw new Error('備註的選項不存在。');
    if (new Set(answer.attachment_ids).size !== answer.attachment_ids.length || answer.attachment_ids.some(id => !q.uploads.some(a => a.id === id))) throw new Error('附件不屬於這個問題。');
    if (submit && !answer.selected.length && !answer.text.trim() && !answer.attachment_ids.length) throw new Error('請選擇選項、輸入回答或加入附件。');
    return answer;
  }
  saveDraft(id: string, raw: unknown): void {
    const q = this.get(id);
    if (q.status !== 'pending') return;
    q.draft = this.validated(q, raw, false);
    this.replace(q);
  }
  answer(id: string, raw: unknown): StoredQuestion {
    const q = this.get(id);
    if (q.status !== 'pending') throw new Error('這題已處理，不能重複提交。');
    q.answer = this.validated(q, raw, true);
    q.draft = q.answer;
    q.status = 'answered'; q.resolved_at = new Date().toISOString();
    this.replace(q);
    return q;
  }
  cancel(id: string): StoredQuestion {
    const q = this.get(id);
    if (q.status !== 'pending') throw new Error('這題已處理。');
    q.status = 'cancelled'; q.resolved_at = new Date().toISOString();
    this.replace(q); return q;
  }
  addUpload(id: string, name: string, dataUrl: string): Attachment {
    const q = this.get(id);
    if (q.status !== 'pending') throw new Error('這題已處理。');
    if (q.uploads.length >= 8) throw new Error('每題最多 8 個附件，請先移除不需要的附件。');
    const { bytes } = decodeDataUrl(dataUrl);
    if (q.uploads.reduce((n, a) => n + a.size, 0) + bytes.length > MAX_TOTAL_BYTES) throw new Error('回答附件總量上限為 20 MB。');
    const a = this.saveAsset(name, dataUrl);
    q.uploads.push(a); q.draft.attachment_ids.push(a.id); this.replace(q); return a;
  }
  removeUpload(id: string, assetId: string): void {
    const q = this.get(id);
    if (q.status !== 'pending') throw new Error('這題已處理。');
    const a = q.uploads.find(a => a.id === assetId);
    if (!a) return;
    q.uploads = q.uploads.filter(a => a.id !== assetId);
    q.draft.attachment_ids = q.draft.attachment_ids.filter(id => id !== assetId);
    this.replace(q);
    unlinkSync(join(this.assets, assetId));
  }
  asset(questionId: string, assetId: string, owner?: string, answersOnly = false): { meta: Attachment; bytes: Buffer } {
    const q = this.get(questionId, owner);
    const assets = answersOnly ? q.uploads.filter(a => q.answer?.attachment_ids.includes(a.id)) : [...q.images, ...q.options.flatMap(o => o.images), ...q.uploads];
    const meta = assets.find(a => a.id === assetId);
    if (!meta) throw new Error('找不到附件。');
    return { meta, bytes: readFileSync(join(this.assets, meta.id)) };
  }
}
