// Controlled chain-capacity comparison, not a replacement for Bundler validation.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../../erc4337-wallet/package.json', import.meta.url));
const {encodeFunctionData, parseEventLogs} = require('viem');
const {entryPoint07Abi, toPackedUserOperation} = require('viem/account-abstraction');

export async function prepareDirect(ctx, smart, call, gas) {
  const operation = {sender: smart.address, nonce: await smart.getNonce(),
    callData: await smart.encodeCalls([call]),
    callGasLimit: BigInt(gas.callGasLimit), verificationGasLimit: BigInt(gas.verificationGasLimit),
    preVerificationGas: BigInt(gas.preVerificationGas),
    maxFeePerGas: BigInt(ctx.config.gas_price_wei), maxPriorityFeePerGas: BigInt(ctx.config.gas_price_wei)};
  operation.signature = await smart.signUserOperation(operation);
  return operation;
}
export function directCalldata(operation, beneficiary) {
  return encodeFunctionData({abi: entryPoint07Abi, functionName: 'handleOps',
    args: [[toPackedUserOperation(operation)], beneficiary]});
}
export function applyDirectReceipt(record, receipt) {
  assert.equal(receipt.transactionHash, record.transactionHash, 'Outer transaction hash mismatch');
  record.blockNumber = receipt.blockNumber; record.blockHash = receipt.blockHash;
  record.outerGasUsed = receipt.gasUsed;
  const events = parseEventLogs({abi: entryPoint07Abi, eventName: 'UserOperationEvent', logs: receipt.logs, strict: true})
    .filter(e => e.address.toLowerCase() === record.entryPoint.toLowerCase() && e.args.userOpHash === record.hash);
  if (receipt.status !== 'success') { record.state = 'reverted'; return; }
  assert.equal(events.length, 1, 'Expected exactly one matching UserOperationEvent');
  const event = events[0].args;
  assert.equal(event.sender.toLowerCase(), record.signed.sender.toLowerCase(), 'UserOperation sender mismatch');
  assert.equal(event.nonce, BigInt(record.signed.nonce), 'UserOperation nonce mismatch');
  record.gasUsed = event.actualGasUsed;
  record.state = event.success ? 'confirmed' : 'reverted';
}
