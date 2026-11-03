import {applyDirectReceipt} from './direct.mjs';
import {mergeTimings} from './timing.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {Worker} from 'node:worker_threads';
import {fileURLToPath} from 'node:url';
import {performance} from 'node:perf_hooks';
import {context, deployment} from '../../../erc4337-wallet/common/wallet.mjs';
import {profiles, validate, read, save, acquire, Pacer, Accounts} from './core.mjs';
import {InclusionWindow, receiptEvidence} from './inclusions.mjs';

process.title = 'bsc-pqc-load';
process.umask(0o077);
const [env, specPath] = process.argv.slice(2);
assert.match(env || '', /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/);
const spec = validate(read(specPath));
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const runtime = path.join(root, 'one-box/.runtime', env);
const directory = path.join(runtime, 'load', spec.dataset);
const runDir = path.join(directory, 'runs', spec.run);
const lock = path.join(runtime, 'load', 'active.lock');
const unresolved = path.join(runtime, 'load', 'unresolved.json');
let release, timer, durationTimer, interrupted = false;
let deadline = Infinity;
const workers = [];
let summary;
const inclusions = new InclusionWindow();
const progress = () => {
  if (!summary) return;
  const elapsed = Math.max(0.001, (Date.now() - summary.startedAt) / 1000);
  Object.assign(summary, inclusions.snapshot());
  summary.updatedAt = Date.now();
  summary.elapsedSeconds = elapsed;
  summary.acceptedPerSecond = summary.accepted / elapsed;
  summary.confirmedPerSecond = summary.confirmed / elapsed;
  save(path.join(runDir, 'progress.json'), summary);
};
try {
  release = acquire(lock, spec.action === 'reconcile');
  const ctx = await context(env, profiles[spec.algorithm]);
  const metadataPath = path.join(directory, 'dataset.json');
  const binding = {algorithm: spec.algorithm, accounts: spec.accounts, genesisHash: ctx.manifest.genesis_hash,
    chainId: ctx.chain.id, entryPoint: ctx.base.entryPoint, fundingAddress: ctx.wallet.account.address};
  if (fs.existsSync(metadataPath)) assert.deepEqual(read(metadataPath), binding, 'Dataset belongs to a different chain/config');
  else {
    assert.equal(spec.action, 'prepare', 'Prepare dataset first');
    save(metadataPath, binding);
  }
  if (spec.action === 'reconcile') {
    assert(fs.existsSync(runDir), 'Unknown run');
    const previous = read(path.join(runDir, 'spec.json'));
    assert.equal(previous.algorithm, spec.algorithm);
    assert.equal(previous.accounts, spec.accounts);
    let pending = 0, confirmed = 0, reverted = 0;
    for (let i = 0; i < previous.count; i++) {
      const file = previous.action === 'prepare'
        ? path.join(directory, 'funding', `${i}.json`)
        : path.join(runDir, 'operations', `${i}.json`);
      if (!fs.existsSync(file)) continue;
      const record = read(file);
      if (record.state === 'pending') {
        try {
          if (record.submission === 'direct') {
            applyDirectReceipt(record, await ctx.client.getTransactionReceipt({hash: record.transactionHash}));
          } else if (record.kind === 'transaction') {
            const receipt = await ctx.client.getTransactionReceipt({hash: record.hash});
            record.state = receipt.status === 'success' ? 'confirmed' : 'reverted';
            record.gasUsed = receipt.gasUsed;
            record.transactionHash = receipt.transactionHash;
            record.blockNumber = receipt.blockNumber; record.blockHash = receipt.blockHash;
          } else {
            const receipt = await ctx.bundler.getUserOperationReceipt({hash: record.hash});
            if (!receipt) { pending++; continue; }
            record.state = receipt.success && receipt.receipt.status === 'success' ? 'confirmed' : 'reverted';
            record.gasUsed = receipt.actualGasUsed;
            record.transactionHash = receipt.receipt.transactionHash;
            record.blockNumber = receipt.receipt.blockNumber; record.blockHash = receipt.receipt.blockHash;
          }
          record.reconciledAt = Date.now(); save(file, record);
        } catch { pending++; continue; }
      }
      if (record.state === 'confirmed') {
        inclusions.add(receiptEvidence(record));
        confirmed++;
        if (record.action === 'create' || record.action === 'fund') {
          const f = path.join(directory, 'accounts', `${record.index}.json`), entry = read(f);
          if (record.action === 'create' && spec.algorithm !== 'ecdsa') {
            assert(await ctx.client.getCode({address: entry.address}));
            assert.equal(await ctx.profile.checkPublicKey(ctx, entry.address), entry.publicKey);
          }
          entry[record.action === 'create' ? 'created' : 'funded'] = true; save(f, entry);
        }
      } else reverted++;
    }
    const result = {pending, confirmed, reverted, checkedAt: Date.now(), resubmitted: 0};
    save(path.join(runDir, 'reconciliation.json'), result);
    if (pending === 0 && fs.existsSync(unresolved)) {
      const marker = read(unresolved);
      if (marker.dataset === spec.dataset && marker.run === spec.run) fs.unlinkSync(unresolved);
    }
    console.log(JSON.stringify(result));
    const old = read(path.join(runDir, 'progress.json'));
    save(path.join(runDir, 'progress.json'), {...old, reconciliation: result, pending, ...inclusions.snapshot()});
  } else {
    assert(!fs.existsSync(runDir), 'Run ID exists; use reconcile, never blindly resend');
    // A durable run marker avoids scanning every private key on large datasets.
    assert(!fs.existsSync(unresolved), 'Unresolved run exists: reconcile the run in load/unresolved.json first');
    if (spec.recipientAddresses) {
      const recipients = new Set(spec.recipientAddresses.map(a => a.toLowerCase()));
      for (let i = 0; i < spec.accounts; i++) {
        const entry = read(path.join(directory, 'accounts', `${i}.json`));
        assert(entry.created && recipients.has(entry.address.toLowerCase()), 'Recipient pool must include every created sender');
      }
    }
    if (spec.algorithm !== 'ecdsa') await deployment(ctx);
    save(path.join(runDir, 'spec.json'), spec);
    summary = {run: spec.run, dataset: spec.dataset, algorithm: spec.algorithm, action: spec.action,
      submission: spec.submission, targetQps: spec.qps, total: spec.count, startedAt: Date.now(), status: 'starting',
      slhBackend: spec.slhBackend, mlBackend: spec.mlBackend, workers: spec.workers, maxInflight: Math.min(spec.maxInflight, spec.accounts, spec.count), peakInflight: 0, timings: {},
      durationSeconds: spec.durationSeconds, cancelled: 0, endReason: null,
      dispatched: 0, attempted: 0, accepted: 0, confirmed: 0, failed: 0, skipped: 0, pending: 0, inflight: 0, cacheHits: 0, cacheMisses: 0, cacheAccounts: spec.cacheAccounts};
    const stop = () => { interrupted = true; summary.endReason ??= 'signal'; summary.status = 'draining'; progress(); };
    process.on('SIGTERM', stop); process.on('SIGINT', stop);
    progress();
    save(unresolved, {dataset: spec.dataset, run: spec.run, algorithm: spec.algorithm, accounts: spec.accounts});
    const pacer = new Pacer(spec.qps), accounts = new Accounts(spec.accounts);
    let nonce = await ctx.client.getTransactionCount({address: ctx.wallet.account.address, blockTag: 'pending'});
    let nextSequence = 0;
    const poolSize = Math.min(spec.workers, summary.maxInflight);
    timer = setInterval(progress, 1000);
    const slots = await Promise.all(Array.from({length: poolSize}, () => new Promise((resolve, reject) => {
      const worker = new Worker(new URL('./worker.mjs', import.meta.url), {workerData: {spec, env, directory}});
      workers.push(worker);
      worker.once('error', reject);
      worker.once('exit', code => reject(new Error(`Worker exited before readiness: ${code}`)));
      worker.once('message', msg => msg.type === 'ready' ? resolve(worker) : reject(new Error('Worker init failed')));
    })));
    while (!interrupted && Date.now() < spec.startAt) {
      await new Promise(r => setTimeout(r, Math.min(1000, spec.startAt - Date.now())));
    }
    summary.startedAt = Date.now(); summary.startLagMs = spec.startAt ? Math.max(0, summary.startedAt - spec.startAt) : 0; summary.status = interrupted ? 'draining' : 'running'; progress();
    const expire = () => {
      if (spec.durationSeconds && performance.now() >= deadline) {
        interrupted = true; summary.endReason ??= 'duration'; summary.status = 'draining';
      }
      return interrupted;
    };
    if (spec.durationSeconds && !interrupted) {
      deadline = performance.now() + spec.durationSeconds * 1000;
      durationTimer = setTimeout(() => { expire(); progress(); }, spec.durationSeconds * 1000);
    }
    await Promise.all(slots.map((worker, slot) => new Promise((resolve, reject) => {
      const capacity = Math.floor(summary.maxInflight / poolSize) + (slot < summary.maxInflight % poolSize ? 1 : 0);
      const active = new Map();
      let cacheHits = 0, cacheMisses = 0, previousTimings = {}, assigned = 0;
      const dispatch = () => {
        while (active.size < capacity && !expire() && nextSequence < spec.count) {
          if (spec.action !== 'transfer' && assigned >= Math.ceil((spec.accounts - slot) / poolSize)) break;
          const index = accounts.take(slot, poolSize);
          assigned++;
          assert.notEqual(index, null);
          const sequence = nextSequence++;
          active.set(sequence, index);
          summary.dispatched++; summary.inflight++;
          summary.peakInflight = Math.max(summary.peakInflight, summary.inflight);
          worker.postMessage({type: 'task', index, sequence});
        }
        if (nextSequence >= spec.count) summary.endReason ??= 'count';
        if (active.size === 0) resolve();
      };
      worker.on('error', reject);
      worker.on('exit', code => reject(new Error(`Worker exited: ${code}`)));
      worker.on('message', async msg => {
        try {
          if (msg.type === 'nonce') { worker.postMessage({type: 'nonce', requestId: msg.requestId, nonce: nonce++}); return; }
          if (msg.type === 'permit') {
            const allowed = await pacer.take(deadline, expire);
            if (allowed) summary.attempted++;
            worker.postMessage({type: 'permit', requestId: msg.requestId, allowed});
            return;
          }
          if (msg.type === 'accepted') { summary.accepted++; return; }
          if (msg.type === 'done' || msg.type === 'error') {
            mergeTimings(summary.timings, msg.timings, previousTimings);
            previousTimings = msg.timings || {};
            assert(active.has(msg.sequence), 'Unknown or duplicate task reply');
            const index = active.get(msg.sequence);
            active.delete(msg.sequence);
            summary.inflight--; accounts.release(index);
            if (msg.type === 'done') {
              summary.cacheHits += msg.cacheHits - cacheHits;
              summary.cacheMisses += msg.cacheMisses - cacheMisses;
              cacheHits = msg.cacheHits; cacheMisses = msg.cacheMisses;
              if (msg.result?.state === 'cancelled') summary.cancelled++;
              else if (msg.result?.state === 'skipped') summary.skipped++;
              else { summary.confirmed++; inclusions.add(msg.result); }
            } else {
              summary.failed++; interrupted = true;
              // Full errors can contain transaction payloads; retain privately only.
              save(path.join(runDir, `error-${index}.json`), {error: msg.error, rpcCode: msg.rpcCode, details: msg.details});
            }
            dispatch();
          }
        } catch (error) { reject(error); }
      });
      dispatch();
    })));
    const journals = spec.action === 'prepare' ? path.join(directory, 'funding') : path.join(runDir, 'operations');
    summary.pending = summary.failed && fs.existsSync(journals) ? fs.readdirSync(journals).filter(f => f.endsWith('.json') && read(path.join(journals, f)).state === 'pending').length : 0;
    if (summary.pending === 0) fs.unlinkSync(unresolved);
    clearTimeout(durationTimer);
    summary.endReason = summary.failed ? 'error' : summary.endReason || 'count';
    summary.status = summary.failed ? 'failed' : summary.endReason === 'signal' ? 'stopped' : 'complete';
    progress(); console.log(JSON.stringify(summary));
    if (summary.failed) process.exitCode = 1;
  }
} catch (e) {
  if (summary) { summary.status = 'failed'; summary.endReason = 'error'; summary.failed++; progress(); }
  console.error(e.shortMessage || e.message);
  process.exitCode = 1;
} finally {
  clearInterval(timer);
  clearTimeout(durationTimer);
  await Promise.all(workers.map(w => w.terminate()));
  release?.();
}
