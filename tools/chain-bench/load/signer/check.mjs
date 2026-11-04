import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
import {SlhSigner, binary} from './client.mjs';
const require = createRequire(new URL('../../../../erc4337-wallet/package.json', import.meta.url));
const {slh_dsa_sha2_128s: slh} = require('@noble/post-quantum/slh-dsa.js');
const hex = b => '0x' + Buffer.from(b).toString('hex');
const keys = slh.keygen();
const native = new SlhSigner(process.argv[2] || binary);
try {
  assert.throws(() => native.sign('private-test-input', '0x' + '00'.repeat(32)),
    error => error.message === 'Invalid SLH secret key encoding');
  const messages = Array.from({length: 4}, (_, i) => new Uint8Array(32).fill(i));
  const start = performance.now();
  const signatures = await Promise.all(messages.map(m => native.sign(hex(keys.secretKey), hex(m))));
  const nativeMs = (performance.now() - start) / messages.length;
  for (let i = 0; i < messages.length; i++) {
    const sig = Buffer.from(signatures[i].slice(2), 'hex');
    assert.equal(sig.length, 7856);
    assert(slh.verify(sig, messages[i], keys.publicKey));
    assert(!slh.verify(sig, messages[(i + 1) % messages.length], keys.publicKey));
    sig[100] ^= 1;
    assert(!slh.verify(sig, messages[i], keys.publicKey));
  }
  const then = performance.now();
  for (const m of messages) assert(slh.verify(slh.sign(m, keys.secretKey), m, keys.publicKey));
  console.log(JSON.stringify({checks: 'native signatures cross-verified; wrong-message/tampering rejected; concurrent requests matched',
    samples: messages.length, nativeMs, nobleMs: (performance.now() - then) / messages.length}));
} finally { native.close(); keys.secretKey.fill(0); }
assert.throws(() => new SlhSigner('/nonexistent-bench-signer'));
const interrupted = new SlhSigner(process.argv[2] || binary);
const pending = interrupted.sign('0x' + '00'.repeat(64), '0x' + '00'.repeat(32));
const rejection = assert.rejects(pending, /signer/);
interrupted.close(); await rejection;
await assert.rejects(interrupted.sign('0x' + '00'.repeat(64), '0x' + '00'.repeat(32)), /unavailable/);
