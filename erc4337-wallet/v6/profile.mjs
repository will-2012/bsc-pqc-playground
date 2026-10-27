import assert from 'node:assert/strict';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createPublicClient, http} from 'viem';
import {createSlhProfile} from '../common/slh-dsa.mjs';
import {slh_dsa_sha2_128f} from '@noble/post-quantum/slh-dsa.js';
const slhProfile=createSlhProfile(slh_dsa_sha2_128f,'SLH-DSA-SHA2-128f');

const address = '0x0000000000000000000000000000000000000105';

// The key format, Pure signing envelope, account and factory stay compatible.
// Separate deployment/identity directories prevent old versions from being overwritten.
export const profile = {
  ...slhProfile, id: 'slh-dsa-v6', contractVersion: 6,
  contracts: ctx => [
    ['verifier', 'SLHDSA128fPrecompile', []],
    ['factory', 'PQAccountFactory', [ctx.base.entryPoint, ctx.deployment.verifier]],
  ],
  async probe(ctx) {
    assert([6].includes(ctx.config.pq_precompile), 'v6 requires an explicitly enabled PQ environment');
    const file = fileURLToPath(new URL('../../one-box/.runtime/build/v6/slh-public.json', import.meta.url));
    const input = '0x' + JSON.parse(fs.readFileSync(file, 'utf8')).input;
    for (let i = 0; i < ctx.config.validators; i++) {
      const node = createPublicClient({chain: ctx.chain, transport: http(`http://127.0.0.1:${ctx.config.rpc_port + 2*i}`)});
      const result = await node.call({to: address, data: input, gas: 1000000n});
      assert.equal(result.data, '0x' + '00'.repeat(31) + '01', `Node ${i} lacks the v6 precompile`);
    }
  },
};
