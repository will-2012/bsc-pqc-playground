import assert from 'node:assert/strict';
import {Mempool,localValidationConcurrency} from '../.runtime/build/alto/src/esm/mempool/mempool.js';
const address=i=>'0x'+i.toString(16).padStart(40,'0');
const entryPoint=address(1000),paymaster=address(2000);
const operations=Array.from({length:12},(_,i)=>({userOpHash:`hash-${i}`,userOp:{
 sender:address(i+1),nonce:0n,factory:undefined,paymaster:undefined,callData:'0x',
 callGasLimit:30000n,verificationGasLimit:100000n,preVerificationGas:50000n,
 maxFeePerGas:1000000000n,maxPriorityFeePerGas:1000000000n,signature:'0x'+'11'.repeat(2420)
}}));
async function run(width,mode='valid') {
 process.env.ALTO_LOCAL_PRECOMPILE_V4='1';process.env.ALTO_LOCAL_VALIDATION_CONCURRENCY=String(width);
 const ops=structuredClone(operations),queue=[...ops],processed=[],checked=[];let active=0,peak=0;
 if(mode==='dependent'){ops[1].userOp.sender=ops[0].userOp.sender;ops[1].userOp.nonce=1n;}
 if(mode==='paymaster')for(const op of ops)op.userOp.paymaster=paymaster;
 if(mode==='factory')for(const op of ops)op.userOp.factory=address(3000);
 if(mode==='nonce-key')for(const op of ops)op.userOp.nonce=1n<<64n;
 const logger={trace(){},debug(){},error(){}};
 const store={peekOutstanding:async()=>queue[0],popOutstanding:async()=>queue.shift(),dumpOutstanding:async()=>mode==='reordered'?[...queue].reverse():[...queue],
 addOutstanding:async({userOpInfo})=>queue.push(userOpInfo),removeOutstanding:({userOpHash})=>{const i=queue.findIndex(o=>o.userOpHash===userOpHash);if(i>=0)queue.splice(i,1);},
 addProcessing:({userOpInfo})=>processed.push(userOpInfo.userOpHash),getQueuedOutstandingUserOps:async()=>[]};
 const mempool=new Mempool({store,config:{safeMode:true,enforceUniqueSendersPerBundle:true,getLogger:()=>logger,publicClient:{readContract:async()=>3n}},
 monitor:{},eventManager:{},reputationManager:{getStatus:()=>mode==='banned'?2n:0n,decreaseUserOperationCount(){},decreaseUserOperationSeenStatus(){}},
 validator:{async validateUserOperation({userOperation}){
   active++;peak=Math.max(peak,active);checked.push(userOperation.sender);
   try{await new Promise(r=>setTimeout(r,Number(BigInt(userOperation.sender)%4n)+1));
    if(mode==='invalid'&&userOperation.sender===address(4))throw new Error('invalid signature');
    return {returnInfo:{prefund:2n},storageMap:mode==='storage'&&userOperation.sender===address(4)?{[address(5)]:{}}:{}};
   }finally{active--;}
 }}});
 mempool.getKnownEntities=async()=>({sender:new Set(ops.map(o=>o.userOp.sender)),paymasters:new Set(),factories:new Set()});
 const bundles=await mempool.process({entryPoint,maxGasLimit:390000n,minOpsPerBundle:1});
 assert.equal(active,0);assert(peak<=width,`concurrency ${peak} exceeds ${width}`);
 assert.equal(new Set(processed).size,processed.length);
 assert.deepEqual(bundles.flatMap(b=>b.userOps.map(o=>o.userOpHash)),processed);
 if(['paymaster','factory','nonce-key'].includes(mode))assert.equal(peak,1);
 return {bundles:bundles.map(b=>b.userOps.map(o=>o.userOpHash)),remaining:queue.map(o=>o.userOpHash),peak};
}
for(const mode of ['valid','invalid','storage','dependent','paymaster','factory','nonce-key','banned','reordered']) {
 const serial=await run(1,mode);
 for(const width of [2,4,8]){
  const parallel=await run(width,mode);
  assert.deepEqual(parallel.bundles,serial.bundles,mode);
  assert.deepEqual(parallel.remaining,serial.remaining,mode);
  if(mode==='valid')assert(parallel.peak>1);
 }
}
for(const value of ['0','3','16','NaN','']){process.env.ALTO_LOCAL_VALIDATION_CONCURRENCY=value;assert.throws(localValidationConcurrency);}
delete process.env.ALTO_LOCAL_VALIDATION_CONCURRENCY;delete process.env.ALTO_LOCAL_PRECOMPILE_V4;
assert.equal(localValidationConcurrency(),1);
console.log('PASS: bounded validation preserves bundle order, skips, gas splits and rejected operations; default stays serial');
