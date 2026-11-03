import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

export const binary = fileURLToPath(new URL('../../../../one-box/.runtime/build/v4/pq-wallet', import.meta.url));
// Sequential requests per worker; the runner bounds total work by maxInflight.
export class MlSigner {
  constructor(executable = binary) {
    this.pending = []; this.active = null; this.buffer = ''; this.closed = false;
    this.child = spawn(executable, ['--serve'], {stdio: ['pipe', 'pipe', 'ignore']});
    this.child.on('error', () => this.close());
    this.child.on('exit', () => this.close());
    this.child.stdin.on('error', () => this.close());
    this.child.stdout.on('error', () => this.close());
    this.child.stdout.on('data', chunk => {
      this.buffer += chunk.toString();
      if (this.buffer.length > 100000) return this.close();
      if (!this.buffer.includes('\n')) return;
      if (!this.active) return this.close();
      try {
        const value = JSON.parse(this.buffer.trim());
        assert.match(value.signature, /^[0-9a-f]{4840}$/);
        const item = this.active;
        clearTimeout(this.timer); this.active = null; this.buffer = '';
        item.resolve('0x' + value.signature); this.next();
      } catch { this.close(); }
    });
    this.onExit = () => this.child.kill();
    process.once('exit', this.onExit);
  }
  sign(secretKey, publicKey, message) {
    assert(typeof secretKey === 'string' && /^0x[0-9a-f]{5120}$/i.test(secretKey), 'Invalid ML secret key encoding');
    assert(typeof publicKey === 'string' && /^0x[0-9a-f]{2624}$/i.test(publicKey), 'Invalid ML public key encoding');
    assert(typeof message === 'string' && /^0x[0-9a-f]{64}$/i.test(message), 'Invalid ML message encoding');
    if (this.closed) return Promise.reject(new Error('ML signer unavailable'));
    return new Promise((resolve, reject) => {
      const input = JSON.stringify({action: 'signVerify', secretKey: secretKey.slice(2),
        publicKey: publicKey.slice(2), message: message.slice(2)}) + '\n';
      this.pending.push({input, resolve, reject}); this.next();
    });
  }
  next() {
    if (this.closed || this.active || !this.pending.length) return;
    this.active = this.pending.shift();
    this.timer = setTimeout(() => this.close(), 30000);
    this.child.stdin.write(this.active.input); this.active.input = '';
  }
  close() {
    if (this.closed) return;
    this.closed = true; clearTimeout(this.timer);
    for (const item of [this.active, ...this.pending].filter(Boolean)) {
      item.input = ''; item.reject(new Error('ML signer failed'));
    }
    this.pending = []; this.active = null;
    this.child.stdin.destroy(); this.child.kill();
    process.removeListener('exit', this.onExit);
  }
}
