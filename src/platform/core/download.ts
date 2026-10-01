import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
export async function downloadModule(raw:string,directory:string,fetcher:typeof fetch=fetch):Promise<string>{
 let url=new URL(raw);if(url.protocol!=='https:'||url.username||url.password)throw new Error('請使用 HTTPS 模組檔案網址，不是 GitHub 專案首頁。');
 let response:Response|undefined;
 for(let redirects=0;redirects<6;redirects++){
  response=await fetcher(url,{redirect:'manual',signal:AbortSignal.timeout(60000)});
  if([301,302,303,307,308].includes(response.status)){const location=response.headers.get('location');await response.body?.cancel();if(!location)throw new Error('下載重新導向無效。');url=new URL(location,url);if(url.protocol!=='https:'||url.username||url.password)throw new Error('拒絕不安全的下載重新導向。');continue;}break;
 }
 if(!response?.ok||!response.body)throw new Error(`下載失敗：${response?.status??'無回應'}`);
 const reader=response.body.getReader(),parts:Uint8Array[]=[];let size=0,timedOut=false;const timeout=setTimeout(()=>{timedOut=true;void reader.cancel();},60000);
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>25*1024*1024){await reader.cancel();throw new Error('模組包超過 25 MB。');}parts.push(value);}}finally{clearTimeout(timeout);}
 if(timedOut)throw new Error('下載逾時，請重試。');if(!size)throw new Error('下載檔案是空的。');mkdirSync(directory,{recursive:true});const path=join(directory,`${randomUUID()}.admod`);writeFileSync(path,Buffer.concat(parts));return path;
}
