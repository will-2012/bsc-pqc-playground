// Shared finite-load experiment; count outer transactions once.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {context,deployment,createAccount,loadIdentity,prepare,submit,write} from '../../../erc4337-wallet/common/wallet.mjs';
export async function run({defaultName,profiles,outputName}){
const [name=defaultName,countText='8',wavesText='5']=process.argv.slice(2);
const count=Number(countText),waves=Number(wavesText);
assert(Number.isInteger(count)&&count>=2&&count<=32&&Number.isInteger(waves)&&waves>=1&&waves<=20);
const output=[];
for(const profile of profiles){
 const ctx=await context(name,profile);await deployment(ctx);
 const ids=fs.existsSync(path.join(ctx.dir,'accounts'))?fs.readdirSync(path.join(ctx.dir,'accounts')).filter(x=>x.endsWith('.json')).map(x=>x.slice(0,-5)).slice(0,count):[];
 while(ids.length<count){const created=await createAccount(ctx);ids.push(created.id);}
 const identities=ids.map(id=>loadIdentity(ctx,id));
 const recipient=ctx.manifest.accounts[1].address,value=1000000000000000n;
 const before=await ctx.client.getBalance({address:recipient});
 const rows=[];const rounds=[];const start=performance.now();
 for(let wave=0;wave<waves;wave++){
  const preparing=performance.now();
  const operations=await Promise.all(identities.map(identity=>prepare(ctx,identity,{to:recipient,value})));
  const prepared=performance.now();
  const results=await Promise.all(operations.map(async op=>{const sent=performance.now();const r=await submit(ctx,op);return {...r,receiptMs:performance.now()-sent};}));
  rows.push(...results);rounds.push({wave:wave+1,prepareMs:prepared-preparing,submitWindowMs:performance.now()-prepared});
  console.log(`${profile} wave ${wave+1}/${waves}: ${results.length} successful`);
 }
 const elapsedMs=performance.now()-start;
 assert.equal(await ctx.client.getBalance({address:recipient})-before,value*BigInt(count*waves));
 const hashes=[...new Set(rows.map(r=>r.transactionHash))];const receipts=await Promise.all(hashes.map(hash=>ctx.client.getTransactionReceipt({hash})));
 const blocks=[...new Set(receipts.map(r=>r.blockNumber.toString()))];
 const blockStats=await Promise.all(blocks.map(async n=>{const b=await ctx.client.getBlock({blockNumber:BigInt(n)});return {number:n,hash:b.hash,timestamp:b.timestamp,gasUsed:b.gasUsed,gasLimit:b.gasLimit,gasUtilization:Number(b.gasUsed)/Number(b.gasLimit)};}));
 const times=rows.map(r=>r.receiptMs).sort((a,b)=>a-b);
 output.push({profile,accounts:count,waves,operations:rows.length,elapsedMs,endToEndOpsPerSecond:rows.length/(elapsedMs/1000),
   receiptMedianMs:times[Math.floor(times.length*.5)],receiptP95Ms:times[Math.ceil(times.length*.95)-1],
   uniqueOuterTransactions:hashes.length,includedBlocks:blocks.length,maxOpsPerOuterTransaction:Math.max(...hashes.map(h=>rows.filter(r=>r.transactionHash===h).length)),
   totalOuterGas:receipts.reduce((s,r)=>s+r.gasUsed,0n),rounds,blockStats,rows});
}
const ctx=await context(name,profiles.at(-1));
const result={checkedAt:new Date().toISOString(),genesisHash:ctx.manifest.genesis_hash,environment:name,
 mode:`same chain/bundler, ${profiles.join(' then ')}; each wave prepares then submits distinct senders concurrently; includes wallet/simulation/inclusion; finite local load, not a capacity limit`,
 bundler:Object.fromEntries(Object.entries(JSON.parse(fs.readFileSync(path.join(ctx.env,'bundler/config.json')))).filter(([key])=>['safe-mode','max-gas-per-bundle','bundle-mode'].includes(key))),results:output};
const file=path.join(ctx.env,'wallet',outputName);write(file,result);console.log('PASS: '+file);

}
