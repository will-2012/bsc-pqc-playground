import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {context,deployment,createAccount,transfer,write} from '../../../erc4337-wallet/common/wallet.mjs';
const [name,algorithm,dirArg]=process.argv.slice(2);assert(['ecdsa','ml','slh','slh-storage'].includes(algorithm));
const dir=path.resolve(dirArg), root=fileURLToPath(new URL('../../../',import.meta.url));
fs.mkdirSync(dir,{recursive:true,mode:0o700});
const profiles={ecdsa:'slh-dsa-v2',ml:'mldsa-v4',slh:'slh-dsa-v2','slh-storage':'slh-dsa-v7'};
const accountNames={ml:'MLDSAAccount',slh:'PQAccount','slh-storage':'PQStorageAccount'};
const ctx=await context(name,profiles[algorithm]);
if(algorithm!=='ecdsa')await deployment(ctx);
const batchArtifact=path.join(root,'one-box/.runtime/storage-bench/contracts/StorageBatch.sol/StorageBatch.json');
const art=JSON.parse(fs.readFileSync(batchArtifact));
const deployed=await ctx.client.waitForTransactionReceipt({hash:await ctx.wallet.deployContract({abi:art.abi,bytecode:art.bytecode.object,...ctx.fee})});
assert.equal(deployed.status,'success');
const sample=[];
if(algorithm!=='ecdsa') {
 for(let i=0;i<2;i++) {
  const created=await createAccount(ctx);
  const sent=await transfer(ctx,created.id,ctx.manifest.accounts[1].address,1000000000000000n);
  sample.push({created,transferred:sent});
 }
}
write(path.join(dir,'functional-samples.json'),{algorithm,samples:sample,note:'Outside the measured population; completed before the zero-account baseline.'});
const info=ctx.manifest.accounts[0], keydir=path.join(ctx.env,info.keystore);
const cfg={schema:1,algorithm,chainId:ctx.chain.id,genesisHash:ctx.manifest.genesis_hash,
 rpc:`http://127.0.0.1:${ctx.config.rpc_port}`,keystore:path.join(keydir,fs.readdirSync(keydir).find(f=>f.startsWith('UTC--'))),passwordFile:path.join(ctx.env,info.password_file),
 batch:deployed.contractAddress,batchArtifact,entryPoint:ctx.base.entryPoint,
 ...(algorithm!=='ecdsa'?{factory:ctx.deployment.factory,verifier:ctx.deployment.verifier,
 accountArtifact:path.join(root,`contract/out/${accountNames[algorithm]}.sol/${accountNames[algorithm]}.json`)}:{}),
 keygenBinary:path.join(root,'one-box/.runtime/storage-bench/keygen'),keyWorkers:4,
 gasPrice:ctx.config.gas_price_wei,gasPerAccount:algorithm==='ecdsa'?40000:algorithm==='ml'?1700000:700000,
 batchSize:algorithm==='ecdsa'?256:8,inflight:4,minFreeBytes:30*2**30,
 nodes:Array.from({length:3},(_,i)=>({id:`node${i}`,rpc:`http://127.0.0.1:${ctx.config.rpc_port+2*i}`})),
 policy:'Independent keys; real existing Factory contracts; exactly 1 wei per address; no UserOperation for population creation; no history pruning.'};
write(path.join(dir,'load.json'),cfg);
console.log('Ready: '+path.join(dir,'load.json'));
