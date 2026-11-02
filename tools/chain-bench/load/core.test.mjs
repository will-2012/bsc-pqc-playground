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
