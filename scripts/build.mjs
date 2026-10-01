import {build} from 'esbuild';
import {mkdirSync,cpSync,writeFileSync,readFileSync,readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {zipSync} from 'fflate';
const common={bundle:true,platform:'node',format:'esm',mainFields:['module','main'],target:'node22',external:['electron'],legalComments:'external',banner:{js:"import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);"}};
mkdirSync('dist/platform',{recursive:true});mkdirSync('dist/modules/QAInteract',{recursive:true});
const platform=await build({...common,entryPoints:['src/platform/desktop/main.ts'],outfile:'dist/platform/main.js',metafile:true});
await build({...common,entryPoints:['src/platform/install-module.ts'],outfile:'dist/platform/install-module.js'});
await build({...common,entryPoints:['src/platform/setup-qai.ts'],outfile:'dist/platform/setup-qai.js'});
if(Object.keys(platform.metafile.inputs).some(p=>p.startsWith('extensions/')))throw new Error('Platform bundle contains module implementation');
await build({...common,entryPoints:['extensions/QAInteract/src/desktop.ts'],outfile:'dist/modules/QAInteract/desktop.js'});
await build({...common,entryPoints:['extensions/QAInteract/src/mcp.ts'],outfile:'dist/modules/QAInteract/mcp.js'});
await build({...common,entryPoints:['src/shared/client.ts'],outfile:'dist/client.js'});
cpSync('src/platform/desktop/ui','dist/platform/ui',{recursive:true});cpSync('src/platform/desktop/preload.cjs','dist/platform/preload.cjs');
cpSync('extensions/QAInteract/src/ui','dist/modules/QAInteract/ui',{recursive:true});
writeFileSync('dist/modules/QAInteract/package.json',JSON.stringify({name:'agentdock-qainteract',version:'0.5.0',type:'module'}));
writeFileSync('dist/mcp.js',"// Legacy MCP path. Module code is a separate development artifact.\nimport './modules/QAInteract/mcp.js';\n");
const root='dist/modules/QAInteract',files={},hashes={};
for(const name of ['desktop.js','mcp.js','ui/app.js','ui/view.js','ui/styles.css','package.json','desktop.js.LICENSE.txt','mcp.js.LICENSE.txt']){
  try{const bytes=readFileSync(`${root}/${name}`);files[name]=bytes;hashes[name]=createHash('sha256').update(bytes).digest('hex');}catch(e){if(!name.endsWith('LICENSE.txt'))throw e;}
}
const manifest={id:'qainteract',name:'QAInteract',version:'0.5.0',api:1,files:hashes};
files['manifest.json']=Buffer.from(JSON.stringify(manifest,null,2));writeFileSync(`${root}/manifest.json`,files['manifest.json']);
mkdirSync('dist/packages',{recursive:true});writeFileSync('dist/packages/QAInteract-0.5.0.admod',zipSync(files,{level:9}));
writeFileSync('dist/platform/build-inputs.json',JSON.stringify(Object.keys(platform.metafile.inputs),null,2));
