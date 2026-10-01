import type { BrowserWindow, Tray } from 'electron';
import type { IncomingMessage, ServerResponse } from 'node:http';
export interface ModuleContext {
  directory:string; window:BrowserWindow; tray:Tray;
  show:(id?:string)=>void; handle:(name:string,fn:(...args:any[])=>unknown)=>void; changed:()=>void;
}
export interface ModuleRuntime {
  route:(req:IncomingMessage,res:ServerResponse,owner:string)=>void|Promise<void>;
  pending:()=>boolean; received:()=>Array<{source:string;at:string}>; dispose:()=>void;
}
