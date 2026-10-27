import assert from 'node:assert/strict';
import {native} from './native.mjs';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {bytesToHex, createPublicClient, http, encodeAbiParameters, encodeFunctionData, parseAbi} from 'viem';
import {entryPoint07Abi, getUserOperationHash, toPackedUserOperation, toSmartAccount} from 'viem/account-abstraction';
const factoryAbi = parseAbi([
  'function getAddress(bytes,uint256) view returns(address)',
  'function createAccount(bytes,uint256) returns(address)',
]);
const accountAbi = parseAbi(['function execute(address,uint256,bytes)', 'function publicKey() view returns(bytes)']);
const nonceAbi = parseAbi(['function getNonce(address,uint192) view returns(uint256)']);

export async function smartAccount(ctx, identity) {
  const d = ctx.deployment;
  assert.equal(identity.genesisHash, d.genesisHash);
  assert.equal(identity.entryPoint, d.entryPoint);
  assert.equal(identity.factory, d.factory);
  const secretKey = identity.secretKey;
  const publicKey = identity.publicKey;
  const checkMessage = bytesToHex(new Uint8Array(32));
  const proof = native('sign', {secretKey, message: checkMessage}).signature;
  assert(native('verify', {publicKey, message: checkMessage, signature: proof}).valid, 'MLDSA65 key mismatch');
  const salt = BigInt(identity.salt);
  const address = await ctx.client.readContract({address: d.factory, abi: factoryAbi, functionName: 'getAddress', args: [publicKey, salt]});
  assert.equal(address.toLowerCase(), identity.address.toLowerCase());
  const unsupported = async () => { throw new Error('Only ERC-4337 UserOperation signing is supported'); };
  return toSmartAccount({client: ctx.client, entryPoint: {address: d.entryPoint, version: ctx.profile.entryPointVersion},
    getAddress: async () => address,
    getFactoryArgs: async () => ({factory: d.factory, factoryData: encodeFunctionData({abi: factoryAbi,
      functionName: 'createAccount', args: [publicKey, salt]})}),
    getNonce: async () => ctx.client.readContract({address: d.entryPoint, abi: nonceAbi, functionName: 'getNonce', args: [address, 0n]}),
    encodeCalls: async calls => {
      assert.equal(calls.length, 1, 'Exactly one call per UserOperation');
      return encodeFunctionData({abi: accountAbi, functionName: 'execute', args: [calls[0].to, calls[0].value || 0n, calls[0].data || '0x']});
    },
    // Estimate with a fixed-size Pure ML-DSA signature.
    // EntryPoint estimation permits SIG_VALIDATION_FAILED for the dummy signature.
    getStubSignature: async () => '0x'+'ff'.repeat(3309),
    signUserOperation: async op => {
      const hash = getUserOperationHash({chainId: ctx.chain.id, entryPointAddress: d.entryPoint,
        entryPointVersion: ctx.profile.entryPointVersion, userOperation: {...op, sender: address}});
      const signature = native('sign', {secretKey, message: hash}).signature;
      assert(native('verify', {publicKey, message: hash, signature}).valid, 'Local signature verification failed');
      return signature;
    }, signMessage: unsupported, signTypedData: unsupported,
  });
}

export const profile = {
  id: 'mldsa65-v5', contractVersion: 5,
  entryPointVersion: '0.7', algorithm: 'ML-DSA-65',
  contracts: ctx => [
    ['verifier','MLDSA65Precompile',[]],
    ['factory','MLDSA65AccountFactory',[ctx.base.entryPoint,ctx.deployment.verifier]],
  ],
  keygen: () => native('keygen'),
  signDigest: (secretKey,message) => native('sign',{secretKey,message}).signature,
  async probe(ctx) {
    assert([5,6].includes(ctx.config.pq_precompile),'v5 requires a compatible PQ-fork client');
    const file=fileURLToPath(new URL(`../../one-box/.runtime/build/v${ctx.config.pq_precompile}/mldsa-public.json`,import.meta.url));
    const {input}=JSON.parse(fs.readFileSync(file,'utf8'));
    for(let i=0;i<ctx.config.validators;i++){
      const node=createPublicClient({chain:ctx.chain,transport:http(`http://127.0.0.1:${ctx.config.rpc_port+2*i}`)});
      const r=await node.call({to:'0x0000000000000000000000000000000000000104',data:'0x'+input,gas:1000000n});
      assert.equal(r.data,'0x'+'00'.repeat(31)+'01','Missing MLDSA65 precompile');
    }
  },
  getAddress: (ctx, publicKey, salt) => ctx.client.readContract({address: ctx.deployment.factory,
    abi: factoryAbi, functionName: 'getAddress', args: [publicKey, salt]}),
  checkPublicKey: (ctx, address) => ctx.client.readContract({address, abi: accountAbi, functionName: 'publicKey'}),
  encodedOperation: op => encodeAbiParameters(entryPoint07Abi.find(x => x.name === 'getUserOpHash').inputs,
    [toPackedUserOperation(op)]),
  onchainOperationHash: (ctx, op) => ctx.client.readContract({address: ctx.deployment.entryPoint, abi: entryPoint07Abi,
    functionName: 'getUserOpHash', args: [toPackedUserOperation(op)]}),
  smartAccount,
};
