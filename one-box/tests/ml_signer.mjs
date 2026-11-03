import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {MlSigner, binary} from '../../tools/chain-bench/load/signer/ml-client.mjs';

const executable = process.argv[2] || binary;
const native = request => JSON.parse(execFileSync(executable, [], {
  input: JSON.stringify(request), encoding: 'utf8', maxBuffer: 1024 * 1024,
}));
const key = native({action: 'keygen'});
const other = native({action: 'keygen'});
const signer = new MlSigner(executable);
try {
  assert.throws(() => signer.sign('private-test-input', '0x' + key.publicKey, '0x' + '00'.repeat(32)), error => error.message === 'Invalid ML secret key encoding');
  // Concurrent callers must receive their own message's signature, in order.
  const messages = Array.from({length: 32}, (_, i) => i.toString(16).padStart(64, '0'));
  const signatures = await Promise.all(messages.map(message =>
    signer.sign('0x' + key.secretKey, '0x' + key.publicKey, '0x' + message)));
  for (let i = 0; i < messages.length; i++) {
    const request = {action: 'verify', publicKey: key.publicKey, message: messages[i], signature: signatures[i].slice(2)};
    assert(native(request).valid);
    assert(!native({...request, message: messages[(i + 1) % messages.length]}).valid);
  }
  await assert.rejects(signer.sign('0x' + key.secretKey, '0x' + other.publicKey, '0x' + messages[0]));
  await assert.rejects(signer.sign('0x' + key.secretKey, '0x' + key.publicKey, '0x' + messages[0]));
  console.log('ML persistent signer: cross-verification, ordering, wrong-key and closed-process checks passed');
} finally {
  signer.close();
}
