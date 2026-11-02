import {profile as v2} from '../v2/profile.mjs';

// Same signing and precompile as v2; separate account, factory and identities.
export const profile = {
  ...v2, id: 'slh-dsa-v7', contractVersion: 7,
  contracts: ctx => [
    ['verifier', 'SLHDSA128sPrecompile', []],
    ['factory', 'PQStorageAccountFactory', [ctx.base.entryPoint, ctx.deployment.verifier]],
  ],
};
