// Assert the actual STATICCALL charge of selected native verifiers.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../../../erc4337-wallet/package.json',import.meta.url));
const {encodeFunctionData}=await import(require.resolve('viem'));
import {context,deployment,artifact,write} from '../../../erc4337-wallet/common/wallet.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
export async function run(version){
const name=process.argv[2]||`devnet-v${version}`;const checks=[];
const targets=[
 ['slh-dsa-v2',32,path.join(root,'one-box/precompile/v2/testdata/slh_v2.json'),'0101',60000n],
 ['falcon-v3',896,path.join(root,`one-box/.runtime/build/v${version}/falcon-public.json`),'0102',10000n],
];
if(version===4)targets.push(['mldsa-v4',1312,path.join(root,'one-box/.runtime/build/v4/mldsa-public.json'),'0103',10000n]);
for(const [profile,size,source,address,gas] of targets){
 const ctx=await context(name,profile);const d=await deployment(ctx);
 const parsed=JSON.parse(fs.readFileSync(source,'utf8'));const input=Array.isArray(parsed)?parsed[0].input:parsed.input;
 const contract=ctx.profile.contracts(ctx)[0][1];
 const data=encodeFunctionData({abi:artifact(contract).abi,functionName:'verify',args:['0x'+input.slice(0,size*2),'0x'+input.slice(size*2,size*2+64),'0x'+input.slice(size*2+64)]});
 const block=await ctx.client.getBlock();
 const trace=await ctx.client.request({method:'debug_traceCall',params:[{to:d.verifier,data,gas:'0x989680'},'0x'+block.number.toString(16),{tracer:'callTracer'}]});
 const frames=[];function walk(t){if(t.to?.toLowerCase()==='0x'+'0'.repeat(36)+address)frames.push(t);for(const c of t.calls||[])walk(c)}walk(trace);
 assert.equal(frames.length,1);assert.equal(BigInt(frames[0].gasUsed),gas);assert.equal(frames[0].output,'0x'+'00'.repeat(31)+'01');
 checks.push({profile,blockNumber:block.number,blockHash:block.hash,precompile:frames[0].to,gasUsed:gas,input:frames[0].input,result:frames[0].output});
 write(path.join(ctx.dir,'native-call-trace.json'),trace);
}
const ctx=await context(name,version===4?'mldsa-v4':'falcon-v3');write(path.join(ctx.env,'native-gas-check.json'),{checks});console.log('PASS: all selected precompile STATICCALL charges match constants');
}
