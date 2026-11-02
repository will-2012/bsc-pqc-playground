import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {performance} from 'node:perf_hooks';

export const profiles = {ecdsa: 'mldsa-v4', ml: 'mldsa-v4', slh: 'slh-dsa-v7'};
export function validate(s) {
  assert(['prepare', 'create', 'transfer', 'reconcile'].includes(s.action), 'Invalid action');
  assert(Object.hasOwn(profiles, s.algorithm), 'Use ecdsa, ml or slh');
  for (const k of ['dataset', 'run']) assert.match(s[k], /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/);
  for (const k of ['accounts', 'count', 'workers']) assert(Number.isSafeInteger(s[k]) && s[k] > 0, `Invalid ${k}`);
  s.cacheAccounts ??= 64;
  assert(Number.isSafeInteger(s.cacheAccounts) && s.cacheAccounts >= 0 && s.cacheAccounts <= 4096, 'Invalid cache capacity');
  assert(s.workers <= 64, 'At most 64 workers per client');
  assert(Number.isFinite(s.qps) && s.qps > 0, 'Invalid QPS');
  assert(Number.isFinite(s.startAt) && s.startAt >= 0, 'Invalid start time');
  assert(/^\d+$/.test(s.fundWei) && BigInt(s.fundWei) > 0n, 'Invalid funding amount');
  assert(/^\d+$/.test(s.valueWei) && BigInt(s.valueWei) > 0n, 'Invalid transfer amount');
  if (s.action === 'prepare' || s.action === 'create') assert(s.count === s.accounts, 'Create/prepare covers entire dataset');
  return s;
}
export function read(p) { return JSON.parse(fs.readFileSync(p, 'utf8')); }
export function save(p, value) {
  fs.mkdirSync(path.dirname(p), {recursive: true, mode: 0o700});
  const tmp = p + '.tmp';
  const fd = fs.openSync(tmp, 'w', 0o600);
  try {
    fs.writeFileSync(fd, JSON.stringify(value, (_, v) => typeof v === 'bigint' ? v.toString() : v) + '\n');
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, p);
}
export function acquire(p, recover = false) {
  fs.mkdirSync(path.dirname(p), {recursive: true, mode: 0o700});
  if (recover && fs.existsSync(p)) {
    const pid = Number(fs.readFileSync(p, 'utf8').trim());
    assert(Number.isSafeInteger(pid) && pid > 0, 'Invalid lock; inspect privately');
    try { process.kill(pid, 0); }
    catch (e) { if (e.code === 'ESRCH') fs.unlinkSync(p); else throw e; }
  }
  const fd = fs.openSync(p, 'wx', 0o600);
  fs.writeFileSync(fd, `${process.pid}\n`);
  fs.closeSync(fd);
  return () => fs.unlinkSync(p);
}
// No catch-up burst: delays lower achieved throughput rather than accumulate tokens.
export class Pacer {
  constructor(qps, now = () => performance.now(), sleep = ms => new Promise(r => setTimeout(r, ms))) {
    this.interval = 1000 / qps; this.now = now; this.sleep = sleep; this.last = -Infinity;
    this.queue = Promise.resolve();
  }
  take() {
    const next = this.queue.then(async () => {
      const wait = this.last + this.interval - this.now();
      if (wait > 0) await this.sleep(wait);
      this.last = this.now();
    });
    this.queue = next.catch(() => {});
    return next;
  }
}
export class Accounts {
  constructor(count) { this.count = count; this.next = 0; this.busy = new Set(); }
  take() {
    for (let i = 0; i < this.count; i++) {
      const index = this.next++ % this.count;
      if (!this.busy.has(index)) { this.busy.add(index); return index; }
    }
    return null;
  }
  release(index) { assert(this.busy.delete(index)); }
}

// Per-worker LRU: keep only immutable signing context; never cache nonces or receipts.
export class AccountCache {
  constructor(capacity) { this.capacity = capacity; this.items = new Map(); this.hits = 0; this.misses = 0; }
  get(key) {
    if (!this.items.has(key)) { this.misses++; return undefined; }
    const value = this.items.get(key);
    this.items.delete(key); this.items.set(key, value); this.hits++;
    return value;
  }
  set(key, value) {
    if (!this.capacity) return;
    this.items.delete(key); this.items.set(key, value);
    if (this.items.size > this.capacity) this.items.delete(this.items.keys().next().value);
  }
}
