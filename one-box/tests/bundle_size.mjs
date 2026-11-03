// BSC transaction-size boundary for all local PQ profiles; build Alto first.
import assert from 'node:assert/strict';
import {Mempool} from '../.runtime/build/alto/src/esm/mempool/mempool.js';
import {encodeHandleOpsCalldata,calculateAA95GasFloor} from '../.runtime/build/alto/src/esm/executor/utils.js';
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
for(const version of [null,'V2','V3','V4','V5','V6']){
  for(const v of ['V2','V3','V4','V5','V6'])delete process.env[`ALTO_LOCAL_PRECOMPILE_${v}`];
  if(version)process.env[`ALTO_LOCAL_PRECOMPILE_${version}`]='1';
  for(const [signatureBytes,count,expected] of [[17088,8,[7,1]],[7856,16,[15,1]]]){
    const ops=Array.from({length:count},(_,i)=>({...operations[i%8],userOpHash:`hash-${i}`,
      userOp:{...operations[i%8].userOp,signature:'0x'+'11'.repeat(signatureBytes)}}));
    const queue=[...ops],processed=[];
    const mock={store:{peekOutstanding:async()=>queue[0],popOutstanding:async()=>queue.shift(),
      addOutstanding:async({userOpInfo})=>queue.push(userOpInfo),addProcessing:({userOpInfo})=>processed.push(userOpInfo)},
      getKnownEntities:async()=>({}),shouldSkip:async args=>({...args,skip:false}),
      reputationManager:{decreaseUserOperationCount:()=>{}},
    };
    const bundles=await Mempool.prototype.process.call(mock,{entryPoint,maxGasLimit:50000000n,minOpsPerBundle:1});
    assert.deepEqual(bundles.map(x=>x.userOps.length),version?expected:[count]);
    assert.equal(new Set(processed.map(x=>x.userOpHash)).size,count);
    assert.equal(queue.length,0);
    if(version)for(const b of bundles)assert(size(b.userOps)+1024<=131072);
  }
}
for(const v of ['V2','V3','V4','V5','V6'])delete process.env[`ALTO_LOCAL_PRECOMPILE_${v}`];
console.log('PASS: PQ bundles split at the BSC size boundary; no loss/duplication; non-PQ mode unchanged');

assert.equal(calculateAA95GasFloor([],entryPoint),0n);
// Intrinsic calldata gas is per transaction, not per operation.
for(const n of [1,4,8]){
  const ops=operations.slice(0,n);
  const data=Buffer.from(encodeHandleOpsCalldata({userOps:ops,beneficiary:entryPoint}).slice(2),'hex');
  const intrinsic=BigInt([...data].reduce((s,b)=>s+(b===0?4:16),0));
  const perOp=(40000n*64n/63n)+330000n+30000n;
  assert.equal(calculateAA95GasFloor(ops,entryPoint),BigInt(n)*perOp+intrinsic);
  const v6=ops.map(op=>{const userOp={...op.userOp,initCode:'0x',paymasterAndData:'0x'};
    delete userOp.factory;delete userOp.paymaster;return {...op,userOp};});
  const data6=Buffer.from(encodeHandleOpsCalldata({userOps:v6,beneficiary:entryPoint}).slice(2),'hex');
  const intrinsic6=BigInt([...data6].reduce((s,b)=>s+(b===0?4:16),0));
  assert.equal(calculateAA95GasFloor(v6,entryPoint),BigInt(n)*(30000n+330000n*2n+5000n+30000n)+intrinsic6);
}
console.log('PASS: bundle calldata gas is counted once');

// ML account creation can fit in bytes while exceeding the per-transaction gas cap.
for (const enabled of [false,true]) {
  if(enabled)process.env.ALTO_LOCAL_PRECOMPILE_V4='1';
  else delete process.env.ALTO_LOCAL_PRECOMPILE_V4;
  const ops=Array.from({length:8},(_,i)=>({...operations[i],userOp:{...operations[i].userOp,
    signature:'0x'+'11'.repeat(2420),verificationGasLimit:2400000n}}));
  const queue=[...ops],processed=[];
  const mock={store:{peekOutstanding:async()=>queue[0],popOutstanding:async()=>queue.shift(),
    addOutstanding:async({userOpInfo})=>queue.push(userOpInfo),addProcessing:({userOpInfo})=>processed.push(userOpInfo)},
    getKnownEntities:async()=>({}),shouldSkip:async args=>({...args,skip:false}),
    reputationManager:{decreaseUserOperationCount:()=>{}}};
  const bundles=await Mempool.prototype.process.call(mock,{entryPoint,maxGasLimit:50000000n,minOpsPerBundle:1});
  assert.deepEqual(bundles.map(b=>b.userOps.length),enabled?[6,2]:[8]);
  assert.equal(new Set(processed.map(op=>op.userOpHash)).size,8);
  if(enabled)for(const b of bundles)assert(calculateAA95GasFloor(b.userOps,entryPoint)+10000n<=16777216n);
}
delete process.env.ALTO_LOCAL_PRECOMPILE_V4;
console.log('PASS: PQ creation bundles respect the outer gas floor and preserve every operation');
