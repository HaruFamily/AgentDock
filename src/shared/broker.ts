import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { join } from 'node:path';
import { atomicJson } from './files.js';
export type Endpoint = { port: number; token: string; pid: number };
export async function body(req: IncomingMessage): Promise<unknown> {
  const parts: Buffer[]=[]; let size=0;
  for await(const part of req) { size+=part.length; if(size>30*1024*1024) throw new Error('Request too large'); parts.push(part); }
  return JSON.parse(Buffer.concat(parts).toString('utf8'));
}
export function send(res: ServerResponse, code: number, data: unknown): void {
  res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
  res.end(JSON.stringify(data));
}
export interface BrokerHooks {
  show: (id?: string)=>void;
  ensureModule: (id: string)=>void|Promise<void>;
  route: (req: IncomingMessage, res: ServerResponse, owner: string)=>void|Promise<void>;
}
export async function startPlatformBroker(directory: string, hooks: BrokerHooks) {
  const token=randomBytes(32).toString('hex');
  const server=createServer(async(req,res)=>{
    const provided=Buffer.from(req.headers.authorization??''), expected=Buffer.from(`Bearer ${token}`);
    if(req.headers.origin || !/^127\.0\.0\.1:\d+$/.test(req.headers.host??'') || provided.length!==expected.length || !timingSafeEqual(provided,expected)) return send(res,403,{error:'Forbidden'});
    try {
      const path=new URL(req.url??'/','http://127.0.0.1').pathname;
      if(req.method==='GET' && path==='/health') return send(res,200,{ok:true,protocol:1,platform:'AgentDock',version:'0.5.0'});
      const owner=req.headers['x-ail-owner'];
      if(typeof owner!=='string'|| !/^[a-f0-9]{64}$/.test(owner)) throw new Error('Invalid client identity');
      if(req.method==='POST' && path==='/show') { hooks.show(); return send(res,200,{ok:true}); }
      if(req.method==='POST' && path==='/modules/ensure') {
        const data=await body(req) as {id?:unknown};
        if(typeof data?.id!=='string') throw new Error('Invalid module');
        await hooks.ensureModule(data.id); return send(res,200,{ok:true});
      }
      await hooks.route(req,res,owner);
    } catch(e) { send(res,400,{error:e instanceof Error?e.message:'Invalid request'}); }
  });
  await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const address=server.address(); if(!address||typeof address==='string') throw new Error('Local listener failed');
  const endpoint:Endpoint={port:address.port,token,pid:process.pid}; atomicJson(join(directory,'endpoint.json'),endpoint);
  return {endpoint,close:()=>new Promise<void>(resolve=>{server.close(()=>resolve());server.closeAllConnections();})};
}
