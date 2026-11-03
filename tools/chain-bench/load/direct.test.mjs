import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {prepareDirect, directCalldata, applyDirectReceipt} from './direct.mjs';
const require=createRequire(new URL('../../../erc4337-wallet/package.json',import.meta.url));
const {encodeEventTopics,encodeAbiParameters,decodeFunctionData}=require('viem');
const {entryPoint07Abi}=require('viem/account-abstraction');
const address=n=>'0x'+n.toString(16).padStart(40,'0'), hash=n=>'0x'+n.toString(16).padStart(64,'0');
const gas={callGasLimit:'30000',verificationGasLimit:'300000',preVerificationGas:'150000',outerGasLimit:'750000'};
function fixture(success=true){
 const record={transactionHash:hash(1),hash:hash(2),entryPoint:address(1),signed:{sender:address(2),nonce:'3'}};
 const receipt={transactionHash:hash(1),status:'success',blockNumber:4n,blockHash:hash(4),gasUsed:100n,logs:[{
 address:address(1),topics:encodeEventTopics({abi:entryPoint07Abi,eventName:'UserOperationEvent',args:{userOpHash:hash(2),sender:address(2),paymaster:address(0)}}),
 data:encodeAbiParameters([{type:'uint256'},{type:'bool'},{type:'uint256'},{type:'uint256'}],[3n,success,10n,20n]),
 }]};return {record,receipt};
}
test('direct preparation signs the actual call and nonce without a Bundler request',async()=>{
 let signed;const smart={address:address(2),getNonce:async()=>3n,encodeCalls:async calls=>{assert.equal(calls[0].value,1n);return '0x1234';},signUserOperation:async op=>{signed=structuredClone(op);return '0xabcd';}};
 const op=await prepareDirect({config:{gas_price_wei:'1'}},smart,{to:address(3),value:1n},gas);
 assert.equal(signed.nonce,3n);assert.equal(signed.callData,'0x1234');assert.equal(op.signature,'0xabcd');
 const decoded=decodeFunctionData({abi:entryPoint07Abi,data:directCalldata(op,address(4))});
 assert.equal(decoded.functionName,'handleOps');assert.equal(decoded.args[0].length,1);assert.equal(decoded.args[0][0].signature,op.signature);
});
test('outer success alone is insufficient; exact successful UserOperation event is required',()=>{
 let {record,receipt}=fixture();applyDirectReceipt(record,receipt);assert.equal(record.state,'confirmed');assert.equal(record.gasUsed,20n);
 ({record,receipt}=fixture(false));applyDirectReceipt(record,receipt);assert.equal(record.state,'reverted');
 for(const change of [r=>r.logs=[],r=>r.logs.push(r.logs[0]),r=>r.logs[0].address=address(9),r=>r.transactionHash=hash(9)]){
  ({record,receipt}=fixture());change(receipt);assert.throws(()=>applyDirectReceipt(record,receipt));
 }
 ({record,receipt}=fixture());record.signed.nonce='4';assert.throws(()=>applyDirectReceipt(record,receipt));
 ({record,receipt}=fixture());receipt.status='reverted';applyDirectReceipt(record,receipt);assert.equal(record.state,'reverted');
});

test('direct mode is explicit, transfer-only and requires bounded gas parameters',async()=>{
 const {validate}=await import('./core.mjs');
 const base={action:'transfer',algorithm:'ml',dataset:'test',run:'test',accounts:2,count:2,workers:1,qps:1,startAt:0,fundWei:'1',valueWei:'1'};
 assert.equal(validate({...base}).submission,'bundler');
 assert.equal(validate({...base,submission:'direct',directGas:gas}).submission,'direct');
 for(const changes of [{action:'create'},{algorithm:'ecdsa'},{directGas:undefined},{directGas:{...gas,callGasLimit:30000}},{directGas:{...gas,outerGasLimit:'100'}},{directGas:{...gas,verificationGasLimit:'-1'}}])
  assert.throws(()=>validate({...base,submission:'direct',directGas:gas,...changes}));
});
