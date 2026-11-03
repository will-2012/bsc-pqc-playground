import assert from 'node:assert/strict';

export async function prepareTransaction(ctx, wallet, tx) {
  // context() already checked the RPC chain ID and genesis. Supplying chainId
  // avoids eth_fillTransaction when nonce, gas and fees are already known.
  const request = await wallet.prepareTransactionRequest({
    ...tx, chainId: ctx.chain.id, gas: 21000n, ...ctx.fee,
  });
  assert.equal(request.chainId, ctx.chain.id, 'Transaction chain mismatch');
  return request;
}

export async function waitForTransaction(client, hash) {
  // Load owns its accounts and never replaces a submitted transaction. Wait for
  // the exact hash; missing receipts still time out and require reconciliation.
  const receipt = await client.waitForTransactionReceipt({
    hash, timeout: 180000, checkReplacement: false,
  });
  assert.equal(receipt.transactionHash, hash, 'Transaction receipt hash mismatch');
  return receipt;
}
