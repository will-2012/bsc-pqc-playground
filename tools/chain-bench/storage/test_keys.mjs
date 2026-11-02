import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {openKeys} from './keys.mjs';
import {slh_dsa_sha2_128s as slh} from '../../../erc4337-wallet/node_modules/@noble/post-quantum/slh-dsa.js';
import {ml_dsa44 as ml} from '../../../erc4337-wallet/node_modules/@noble/post-quantum/ml-dsa.js';
const root=path.resolve(new URL('../../../',import.meta.url).pathname),dir=path.join(root,'one-box/.runtime/storage-bench');
const seed=path.join(dir,'test.seed');
for(const [algorithm,impl] of [['ml',ml],['slh',slh]]) {
 const keys=openKeys(seed,algorithm,path.join(dir,'keygen'));
 try {
  const pk=await keys.batch(0,16);assert.equal(new Set(pk).size,16);
  for(let i=0;i<2;i++)assert.equal(pk[i],'0x'+Buffer.from(impl.keygen(keys.seed(i)).publicKey).toString('hex'));
  assert.deepEqual(await keys.batch(0,2),pk.slice(0,2));
  console.log('PASS independent key generation and deterministic resume: '+algorithm);
 }finally{keys.close();}
}
fs.unlinkSync(seed);
