import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
export const binary = fileURLToPath(new URL('../../../../one-box/.runtime/build/bsc-pqc-slh-signer', import.meta.url));
export const avx2Binary = fileURLToPath(new URL('../../../../one-box/.runtime/build/bsc-pqc-slh-avx2-signer', import.meta.url));
export const avx512Binary = fileURLToPath(new URL('../../../../one-box/.runtime/build/bsc-pqc-slh-avx512-signer', import.meta.url));

// One persistent, sequential native process per worker. Workers provide CPU parallelism.
// Queueing here is bounded by the runner's maxInflight, not an unbounded producer.
export class SlhSigner {
  constructor(executable = binary) {
    fs.accessSync(executable, fs.constants.X_OK);
    this.pending = []; this.active = null; this.closed = false; this.buffer = Buffer.alloc(0);
    this.child = spawn(executable, [], {stdio: ['pipe', 'pipe', 'ignore']});
    this.child.on('error', () => this.close());
    this.child.on('exit', () => this.close());
    this.child.stdin.on('error', () => this.close());
    this.child.stdout.on('error', () => this.close());
    this.child.stdout.on('data', chunk => {
      this.buffer = Buffer.concat([this.buffer, chunk]);
      if (!this.active || this.buffer.length > 7856) return this.close();
      if (this.buffer.length !== 7856) return;
      const done = this.active;
      clearTimeout(this.timer); this.active = null;
      const signature = '0x' + this.buffer.toString('hex'); this.buffer = Buffer.alloc(0);
      done.resolve(signature); this.next();
    });
    this.onExit = () => this.child.kill();
    process.once('exit', this.onExit);
  }
  sign(secretKey, message) {
    assert(typeof secretKey === 'string' && /^0x[0-9a-fA-F]{128}$/.test(secretKey), 'Invalid SLH secret key encoding');
    assert(typeof message === 'string' && /^0x[0-9a-fA-F]{64}$/.test(message), 'Invalid SLH message encoding');
    if (this.closed) return Promise.reject(new Error('SLH signer unavailable'));
    return new Promise((resolve, reject) => {
      this.pending.push({input: Buffer.from(secretKey.slice(2) + message.slice(2), 'hex'), resolve, reject});
      this.next();
    });
  }
  next() {
    if (this.closed || this.active || !this.pending.length) return;
    this.active = this.pending.shift();
    const input = this.active.input;
    this.timer = setTimeout(() => this.close(), 120000);
    this.child.stdin.write(input, () => input.fill(0));
  }
  close() {
    if (this.closed) return;
    this.closed = true; clearTimeout(this.timer);
    for (const item of [this.active, ...this.pending].filter(Boolean)) {
      item.input.fill(0); item.reject(new Error('SLH signer failed or closed'));
    }
    this.active = null; this.pending = []; this.buffer = Buffer.alloc(0);
    this.child.stdin.destroy(); this.child.kill();
    process.removeListener('exit', this.onExit);
  }
}
