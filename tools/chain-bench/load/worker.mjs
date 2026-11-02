import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {parentPort, workerData} from 'node:worker_threads';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../../erc4337-wallet/package.json', import.meta.url));
const {generatePrivateKey, privateKeyToAccount} = require('viem/accounts');
const {createWalletClient, custom, keccak256} = require('viem');
const {getUserOperationHash} = require('viem/account-abstraction');
import {context, newIdentity, loadIdentity, prepare, smartAccount} from '../../../erc4337-wallet/common/wallet.mjs';
import {profiles, read, save, AccountCache} from './core.mjs';

const {spec, env, directory} = workerData;
const ctx = await context(env, profiles[spec.algorithm]);
if (spec.algorithm !== 'ecdsa') {
  ctx.deployment = read(path.join(ctx.dir, 'deployment.json'));
  assert.equal(ctx.deployment.genesisHash, ctx.manifest.genesis_hash);
  assert.equal(ctx.deployment.entryPoint, ctx.base.entryPoint);
}
const cache = new AccountCache(spec.cacheAccounts);
let permit, nonceReply;
parentPort.on('message', async msg => {
  if (msg.type === 'nonce') { nonceReply(msg.nonce); return; }
  if (msg.type === 'permit') { permit?.(); return; }
  if (msg.type !== 'task') return;
  try {
    const result = await task(msg);
    parentPort.postMessage({type: 'done', result, cacheHits: cache.hits, cacheMisses: cache.misses});
  }
  catch (e) { parentPort.postMessage({type: 'error', error: String(e.shortMessage || e.message).slice(0, 1000)}); }
});
parentPort.postMessage({type: 'ready'});
const faucetNonce = () => new Promise(resolve => { nonceReply = resolve; parentPort.postMessage({type: 'nonce'}); });
const allowSend = () => new Promise(resolve => { permit = resolve; parentPort.postMessage({type: 'permit'}); });

async function task({index, sequence}) {
  const file = path.join(directory, 'accounts', `${index}.json`);
  const cached = spec.action === 'transfer' ? cache.get(index) : undefined;
  let entry = cached?.entry || (fs.existsSync(file) ? read(file) : null);
  let identity = cached?.identity;
  if (entry && !cached) {
    assert.equal(entry.algorithm, spec.algorithm);
    assert.equal(entry.genesisHash, ctx.manifest.genesis_hash);
    identity = spec.algorithm === 'ecdsa' ? entry : loadIdentity(ctx, entry.id);
  }
  if (spec.action === 'prepare') {
    if (!entry) {
      if (spec.algorithm === 'ecdsa') {
        const secretKey = generatePrivateKey();
        const account = privateKeyToAccount(secretKey);
        identity = {secretKey, publicKey: account.publicKey, address: account.address};
      } else identity = await newIdentity(ctx);
      entry = {algorithm: spec.algorithm, genesisHash: ctx.manifest.genesis_hash,
        index, ...identity, funded: false, created: false};
      save(file, entry); // Keys are durable before the first funding transaction.
    }
    if (spec.algorithm === 'ecdsa') return {state: 'confirmed', address: entry.address};
    const funding = path.join(directory, 'funding', `${index}.json`);
    if (fs.existsSync(funding)) {
      const previous = read(funding);
      if (previous.state !== 'confirmed') {
        await confirm(previous, funding, entry, file);
      }
      entry.funded = true; save(file, entry);
      return {state: 'confirmed', address: entry.address};
    }
    await raw(ctx.wallet, {to: entry.address, value: BigInt(spec.fundWei), nonce: await faucetNonce()}, funding, entry, file, 'fund');
    entry.funded = true; save(file, entry);
    return {state: 'confirmed', address: entry.address};
  }
  assert(entry, 'Run prepare first');
  const journal = path.join(directory, 'runs', spec.run, 'operations', `${sequence}.json`);
  assert(!fs.existsSync(journal), 'Operation already exists; reconcile instead of resending');
  if (spec.action === 'create' && entry.created) return {state: 'skipped'};
  if (spec.action === 'transfer') assert(entry.created, 'Create this account first');
  if (spec.algorithm === 'ecdsa') {
    if (spec.action === 'create') {
      assert(!(await ctx.client.getCode({address: entry.address})), 'EOA has contract code');
      assert.equal(await ctx.client.getBalance({address: entry.address}), 0n, 'EOA was already funded');
      return raw(ctx.wallet, {to: entry.address, value: BigInt(spec.fundWei), nonce: await faucetNonce()}, journal, entry, file, 'create');
    }
    const wallet = cached?.wallet || createWalletClient({account: privateKeyToAccount(entry.secretKey), chain: ctx.chain,
      transport: custom({request: args => ctx.client.request(args)})});
    cache.set(index, {entry, identity, wallet});
    const nonce = await ctx.client.getTransactionCount({address: entry.address, blockTag: 'pending'});
    return raw(wallet, {to: ctx.manifest.accounts[1].address, value: BigInt(spec.valueWei), nonce}, journal, entry, file, 'transfer');
  }
  assert(entry.funded, 'PQ account not prefunded');
  const smart = cached?.smart || await smartAccount(ctx, identity);
  if (spec.algorithm === 'slh') {
    // Estimation permits SIG_VALIDATION_FAILED. Nonzero bytes conservatively price calldata.
    // The actual UserOperation is still freshly signed and locally verified by the profile.
    smart.getStubSignature = async () => '0x' + 'ff'.repeat(7856);
  }
  if (spec.action === 'transfer') cache.set(index, {entry, identity, smart});
  const operation = await prepare(ctx, identity, {
    to: spec.action === 'create' ? entry.address : ctx.manifest.accounts[1].address,
    value: spec.action === 'create' ? 0n : BigInt(spec.valueWei),
  }, smart);
  const hash = getUserOperationHash({chainId: ctx.chain.id, entryPointAddress: ctx.deployment.entryPoint,
    entryPointVersion: ctx.profile.entryPointVersion, userOperation: operation});
  assert.equal(hash, await ctx.profile.onchainOperationHash(ctx, operation));
  const record = {kind: 'userop', action: spec.action, index, hash, state: 'pending', signed: operation};
  save(journal, record); // An RPC timeout must never result in a fresh nonce/send on restart.
  await allowSend();
  record.sentAt = Date.now(); save(journal, record);
  assert.equal(await ctx.bundler.sendUserOperation(operation), hash);
  parentPort.postMessage({type: 'accepted'});
  return confirm(record, journal, entry, file);
}

async function raw(wallet, tx, journal, entry, file, action) {
  const request = await wallet.prepareTransactionRequest({...tx, gas: 21000n, ...ctx.fee});
  const signed = await wallet.signTransaction(request);
  const record = {kind: 'transaction', action, index: entry.index, hash: keccak256(signed), signed, state: 'pending'};
  save(journal, record);
  await allowSend();
  record.sentAt = Date.now(); save(journal, record);
  assert.equal(await ctx.client.sendRawTransaction({serializedTransaction: signed}), record.hash);
  parentPort.postMessage({type: 'accepted'});
  return confirm(record, journal, entry, file);
}

async function confirm(record, journal, entry, file) {
  if (record.kind === 'userop') {
    const receipt = await ctx.bundler.waitForUserOperationReceipt({hash: record.hash, timeout: 180000});
    record.transactionHash = receipt.receipt.transactionHash;
    record.gasUsed = receipt.actualGasUsed;
    record.state = receipt.success && receipt.receipt.status === 'success' ? 'confirmed' : 'reverted';
  } else {
    const receipt = await ctx.client.waitForTransactionReceipt({hash: record.hash, timeout: 180000});
    record.transactionHash = receipt.transactionHash; record.gasUsed = receipt.gasUsed;
    record.state = receipt.status === 'success' ? 'confirmed' : 'reverted';
  }
  record.confirmedAt = Date.now();
  save(journal, record);
  assert.equal(record.state, 'confirmed', 'Transaction reverted');
  if (record.action === 'create') {
    if (spec.algorithm !== 'ecdsa') {
      assert(await ctx.client.getCode({address: entry.address}));
      assert.equal(await ctx.profile.checkPublicKey(ctx, entry.address), entry.publicKey);
    }
    entry.created = true; save(file, entry);
  }
  return {state: record.state, hash: record.hash, address: entry.address};
}
