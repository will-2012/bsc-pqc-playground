import assert from 'node:assert/strict';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createPublicClient, http} from 'viem';
import {slhProfile} from '../common/slh-dsa.mjs';

const address = '0x0000000000000000000000000000000000000101';

// The key format, Pure signing envelope, account and factory stay compatible.
// Separate deployment/identity directories prevent v1 from being overwritten.
export const profile = {
  ...slhProfile, id: 'slh-dsa-v2', contractVersion: 2,
  contracts: ctx => [
    ['verifier', 'SLHDSA128sPrecompile', []],
    ['factory', 'PQAccountFactory', [ctx.base.entryPoint, ctx.deployment.verifier]],
  ],
  async probe(ctx) {
    assert([1,3,4,5,6].includes(ctx.config.pq_precompile), 'v2 requires an explicitly enabled PQ environment');
    const file = fileURLToPath(new URL('../../one-box/precompile/v2/testdata/slh_v2.json', import.meta.url));
    const input = '0x' + JSON.parse(fs.readFileSync(file, 'utf8'))[0].input;
    for (let i = 0; i < ctx.config.validators; i++) {
      const node = createPublicClient({chain: ctx.chain, transport: http(`http://127.0.0.1:${ctx.config.rpc_port + 2*i}`)});
      const result = await node.call({to: address, data: input, gas: 1000000n});
      assert.equal(result.data, '0x' + '00'.repeat(31) + '01', `Node ${i} lacks the v2 precompile`);
    }
  },
};
