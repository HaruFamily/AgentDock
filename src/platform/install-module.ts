import { ModulePackages } from './core/packages.js';
import { resolve } from 'node:path';
const [archive,root]=process.argv.slice(2);
if(!archive||!root)throw new Error('Usage: install-module <archive.admod> <modules-directory>');
new ModulePackages(resolve(root)).install(resolve(archive));
console.log('QAInteract module package installed.');
