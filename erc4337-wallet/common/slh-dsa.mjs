import assert from 'node:assert/strict';
import {slh_dsa_sha2_128s as defaultSlh} from '@noble/post-quantum/slh-dsa.js';
import {bytesToHex, hexToBytes, encodeAbiParameters, encodeFunctionData, parseAbi} from 'viem';
import {entryPoint07Abi, getUserOperationHash, toPackedUserOperation, toSmartAccount} from 'viem/account-abstraction';
const factoryAbi = parseAbi([
  'function getAddress(bytes32,uint256) view returns(address)',
  'function createAccount(bytes32,uint256) returns(address)',
]);
const accountAbi = parseAbi(['function execute(address,uint256,bytes)', 'function publicKey() view returns(bytes32)']);
const nonceAbi = parseAbi(['function getNonce(address,uint192) view returns(uint256)']);

export function createSlhProfile(slh,algorithm){
  async function smartAccount(ctx, identity) {
    const d = ctx.deployment;
    assert.equal(identity.genesisHash, d.genesisHash);
    assert.equal(identity.entryPoint, d.entryPoint);
    assert.equal(identity.factory, d.factory);
    const secretKey = hexToBytes(identity.secretKey);
    const publicKey = bytesToHex(slh.getPublicKey(secretKey));
    assert.equal(publicKey, identity.publicKey);
    const salt = BigInt(identity.salt);
    const address = await ctx.client.readContract({address: d.factory, abi: factoryAbi, functionName: 'getAddress', args: [publicKey, salt]});
    assert.equal(address.toLowerCase(), identity.address.toLowerCase());
    let stub;
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
      getStubSignature: async () => stub ||= bytesToHex(slh.sign(new Uint8Array(32), secretKey)),
      signUserOperation: async op => {
        const hash = getUserOperationHash({chainId: ctx.chain.id, entryPointAddress: d.entryPoint,
          entryPointVersion: ctx.profile.entryPointVersion, userOperation: {...op, sender: address}});
        const signature = slh.sign(hexToBytes(hash), secretKey);
        assert(slh.verify(signature, hexToBytes(hash), hexToBytes(publicKey)), 'Local signature verification failed');
        return bytesToHex(signature);
      }, signMessage: unsupported, signTypedData: unsupported,
    });
  }

  return {
    entryPointVersion: '0.7', algorithm,
    signDigest: (secretKey,message) => bytesToHex(slh.sign(hexToBytes(message),hexToBytes(secretKey))),
    keygen() {
      const keys = slh.keygen();
      const result = {publicKey: bytesToHex(keys.publicKey), secretKey: bytesToHex(keys.secretKey)};
      keys.secretKey.fill(0);
      return result;
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

}
export const slhProfile=createSlhProfile(defaultSlh,'SLH-DSA-SHA2-128s');
export const smartAccount=slhProfile.smartAccount;
