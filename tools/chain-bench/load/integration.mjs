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
const sources = ['tools/chain-bench/load/core.mjs', 'tools/chain-bench/load/run.mjs',
  'tools/chain-bench/load/worker.mjs', 'erc4337-wallet/common/wallet.mjs'];
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
for (const algorithm of Object.keys(profiles)) {
  const spec = {algorithm, dataset: `verify-${stamp}-${algorithm}`, run: 'prepare', action: 'prepare',
    accounts: 3, count: 3, workers: 2, qps: 5, startAt: 0, fundWei: '100000000000000000', valueWei: '1'};
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
  const beforeCache = await ctx.client.getBalance({address: recipient});
  await execute({...spec, action: 'transfer', run: 'cache', count: 6, workers: 1, cacheAccounts: 3});
  const cacheResult = read(path.join(dir, 'runs/cache/progress.json'));
  assert.equal(cacheResult.cacheHits, 3); assert.equal(cacheResult.cacheMisses, 3);
  assert.equal(await ctx.client.getBalance({address: recipient}) - beforeCache, 6n);
  output.cacheRows.push({algorithm, cacheHits: cacheResult.cacheHits, cacheMisses: cacheResult.cacheMisses,
    confirmed: cacheResult.confirmed, elapsedSeconds: cacheResult.elapsedSeconds});
  output.checks.push(`${algorithm}: bounded cache reused signing context; fresh nonces and balances verified`);
  console.log(`${algorithm}: PASS`);
}
save(path.join(runtime, `verification-${stamp}.json`), output);
console.log(JSON.stringify(output));
