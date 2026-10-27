import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {randomBytes} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {slh_dsa_sha2_128s as slh} from '@noble/post-quantum/slh-dsa.js';
import {bytesToHex, hexToBytes, createPublicClient, http, parseAbi, encodeFunctionData, parseEther} from 'viem';
import {getUserOperationHash} from 'viem/account-abstraction';
import {artifact, createAccount, deployment, loadIdentity, prepare, smartAccount, submit, write} from './wallet.mjs';
import {getProfile} from '../profiles.mjs';

export async function test(ctx) {
  assert(['slh-dsa-v1', 'slh-dsa-v2'].includes(ctx.profile.id), 'This suite covers the two SLH-DSA profiles');
  const d = await deployment(ctx);
  const verifierContract = ctx.profile.contracts(ctx).find(([label]) => label === 'verifier')[1];
  const abi = artifact(verifierContract).abi;
  const verify = (pk, message, sig) => ctx.client.readContract({address: d.verifier, abi,
    functionName: 'verify', args: [pk, message, sig], gas: 16000000n});
  const {publicKey, secretKey} = slh.keygen();
  let sample;
  for (const message of [new Uint8Array(32), new Uint8Array(32).fill(255), randomBytes(32)]) {
    const signature = slh.sign(message, secretKey);
    assert.equal(signature.length, 7856);
    assert(slh.verify(signature, message, publicKey));
    sample = [bytesToHex(publicKey), bytesToHex(message), bytesToHex(signature)];
    assert.equal(await verify(...sample), true);
  }
  secretKey.fill(0);
  const [pk, message, signature] = sample;
  const mutate = value => value.slice(0, -2) + (value.endsWith('00') ? '01' : '00');
  for (const args of [[pk, mutate(message), signature], [mutate(pk), message, signature],
    [pk, message, mutate(signature)], [pk, message, signature.slice(0, -2)], [pk, message, signature + '00']]) {
    assert.equal(await verify(...args), false);
  }
  const verifierGas = await ctx.client.estimateContractGas({address: d.verifier, abi, functionName: 'verify', args: sample});
  console.log('PASS: noble signatures, three messages and five verifier negative cases');

  // Independent implementation, fresh random private key confined to ignored runtime.
  const vectorDir = path.join(ctx.dir, 'openssl-test');
  fs.mkdirSync(vectorDir, {recursive: true, mode: 0o700});
  const key = path.join(vectorDir, 'key.pem');
  const msg = path.join(vectorDir, 'message.bin');
  const sig = path.join(vectorDir, 'signature.bin');
  const openssl = process.env.OPENSSL || 'openssl';
  const opensslVersion = execFileSync(openssl, ['version'], {encoding: 'utf8'}).trim();
  let independent;
  try {
    execFileSync(openssl, ['genpkey', '-algorithm', 'SLH-DSA-SHA2-128s', '-out', key]);
    fs.writeFileSync(msg, randomBytes(32), {mode: 0o600});
    execFileSync(openssl, ['pkeyutl', '-sign', '-rawin', '-inkey', key, '-in', msg, '-out', sig]);
    const der = execFileSync(openssl, ['pkey', '-in', key, '-pubout', '-outform', 'DER']);
    independent = {publicKey: bytesToHex(der.subarray(-32)), message: bytesToHex(fs.readFileSync(msg)), signature: bytesToHex(fs.readFileSync(sig))};
    assert(slh.verify(hexToBytes(independent.signature), hexToBytes(independent.message), hexToBytes(independent.publicKey)));
    assert.equal(await verify(independent.publicKey, independent.message, independent.signature), true);
    write(path.join(vectorDir, 'public-vector.json'), {opensslVersion, ...independent});
  } finally {
    fs.rmSync(key, {force: true});
  }
  console.log('PASS: independent OpenSSL signature accepted by noble and Solidity');

  const created = await createAccount(ctx);
  const identity = loadIdentity(ctx, created.id);
  assert.equal(fs.statSync(path.join(ctx.dir, 'accounts', `${created.id}.json`)).mode & 0o777, 0o600);
  assert.throws(() => getProfile('unknown-version'), /Unsupported/);
  await assert.rejects(smartAccount(ctx, {...identity, profile: 'wrong-version'}), /profile mismatch/);
  const recipient = ctx.manifest.accounts[1].address;
  const before = await ctx.client.getBalance({address: recipient});
  const operation = await prepare(ctx, identity, {to: recipient, value: parseEther('0.01')});
  async function rejected(op, pattern) {
    await assert.rejects(() => ctx.bundler.sendUserOperation(op), error => {
      assert.match(error.shortMessage || error.message, pattern);
      return true;
    });
  }
  await rejected({...operation, signature: mutate(operation.signature)}, /signature|AA24/i);
  await rejected({...operation, callData: encodeFunctionData({abi: parseAbi(['function execute(address,uint256,bytes)']),
    functionName: 'execute', args: [recipient, parseEther('0.02'), '0x']})}, /signature|AA24/i);
  // Domain separation is enforced by the on-chain EntryPoint hash.
  const wrongChainHash = getUserOperationHash({chainId: ctx.chain.id + 1, entryPointAddress: d.entryPoint,
    entryPointVersion: '0.7', userOperation: operation});
  const wrongChainSig = bytesToHex(slh.sign(hexToBytes(wrongChainHash), hexToBytes(identity.secretKey)));
  await rejected({...operation, signature: wrongChainSig}, /signature|AA24/i);
  const transferred = await submit(ctx, operation);
  assert.equal(await ctx.client.getBalance({address: recipient}) - before, parseEther('0.01'));
  await rejected(operation, /nonce|AA25/i);
  const execute = encodeFunctionData({abi: parseAbi(['function execute(address,uint256,bytes)']),
    functionName: 'execute', args: [recipient, 1n, '0x']});
  await assert.rejects(() => ctx.client.call({account: ctx.wallet.account, to: identity.address, data: execute}), /not from EntryPoint/);
  console.log('PASS: account creation, transfer, signature/call/domain tampering, nonce replay and direct EOA rejection');

  const counterAbi = parseAbi(['function increment()', 'function count() view returns(uint256)', 'function lastCaller() view returns(address)']);
  const oldCount = await ctx.client.readContract({address: ctx.base.counter, abi: counterAbi, functionName: 'count'});
  const counter = await submit(ctx, await prepare(ctx, identity, {to: ctx.base.counter,
    data: encodeFunctionData({abi: counterAbi, functionName: 'increment'})}));
  const nodes = [];
  for (let i = 0; i < ctx.config.validators; i++) {
    const node = createPublicClient({chain: ctx.chain, pollingInterval: 500,
      transport: http(`http://127.0.0.1:${ctx.config.rpc_port + 2 * i}`)});
    const cr = await node.waitForTransactionReceipt({hash: counter.transactionHash});
    const tr = await node.waitForTransactionReceipt({hash: transferred.transactionHash});
    const ar = await node.waitForTransactionReceipt({hash: created.transactionHash});
    assert.equal(cr.status, 'success'); assert.equal(tr.status, 'success'); assert.equal(ar.status, 'success');
    const count = await node.readContract({address: ctx.base.counter, abi: counterAbi, functionName: 'count', blockNumber: cr.blockNumber});
    const caller = await node.readContract({address: ctx.base.counter, abi: counterAbi, functionName: 'lastCaller', blockNumber: cr.blockNumber});
    assert.equal(count, oldCount + 1n); assert.equal(caller.toLowerCase(), identity.address.toLowerCase());
    nodes.push({node: i, createBlock: ar.blockHash, transferBlock: tr.blockHash, counterBlock: cr.blockHash, count, caller});
  }
  for (const field of ['createBlock', 'transferBlock', 'counterBlock']) assert.equal(new Set(nodes.map(n => n[field])).size, 1);
  const result = {checkedAt: new Date().toISOString(), profile: ctx.profile.id, genesisHash: ctx.manifest.genesis_hash,
    entryPoint: d.entryPoint, factory: d.factory, verifier: d.verifier, safeMode: true, verifierGasEstimate: verifierGas,
    nobleValidMessages: 3, verifierNegativeCases: 5, opensslVersion, independentOpenSSL: true,
    wrongSignatureRejected: true, tamperedCallRejected: true, wrongChainRejected: true,
    replayRejected: true, directEoaRejected: true, profileMismatchRejected: true,
    created, transferred, counter, nodes};
  write(path.join(ctx.dir, 'test-result.json'), result);
  console.log('PASS: three validators agree; result saved to ' + path.join(ctx.dir, 'test-result.json'));
  return result;
}
