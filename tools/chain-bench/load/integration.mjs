// Requires an already running PQ devnet and Bundler. Writes only private runtime files.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {context} from '../../../erc4337-wallet/common/wallet.mjs';
import {profiles, read, save} from './core.mjs';

const env = process.argv[2];
assert.match(env || '', /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const runtime = path.join(root, 'one-box/.runtime', env, 'load');
const stamp = Date.now().toString(36);
const sources = ['tools/chain-bench/load/core.mjs', 'tools/chain-bench/load/run.mjs', 'tools/chain-bench/load/inclusions.mjs',
  'tools/chain-bench/load/worker.mjs', 'erc4337-wallet/common/wallet.mjs', 'tools/chain-bench/load/timing.mjs',
  'tools/chain-bench/load/signer/client.mjs', 'tools/chain-bench/load/signer/slh.c'];
const output = {checkedAt: new Date().toISOString(), platform: process.platform, arch: process.arch, node: process.version,
  sourceSha256: Object.fromEntries(sources.map(file => [file, createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')])),
  rows: [], cacheRows: [], checks: []};
async function execute(spec, expectFailure = false, stopAfterFirst = false) {
  const file = path.join(runtime, 'verification', `${spec.dataset}-${spec.run}-${spec.action}.json`);
  save(file, spec);
  const log = fs.openSync(file + '.log', 'w', 0o600);
  const child = spawn(process.execPath, [path.join(root, 'tools/chain-bench/load/run.mjs'), env, file], {stdio: ['ignore', log, log]});
  fs.closeSync(log);
  let timer;
  if (stopAfterFirst) timer = setInterval(() => {
    const progress = path.join(runtime, spec.dataset, 'runs', spec.run, 'progress.json');
    if (!fs.existsSync(progress)) return;
    const state = read(progress);
    if (stopAfterFirst === 'before-start' ? state.status === 'starting' : state.accepted > 0) {
      child.kill('SIGTERM'); clearInterval(timer);
    }
  }, 50);
  const code = await new Promise((resolve, reject) => { child.on('exit', resolve); child.on('error', reject); });
  clearInterval(timer);
  assert.equal(code === 0, !expectFailure, 'Runner failed; inspect private verification logs');
}
// Uneven worker partitions must prepare every account exactly once.
const uneven = {algorithm: 'ecdsa', dataset: `verify-${stamp}-uneven`, run: 'prepare', action: 'prepare',
  accounts: 7, count: 7, workers: 3, maxInflight: 5, qps: 100, startAt: 0,
  fundWei: '100000000000000000', valueWei: '1'};
await execute(uneven);
const unevenFiles = fs.readdirSync(path.join(runtime, uneven.dataset, 'accounts')).filter(f => f.endsWith('.json'));
assert.equal(unevenFiles.length, 7);
assert.equal(new Set(unevenFiles.map(f => read(path.join(runtime, uneven.dataset, 'accounts', f)).address)).size, 7);
output.checks.push('uneven worker partitions: every setup account generated once');
for (const algorithm of Object.keys(profiles)) {
  const spec = {algorithm, dataset: `verify-${stamp}-${algorithm}`, run: 'prepare', action: 'prepare',
    accounts: 3, count: 3, workers: 1, maxInflight: 3, qps: 5, startAt: 0, fundWei: '100000000000000000', valueWei: '1'};
  const dir = path.join(runtime, spec.dataset);
  await execute(spec);
  const fingerprint = () => createHash('sha256').update(JSON.stringify(Array.from({length: 3}, (_, i) => {
    const a = read(path.join(dir, 'accounts', `${i}.json`));
    assert.equal(fs.statSync(path.join(dir, 'accounts', `${i}.json`)).mode & 0o777, 0o600);
    return [a.address, a.publicKey, a.secretKey];
  }))).digest('hex');
  const keys = fingerprint();
  if (algorithm !== 'ecdsa') {
    const funding = path.join(dir, 'funding/0.json');
    save(funding, {...read(funding), state: 'pending'});
    const account = path.join(dir, 'accounts/0.json');
    save(account, {...read(account), funded: false});
    save(path.join(runtime, 'unresolved.json'), {dataset: spec.dataset, run: 'prepare'});
    await execute({...spec, action: 'reconcile', run: 'prepare'});
    assert.equal(read(account).funded, true);
  }
  await execute({...spec, action: 'create', run: 'create'});
  const ctx = await context(env, profiles[algorithm]);
  const recipient = ctx.manifest.accounts[1].address;
  const before = await ctx.client.getBalance({address: recipient});
  await execute({...spec, action: 'transfer', run: 'transfer', count: 6});
  assert.equal(await ctx.client.getBalance({address: recipient}) - before, 6n, 'Recipient value differs');
  assert.equal(fingerprint(), keys, 'Identities changed');
  await execute({...spec, action: 'transfer', run: 'transfer', count: 6}, true);
  assert.equal(await ctx.client.getBalance({address: recipient}) - before, 6n, 'Duplicate run sent funds');
  const journal = path.join(dir, 'runs/transfer/operations/0.json');
  save(journal, {...read(journal), state: 'pending'});
  save(path.join(runtime, 'unresolved.json'), {dataset: spec.dataset, run: 'transfer'});
  await execute({...spec, action: 'transfer', run: 'blocked', count: 6}, true);
  await execute({...spec, action: 'reconcile', run: 'transfer'});
  const reconciled = read(path.join(dir, 'runs/transfer/reconciliation.json'));
  assert.deepEqual([reconciled.confirmed, reconciled.pending, reconciled.reverted, reconciled.resubmitted], [6, 0, 0, 0]);
  await execute({...spec, action: 'create', run: 'create-again'});
  const skipped = read(path.join(dir, 'runs/create-again/progress.json'));
  assert.deepEqual([skipped.skipped, skipped.accepted], [3, 0]);
  for (const action of ['create', 'transfer']) {
    const s = read(path.join(dir, `runs/${action}/progress.json`));
    assert.equal(s.confirmed, action === 'create' ? 3 : 6);
    assert.equal(s.failed, 0); assert.equal(s.pending, 0);
    assert.equal(s.inclusionTruncated, false);
    assert.equal(s.inclusions.reduce((sum, row) => sum + row.count, 0), s.confirmed);
    for (const row of s.inclusions) {
      const block = await ctx.client.getBlock({blockHash: row.blockHash});
      assert.equal(Number(block.number), row.blockNumber);
      assert.equal(new Set(row.transactions).size, row.transactions.length);
      for (const hash of row.transactions) assert.ok(block.transactions.includes(hash), 'Receipt transaction missing from block');
      assert.equal(row.userOps, algorithm === 'ecdsa' ? 0 : row.count);
    }
    output.rows.push({algorithm, action, count: s.confirmed, elapsedSeconds: s.elapsedSeconds,
      cacheHits: s.cacheHits, cacheMisses: s.cacheMisses, targetQps: s.targetQps, acceptedPerSecond: s.acceptedPerSecond, confirmedPerSecond: s.confirmedPerSecond});
  }
  output.checks.push(`${algorithm}: independent persistent keys; recipient balance; duplicate-run rejection; pending receipt recovery without resending; skip existing accounts`);
  if (algorithm === 'ecdsa') {
    const beforeStop = await ctx.client.getBalance({address: recipient});
    await execute({...spec, action: 'transfer', run: 'stop', count: 100, qps: 1}, false, true);
    const stopped = read(path.join(dir, 'runs/stop/progress.json'));
    assert.equal(stopped.status, 'stopped');
    assert(stopped.confirmed > 0 && stopped.confirmed < 100);
    assert.equal(stopped.pending, 0); assert.equal(stopped.inflight, 0);
    assert.equal(await ctx.client.getBalance({address: recipient}) - beforeStop, BigInt(stopped.confirmed));
    await execute({...spec, action: 'reconcile', run: 'stop'});
    await execute({...spec, action: 'transfer', run: 'stop-before-start', count: 6, startAt: Date.now() + 60000}, false, 'before-start');
    const earlyStop = read(path.join(dir, 'runs/stop-before-start/progress.json'));
    assert.equal(earlyStop.status, 'stopped'); assert.equal(earlyStop.accepted, 0);
    assert.equal(earlyStop.dispatched, 0);
    output.checks.push('scheduled start cancellation: zero dispatched operations');
    output.checks.push('graceful stop: dispatch halted, in-flight drained, balance matched, reconcile did not resend');
  }
  if (algorithm === 'ecdsa') {
    await execute({...spec, action: 'transfer', run: 'serial', count: 12, qps: 50, workers: 1, maxInflight: 1});
    const serial = read(path.join(dir, 'runs/serial/progress.json'));
    assert.equal(serial.confirmed, 12); assert.equal(serial.failed, 0);
    output.rows.push({algorithm, action: 'serial', count: 12, workers: 1, maxInflight: 1,
      peakInflight: serial.peakInflight, elapsedSeconds: serial.elapsedSeconds});
  }
  const beforePipeline = await ctx.client.getBalance({address: recipient});
  await execute({...spec, action: 'transfer', run: 'pipeline', count: 12, qps: 50, workers: 1, maxInflight: 3});
  const pipeline = read(path.join(dir, 'runs/pipeline/progress.json'));
  assert.equal(pipeline.timings.sign.count, 12);
  assert.equal(pipeline.timings.receipt.count, 12);
  assert.equal(pipeline.confirmed, 12); assert.equal(pipeline.failed, 0);
  assert.equal(pipeline.peakInflight, 3); assert.equal(pipeline.inflight, 0);
  assert.equal(await ctx.client.getBalance({address: recipient}) - beforePipeline, 12n);
  const nonces = new Map();
  for (let i = 0; i < 12; i++) {
    const record = read(path.join(dir, 'runs/pipeline/operations', `${i}.json`));
    const nonce = algorithm === 'ecdsa'
      ? (await ctx.client.getTransaction({hash: record.hash})).nonce : record.signed.nonce;
    const seen = nonces.get(record.index) || new Set();
    assert(!seen.has(String(nonce)), 'Account nonce reused');
    seen.add(String(nonce)); nonces.set(record.index, seen);
  }
  output.checks.push(`${algorithm}: one worker, three in-flight accounts, unique nonces and recipient balance verified`);
  output.rows.push({algorithm, action: 'pipeline', count: 12, workers: 1, maxInflight: 3,
    peakInflight: pipeline.peakInflight, elapsedSeconds: pipeline.elapsedSeconds});
  if (algorithm === 'slh') {
    const balance = await ctx.client.getBalance({address: recipient});
    await execute({...spec, action: 'transfer', run: 'native', count: 12, qps: 50,
      workers: 2, maxInflight: 3, slhBackend: 'openssl'});
    const native = read(path.join(dir, 'runs/native/progress.json'));
    assert.equal(native.confirmed, 12); assert.equal(native.failed, 0);
    assert.equal(native.timings.sign.count, 12);
    assert.equal(await ctx.client.getBalance({address: recipient}) - balance, 12n);
    output.rows.push({algorithm, action: 'native', count: 12, workers: 2, maxInflight: 3,
      elapsedSeconds: native.elapsedSeconds, timings: native.timings});
    output.checks.push('SLH native signer: existing keys, independent on-chain verifier, concurrent transfers and balance verified');
  }
  const beforeCache = await ctx.client.getBalance({address: recipient});
  await execute({...spec, action: 'transfer', run: 'cache', count: 6, workers: 1, maxInflight: 1, cacheAccounts: 3});
  const cacheResult = read(path.join(dir, 'runs/cache/progress.json'));
  assert.equal(cacheResult.cacheHits, 3); assert.equal(cacheResult.cacheMisses, 3);
  assert.equal(await ctx.client.getBalance({address: recipient}) - beforeCache, 6n);
  output.cacheRows.push({algorithm, cacheHits: cacheResult.cacheHits, cacheMisses: cacheResult.cacheMisses,
    confirmed: cacheResult.confirmed, elapsedSeconds: cacheResult.elapsedSeconds});
  const beforeTimed = await ctx.client.getBalance({address: recipient});
  await execute({...spec, action: 'transfer', run: 'timed', count: 100, qps: 20, durationSeconds: 2, workers: 1, maxInflight: 3});
  const timed = read(path.join(dir, 'runs/timed/progress.json'));
  assert.equal(timed.status, 'complete'); assert.equal(timed.endReason, 'duration');
  assert.equal(timed.failed, 0); assert.equal(timed.pending, 0); assert.equal(timed.inflight, 0);
  assert.ok(timed.elapsedSeconds >= 2 && timed.confirmed < 100);
  assert.equal(timed.dispatched, timed.confirmed + timed.cancelled);
  assert.equal(await ctx.client.getBalance({address: recipient}) - beforeTimed, BigInt(timed.confirmed));
  assert.ok(!fs.existsSync(path.join(runtime, 'unresolved.json')));
  output.checks.push(`${algorithm}: timed dispatch stopped, unsent work cancelled, receipts drained and balance matched`);
  output.checks.push(`${algorithm}: bounded cache reused signing context; fresh nonces and balances verified`);
  console.log(`${algorithm}: PASS`);
}
const partial = {algorithm: 'ecdsa', dataset: `verify-${stamp}-timed-create`, run: 'prepare', action: 'prepare',
  accounts: 6, count: 6, workers: 1, maxInflight: 3, qps: 1, startAt: 0, fundWei: '100000000000000000', valueWei: '1'};
await execute(partial);
await execute({...partial, action: 'create', run: 'limited', durationSeconds: 1.5});
const created = read(path.join(runtime, partial.dataset, 'runs/limited/progress.json'));
assert.equal(created.endReason, 'duration'); assert.equal(created.failed, 0); assert.equal(created.pending, 0);
assert.ok(created.confirmed < 6);
await execute({...partial, action: 'create', run: 'remaining', qps: 10});
const remaining = read(path.join(runtime, partial.dataset, 'runs/remaining/progress.json'));
assert.equal(remaining.confirmed + created.confirmed, 6);
assert.equal(remaining.skipped, created.confirmed);
assert.equal(remaining.failed, 0); assert.equal(remaining.pending, 0);
output.checks.push('timed ECDSA creation: no faucet nonce gap; next run completes remaining accounts');
save(path.join(runtime, `verification-${stamp}.json`), output);
console.log(JSON.stringify(output));
