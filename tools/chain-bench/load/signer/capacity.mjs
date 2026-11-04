// Signer capacity only: no RPC, no chain. Every signature uses a fresh message and is verified.
import assert from 'node:assert/strict';
import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
import {SlhSigner, avx2Binary, avx512Binary} from './client.mjs';
const require = createRequire(new URL('../../../../erc4337-wallet/package.json', import.meta.url));
const {slh_dsa_sha2_128s: slh} = require('@noble/post-quantum/slh-dsa.js');
const hex = b => '0x' + Buffer.from(b).toString('hex');
if (!isMainThread) {
  const keys = slh.keygen();
  const native = {avx2: avx2Binary, avx512: avx512Binary};
  const signer = workerData.backend === 'noble' ? null : new SlhSigner(native[workerData.backend]);
  try {
    const sign = async m => signer ? Buffer.from((await signer.sign(hex(keys.secretKey), hex(m))).slice(2), 'hex') : slh.sign(m, keys.secretKey);
    assert(slh.verify(await sign(new Uint8Array(32)), new Uint8Array(32), keys.publicKey));
    parentPort.postMessage('ready');
    await new Promise(resolve => parentPort.once('message', resolve));
    const start = performance.now();
    for (let i = 1; i <= workerData.samples; i++) {
      const m = new Uint8Array(32); new DataView(m.buffer).setUint32(0, i);
      assert(slh.verify(await sign(m), m, keys.publicKey));
    }
    parentPort.postMessage({ms: performance.now() - start});
  } finally { signer?.close(); keys.secretKey.fill(0); }
} else {
  const rows = [];
  const backends = process.argv[2] ? [process.argv[2]] : ['noble', 'openssl'];
  assert(backends.every(b => ['noble', 'openssl', 'avx2', 'avx512'].includes(b)), 'Use noble, openssl, avx2 or avx512');
  const workerCounts = process.argv[3] ? process.argv[3].split(',').map(Number) : [1, 2, 4];
  assert(workerCounts.every(n => Number.isSafeInteger(n) && n >= 1 && n <= 128), 'Invalid worker counts');
  for (const backend of backends) for (const workers of workerCounts) {
    const samples = 8;
    const pool = Array.from({length: workers}, () => new Worker(new URL(import.meta.url), {workerData: {backend, samples}}));
    try {
      await Promise.all(pool.map(w => new Promise((resolve, reject) => { w.once('message', resolve); w.once('error', reject); })));
      const start = performance.now();
      const results = await Promise.all(pool.map(w => new Promise((resolve, reject) => { w.once('message', resolve); w.once('error', reject); w.postMessage('start'); })));
      const elapsedMs = performance.now() - start;
      const row = {backend, workers, samples: samples * workers, elapsedMs, signaturesPerSecond: samples * workers * 1000 / elapsedMs,
        meanWorkerMs: results.reduce((s, r) => s + r.ms, 0) / workers};
      rows.push(row); console.log(JSON.stringify(row));
    } finally { await Promise.all(pool.map(w => w.terminate())); }
  }
  console.log(JSON.stringify({platform: process.platform, arch: process.arch, node: process.version, rows}));
}
