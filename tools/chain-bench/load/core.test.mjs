import {Timings, mergeTimings} from './timing.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Pacer, Accounts, save, read, acquire, validate, RecipientPool, failureDetails} from './core.mjs';

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

test('early timer wakeups cannot exceed the target rate', async () => {
  let now = 0;
  const p = new Pacer(100 / 3, () => now, async ms => { now += Math.max(0.25, ms - 1); });
  const times = [];
  for (let i = 0; i < 101; i++) {
    assert.equal(await p.take(), true);
    times.push(now);
  }
  for (let i = 1; i < times.length; i++) assert(times[i] - times[i - 1] >= 30 - 1e-9);
  assert(times.at(-1) - times[0] >= 3000 - 1e-9);
});

test('duration denies queued permits at the monotonic deadline', async () => {
  let now = 0;
  const p = new Pacer(1, () => now, async ms => { now += ms; });
  assert.equal(await p.take(100), true);
  assert.equal(await p.take(100), false);
  assert.equal(now, 100); // Do not wait the full one-second pacing interval.
  assert.equal(await p.take(100), false);
  assert.equal(await p.take(Infinity, () => true), false);
});

test('duration is optional and only valid for create or transfer', () => {
  const s = {action: 'transfer', algorithm: 'ecdsa', dataset: 'a', run: 'r', accounts: 2,
    count: 20, workers: 2, qps: 10, startAt: 0, fundWei: '100', valueWei: '1'};
  assert.equal(validate({...s}).durationSeconds, 0);
  assert.equal(validate({...s, durationSeconds: 5}).durationSeconds, 5);
  for (const value of [-1, NaN, Infinity, 86401]) assert.throws(() => validate({...s, durationSeconds: value}));
  assert.throws(() => validate({...s, action: 'prepare', count: 2, durationSeconds: 1}));
});

test('inflight bound is independent of thread count and defaults to legacy concurrency', () => {
  const s = {action: 'transfer', algorithm: 'ecdsa', dataset: 'a', run: 'r', accounts: 10,
    count: 20, workers: 2, qps: 100, startAt: 0, fundWei: '100', valueWei: '1'};
  assert.equal(validate({...s}).maxInflight, 2);
  assert.equal(validate({...s, maxInflight: 100}).maxInflight, 100);
  for (const maxInflight of [0, -1, 4097, 1.5, NaN, Infinity]) assert.throws(() => validate({...s, maxInflight}));
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


test('account affinity keeps keys on the same worker without concurrent reuse', () => {
  const a = new Accounts(7);
  for (let round = 0; round < 3; round++) {
    const rows = [[], [], []];
    for (let slot = 0; slot < 3; slot++) {
      for (;;) {
        const index = a.take(slot, 3);
        if (index === null) break;
        assert.equal(index % 3, slot);
        rows[slot].push(index);
      }
    }
    assert.equal(new Set(rows.flat()).size, 7);
    for (const index of rows.flat()) a.release(index);
  }
});

test('SLH backend is explicit and defaults to the existing implementation', () => {
  const base = {action: 'transfer', algorithm: 'slh', dataset: 'a', run: 'r', accounts: 2, count: 2, workers: 1, qps: 10, startAt: 0, fundWei: '100', valueWei: '1'};
  assert.equal(validate({...base}).slhBackend, 'noble');
  assert.equal(validate({...base, slhBackend: 'openssl'}).slhBackend, 'openssl');
  assert.throws(() => validate({...base, slhBackend: 'unknown'}));
});


test('stage timings merge cumulative worker counters without double counting', async () => {
  const t = new Timings();
  await t.measure('sign', () => 42);
  await assert.rejects(t.measure('sign', () => Promise.reject(new Error('test'))));
  assert.equal(t.values.sign.count, 2);
  const total = {}, first = structuredClone(t.values);
  mergeTimings(total, first, {});
  mergeTimings(total, first, first);
  assert.equal(total.sign.count, 2);
  await t.measure('sign', () => 43);
  mergeTimings(total, t.values, first);
  assert.equal(total.sign.count, 3);
  assert(total.sign.maxMs >= total.sign.meanMs);
});


test('high QPS uses bounded millisecond batches without accumulating catch-up work', async () => {
  let now = 0;
  const p = new Pacer(10000, () => now, async ms => { now += ms; });
  for (let i = 0; i < 10; i++) assert(await p.take());
  assert.equal(now, 0);
  assert(await p.take()); assert.equal(now, 1);
  now = 100;
  for (let i = 0; i < 10; i++) assert(await p.take());
  assert.equal(now, 100);
  assert(await p.take()); assert.equal(now, 101);
  assert.equal(await p.take(101), false);
});


test('recipient pool selects every other account without selecting self', () => {
  const addresses = [1, 2, 3, 4].map(n => '0x' + n.toString(16).padStart(40, '0'));
  const pool = new RecipientPool(addresses);
  for (const sender of addresses) {
    const selected = [0, 1, 2].map(i => pool.pick(sender, max => { assert.equal(max, 3); return i; }));
    assert.deepEqual(selected, addresses.filter(a => a !== sender));
  }
  assert.throws(() => pool.pick('0x' + 'ff'.repeat(20)), /missing/);
  for (const bad of [[], [addresses[0]], [addresses[0], addresses[0]], [addresses[0], '0x0'], [addresses[0], '0x' + '00'.repeat(20)]]) {
    assert.throws(() => new RecipientPool(bad));
  }
});

test('private failure details retain nested RPC codes without dumping request objects', () => {
  const cause={code:-32504,details:'factory admission limit',request:{secret:'do not serialize'}};
  const error={shortMessage:'UserOperation rejected',cause};cause.cause=error;
  assert.deepEqual(failureDetails(error),{error:'UserOperation rejected',rpcCode:-32504,details:'factory admission limit'});
  assert.equal(failureDetails(new Error('timeout')).error,'timeout');
  assert.equal(failureDetails({message:'x',details:'a'.repeat(10000)}).details.length,2000);
});

test('persistent ML signing is opt-in and cannot select another algorithm', () => {
  const base = {action: 'transfer', algorithm: 'ml', dataset: 'a', run: 'r', accounts: 2, count: 2,
    workers: 1, qps: 10, startAt: 0, fundWei: '100', valueWei: '1'};
  assert.equal(validate({...base}).mlBackend, 'process');
  assert.equal(validate({...base, mlBackend: 'persistent'}).mlBackend, 'persistent');
  assert.throws(() => validate({...base, mlBackend: 'unknown'}));
  assert.throws(() => validate({...base, algorithm: 'slh', mlBackend: 'persistent'}));
});
