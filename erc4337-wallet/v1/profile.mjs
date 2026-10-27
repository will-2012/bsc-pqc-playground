import {slhProfile} from '../common/slh-dsa.mjs';

export const profile = {
  ...slhProfile, id: 'slh-dsa-v1', contractVersion: 1,
  contracts: ctx => [
    ['verifier', 'SLHDSA128s', []],
    ['factory', 'PQAccountFactory', [ctx.base.entryPoint, ctx.deployment.verifier]],
  ],
};
