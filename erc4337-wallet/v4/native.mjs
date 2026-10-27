import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const original=fileURLToPath(new URL('../../one-box/.runtime/build/v4/pq-wallet',import.meta.url));
const fallback=fileURLToPath(new URL('../../one-box/.runtime/build/v5/pq-wallet',import.meta.url));
const binary=fs.existsSync(original)?original:fallback;
// Secret material is sent over stdin, never command-line arguments.
export function native(action,fields={}){
  const request=Object.fromEntries(Object.entries({action,...fields,...(binary===fallback?{algorithm:'mldsa44'}:{})}).map(([k,v])=>[k,typeof v==='string'&&v.startsWith('0x')?v.slice(2):v]));
  const result=JSON.parse(execFileSync(binary,[],{input:JSON.stringify(request),encoding:'utf8',maxBuffer:1024*1024}));
  for(const k of ['publicKey','secretKey','signature'])if(result[k])result[k]='0x'+result[k];
  return result;
}
