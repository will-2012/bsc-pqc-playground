import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {Worker} from 'node:worker_threads';
import {fileURLToPath} from 'node:url';
import {context, deployment} from '../../../erc4337-wallet/common/wallet.mjs';
import {profiles, validate, read, save, acquire, Pacer, Accounts} from './core.mjs';

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
let release, timer, interrupted = false;
const workers = [];
let summary;
const progress = () => {
  if (!summary) return;
  const elapsed = Math.max(0.001, (Date.now() - summary.startedAt) / 1000);
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
          if (record.kind === 'transaction') {
            const receipt = await ctx.client.getTransactionReceipt({hash: record.hash});
            record.state = receipt.status === 'success' ? 'confirmed' : 'reverted';
            record.gasUsed = receipt.gasUsed;
          } else {
            const receipt = await ctx.bundler.getUserOperationReceipt({hash: record.hash});
            if (!receipt) { pending++; continue; }
            record.state = receipt.success && receipt.receipt.status === 'success' ? 'confirmed' : 'reverted';
            record.gasUsed = receipt.actualGasUsed;
          }
          record.reconciledAt = Date.now(); save(file, record);
        } catch { pending++; continue; }
      }
      if (record.state === 'confirmed') {
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
    save(path.join(runDir, 'progress.json'), {...old, reconciliation: result, pending});
  } else {
    assert(!fs.existsSync(runDir), 'Run ID exists; use reconcile, never blindly resend');
    // A durable run marker avoids scanning every private key on large datasets.
    assert(!fs.existsSync(unresolved), 'Unresolved run exists: reconcile the run in load/unresolved.json first');
    if (spec.algorithm !== 'ecdsa') await deployment(ctx);
    save(path.join(runDir, 'spec.json'), spec);
    summary = {run: spec.run, dataset: spec.dataset, algorithm: spec.algorithm, action: spec.action,
      targetQps: spec.qps, total: spec.count, startedAt: Date.now(), status: 'starting',
      dispatched: 0, attempted: 0, accepted: 0, confirmed: 0, failed: 0, skipped: 0, pending: 0, inflight: 0, cacheHits: 0, cacheMisses: 0, cacheAccounts: spec.cacheAccounts};
    const stop = () => { interrupted = true; summary.status = 'draining'; progress(); };
    process.on('SIGTERM', stop); process.on('SIGINT', stop);
    progress();
    save(unresolved, {dataset: spec.dataset, run: spec.run, algorithm: spec.algorithm, accounts: spec.accounts});
    const pacer = new Pacer(spec.qps), accounts = new Accounts(spec.accounts);
    let nonce = await ctx.client.getTransactionCount({address: ctx.wallet.account.address, blockTag: 'pending'});
    let nextSequence = 0;
    const poolSize = Math.min(spec.workers, spec.accounts, spec.count);
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
    await Promise.all(slots.map(worker => new Promise((resolve, reject) => {
      let activeIndex, cacheHits = 0, cacheMisses = 0;
      const dispatch = () => {
        if (interrupted || nextSequence >= spec.count) { resolve(); return; }
        activeIndex = accounts.take();
        assert.notEqual(activeIndex, null);
        const sequence = nextSequence++;
        summary.dispatched++; summary.inflight++;
        worker.postMessage({type: 'task', index: activeIndex, sequence});
      };
      worker.on('error', reject);
      worker.on('exit', code => reject(new Error(`Worker exited: ${code}`)));
      worker.on('message', async msg => {
        if (msg.type === 'nonce') { worker.postMessage({type: 'nonce', nonce: nonce++}); return; }
        if (msg.type === 'permit') {
          try {
            await pacer.take(); summary.attempted++;
            worker.postMessage({type: 'permit'});
          } catch (error) { reject(error); }
          return;
        }
        if (msg.type === 'accepted') { summary.accepted++; return; }
        if (msg.type === 'done' || msg.type === 'error') {
          summary.inflight--; accounts.release(activeIndex);
          if (msg.type === 'done') {
            summary.cacheHits += msg.cacheHits - cacheHits;
            summary.cacheMisses += msg.cacheMisses - cacheMisses;
            cacheHits = msg.cacheHits; cacheMisses = msg.cacheMisses;
            if (msg.result?.state === 'skipped') summary.skipped++;
            else summary.confirmed++;
          }
          else {
            summary.failed++; interrupted = true;
            // Full errors can contain transaction payloads; retain privately only.
            save(path.join(runDir, `error-${activeIndex}.json`), {error: msg.error});
          }
          dispatch();
        }
      });
      dispatch();
    })));
    const journals = spec.action === 'prepare' ? path.join(directory, 'funding') : path.join(runDir, 'operations');
    summary.pending = summary.failed && fs.existsSync(journals) ? fs.readdirSync(journals).filter(f => f.endsWith('.json') && read(path.join(journals, f)).state === 'pending').length : 0;
    if (summary.pending === 0) fs.unlinkSync(unresolved);
    summary.status = summary.failed ? 'failed' : interrupted ? 'stopped' : 'complete';
    progress(); console.log(JSON.stringify(summary));
    if (summary.failed) process.exitCode = 1;
  }
} catch (e) {
  if (summary) { summary.status = 'failed'; summary.failed++; progress(); }
  console.error(e.shortMessage || e.message);
  process.exitCode = 1;
} finally {
  clearInterval(timer);
  await Promise.all(workers.map(w => w.terminate()));
  release?.();
}
