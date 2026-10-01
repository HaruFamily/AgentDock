import type { IncomingMessage, ServerResponse } from 'node:http';
import { z } from 'zod';
import { QuestionStore } from './storage.js';
import { askSchema } from './schema.js';
import { body, send } from '../../../src/shared/broker.js';
export function questionRoutes(store: QuestionStore, onFocus: (id?: string) => void) {
return async (req: IncomingMessage, res: ServerResponse, owner: string) => {
 const url = new URL(req.url ?? '/', 'http://127.0.0.1');
      if (req.method === 'POST' && url.pathname === '/questions') {
        const raw = z.object({ source: z.string().min(1).max(100), input: z.unknown() }).parse(await body(req));
        const parsed = askSchema.parse(raw.input);
        const original = raw.input as { images: Array<{name?: string}>; options: Array<{images: Array<{name?: string}>}> };
        const prep = (images: typeof parsed.images, names: Array<{ name?: string }> = []) => images.map((i, n) => {
          if (!i.data_url) throw new Error('Images must be prepared by the MCP adapter');
          return { name: names[n]?.name ?? 'image', data_url: i.data_url, caption: i.caption };
        });
        const q = store.create(owner, raw.source, { ...parsed, images: prep(parsed.images, original.images), options: parsed.options.map((o, i) => ({ ...o, images: prep(o.images, original.options[i].images) })) });
        store.lastSeen.set(q.id, Date.now());
        return send(res, 200, { id: q.id });
      }
      const match = /^\/questions\/([a-f0-9-]{36})(?:\/(focus|detach|attachments\/([a-f0-9-]{36})))?$/.exec(url.pathname);
      if (!match) return send(res, 404, { error: 'Not found' });
      const q = store.get(match[1], owner);
      if (req.method === 'GET' && !match[2]) {
        store.lastSeen.set(q.id, Date.now());
        return send(res, 200, { id: q.id, status: q.status, question: q.question, options: q.options.map(({id,label}) => ({id,label})), answer: q.answer, attachments: q.uploads.filter(a => q.answer?.attachment_ids.includes(a.id)) });
      }
      if (req.method === 'POST' && match[2] === 'focus') { onFocus(q.id); return send(res, 200, { ok: true }); }
      if (req.method === 'POST' && match[2] === 'detach') { store.lastSeen.delete(q.id); store.emit('change'); return send(res, 200, { ok: true }); }
      if (req.method === 'GET' && match[3]) {
        const asset = store.asset(q.id, match[3], owner, true);
        return send(res, 200, { ...asset.meta, data: asset.bytes.toString('base64') });
      }
      return send(res, 404, { error: 'Not found' });

};
}
