import assert from 'node:assert/strict';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {parseArgs} from 'node:util';
import {context, deployment, createAccount, transfer, write} from '../../../erc4337-wallet/common/wallet.mjs';
import {selectProfile} from '../../../erc4337-wallet/profiles.mjs';

async function describeCalldata(ctx, receipt) {
  const tx = await ctx.client.getTransaction({hash: receipt.transactionHash});
  const bytes = tx.input.slice(2).match(/../g) || [];
  const zero = bytes.filter(b => b === '00').length;
  receipt.calldataBytes = bytes.length;
  receipt.calldataZeroBytes = zero;
  // EIP-7623 floor for a non-creation outer transaction without authorizations.
  receipt.calldataFloorGas = 21000n + 10n * BigInt(zero + 4 * (bytes.length - zero));
}

try {
  const {values, positionals} = parseArgs({allowPositionals: true, options: {
    profile: {type: 'string'}, version: {type: 'string'}, 'ecdsa-baseline': {type: 'boolean', default: false},
  }});
  assert(positionals.length <= 2, 'Expected environment and account count only');
  const [name = 'devnet', countText = '3'] = positionals;
  const count = Number(countText);
  assert(Number.isInteger(count) && count >= 1 && count <= 100, 'Count must be 1..100');
  const ctx = await context(name, selectProfile(values));
  await deployment(ctx);
  const recipient = ctx.manifest.accounts[1].address;
  const baseline = [];
  if (values['ecdsa-baseline']) {
    assert(!await ctx.client.getCode({address: recipient}), 'EOA baseline requires a recipient without code');
    for (let i = 0; i < count; i++) {
      const hash = await ctx.wallet.sendTransaction({to: recipient, value: 10_000_000_000_000_000n, gas: 21000n, ...ctx.fee});
      const receipt = await ctx.client.waitForTransactionReceipt({hash, timeout: 90000});
      assert.equal(receipt.status, 'success');
      assert.equal(receipt.gasUsed, 21000n);
      baseline.push({transactionHash: hash, gasUsed: receipt.gasUsed, effectiveGasPrice: receipt.effectiveGasPrice});
    }
  }
  const rows = [];
  for (let i = 0; i < count; i++) {
    console.log(`PQ account ${i + 1}/${count}: creating`);
    const start = performance.now();
    const created = await createAccount(ctx);
    const createMs = performance.now() - start;
    console.log(`PQ account ${i + 1}/${count}: transferring 0.01 BNB`);
    const sent = performance.now();
    const transferred = await transfer(ctx, created.id, recipient, 10_000_000_000_000_000n);
    const transferMs = performance.now() - sent;
    await describeCalldata(ctx, created);
    await describeCalldata(ctx, transferred);
    rows.push({createMs, transferMs, created, transferred});
  }
  const result = {checkedAt: new Date().toISOString(), chainId: ctx.chain.id, genesisHash: ctx.manifest.genesis_hash,
    profile: ctx.profile.id, algorithm: ctx.deployment.algorithm, mode: 'sequential, includes signing/simulation/inclusion',
    count, ...(baseline.length ? {ecdsaBaseline: baseline} : {}), rows};
  const output = path.join(ctx.dir, `bench-${Date.now()}.json`);
  write(output, result);
  console.log(`PASS: ${count} created, ${count} transfers; ${output}`);
} catch (error) {
  console.error(error.shortMessage || error.message);
  process.exitCode = 1;
}
