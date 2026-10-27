// Public fixtures only. Fresh private keys never leave process memory or ignored runtime.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {randomBytes} from 'node:crypto';
import {execFileSync} from 'node:child_process';

process.umask(0o077);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const require = createRequire(path.join(root, 'erc4337-wallet/package.json'));
const {slh_dsa_sha2_128s: slh} = await import(require.resolve('@noble/post-quantum/slh-dsa.js'));
const vectors = [];
for (let key = 0; key < 2; key++) {
  const {publicKey, secretKey} = slh.keygen();
  for (const msg of [new Uint8Array(32), new Uint8Array(32).fill(255), Uint8Array.from({length: 32}, (_, i) => i), randomBytes(32)]) {
    const sig = slh.sign(msg, secretKey);
    assert(slh.verify(sig, msg, publicKey));
    vectors.push({source: 'noble/post-quantum 0.7.1', input: Buffer.concat([publicKey, msg, sig]).toString('hex')});
  }
  secretKey.fill(0);
}
const temporary = fs.mkdtempSync(path.join(root, 'one-box/.runtime/slh-vectors-'));
try {
  const openssl = process.env.OPENSSL || 'openssl';
  const key = path.join(temporary, 'key.pem');
  const msg = path.join(temporary, 'message.bin');
  const sig = path.join(temporary, 'signature.bin');
  fs.writeFileSync(msg, randomBytes(32));
  execFileSync(openssl, ['genpkey', '-algorithm', 'SLH-DSA-SHA2-128s', '-out', key]);
  execFileSync(openssl, ['pkeyutl', '-sign', '-rawin', '-inkey', key, '-in', msg, '-out', sig]);
  const pk = execFileSync(openssl, ['pkey', '-in', key, '-pubout', '-outform', 'DER']).subarray(-32);
  const message = fs.readFileSync(msg), signature = fs.readFileSync(sig);
  assert(slh.verify(signature, message, pk));
  vectors.push({source: execFileSync(openssl, ['version'], {encoding: 'utf8'}).trim(),
    input: Buffer.concat([pk, message, signature]).toString('hex')});
} finally {
  fs.rmSync(temporary, {recursive: true, force: true});
}
const output = path.resolve(process.argv[2] || path.join(root, 'one-box/precompile/v2/testdata/slh_v2.json'));
fs.writeFileSync(output, JSON.stringify(vectors, null, 2) + '\n');
console.log(`Saved ${vectors.length} public vectors: ${output}`);
