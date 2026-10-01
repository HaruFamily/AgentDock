import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {zipSync,unzipSync} from 'fflate';
import {ModulePackages} from '../src/platform/core/packages.js';
function fixture(t:any){const dir=mkdtempSync(join(tmpdir(),'agentdock-package-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));return dir;}
test('external module package installs atomically, validates hashes and reuses an identical version',t=>{
 const dir=fixture(t),packages=new ModulePackages(join(dir,'modules')),file=resolve('dist/packages/QAInteract-0.5.0.admod');
 assert.equal(packages.available(),false);assert.throws(()=>packages.ensurePresent(),/尚未安裝/);
 packages.install(file);assert.equal(packages.available(),true);packages.ensurePresent();packages.install(file);
 assert.equal(packages.validate().id,'qainteract');
 writeFileSync(join(packages.directory(),'desktop.js'),'tampered');assert.throws(()=>packages.validate(),/校驗失敗/);
});
test('invalid version, modified payload and traversal archives never install',t=>{
 const dir=fixture(t),packages=new ModulePackages(join(dir,'modules')),original=unzipSync(readFileSync(resolve('dist/packages/QAInteract-0.5.0.admod')));
 for(const kind of ['version','payload','traversal','missing']){
   const files={...original},manifest=JSON.parse(Buffer.from(files['manifest.json']).toString());
   if(kind==='version'){manifest.api=999;files['manifest.json']=Buffer.from(JSON.stringify(manifest));}
   if(kind==='payload')files['desktop.js']=Buffer.from('changed');
   if(kind==='traversal')files['../outside.js']=Buffer.from('bad');
   if(kind==='missing')delete files['mcp.js'];
   const path=join(dir,`${kind}.admod`);writeFileSync(path,zipSync(files));assert.throws(()=>packages.install(path));assert.equal(packages.available(),false);
 }
 assert.equal(existsSync(join(dir,'outside.js')),false);
});
