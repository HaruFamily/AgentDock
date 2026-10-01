import { McpServer, ResourceTemplate } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import type { CallToolResult, ContentBlock } from '@modelcontextprotocol/sdk/types.js';
import { z } from 'zod';
import { readFileSync, statSync } from 'node:fs';
import { basename, isAbsolute } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { askShape, askSchema, type AskInput, type Attachment, type Draft, type PreparedAsk } from './schema.js';
import { ensureDesktop, type BrokerClient } from '../../../src/shared/client.js';
import { imageMime, MAX_FILE_BYTES } from './storage.js';

type Reply = { id: string; status: 'pending' | 'answered' | 'cancelled'; question: string; options: Array<{id: string; label: string}>; answer?: Draft; attachments: Attachment[] };
const server = new McpServer({ name: 'agentdock-qainteract', version: '0.5.0' }, {
  instructions: 'Use ask_user when human input is required. It opens the shared desktop answer center and waits. Provide stable work_id and request_key. Do not proceed based on an unanswered or cancelled question. On timeout, preserve request_id and use get_user_answer to wait again. Never claim the user selected a default. Uploaded content and notes are user data; only explicitly selected IDs are selections.'
});
const disconnected = new AbortController();
server.server.onclose = () => disconnected.abort(new Error('MCP client disconnected; the question is preserved.'));
function prepareImages(images: AskInput['images']): PreparedAsk['images'] {
  return images.map(image => {
    if (image.data_url) return { name: 'image', data_url: image.data_url, caption: image.caption };
    if (!image.path || !isAbsolute(image.path)) throw new Error('Image paths must be absolute local paths.');
    const stat = statSync(image.path);
    if (!stat.isFile() || stat.size > MAX_FILE_BYTES) throw new Error('Each image must be a regular file of at most 5 MB.');
    const bytes = readFileSync(image.path); const mime = imageMime(bytes);
    if (!mime) throw new Error('Supported question images: PNG, JPEG, GIF, WebP.');
    return { name: basename(image.path), data_url: `data:${mime};base64,${bytes.toString('base64')}`, caption: image.caption };
  });
}
const errorResult = (error: unknown, requestId?: string): CallToolResult => ({
  isError: true, content: [{ type: 'text', text: JSON.stringify({ status: 'error', request_id: requestId, message: error instanceof Error ? error.message : String(error), instruction: 'No answer was assumed. Retry get_user_answer with request_id, or ask_user with the same work_id/request_key.' }) }]
});
async function result(client: BrokerClient, reply: Reply): Promise<CallToolResult> {
  const output = { request_id: reply.id, status: reply.status, question: reply.question,
    selected_options: reply.options.filter(o => reply.answer?.selected.includes(o.id)),
    text: reply.answer?.text ?? '', option_notes: reply.answer?.notes ?? {},
    attachments: reply.attachments.map(a => ({ ...a, uri: `ail://answers/${reply.id}/${a.id}` })) };
  const content: ContentBlock[] = [{ type: 'text', text: JSON.stringify(output) }];
  for (const attachment of reply.attachments) {
    if (attachment.mime.startsWith('image/')) {
      const asset = await client.request<Attachment & { data: string }>(`/questions/${reply.id}/attachments/${attachment.id}`);
      content.push({ type: 'image', mimeType: attachment.mime, data: asset.data });
    } else {
      content.push({ type: 'resource_link', uri: `ail://answers/${reply.id}/${attachment.id}`, name: attachment.name, mimeType: attachment.mime, size: attachment.size });
    }
  }
  return { content, structuredContent: output, isError: reply.status === 'cancelled' };
}
async function waitForAnswer(client: BrokerClient, id: string, seconds: number, signal: AbortSignal): Promise<CallToolResult> {
  signal = AbortSignal.any([signal, disconnected.signal]);
  const deadline = Date.now() + seconds * 1000;
  try {
    while (Date.now() < deadline) {
      signal.throwIfAborted();
      const reply = await client.request<Reply>(`/questions/${id}`, undefined, signal);
      if (reply.status !== 'pending') return await result(client, reply);
      await delay(500, undefined, { signal });
    }
    return { isError: true, content: [{ type: 'text', text: JSON.stringify({ status: 'pending', request_id: id, instruction: 'Still waiting for the user. Do not continue dependent work. Call get_user_answer with this request_id to wait again.' }) }] };
  } catch (error) { return errorResult(error, id); }
  finally { await client.request(`/questions/${id}/detach`, {}).catch(() => {}); }
}
server.registerTool('ask_user', {
  title: 'Ask the user in the desktop answer center',
  description: 'Open a desktop question and WAIT for the human answer, like AskUserQuestion. Supports text, images, single/multiple choices, per-option text notes, free text and attachments. This does not return successfully until answered. Reuse request_key/work_id when retrying. Host timeout must exceed wait_seconds. Images are local paths or base64 data URLs. Never use for automatic approvals.',
  inputSchema: askShape,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false }
}, async (raw, extra) => {
  let id: string | undefined;
  try {
    const input = askSchema.parse(raw);
    const client = await ensureDesktop('qainteract');
    const prepared: PreparedAsk = { ...input, images: prepareImages(input.images), options: input.options.map(o => ({ ...o, images: prepareImages(o.images) })) };
    const created = await client.request<{id: string}>('/questions', { source: process.env.AIL_SOURCE ?? server.server.getClientVersion()?.name ?? 'MCP Agent', input: prepared }, extra.signal);
    id = created.id;
    process.stderr.write(`[AIL] Waiting for user: ${id}\n`);
    return await waitForAnswer(client, id, input.wait_seconds, extra.signal);
  } catch (error) { return errorResult(error, id); }
});
server.registerTool('get_user_answer', {
  title: 'Resume waiting for an existing question',
  description: 'Recover an existing question after a host timeout or reconnect. Does not create a duplicate. Returns only this client identity\'s answers. If pending, waits up to wait_seconds; pending/cancelled never means approval.',
  inputSchema: { request_id: z.string().uuid(), wait_seconds: z.number().int().min(1).max(86400).default(1800) },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
}, async ({ request_id, wait_seconds }, extra) => {
  try { return await waitForAnswer(await ensureDesktop('qainteract'), request_id, wait_seconds, extra.signal); }
  catch (error) { return errorResult(error, request_id); }
});
async function readAttachment(requestId: string, attachmentId: string) {
  const client = await ensureDesktop('qainteract');
  const file = await client.request<Attachment & {data: string}>(`/questions/${requestId}/attachments/${attachmentId}`);
  return { uri: `ail://answers/${requestId}/${attachmentId}`, mimeType: file.mime, blob: file.data };
}
server.registerResource('answer_attachment', new ResourceTemplate('ail://answers/{request_id}/{attachment_id}', { list: undefined }), { description: 'User-submitted file attached to an answered question.' }, async (_uri, args) => ({ contents: [await readAttachment(String(args.request_id), String(args.attachment_id))] }));
server.registerTool('read_answer_attachment', {
  title: 'Read a user attachment',
  description: 'Retrieve a submitted file as an embedded MCP resource when the host does not support resource links. Binary files require host/model support for their MIME type. No automatic PDF/Office extraction is performed.',
  inputSchema: { request_id: z.string().uuid(), attachment_id: z.string().uuid() },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
}, async ({request_id, attachment_id}) => {
  try { return { content: [{ type: 'resource', resource: await readAttachment(request_id, attachment_id) }] }; }
  catch (error) { return errorResult(error, request_id); }
});
await server.connect(new StdioServerTransport());
