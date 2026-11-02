// RPC-only load generator. Private runtime config is portable across hosts.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {openKeys} from './keys.mjs';
const require=createRequire(new URL('../../../erc4337-wallet/package.json',import.meta.url));
const {createPublicClient,createWalletClient,defineChain,http,encodeFunctionData,encodeAbiParameters,keccak256,decodeEventLog,getCreate2Address,concatHex}=require('viem');
const {privateKeyToAccount}=require('viem/accounts');
const {Wallet}=require('ethers');
process.umask(0o077);
const [configFile,targetText]=process.argv.slice(2);
const cfg=JSON.parse(fs.readFileSync(configFile));
const target=Number(targetText);assert(Number.isSafeInteger(target)&&target>0);
const dir=path.dirname(configFile), read=p=>JSON.parse(fs.readFileSync(p));
const write=(p,v)=>{fs.writeFileSync(p+'.tmp',JSON.stringify(v,(_,x)=>typeof x==='bigint'?x.toString():x,2)+'\n');fs.renameSync(p+'.tmp',p);};
const progressFile=path.join(dir,'progress.json'), pendingFile=path.join(dir,'pending.json');
const lockFile=path.join(dir,'load.lock');
if(fs.existsSync(lockFile)) {
 const pid=Number(fs.readFileSync(lockFile,'utf8'));
 assert(Number.isSafeInteger(pid)&&pid>0,'Invalid load lock');
 try {process.kill(pid,0);throw Error('Another load generator holds this run');}
 catch(e){if(e.code!=='ESRCH')throw e;fs.unlinkSync(lockFile);}
}
fs.writeFileSync(lockFile,String(process.pid),{flag:'wx',mode:0o600});
process.on('exit',()=>{if(fs.existsSync(lockFile)&&fs.readFileSync(lockFile,'utf8')===String(process.pid))fs.unlinkSync(lockFile);});

const chain=defineChain({id:cfg.chainId,name:'storage experiment',nativeCurrency:{name:'BNB',symbol:'BNB',decimals:18},rpcUrls:{default:{http:[cfg.rpc]}}});
const client=createPublicClient({chain,transport:http(cfg.rpc,{timeout:180000,retryCount:0}),pollingInterval:200});
assert.equal(await client.getChainId(),cfg.chainId);assert.equal((await client.getBlock({blockNumber:0n})).hash,cfg.genesisHash);
const signer=await Wallet.fromEncryptedJson(fs.readFileSync(cfg.keystore,'utf8'),fs.readFileSync(cfg.passwordFile,'utf8').trim());
const account=privateKeyToAccount(signer.privateKey), wallet=createWalletClient({chain,account,transport:http(cfg.rpc,{timeout:180000,retryCount:0})});
const helper=read(cfg.batchArtifact), factoryArtifact=cfg.accountArtifact?read(cfg.accountArtifact):null;
let progress=fs.existsSync(progressFile)?read(progressFile):{schema:1,algorithm:cfg.algorithm,genesisHash:cfg.genesisHash,count:0,totalGas:'0',totalCalldata:0,transactions:0,loadMs:0};
assert.equal(progress.genesisHash,cfg.genesisHash);assert.equal(progress.algorithm,cfg.algorithm);assert(progress.count<=target);
const keys=openKeys(path.join(dir,'master.seed'),cfg.algorithm==='slh-storage'?'slh':cfg.algorithm,cfg.keygenBinary,cfg.keyWorkers);
let stopping=false;for(const s of ['SIGTERM','SIGINT'])process.on(s,()=>{stopping=true;});
const enoughDisk=()=>{const s=fs.statfsSync(dir);if(s.bavail*s.bsize<cfg.minFreeBytes)throw Error('Disk reserve reached; pending work preserved');};
function addresses(values,start) {
 if(cfg.algorithm==='ecdsa')return values;
 return values.map((pk,j)=>getCreate2Address({from:cfg.factory,salt:'0x'+BigInt(start+j).toString(16).padStart(64,'0'),
   bytecodeHash:keccak256(concatHex([factoryArtifact.bytecode.object,encodeAbiParameters([{type:'address'},{type:cfg.algorithm==='ml'?'bytes':'bytes32'},{type:'address'}],[cfg.entryPoint,pk,cfg.verifier])]))}));
}
async function complete(jobs) {
 for(const job of jobs) {
  let receipt;
  try {receipt=await client.getTransactionReceipt({hash:job.hash});}catch(e){if(e.name!=='TransactionReceiptNotFoundError')throw e;}
  if(!receipt) {
    try{await wallet.sendRawTransaction({serializedTransaction:job.raw});}
    catch(e){if(!/already known|known transaction|nonce too low/i.test(e.message))throw e;}
    receipt=await client.waitForTransactionReceipt({hash:job.hash,timeout:240000});
  }
  assert.equal(receipt.status,'success',`Batch reverted: ${job.hash}`);
  const events=receipt.logs.filter(l=>l.address.toLowerCase()===cfg.batch.toLowerCase()).map(l=>decodeEventLog({abi:helper.abi,data:l.data,topics:l.topics}));
  assert.equal(events.length,1);const event=events[0];assert.equal(event.eventName,'Created');
  assert.equal(Number(event.args.start),job.start);assert.equal(Number(event.args.count),job.count);assert.equal(event.args.addressesHash,job.addressesHash);
  // Every batch verifies a real account; receipt hash commits to all addresses.
  const addr=job.sampleAddress;
  assert.equal(await client.getBalance({address:addr,blockNumber:receipt.blockNumber}),1n);
  const code=await client.getCode({address:addr,blockNumber:receipt.blockNumber});
  if(cfg.algorithm==='ecdsa') assert(!code || code==='0x');else assert(code&&code!=='0x');
  if(job.start>=progress.count) {
    assert.equal(job.start,progress.count);
    progress.count+=job.count;progress.transactions++;progress.totalGas=(BigInt(progress.totalGas)+receipt.gasUsed).toString();progress.totalCalldata+=job.calldataBytes;
    progress.lastBlock=Number(receipt.blockNumber);progress.lastHash=receipt.blockHash;
    // One bounded journal row per batch; no keys or full transaction payloads in public reports.
    fs.appendFileSync(path.join(dir,'batches.jsonl'),JSON.stringify({start:job.start,count:job.count,hash:job.hash,block:progress.lastBlock,gas:receipt.gasUsed.toString(),addressesHash:job.addressesHash})+'\n');
    write(progressFile,progress);
  }
 }
 fs.unlinkSync(pendingFile);
}
try {
 enoughDisk();
 if(fs.existsSync(pendingFile))await complete(read(pendingFile));
 let nonce=await client.getTransactionCount({address:account.address,blockTag:'pending'});
 let logAt=Date.now();
 while(progress.count<target&&!stopping) {
  enoughDisk();const waveStart=performance.now();const jobs=[];let next=progress.count;
  while(next<target&&jobs.length<cfg.inflight) {
   const count=Math.min(cfg.batchSize,target-next),start=next;const values=await keys.batch(start,count);const addrs=addresses(values,start);
   const args=cfg.algorithm==='ecdsa'?[BigInt(start),values]:[cfg.factory,BigInt(start),values];
   const data=encodeFunctionData({abi:helper.abi,functionName:cfg.algorithm==='ecdsa'?'eoa':cfg.algorithm==='slh-storage'?'slh':cfg.algorithm,args});
   const gas=BigInt(cfg.gasPerAccount)*BigInt(count)+200000n;
   const raw=await wallet.signTransaction({to:cfg.batch,data,value:BigInt(count),nonce:nonce++,gas,gasPrice:BigInt(cfg.gasPrice),type:'legacy'});
   jobs.push({start,count,raw,hash:keccak256(raw),addressesHash:keccak256(encodeAbiParameters([{type:'address[]'}],[addrs])),sampleAddress:addrs[0],calldataBytes:(data.length-2)/2});next+=count;
  }
  write(pendingFile,jobs);
  // Broadcast the entire bounded wave before waiting for receipts.
  for(const job of jobs) {try{await wallet.sendRawTransaction({serializedTransaction:job.raw});}catch(e){if(!/already known|known transaction/i.test(e.message))throw e;}}
  await complete(jobs);progress.loadMs+=performance.now()-waveStart;write(progressFile,progress);
  if(Date.now()-logAt>10000||progress.count===target){console.log(`${cfg.algorithm}: ${progress.count}/${target}; ${(progress.count/(progress.loadMs/1000)).toFixed(1)} accounts/s; ${(fs.statfsSync(dir).bavail*fs.statfsSync(dir).bsize/2**30).toFixed(1)} GiB free`);logAt=Date.now();}
 }
 if(stopping)throw Error('Stopped after completing the current wave');
 console.log('PASS target '+target);
}finally{keys.close();}
