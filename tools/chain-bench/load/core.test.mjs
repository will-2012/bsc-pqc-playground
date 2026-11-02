import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Pacer, Accounts, save, read, acquire, validate} from './core.mjs';

test('100 QPS is paced at 10ms and never catches up after stalls', async () => {
  let now = 0;
  const times = [];
  const p = new Pacer(100, () => now, async ms => { now += ms; });
  await Promise.all(Array.from({length: 5}, async () => { await p.take(); times.push(now); }));
  assert.equal(now, 40);
  now = 1000;
  await p.take(); assert.equal(now, 1000);
  await p.take(); assert.equal(now, 1010);
});

test('one pending operation per account, round robin does not reuse a busy account', () => {
  const a = new Accounts(3);
  assert.deepEqual([a.take(), a.take(), a.take()], [0, 1, 2]);
  assert.equal(a.take(), null);
  a.release(1); assert.equal(a.take(), 1);
  assert.equal(a.take(), null);
});

test('private persistent identities and exclusive sender lock', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pqc-load-'));
  try {
    const f = path.join(dir, 'keys', '0.json');
    save(f, {secretKey: 'test-only', nonce: 9n});
    assert.deepEqual(read(f), {secretKey: 'test-only', nonce: '9'});
    assert.equal(fs.statSync(f).mode & 0o777, 0o600);
    assert(!fs.existsSync(f + '.tmp'));
    const release = acquire(path.join(dir, 'active.lock'));
    assert.throws(() => acquire(path.join(dir, 'active.lock')), /EEXIST/);
    release(); acquire(path.join(dir, 'active.lock'))();
  } finally { fs.rmSync(dir, {recursive: true}); }
});

test('invalid requests fail before any transaction', () => {
  const s = {action: 'create', algorithm: 'slh', dataset: 'a', run: 'r', accounts: 2,
    count: 2, workers: 2, qps: 100 / 3, startAt: 0, fundWei: '100', valueWei: '1'};
  validate(s);
  for (const patch of [{dataset: '../key'}, {qps: NaN}, {workers: 0}, {count: 1}, {algorithm: 'falcon'}]) {
    assert.throws(() => validate({...s, ...patch}));
  }
});

test('recovery never steals a live runner lock', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pqc-load-lock-'));
  try {
    const file = path.join(dir, 'active.lock');
    const release = acquire(file);
    assert.throws(() => acquire(file, true), /EEXIST/);
    release();
  } finally { fs.rmSync(dir, {recursive: true}); }
});

test('account cache is bounded, refreshes LRU entries and can be disabled', async () => {
  const {AccountCache} = await import('./core.mjs');
  const cache = new AccountCache(2);
  cache.set(1, 'one'); cache.set(2, 'two');
  assert.equal(cache.get(1), 'one');
  cache.set(3, 'three');
  assert.equal(cache.get(2), undefined);
  assert.equal(cache.items.size, 2);
  assert.deepEqual([cache.hits, cache.misses], [1, 1]);
  const disabled = new AccountCache(0);
  disabled.set(1, 'one'); assert.equal(disabled.get(1), undefined);
});

test('receipt evidence excludes private data and distinguishes UserOperations', async () => {
  const {receiptEvidence} = await import('./inclusions.mjs');
  const record = {state: 'confirmed', kind: 'userop', action: 'transfer', hash: 'operation',
    transactionHash: 'transaction', blockNumber: 12n, blockHash: 'block', signed: {secret: 'private'},
    publicKey: 'public', secretKey: 'secret'};
  const result = receiptEvidence(record);
  assert.deepEqual(Object.keys(result).sort(),
    ['action', 'blockHash', 'blockNumber', 'kind', 'state', 'transactionHash', 'userOpHash'].sort());
  assert.equal(result.userOpHash, 'operation');
  assert(!Object.hasOwn(receiptEvidence({...record, kind: 'transaction'}), 'userOpHash'));
});

test('inclusions deduplicate outer transactions without merging UserOperation counts', async () => {
  const {InclusionWindow} = await import('./inclusions.mjs');
  const w = new InclusionWindow();
  const result = {state: 'confirmed', kind: 'userop', blockNumber: 9n,
    blockHash: '0x' + '11'.repeat(32), transactionHash: '0x' + '22'.repeat(32)};
  w.add(result); w.add(result);
  w.add({...result, kind: 'transaction', transactionHash: '0x' + '33'.repeat(32)});
  w.add({state: 'confirmed'}); // ECDSA preparation is off-chain.
  w.add({...result, state: 'skipped'});
  assert.deepEqual(w.snapshot(), {inclusions: [{blockNumber: 9, blockHash: result.blockHash,
    transactions: [result.transactionHash, '0x' + '33'.repeat(32)], userOps: 2, count: 3}], inclusionTruncated: false});
});

test('inclusion window is bounded and reports missing or truncated evidence', async () => {
  const {InclusionWindow} = await import('./inclusions.mjs');
  const w = new InclusionWindow();
  const result = {state: 'confirmed', kind: 'transaction', blockNumber: 1,
    blockHash: '0x' + '11'.repeat(32), transactionHash: '0x' + '22'.repeat(32)};
  for (let i = 0; i < 257; i++) w.add({...result, transactionHash: '0x' + i.toString(16).padStart(64, '0')});
  assert.equal(w.blocks[0].transactions.length, 256);
  assert.equal(w.blocks[0].count, 257);
  assert.equal(w.truncated, true);
  for (let i = 2; i <= 130; i++) w.add({...result, blockNumber: i});
  assert.equal(w.blocks.length, 128); assert.equal(w.blocks[0].blockNumber, 3);
  w.add({...result, blockNumber: 1});
  assert.equal(w.blocks[0].blockNumber, 3);
  const missing = new InclusionWindow(); missing.add({...result, blockHash: undefined});
  assert.equal(missing.truncated, true); assert.deepEqual(missing.blocks, []);
});

test('same-height different hashes remain distinct for reorg checks', async () => {
  const {InclusionWindow} = await import('./inclusions.mjs');
  const w = new InclusionWindow();
  const result = {state: 'confirmed', kind: 'transaction', blockNumber: 5,
    blockHash: '0x' + '11'.repeat(32), transactionHash: '0x' + '22'.repeat(32)};
  w.add(result); w.add({...result, blockHash: '0x' + '33'.repeat(32)});
  assert.equal(w.blocks.length, 2);
});
