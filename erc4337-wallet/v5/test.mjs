import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {bytesToHex,hexToBytes} from 'viem';
import {ml_dsa44,ml_dsa65} from '@noble/post-quantum/ml-dsa.js';
import {deployment,artifact} from '../common/wallet.mjs';
import {accountFlow} from '../common/test.mjs';
import {native} from './native.mjs';
export async function test(ctx){
  const d=await deployment(ctx);
  const abi=artifact('MLDSA65Precompile').abi;
  const verify=(pk,message,signature)=>ctx.client.readContract({address:d.verifier,abi,functionName:'verify',args:[pk,message,signature],gas:2000000n});
  const key=native('keygen');const lengths=[];let sample;
  for(const message of [bytesToHex(new Uint8Array(32)),bytesToHex(new Uint8Array(32).fill(255)),bytesToHex(randomBytes(32))]){
    const {signature}=native('sign',{secretKey:key.secretKey,message});lengths.push((signature.length-2)/2);
    assert(native('verify',{publicKey:key.publicKey,message,signature}).valid);
    assert(ml_dsa65.verify(hexToBytes(signature),hexToBytes(message),hexToBytes(key.publicKey)),'Noble must accept native Pure ML-DSA');
    const independent=ml_dsa65.sign(hexToBytes(message),hexToBytes(key.secretKey));
    assert(native('verify',{publicKey:key.publicKey,message,signature:bytesToHex(independent)}).valid,'Native must accept Noble Pure ML-DSA');
    assert.equal(await verify(key.publicKey,message,signature),true);sample=[key.publicKey,message,signature];
  }
  const mutate=x=>x.slice(0,-2)+(x.endsWith('00')?'01':'00');
  const [pk,message,sig]=sample;
  for(const args of [[mutate(pk),message,sig],[pk,mutate(message),sig],[pk,message,mutate(sig)],[pk,message,sig.slice(0,-2)],[pk,message,sig+'00']])assert.equal(await verify(...args),false);
  const kat=fs.readFileSync(fileURLToPath(new URL('../../one-box/precompile/v5/testdata/mldsa65.tsv',import.meta.url)),'utf8').trim().split('\n');
  let kats=0;for(const line of kat){const [algorithm,publicKey,message,signature,context,valid]=line.split('\t');if(algorithm!=='ML-DSA-65'||context!=='-')continue;assert.equal(native('verify',{publicKey,message,signature}).valid,valid==='1');kats++;}
  const legacy=ml_dsa44.keygen();
  const legacySignature=ml_dsa44.sign(hexToBytes(message),legacy.secretKey);
  assert.equal(await verify(bytesToHex(legacy.publicKey),message,bytesToHex(legacySignature)),false,'ML44 cannot authorize ML65');
  legacy.secretKey.fill(0);
  const verifierGasEstimate=await ctx.client.estimateContractGas({address:d.verifier,abi,functionName:'verify',args:sample});
  console.log('PASS: native signatures, five negatives and independent FIPS 204 public KATs');
  return accountFlow(ctx,{verifierGasEstimate,validMessages:3,verifierNegativeCases:5,crossImplementationDirections:2,crossParameterRejected:true,independentFips204Kats:kats,signatureBytes:lengths});
}
