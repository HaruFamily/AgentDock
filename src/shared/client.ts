import { readFileSync, existsSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import type { Endpoint } from './broker.js';

function workspaceRoot():string {
  let current=dirname(fileURLToPath(import.meta.url));
  while(dirname(current)!==current){
    try{if(JSON.parse(readFileSync(join(current,'package.json'),'utf8')).name==='agentdock')return current;}catch{}
    current=dirname(current);
  }
  return process.cwd();
}
export const projectRoot = workspaceRoot();
// Project-local by default: no user-wide client settings are modified.
export const dataDirectory = resolve(process.env.AIL_DATA_DIR ?? join(projectRoot, '.local/agentdock'));
export const ownerId = createHash('sha256').update(process.env.AIL_CLIENT_ID ?? `${process.env.AIL_SOURCE ?? 'MCP'}:${process.cwd()}`).digest('hex');
export class BrokerClient {
  constructor(readonly endpoint: Endpoint, readonly owner: string = ownerId) {}
  async request<T>(path: string, data?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`http://127.0.0.1:${this.endpoint.port}${path}`, {
      method: data === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${this.endpoint.token}`, 'X-Ail-Owner': this.owner, 'Content-Type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)
    });
    const value = await response.json() as T & { error?: string };
    if (!response.ok) throw new Error(value.error ?? `Local service returned ${response.status}`);
    return value;
  }
}
let connecting: Promise<BrokerClient> | undefined;
export async function ensureDesktop(module?: string): Promise<BrokerClient> {
  connecting ??= connectDesktop().finally(() => { connecting = undefined; });
  const client = await connecting;
  if (module) await client.request('/modules/ensure', { id: module });
  return client;
}
async function connectDesktop(): Promise<BrokerClient> {
  const registry = join(dataDirectory, 'endpoint.json');
  async function find(): Promise<BrokerClient | undefined> {
    if (!existsSync(registry)) return;
    try {
      const endpoint = JSON.parse(readFileSync(registry, 'utf8')) as Endpoint;
      if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 || !/^[a-f0-9]{64}$/.test(endpoint.token)) return;
      const client = new BrokerClient(endpoint);
      const health = await client.request<{ ok: boolean; protocol: number }>('/health', undefined, AbortSignal.timeout(700));
      if (health.ok && health.protocol === 1) return client;
    } catch { /* Registry may belong to a previous desktop process. */ }
  }
  const current = await find();
  if (current) return current;
  if (process.env.AIL_NO_LAUNCH === '1') throw new Error('Answer center is not running. Start it or unset AIL_NO_LAUNCH.');
  const req = createRequire(import.meta.url);
  // Never require electron here: its lazy download writes a banner to stdout,
  // which would corrupt the MCP stdio stream.
  const electronRoot = process.env.AIL_DESKTOP_EXE ? '' : dirname(req.resolve('electron/package.json'));
  const pathFile = join(electronRoot, 'path.txt');
  if (!process.env.AIL_DESKTOP_EXE && !existsSync(pathFile)) throw new Error('Desktop runtime is missing. Run npm run setup before using MCP.');
  const executable = process.env.AIL_DESKTOP_EXE ?? join(electronRoot, 'dist', readFileSync(pathFile, 'utf8').trim());
  const env: NodeJS.ProcessEnv = { ...process.env, AIL_DATA_DIR: dataDirectory };
  delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(executable, process.env.AIL_DESKTOP_EXE ? ['--background'] : [projectRoot, '--background'], { detached: true, stdio: 'ignore', windowsHide: true, env });
  let launchError: Error | undefined;
  child.on('error', error => { launchError = error; }); child.unref();
  for (let i = 0; i < 100; i++) {
    if (launchError) throw launchError;
    await new Promise(r => setTimeout(r, 200));
    const ready = await find(); if (ready) return ready;
  }
  throw new Error('桌面回答中心未能啟動。請先執行 npm start 檢查啟動錯誤。');
}
