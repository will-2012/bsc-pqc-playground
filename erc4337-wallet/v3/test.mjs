import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {bytesToHex} from 'viem';
import {deployment,artifact} from '../common/wallet.mjs';
import {accountFlow} from '../common/test.mjs';
import {native} from './native.mjs';
export async function test(ctx){
  const d=await deployment(ctx);
  const abi=artifact('FalconPrecompile').abi;
  const verify=(pk,message,signature)=>ctx.client.readContract({address:d.verifier,abi,functionName:'verify',args:[pk,message,signature],gas:2000000n});
  const key=native('keygen');const lengths=[];let sample;
  for(const message of [bytesToHex(new Uint8Array(32)),bytesToHex(new Uint8Array(32).fill(255)),bytesToHex(randomBytes(32))]){
    const {signature}=native('sign',{secretKey:key.secretKey,message});lengths.push((signature.length-2)/2);
    assert(native('verify',{publicKey:key.publicKey,message,signature}).valid);
    assert.equal(await verify(key.publicKey,message,signature),true);sample=[key.publicKey,message,signature];
  }
  const mutate=x=>x.slice(0,-2)+(x.endsWith('00')?'01':'00');
  const [pk,message,sig]=sample;
  for(const args of [[mutate(pk),message,sig],[pk,mutate(message),sig],[pk,message,mutate(sig)],[pk,message,sig.slice(0,-2)],[pk,message,sig+'00']])assert.equal(await verify(...args),false);
  const kat=fs.readFileSync(fileURLToPath(new URL('../../tools/crypto-bench/testdata/falcon.tsv',import.meta.url)),'utf8').trim().split('\n');
  for(const line of kat){const [publicKey,message,signature]=line.split('\t');assert(native('verify',{publicKey,message,signature}).valid);}
  const verifierGasEstimate=await ctx.client.estimateContractGas({address:d.verifier,abi,functionName:'verify',args:sample});
  console.log('PASS: native signatures, five negatives and two independent Bouncy Castle public KATs');
  return accountFlow(ctx,{verifierGasEstimate,validMessages:3,verifierNegativeCases:5,independentBouncyCastleKats:kat.length,signatureBytes:lengths});
}
