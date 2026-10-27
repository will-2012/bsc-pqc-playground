import assert from 'node:assert/strict';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {parseArgs} from 'node:util';
import {context, deployment, createAccount, transfer, write} from '../../erc4337-wallet/wallet.mjs';
import {DEFAULT_PROFILE} from '../../erc4337-wallet/profiles/index.mjs';

try {
  const {values, positionals} = parseArgs({allowPositionals: true, options: {profile: {type: 'string', default: DEFAULT_PROFILE}}});
  assert(positionals.length <= 2, 'Expected environment and account count only');
  const [name = 'devnet', countText = '3'] = positionals;
  const count = Number(countText);
  assert(Number.isInteger(count) && count >= 1 && count <= 100, 'Count must be 1..100');
  const ctx = await context(name, values.profile);
  await deployment(ctx);
  const recipient = ctx.manifest.accounts[1].address;
  const rows = [];
  for (let i = 0; i < count; i++) {
    console.log(`PQ account ${i + 1}/${count}: creating`);
    const start = performance.now();
    const created = await createAccount(ctx);
    const createMs = performance.now() - start;
    console.log(`PQ account ${i + 1}/${count}: transferring 0.01 BNB`);
    const sent = performance.now();
    const transferred = await transfer(ctx, created.id, recipient, 10_000_000_000_000_000n);
    rows.push({createMs, transferMs: performance.now() - sent, created, transferred});
  }
  const result = {checkedAt: new Date().toISOString(), chainId: ctx.chain.id, genesisHash: ctx.manifest.genesis_hash,
    profile: ctx.profile.id, algorithm: ctx.deployment.algorithm, mode: 'sequential, includes signing/simulation/inclusion',
    count, rows};
  const output = path.join(ctx.dir, `bench-${Date.now()}.json`);
  write(output, result);
  console.log(`PASS: ${count} created, ${count} transfers; ${output}`);
} catch (error) {
  console.error(error.shortMessage || error.message);
  process.exitCode = 1;
}
