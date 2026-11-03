import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from '../.runtime/build/alto/node_modules/typescript/lib/typescript.js';
const source=fs.readFileSync(new URL('../.runtime/build/alto/src/rpc/validation/BundlerCollectorTracerV07.ts',import.meta.url),'utf8');
const optimized=/const stackTop3 = opcode\.startsWith\("EXT"\) && log\.stack\.length\(\) > 0\s*\? \[log\.stack\.peek\(0\)\] : \[\]/;
assert(optimized.test(source));
const original=source.replace(optimized,`const stackSize=log.stack.length();const stackTop3=[];for(let i=0;i<3&&i<stackSize;i++)stackTop3.push(log.stack.peek(i))`);
function run(text,ops){
  const context={exports:{},toHex:b=>'0x'+Buffer.from(b).toString('hex'),
    toAddress:s=>Buffer.from(s.padStart(40,'0'),'hex'),toWord:s=>Buffer.from(s.padStart(64,'0'),'hex')};
  vm.runInNewContext(ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,context);
  const tracer=context.exports.bundlerCollectorTracer();let peeks=0;
  const db={getCode:()=>Buffer.alloc(16),getState:()=>Buffer.alloc(32)};
  for(const [opcode,depth=4,gas=10000] of [['CALL',3],...ops]){
    tracer.step({getDepth:()=>depth,op:{toString:()=>opcode},getGas:()=>gas,getCost:()=>3,
      stack:{length:()=>4,peek:i=>{peeks++;return [32n,64n,1n,0n][i];}},
      memory:{slice:(a,b)=>Buffer.alloc(Math.max(0,b-a))},contract:{getAddress:()=>Buffer.alloc(20,1)}},db);
  }
  return {result:JSON.parse(JSON.stringify(tracer.result({},db))),peeks};
}
for(const ops of [
  ['PUSH1','EXTCODESIZE','ISZERO','PUSH1','EXTCODEHASH','POP','GAS','CALL'],
  ['SLOAD','SSTORE','SLOAD','TLOAD','TSTORE','KECCAK256','LOG2','RETURN'],
  ['EXTCODECOPY','POP','PUSH1','EXTCODESIZE','ISZERO','REVERT'],
  Array(300).fill('ADD'),
].map(ops=>ops.map(op=>[op]))){
  const before=run(original,ops),after=run(source,ops);
  assert.deepEqual(after.result,before.result);assert(after.peeks<before.peeks);
}
assert.deepEqual(run(source,[['SSTORE',4,2000]]).result,run(original,[['SSTORE',4,2000]]).result);
console.log('PASS: tracer output unchanged for code access, storage, gas, logs and returns; fewer stack reads');

// Exercise the actual logging statement: disabled debug must not serialize a trace.
const validator=fs.readFileSync(new URL('../.runtime/build/alto/src/rpc/validation/SafeValidator.ts',import.meta.url),'utf8');
const statement=validator.match(/        if \(this\.logger\.isLevelEnabled\("debug"\)\) \{[\s\S]*?\n        \}/)?.[0];
assert(statement,'trace logging must be guarded');
const logTrace=new Function('tracerResult',statement);
let serializations=0;
const trace={toJSON(){serializations++;return {gas:1n};}};
logTrace.call({logger:{isLevelEnabled:()=>false,debug(){throw new Error('unexpected log');}}},trace);
assert.equal(serializations,0);
let message;
logTrace.call({logger:{isLevelEnabled:()=>true,debug(value){message=value;}}},trace);
assert.equal(serializations,1);
assert.equal(message,'tracerResult: {"gas":"1"}');
console.log('PASS: trace serialization runs only when debug logging is enabled');
