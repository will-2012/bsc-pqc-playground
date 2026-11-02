// Bounded receipt evidence only: never include signed payloads, addresses or keys.
const hash = value => typeof value === 'string' && /^0x[0-9a-f]{64}$/i.test(value);
export function receiptEvidence(record) {
  return {state: record.state, kind: record.kind, action: record.action,
    transactionHash: record.transactionHash, blockNumber: record.blockNumber,
    blockHash: record.blockHash,
    ...(record.kind === 'userop' ? {userOpHash: record.hash} : {})};
}

export class InclusionWindow {
  constructor() { this.blocks = []; this.truncated = false; }
  add(result) {
    if (result?.state !== 'confirmed' || !['transaction', 'userop'].includes(result.kind)) return;
    const number = Number(result.blockNumber);
    if (result.blockNumber == null || !Number.isSafeInteger(number) || number < 0 ||
        !hash(result.blockHash) || !hash(result.transactionHash)) {
      this.truncated = true; // Older journals may lack receipt coordinates.
      return;
    }
    const blockHash = result.blockHash.toLowerCase(), tx = result.transactionHash.toLowerCase();
    let block = this.blocks.find(b => b.blockNumber === number && b.blockHash === blockHash);
    if (!block) {
      block = {blockNumber: number, blockHash, transactions: [], userOps: 0, count: 0};
      this.blocks.push(block);
      this.blocks.sort((a, b) => a.blockNumber - b.blockNumber);
      if (this.blocks.length > 128) { this.blocks.shift(); this.truncated = true; }
      if (!this.blocks.includes(block)) return;
    }
    block.count++;
    if (result.kind === 'userop') block.userOps++;
    if (!block.transactions.includes(tx)) {
      if (block.transactions.length < 256) block.transactions.push(tx);
      else this.truncated = true;
    }
  }
  snapshot() { return {inclusions: this.blocks, inclusionTruncated: this.truncated}; }
}
