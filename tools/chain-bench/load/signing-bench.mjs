// CPU-only diagnostic; no chain, RPC, or persistent keys. Run with the devnet stopped.
import assert from 'node:assert/strict';
import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
import os from 'node:os';
const require = createRequire(new URL('../../../erc4337-wallet/package.json', import.meta.url));
if (!isMainThread) {
  const {slh_dsa_sha2_128s: slh} = await import(require.resolve('@noble/post-quantum/slh-dsa.js'));
  const {publicKey, secretKey} = slh.keygen();
  const message = new Uint8Array(32);
  assert(slh.verify(slh.sign(message, secretKey), message, publicKey));
  parentPort.postMessage('ready');
  parentPort.once('message', () => {
    for (let i = workerData.index; i < workerData.count; i += workerData.workers) {
      new DataView(message.buffer).setUint32(0, i);
      if (workerData.realStub) slh.sign(new Uint8Array(32), secretKey);
      const signature = slh.sign(message, secretKey);
      assert.equal(signature.length, 7856);
      assert(slh.verify(signature, message, publicKey));
    }
    secretKey.fill(0);
    parentPort.postMessage('done');
  });
} else {
  const count = Number(process.argv[2] || 12);
  assert(Number.isSafeInteger(count) && count >= 4 && count <= 1000);
  const rows = [];
  for (const [workers, realStub] of [[1,true],[1,false],[2,false],[4,false]]) {
    const pool = [];
    try {
      await Promise.all(Array.from({length: workers}, (_, index) => new Promise((resolve, reject) => {
        const w = new Worker(new URL(import.meta.url), {workerData: {workers, realStub, count, index}});
        pool.push(w); w.once('error', reject); w.once('message', resolve);
      })));
      let rssPeak = process.memoryUsage().rss;
      const rssStart = rssPeak;
      const timer = setInterval(() => { rssPeak = Math.max(rssPeak, process.memoryUsage().rss); }, 10);
      const cpu = process.cpuUsage(), start = performance.now();
      await Promise.all(pool.map(w => new Promise((resolve, reject) => {
        w.once('error', reject); w.once('message', resolve); w.postMessage('start');
      })));
      const seconds = (performance.now() - start) / 1000, usage = process.cpuUsage(cpu);
      clearInterval(timer);
      rows.push({workers, realStub, count, seconds, signaturesPerSecond: count / seconds,
        processCpuSeconds: (usage.user + usage.system) / 1e6,
        rssStartMiB: rssStart / 2**20, rssPeakMiB: rssPeak / 2**20});
      console.error(`${workers} worker(s), real stub=${realStub}: ${seconds.toFixed(2)} s`);
    } finally { await Promise.all(pool.map(w => w.terminate())); }
  }
  console.log(JSON.stringify({checkedAt: new Date().toISOString(), platform: process.platform, arch: process.arch,
    cpu: os.cpus()[0].model, node: process.version, count, rows}, null, 2));
}
