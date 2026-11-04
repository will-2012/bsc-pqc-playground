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
import {profiles, read, save, AccountCache, RecipientPool, failureDetails} from './core.mjs';
import {prepareDirect, directCalldata, applyDirectReceipt} from './direct.mjs';
import {receiptEvidence} from './inclusions.mjs';
import {MlSigner} from './signer/ml-client.mjs';
import {SlhSigner, avx2Binary, avx512Binary} from './signer/client.mjs';
import {Timings} from './timing.mjs';
import {prepareTransaction, waitForTransaction} from './transactions.mjs';
const timings = new Timings();

const {spec, env, directory} = workerData;
const ctx = await context(env, profiles[spec.algorithm]);
if (spec.algorithm !== 'ecdsa') {
  ctx.deployment = read(path.join(ctx.dir, 'deployment.json'));
  assert.equal(ctx.deployment.genesisHash, ctx.manifest.genesis_hash);
  assert.equal(ctx.deployment.entryPoint, ctx.base.entryPoint);
}
const cache = new AccountCache(spec.cacheAccounts);
const recipients = spec.recipientAddresses ? new RecipientPool(spec.recipientAddresses) : null;
const mlSigner = spec.algorithm === 'ml' && spec.mlBackend === 'persistent' ? new MlSigner() : null;
let signer;
if (spec.algorithm === 'slh' && spec.slhBackend === 'openssl') signer = new SlhSigner();
if (spec.algorithm === 'slh' && spec.slhBackend === 'avx2') signer = new SlhSigner(avx2Binary);
if (spec.algorithm === 'slh' && spec.slhBackend === 'avx512') signer = new SlhSigner(avx512Binary);
let requestId = 0;
const replies = new Map();
class SendCancelled extends Error {}
parentPort.on('message', async msg => {
  if (msg.type === 'nonce' || msg.type === 'permit') {
    const reply = replies.get(msg.requestId);
    assert(reply, 'Unknown worker request');
    replies.delete(msg.requestId);
    reply(msg);
    return;
  }
  if (msg.type !== 'task') return;
  try {
    const result = await task(msg);
    parentPort.postMessage({type: 'done', sequence: msg.sequence, result, timings: timings.values, cacheHits: cache.hits, cacheMisses: cache.misses});
  }
  catch (e) {
    if (e instanceof SendCancelled) parentPort.postMessage({type: 'done', sequence: msg.sequence, result: {state: 'cancelled'}, timings: timings.values, cacheHits: cache.hits, cacheMisses: cache.misses});
    else parentPort.postMessage({type: 'error', sequence: msg.sequence, timings: timings.values, ...failureDetails(e)});
  }
});
parentPort.postMessage({type: 'ready'});
const request = type => new Promise(resolve => {
  const id = requestId++;
  replies.set(id, resolve);
  parentPort.postMessage({type, requestId: id});
});
const faucetNonce = async () => (await request('nonce')).nonce;
const allowSend = async () => {
  if (!(await timings.measure('admission', () => request('permit'))).allowed) throw new SendCancelled();
};

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
    await allowSend(); // Reserve a faucet nonce only after admission; cancellation must not leave nonce gaps.
    const result = await raw(ctx.wallet, {to: entry.address, value: BigInt(spec.fundWei), nonce: await faucetNonce()}, funding, entry, file, 'fund', true);
    entry.funded = true; save(file, entry);
    return result;
  }
  assert(entry, 'Run prepare first');
  const journal = path.join(directory, 'runs', spec.run, 'operations', `${sequence}.json`);
  assert(!fs.existsSync(journal), 'Operation already exists; reconcile instead of resending');
  if (spec.action === 'create' && entry.created) return {state: 'skipped'};
  if (spec.action === 'transfer') assert(entry.created, 'Create this account first');
  const to = spec.action === 'transfer' ? (recipients?.pick(entry.address) || ctx.manifest.accounts[1].address) : entry.address;
  if (spec.algorithm === 'ecdsa') {
    if (spec.action === 'create') {
      assert(!(await ctx.client.getCode({address: entry.address})), 'EOA has contract code');
      assert.equal(await ctx.client.getBalance({address: entry.address}), 0n, 'EOA was already funded');
      await allowSend();
      return raw(ctx.wallet, {to: entry.address, value: BigInt(spec.fundWei), nonce: await faucetNonce()}, journal, entry, file, 'create', true);
    }
    const wallet = cached?.wallet || createWalletClient({account: privateKeyToAccount(entry.secretKey), chain: ctx.chain,
      transport: custom({request: args => ctx.client.request(args)})});
    cache.set(index, {entry, identity, wallet});
    const nonce = await ctx.client.getTransactionCount({address: entry.address, blockTag: 'pending'});
    return raw(wallet, {to, value: BigInt(spec.valueWei), nonce}, journal, entry, file, 'transfer');
  }
  assert(entry.funded, 'PQ account not prefunded');
  const smart = cached?.smart || await smartAccount(ctx, identity);
  if (spec.algorithm === 'slh') {
    // Estimation permits SIG_VALIDATION_FAILED. Nonzero bytes conservatively price calldata.
    // The actual UserOperation is still freshly signed and locally verified by the profile.
    smart.getStubSignature = async () => '0x' + 'ff'.repeat(7856);
    if (signer && !cached?.smart) smart.signUserOperation = async op => signer.sign(identity.secretKey, getUserOperationHash({
      chainId: ctx.chain.id, entryPointAddress: ctx.deployment.entryPoint, entryPointVersion: ctx.profile.entryPointVersion,
      userOperation: {...op, sender: entry.address},
    }));
  }
  if (mlSigner && !cached?.smart) smart.signUserOperation = op => mlSigner.sign(identity.secretKey, identity.publicKey, getUserOperationHash({
    chainId: ctx.chain.id, entryPointAddress: ctx.deployment.entryPoint, entryPointVersion: ctx.profile.entryPointVersion,
    userOperation: {...op, sender: entry.address},
  }));
  if (spec.action === 'transfer') cache.set(index, {entry, identity, smart});
  if (!cached?.smart) {
    const sign = smart.signUserOperation.bind(smart);
    smart.signUserOperation = op => timings.measure('sign', () => sign(op));
  }
  const call = {to, value: spec.action === 'create' ? 0n : BigInt(spec.valueWei)};
  const operation = spec.submission === 'direct'
    ? await timings.measure('prepareUserOperation', () => prepareDirect(ctx, smart, call, spec.directGas))
    : await timings.measure('prepareUserOperation', () => prepare(ctx, identity, call, smart));
  const hash = getUserOperationHash({chainId: ctx.chain.id, entryPointAddress: ctx.deployment.entryPoint,
    entryPointVersion: ctx.profile.entryPointVersion, userOperation: operation});
  assert.equal(hash, await ctx.profile.onchainOperationHash(ctx, operation));
  const record = {kind: 'userop', action: spec.action, index, hash, to, value: spec.action === 'create' ? '0' : spec.valueWei, state: 'pending', signed: operation};
  await allowSend();
  if (spec.submission === 'direct') {
    const request = await ctx.wallet.prepareTransactionRequest({
      to: ctx.deployment.entryPoint, data: directCalldata(operation, ctx.wallet.account.address),
      chainId: ctx.chain.id, nonce: await faucetNonce(), gas: BigInt(spec.directGas.outerGasLimit), ...ctx.fee,
    });
    const signedTransaction = await ctx.wallet.account.signTransaction(request, {serializer: ctx.chain.serializers?.transaction});
    Object.assign(record, {submission: 'direct', entryPoint: ctx.deployment.entryPoint,
      signedTransaction, transactionHash: keccak256(signedTransaction), sentAt: Date.now()});
    save(journal, record);
    assert.equal(await timings.measure('submit', () => ctx.client.sendRawTransaction({serializedTransaction: signedTransaction})), record.transactionHash);
    parentPort.postMessage({type: 'accepted'});
    return confirm(record, journal, entry, file);
  }
  // Persist before broadcasting; cancelled, unsent work leaves no pending journal.
  record.sentAt = Date.now(); save(journal, record);
  assert.equal(await timings.measure('submit', () => ctx.bundler.sendUserOperation(operation)), hash);
  parentPort.postMessage({type: 'accepted'});
  return confirm(record, journal, entry, file);
}

async function raw(wallet, tx, journal, entry, file, action, admitted = false) {
  const request = await timings.measure('prepareTransaction', () => prepareTransaction(ctx, wallet, tx));
  const signed = await timings.measure('sign', () => wallet.account.signTransaction(request, {serializer: ctx.chain.serializers?.transaction}));
  const record = {kind: 'transaction', action, index: entry.index, hash: keccak256(signed), to: tx.to, value: tx.value, signed, state: 'pending'};
  if (!admitted) await allowSend();
  record.sentAt = Date.now(); save(journal, record);
  assert.equal(await timings.measure('submit', () => ctx.client.sendRawTransaction({serializedTransaction: signed})), record.hash);
  parentPort.postMessage({type: 'accepted'});
  return confirm(record, journal, entry, file);
}

async function confirm(record, journal, entry, file) {
  if (record.submission === 'direct') {
    applyDirectReceipt(record, await timings.measure('receipt', () => waitForTransaction(ctx.client, record.transactionHash)));
  } else if (record.kind === 'userop') {
    const receipt = await timings.measure('receipt', () => ctx.bundler.waitForUserOperationReceipt({hash: record.hash, timeout: 180000}));
    record.transactionHash = receipt.receipt.transactionHash;
    record.blockNumber = receipt.receipt.blockNumber; record.blockHash = receipt.receipt.blockHash;
    record.gasUsed = receipt.actualGasUsed;
    record.state = receipt.success && receipt.receipt.status === 'success' ? 'confirmed' : 'reverted';
  } else {
    const receipt = await timings.measure('receipt', () => waitForTransaction(ctx.client, record.hash));
    record.transactionHash = receipt.transactionHash; record.gasUsed = receipt.gasUsed;
    record.blockNumber = receipt.blockNumber; record.blockHash = receipt.blockHash;
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
  return receiptEvidence(record);
}
