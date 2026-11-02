import assert from 'node:assert/strict';
import test from 'node:test';
import {validatorRpcs} from './rpc-targets.mjs';
const config = {validators: 3, rpc_port: 29545};
test('local probes still cover every validator', () => {
  assert.deepEqual(validatorRpcs(config), [29545, 29547, 29549].map(p => `http://127.0.0.1:${p}`));
});
test('remote probe ignores legacy all-validator list', () => {
  assert.deepEqual(validatorRpcs(config, {rpc: 'http://assigned:29545', validators: ['http://other:29545']}), ['http://assigned:29545']);
});
test('explicit local targets remain supported', () => {
  assert.deepEqual(validatorRpcs(config, {validators: ['http://custom:29545']}), ['http://custom:29545']);
});
