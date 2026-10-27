import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const binary=fileURLToPath(new URL('../../one-box/.runtime/build/v3/pq-wallet',import.meta.url));
// Secret material is sent over stdin, never command-line arguments.
export function native(action,fields={}){
  const request=Object.fromEntries(Object.entries({action,...fields}).map(([k,v])=>[k,typeof v==='string'&&v.startsWith('0x')?v.slice(2):v]));
  const result=JSON.parse(execFileSync(binary,[],{input:JSON.stringify(request),encoding:'utf8',maxBuffer:1024*1024}));
  for(const k of ['publicKey','secretKey','signature'])if(result[k])result[k]='0x'+result[k];
  return result;
}
