import { z } from 'zod';

export const imageInput = z.object({
  path: z.string().max(4096).optional().describe('Absolute local PNG/JPEG/WebP/GIF path. Read by this local MCP process.'),
  data_url: z.string().max(7_000_000).optional().describe('Alternatively, a base64 image data URL.'),
  caption: z.string().max(500).default('')
}).refine(v => Boolean(v.path) !== Boolean(v.data_url), 'Provide exactly one of path or data_url');
export const askShape = {
  request_key: z.string().min(1).max(160).describe('Unique stable key for this question. Reuse on retry to avoid duplicates.'),
  work_id: z.string().min(1).max(160).describe('Stable ID for the originating conversation/task.'),
  work_title: z.string().min(1).max(160).describe('Human-readable project or task name.'),
  question: z.string().trim().min(1).max(16000),
  images: z.array(imageInput).max(6).default([]),
  mode: z.enum(['text', 'single', 'multiple']).default('text'),
  options: z.array(z.object({
    id: z.string().regex(/^[a-zA-Z0-9_-]{1,64}$/),
    label: z.string().trim().min(1).max(200),
    description: z.string().max(2000).default(''),
    images: z.array(imageInput).max(3).default([])
  })).max(12).default([]),
  wait_seconds: z.number().int().min(10).max(86400).default(1800)
    .describe('Maximum wait for this call. Configure the host tool timeout above this value. Timeout never approves or answers.')
};
export const askSchema = z.object(askShape).superRefine((v, ctx) => {
  if (v.mode === 'text' && v.options.length) ctx.addIssue({ code: 'custom', message: 'Text questions cannot contain options' });
  if (v.mode !== 'text' && v.options.length < 2) ctx.addIssue({ code: 'custom', message: 'Choice questions require at least two options' });
  if (new Set(v.options.map(o => o.id)).size !== v.options.length) ctx.addIssue({ code: 'custom', message: 'Option IDs must be unique' });
});
export const draftSchema = z.object({
  selected: z.array(z.string().max(64)).max(12),
  text: z.string().max(20000),
  notes: z.record(z.string().max(4000)),
  attachment_ids: z.array(z.string().uuid()).max(8)
}).strict();
export type AskInput = z.infer<typeof askSchema>;
export type Draft = z.infer<typeof draftSchema>;
export type Attachment = { id: string; name: string; mime: string; size: number; caption?: string };
export type StoredQuestion = Omit<AskInput, 'images' | 'options' | 'wait_seconds'> & {
  id: string; owner: string; source: string; created_at: string;
  status: 'pending' | 'answered' | 'cancelled'; resolved_at?: string;
  images: Attachment[];
  options: Array<Omit<AskInput['options'][number], 'images'> & { images: Attachment[] }>;
  uploads: Attachment[]; draft: Draft; answer?: Draft;
};
export const emptyDraft = (): Draft => ({ selected: [], text: '', notes: {}, attachment_ids: [] });
export type PreparedAsk = Omit<AskInput, 'images' | 'options'> & {
  images: Array<{ name: string; data_url: string; caption: string }>;
  options: Array<Omit<AskInput['options'][number], 'images'> & { images: Array<{ name: string; data_url: string; caption: string }> }>;
};
