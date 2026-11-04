import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {randomInt} from 'node:crypto';

export const profiles = {ecdsa: 'mldsa-v4', ml: 'mldsa-v4', slh: 'slh-dsa-v7'};
export function validate(s) {
  s.submission ??= 'bundler';
  assert(['bundler', 'direct'].includes(s.submission), 'Invalid submission mode');
  if (s.submission === 'direct') {
    assert(['transfer', 'reconcile'].includes(s.action) && ['ml', 'slh'].includes(s.algorithm), 'Direct mode supports existing PQ transfers only');
    for (const k of ['callGasLimit', 'verificationGasLimit', 'preVerificationGas', 'outerGasLimit'])
      assert(typeof s.directGas?.[k] === 'string' && /^\d+$/.test(s.directGas[k]) && BigInt(s.directGas[k]) > 0n && BigInt(s.directGas[k]) <= 16777216n, `Invalid direct gas: ${k}`);
    assert(BigInt(s.directGas.outerGasLimit) > BigInt(s.directGas.callGasLimit) + BigInt(s.directGas.verificationGasLimit) + BigInt(s.directGas.preVerificationGas), 'Outer gas needs execution headroom');
  }

  if (s.recipientAddresses !== undefined) {
    assert(s.action === 'transfer', 'Recipient pool applies only to transfers');
    new RecipientPool(s.recipientAddresses);
  }
  assert(['prepare', 'create', 'transfer', 'reconcile'].includes(s.action), 'Invalid action');
  assert(Object.hasOwn(profiles, s.algorithm), 'Use ecdsa, ml or slh');
  for (const k of ['dataset', 'run']) assert.match(s[k], /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/);
  for (const k of ['accounts', 'count', 'workers']) assert(Number.isSafeInteger(s[k]) && s[k] > 0, `Invalid ${k}`);
  s.mlBackend ??= 'process';
  assert(['process', 'persistent'].includes(s.mlBackend), 'Invalid ML backend');
  assert(s.mlBackend === 'process' || s.algorithm === 'ml', 'Persistent ML backend requires ML');
  s.slhBackend ??= 'noble';
  assert(['noble', 'openssl', 'avx2', 'avx512'].includes(s.slhBackend), 'Invalid SLH backend');
  s.cacheAccounts ??= 64;
  assert(Number.isSafeInteger(s.cacheAccounts) && s.cacheAccounts >= 0 && s.cacheAccounts <= 4096, 'Invalid cache capacity');
  assert(s.workers <= 64, 'At most 64 workers per client');
  s.maxInflight ??= s.workers;
  assert(Number.isSafeInteger(s.maxInflight) && s.maxInflight > 0 && s.maxInflight <= 4096, 'Invalid max inflight');
  assert(Number.isFinite(s.qps) && s.qps > 0, 'Invalid QPS');
  assert(Number.isFinite(s.startAt) && s.startAt >= 0, 'Invalid start time');
  s.durationSeconds ??= 0;
  assert(Number.isFinite(s.durationSeconds) && s.durationSeconds >= 0 && s.durationSeconds <= 86400, 'Invalid duration');
  assert(!s.durationSeconds || ['create', 'transfer'].includes(s.action), 'Duration applies only to create/transfer');
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
// No catch-up burst. At >1000 QPS, admit ceil(QPS / 1000) operations per batch.
export class Pacer {
  constructor(qps, now = () => performance.now(), sleep = ms => new Promise(r => setTimeout(r, ms))) {
    this.batch = Math.max(1, Math.ceil(qps / 1000));
    this.interval = 1000 * this.batch / qps; this.remaining = 0;
    this.now = now; this.sleep = sleep; this.last = -Infinity;
    this.queue = Promise.resolve();
  }
  take(deadline = Infinity, cancelled = () => false) {
    const next = this.queue.then(async () => {
      while (true) {
        const now = this.now();
        if (cancelled() || now >= deadline) return false;
        if (this.remaining > 0 && now < this.last + this.interval) { this.remaining--; return true; }
        const wait = this.last + this.interval - now;
        if (wait <= 0) break;
        // Timers can wake early; recheck the monotonic clock before granting a permit.
        await this.sleep(Math.max(1, Math.ceil(Math.min(wait, deadline - now))));
      }
      this.last = this.now(); this.remaining = this.batch - 1;
      return true;
    });
    this.queue = next.catch(() => {});
    return next;
  }
}
export class Accounts {
  constructor(count) { this.count = count; this.next = new Map(); this.busy = new Set(); }
  take(owner = 0, owners = 1) {
    assert(Number.isInteger(owners) && owners > 0 && Number.isInteger(owner) && owner >= 0 && owner < owners);
    const count = Math.ceil((this.count - owner) / owners);
    for (let i = 0; i < count; i++) {
      const cursor = this.next.get(owner) || 0;
      this.next.set(owner, (cursor + 1) % count);
      const index = owner + (cursor % count) * owners;
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

export class RecipientPool {
  constructor(addresses) {
    assert(Array.isArray(addresses) && addresses.length >= 2, 'Need at least two recipients');
    assert(addresses.every(a => typeof a === 'string' && /^0x[0-9a-fA-F]{40}$/.test(a) && !/^0x0{40}$/.test(a)), 'Invalid recipient address');
    this.addresses = addresses;
    this.indices = new Map(addresses.map((a, i) => [a.toLowerCase(), i]));
    assert.equal(this.indices.size, addresses.length, 'Duplicate recipient');
  }
  pick(sender, random = randomInt) {
    const self = this.indices.get(sender.toLowerCase());
    assert(self !== undefined, 'Sender missing from recipient pool');
    const i = random(this.addresses.length - 1);
    return this.addresses[i >= self ? i + 1 : i];
  }
}

// Keep RPC causes in private run logs; the console still receives a short message.
export function failureDetails(error) {
  const result = {error: String(error?.shortMessage || error?.message || error).slice(0, 1000)};
  const seen = new Set(), details = [];
  for (let e = error; e && typeof e === 'object' && !seen.has(e) && seen.size < 8; e = e.cause) {
    seen.add(e);
    if (typeof e.code === 'number') result.rpcCode = e.code;
    if (typeof e.details === 'string') {
      // Large RPC payloads can otherwise truncate the actual cause at the end.
      const compact = e.details.replace(/0x[0-9a-fA-F]{80,}/g, '[payload]');
      const detail = compact.length > 2000 ? compact.slice(0, 500) + '\n…\n' + compact.slice(-1497) : compact;
      if (!details.includes(detail)) details.push(detail);
    }
  }
  if (details.length) result.details = details.join('\n').slice(0, 4000);
  return result;
}
