// Public probe only; private key never leaves process memory.
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../../../erc4337-wallet/package.json',import.meta.url));
const {slh_dsa_sha2_128f:slh}=await import(require.resolve('@noble/post-quantum/slh-dsa.js'));
const {publicKey,secretKey}=slh.keygen(),message=new Uint8Array(32),signature=slh.sign(message,secretKey);
if(!slh.verify(signature,message,publicKey))throw new Error('Invalid probe signature');
secretKey.fill(0);
fs.writeFileSync(new URL('../../../one-box/.runtime/build/v6/slh-public.json',import.meta.url),JSON.stringify({input:Buffer.concat([publicKey,message,signature]).toString('hex'),source:'Noble Pure SLH-DSA-SHA2-128f, empty context'})+'\n',{mode:0o600});
