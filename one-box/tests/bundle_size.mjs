// Regression for v6's BSC transaction-size boundary; build Alto first.
import assert from 'node:assert/strict';
import {Mempool} from '../.runtime/build/alto/src/esm/mempool/mempool.js';
import {encodeHandleOpsCalldata} from '../.runtime/build/alto/src/esm/executor/utils.js';
const entryPoint='0x'+'11'.repeat(20);
const operations=Array.from({length:8},(_,i)=>({userOpHash:`hash-${i}`,userOp:{
  sender:'0x'+(i+1).toString(16).padStart(40,'0'),nonce:0n,
  factory:undefined,paymaster:undefined,callData:'0x'+'11'.repeat(132),
  callGasLimit:30000n,verificationGasLimit:330000n,preVerificationGas:660000n,
  maxFeePerGas:1000000000n,maxPriorityFeePerGas:1000000000n,
  signature:'0x'+'11'.repeat(17088),
}}));
const size=ops=>(encodeHandleOpsCalldata({userOps:ops,beneficiary:entryPoint}).length-2)/2;
assert(size(operations.slice(0,7))<130048);
assert(size(operations)>131072);
for(const enabled of [false,true]){
  if(enabled)process.env.ALTO_LOCAL_PRECOMPILE_V6='1';else delete process.env.ALTO_LOCAL_PRECOMPILE_V6;
  const queue=[...operations],processed=[];
  const mock={store:{peekOutstanding:async()=>queue[0],popOutstanding:async()=>queue.shift(),
    addOutstanding:async({userOpInfo})=>queue.push(userOpInfo),addProcessing:({userOpInfo})=>processed.push(userOpInfo)},
    getKnownEntities:async()=>({}),shouldSkip:async args=>({...args,skip:false}),
    reputationManager:{decreaseUserOperationCount:()=>{}},
  };
  const bundles=await Mempool.prototype.process.call(mock,{entryPoint,maxGasLimit:20000000n,minOpsPerBundle:1});
  assert.deepEqual(bundles.map(x=>x.userOps.length),enabled?[7,1]:[8]);
  assert.equal(new Set(processed.map(x=>x.userOpHash)).size,8);
  assert.equal(queue.length,0);
  if(enabled)for(const b of bundles)assert(size(b.userOps)+1024<=131072);
}
console.log('PASS: v6 splits 8 large operations into 7+1, no loss/duplication; old mode unchanged');
