import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {prepareTransaction, waitForTransaction} from './transactions.mjs';

const require = createRequire(new URL('../../../erc4337-wallet/package.json', import.meta.url));
const {createPublicClient, createWalletClient, custom} = require('viem');
const {generatePrivateKey, privateKeyToAccount} = require('viem/accounts');
const chain = {id: 12345, name: 'test', nativeCurrency: {name: 'Test', symbol: 'T', decimals: 18}};
const ctx = {chain, fee: {type: 'legacy', gasPrice: 50000000n}};
const hash = '0x' + 'ab'.repeat(32);

test('explicit chain ID removes fill RPC without changing signed transactions', async () => {
  const account = privateKeyToAccount(generatePrivateKey());
  for (const nonce of [0, 1, 1000]) {
    const calls = [];
    const wallet = createWalletClient({account, chain, transport: custom({request: async ({method}) => {
      calls.push(method);
      throw Object.assign(new Error('Method not found'), {code: -32601});
    }}, {retryCount: 0})});
    const tx = {to: account.address, value: 1n, nonce};
    const before = await wallet.prepareTransactionRequest({...tx, gas: 21000n, ...ctx.fee});
    assert(calls.includes('eth_fillTransaction'));
    calls.length = 0;
    const after = await prepareTransaction(ctx, wallet, tx);
    assert.deepEqual(calls, []);
    assert.equal(await account.signTransaction(after), await account.signTransaction(before));
  }
});

test('prepared transactions remain bound to the verified chain', async () => {
  await assert.rejects(prepareTransaction(ctx, {
    prepareTransactionRequest: async () => ({chainId: chain.id + 1}),
  }, {}), /chain mismatch/);
});

function receiptClient({status = '0x1', missing = false, timeout = 1000} = {}) {
  const calls = [];
  let attempts = 0, height = 0;
  const client = createPublicClient({chain, pollingInterval: 5,
    transport: custom({request: async ({method, params}) => {
      calls.push(method);
      if (method === 'eth_blockNumber') return '0x' + (++height).toString(16);
      assert.equal(method, 'eth_getTransactionReceipt', 'No transaction or full-block scan');
      assert.equal(params[0], hash);
      if (++attempts < 4 || missing) return null;
      return {transactionHash: hash, blockNumber: '0x1', status, gasUsed: '0x5208', logs: []};
    }}, {retryCount: 0}),
  });
  return {calls, client: {waitForTransactionReceipt: params => {
    assert.equal(params.timeout, 180000);
    // Shorten only the test deadline; production retains its existing timeout.
    return client.waitForTransactionReceipt({...params, timeout});
  }}};
}

test('pending receipts resolve without replacement scans', async () => {
  const {client, calls} = receiptClient();
  const receipt = await waitForTransaction(client, hash);
  assert.equal(receipt.status, 'success');
  assert.equal(calls.filter(x => x === 'eth_getTransactionReceipt').length, 4);
  assert(calls.includes('eth_blockNumber'));
});

test('reverts remain visible to the worker and missing receipts time out', async () => {
  const reverted = receiptClient({status: '0x0'});
  assert.equal((await waitForTransaction(reverted.client, hash)).status, 'reverted');
  const pending = receiptClient({missing: true, timeout: 50});
  await assert.rejects(waitForTransaction(pending.client, hash), /timed out/i);
});

test('a different transaction hash cannot count as the requested receipt', async () => {
  await assert.rejects(waitForTransaction({waitForTransactionReceipt: async () => ({
    transactionHash: '0x' + 'cd'.repeat(32), status: 'success',
  })}, hash), /hash mismatch/);
});
