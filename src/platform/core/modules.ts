import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { atomicJson } from '../../shared/files.js';

export const platformVersion = '0.5.0';
export const platformApi = 1;
export interface ModuleManifest {
  id: string; name: string; title: string; version: string; api: number;
  description: string; available: boolean;
}
// Supported module catalog only. Payloads are installed separately and validated before loading.
export const catalog: ModuleManifest[] = [
  { id: 'qainteract', name: 'QAInteract', title: '回答中心', version: '0.5.0', api: 1, available: true, description: '接收 Agent 提問，使用文字、選項與附件回答。' },
  { id: 'usage-monitor', name: 'UsageMonitor', title: '用量中心', version: '0.0.0', api: 1, available: false, description: '規劃中：查看各平台的額度與消耗。' },
  { id: 'agent-connector', name: 'AgentConnector', title: 'Agent 訊息', version: '0.0.0', api: 1, available: false, description: '評估中：集中接收與派發 Agent 訊息。' }
];
const stateSchema = z.object({ schema: z.literal(1), modules: z.record(z.enum(['enabled','disabled','removed'])) });
export class ModuleRegistry {
  private file: string;
  private state: z.infer<typeof stateSchema>;
  constructor(readonly directory: string) {
    mkdirSync(directory, { recursive: true });
    this.file = join(directory, 'modules.json');
    this.state = existsSync(this.file) ? stateSchema.parse(JSON.parse(readFileSync(this.file,'utf8'))) : {
      schema: 1, modules: existsSync(join(directory,'questions.json')) ? { qainteract:'enabled' } : {}
    };
    this.save();
  }
  private save(): void { atomicJson(this.file,this.state); }
  list() { return catalog.map(m => ({ ...m, state: this.state.modules[m.id] ?? 'absent' })); }
  enabled(id: string): boolean { return this.state.modules[id] === 'enabled'; }
  set(id: string, action: 'install'|'disable'|'remove'): void {
    const manifest = catalog.find(m=>m.id===id && m.available);
    if (!manifest) throw new Error('此模組尚未提供安裝。');
    if (manifest.api !== platformApi) throw new Error('此模組需要不同版本的 AgentDock，請先更新平台。');
    const next = { ...this.state, modules: { ...this.state.modules, [id]: action === 'install' ? 'enabled' as const : action === 'disable' ? 'disabled' as const : 'removed' as const } };
    atomicJson(this.file,next);
    this.state=next;
  }
  ensure(id: string): void {
    const state = this.state.modules[id];
    if (state === 'disabled' || state === 'removed') throw new Error('QAInteract 已停用或移除。請在 AgentDock 的模組頁重新啟用。');
    if (!state) this.set(id,'install');
  }
}
