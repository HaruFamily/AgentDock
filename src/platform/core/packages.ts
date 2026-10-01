import { existsSync, readFileSync, mkdirSync, writeFileSync, renameSync, rmSync, statSync } from 'node:fs';
import { join, resolve, dirname, relative, isAbsolute } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { unzipSync } from 'fflate';
import { z } from 'zod';
export const packageSchema=z.object({
  id:z.literal('qainteract'),name:z.literal('QAInteract'),version:z.literal('0.5.0'),api:z.literal(1),
  files:z.record(z.string().regex(/^[a-f0-9]{64}$/))
}).strict();
const required=['desktop.js','mcp.js','ui/app.js','ui/view.js','ui/styles.css','package.json'];
const allowed=new Set([...required,'desktop.js.LICENSE.txt','mcp.js.LICENSE.txt']);
export const digest=(bytes:Uint8Array)=>createHash('sha256').update(bytes).digest('hex');
export function contained(root:string,path:string):string {
  const target=resolve(root,path),rel=relative(resolve(root),target);
  if(!rel||rel.startsWith('..')||isAbsolute(rel))throw new Error('Invalid module path');
  return target;
}
export class ModulePackages {
  constructor(readonly root:string,readonly developmentPackage?:string){}
  directory():string {return join(this.root,'qainteract','0.5.0');}
  available():boolean {return existsSync(join(this.directory(),'manifest.json'));}
  validate():ReturnType<typeof packageSchema.parse> {
    const dir=this.directory(),manifest=packageSchema.parse(JSON.parse(readFileSync(join(dir,'manifest.json'),'utf8')));
    for(const file of required)if(!manifest.files[file])throw new Error(`模組缺少 ${file}`);
    for(const [name,hash]of Object.entries(manifest.files)){
      if(!allowed.has(name))throw new Error(`不支援的模組檔案：${name}`);
      if(digest(readFileSync(contained(dir,name)))!==hash)throw new Error(`模組檔案校驗失敗：${name}`);
    }
    return manifest;
  }
  install(path:string):void {
    if(statSync(path).size>25*1024*1024)throw new Error('模組包超過 25 MB。');
    let expanded=0;
    const files=unzipSync(readFileSync(path),{filter:file=>{
      expanded+=file.originalSize;
      if(expanded>60*1024*1024||file.originalSize>20*1024*1024)throw new Error('模組包解壓大小超過限制。');
      if(!allowed.has(file.name)&&file.name!=='manifest.json')throw new Error(`不支援的模組檔案：${file.name}`);
      return true;
    }});
    if(!files['manifest.json'])throw new Error('找不到模組 manifest.json。');
    const manifest=packageSchema.parse(JSON.parse(Buffer.from(files['manifest.json']).toString('utf8')));
    for(const file of required)if(!manifest.files[file])throw new Error(`模組缺少 ${file}`);
    for(const [name,bytes]of Object.entries(files))if(name!=='manifest.json'&&digest(bytes)!==manifest.files[name])throw new Error(`模組檔案校驗失敗：${name}`);
    for(const [name,hash]of Object.entries(manifest.files))if(!allowed.has(name)||!files[name]||digest(files[name])!==hash)throw new Error(`模組清單無效：${name}`);
    if(this.available()){
      const current=this.validate();
      if(JSON.stringify(current.files)!==JSON.stringify(manifest.files))throw new Error('同版本模組內容不同。請保留現有版本，改用新版本號發佈。');
      return;
    }
    mkdirSync(this.root,{recursive:true});
    const staging=contained(this.root,`.install-${randomUUID()}`),target=this.directory();
    mkdirSync(staging);
    try {
      for(const [name,bytes]of Object.entries(files)){const file=contained(staging,name);mkdirSync(dirname(file),{recursive:true});writeFileSync(file,bytes);}
      mkdirSync(dirname(target),{recursive:true});renameSync(staging,target);
    }finally{if(existsSync(staging))rmSync(staging,{recursive:true});}
  }
  ensurePresent():void {
    if(!this.available()&&this.developmentPackage)this.install(this.developmentPackage);
    if(!this.available())throw new Error('QAInteract 尚未安裝。請在模組頁匯入 QAInteract 的 .admod 模組包。');
    this.validate();
  }
}
