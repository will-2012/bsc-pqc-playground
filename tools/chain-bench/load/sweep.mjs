// Small real-chain capacity comparison. Creates a new private dataset; never resets the chain.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {context} from '../../../erc4337-wallet/common/wallet.mjs';
import {profiles, read, save} from './core.mjs';
const [env, algorithm = 'ecdsa', accountArg = '32', countArg = '96', casesArg] = process.argv.slice(2);
assert.match(env || '', /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/);
assert(Object.hasOwn(profiles, algorithm));
const accounts = Number(accountArg), count = Number(countArg);
assert(Number.isSafeInteger(accounts) && accounts >= 4 && accounts <= 1000);
assert(Number.isSafeInteger(count) && count >= accounts && count <= 10000);
const cases = casesArg ? casesArg.split(',').map(value => {
  assert.match(value, /^\d+:\d+$/);return value.split(':').map(Number);
}) : [[1,1],[4,4],...(accounts > 4 ? [[4,Math.min(32,accounts)]] : [])];
assert(cases.length > 0 && cases.length <= 16);
assert.equal(new Set(cases.map(x=>x.join(':'))).size,cases.length);
for(const [workers,inflight] of cases)assert(workers >= 1 && workers <= 64 && inflight >= 1 && inflight <= accounts);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const runtime = path.join(root, 'one-box/.runtime', env, 'load');
const sources = ['run.mjs', 'worker.mjs', 'transactions.mjs', 'core.mjs', 'timing.mjs', 'signer/client.mjs', 'signer/slh.c'];
const snapshot = () => Object.fromEntries(sources.map(f => [f, createHash('sha256').update(fs.readFileSync(new URL(f, import.meta.url))).digest('hex')]));
const sourceHashes = snapshot();
const dataset = `sweep-${algorithm}-${Date.now().toString(36)}`;
const base = {algorithm, dataset, accounts, qps: 10000, workers: 4, maxInflight: 16,
  slhBackend: 'openssl', cacheAccounts: accounts, startAt: 0, fundWei: '100000000000000000', valueWei: '1'};
async function run(action, name, patch = {}) {
  const spec = {...base, action, run: name, count: action === 'transfer' ? count : accounts, ...patch};
  const file = path.join(runtime, dataset, `${name}.json`); save(file, spec);
  const log = fs.openSync(file + '.log', 'w', 0o600);
  const child = spawn(process.execPath, [path.join(root, 'tools/chain-bench/load/run.mjs'), env, file], {stdio: ['ignore', log, log]});
  fs.closeSync(log);
  const code = await new Promise((resolve, reject) => { child.on('exit', resolve); child.on('error', reject); });
  assert.equal(code, 0, 'Sweep failed; inspect private dataset logs');
  const result = read(path.join(runtime, dataset, 'runs', name, 'progress.json'));
  assert.equal(result.failed, 0); assert.equal(result.confirmed, spec.count);
  return result;
}
await run('prepare', 'prepare');
assert.equal(fs.readdirSync(path.join(runtime, dataset, 'accounts')).filter(f => f.endsWith('.json')).length, accounts);
// Setup is not the capacity measurement: respect Bundler factory admission limits.
await run('create', 'create', algorithm === 'ecdsa' ? {} : {workers: 1, maxInflight: 1, qps: 2});
const ctx = await context(env, profiles[algorithm]);
const recipient = ctx.manifest.accounts[1].address;
const rows = [];
for (const [workers, maxInflight] of cases) {
  const before = await ctx.client.getBalance({address: recipient});
  const result = await run('transfer', `w${workers}-i${maxInflight}`, {workers, maxInflight});
  assert.equal(await ctx.client.getBalance({address: recipient}) - before, BigInt(count));
  const row = {workers, maxInflight, confirmed: result.confirmed, elapsedSeconds: result.elapsedSeconds,
    receiptTPS: result.confirmedPerSecond, peakInflight: result.peakInflight, cacheHits: result.cacheHits,
    cacheMisses: result.cacheMisses, timings: result.timings};
  rows.push(row); console.log(JSON.stringify({algorithm, ...row}));
}

assert.deepEqual(snapshot(), sourceHashes, 'Bench sources changed during measurement');
const result = {algorithm, accounts, count, checkedAt: new Date().toISOString(), platform: process.platform, arch: process.arch,
  cpu: os.cpus()[0].model, logicalCpus: os.cpus().length, node: process.version,
  sources: sourceHashes, rows};
save(path.join(runtime, dataset, 'comparison.json'), result);
console.log(JSON.stringify(result));
