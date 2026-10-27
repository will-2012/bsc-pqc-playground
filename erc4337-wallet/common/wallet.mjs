import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {randomBytes} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {Wallet} from 'ethers';
import {getProfile, DEFAULT_PROFILE} from '../profiles.mjs';
import {
  bytesToHex, createPublicClient, createWalletClient, defineChain,
  encodeDeployData, http, keccak256, parseEther,
} from 'viem';
import {privateKeyToAccount} from 'viem/accounts';
import {createBundlerClient, getUserOperationHash} from 'viem/account-abstraction';

process.umask(0o077);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
export function write(p, data) {
  fs.mkdirSync(path.dirname(p), {recursive: true, mode: 0o700});
  fs.writeFileSync(`${p}.tmp`, JSON.stringify(data, (_, v) => typeof v === 'bigint' ? v.toString() : v, 2) + '\n', {mode: 0o600});
  fs.renameSync(`${p}.tmp`, p);
}

export async function context(name = 'devnet', profileId = DEFAULT_PROFILE) {
  const profile = getProfile(profileId);
  assert.match(name, /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/);
  const env = path.join(ROOT, 'one-box/.runtime', name);
  const manifest = read(path.join(env, 'environment.json'));
  const config = read(path.join(env, 'config.json'));
  const base = read(path.join(env, 'bundler/deployment.json'));
  const alto = read(path.join(env, 'bundler/config.json'));
  const rpc = `http://127.0.0.1:${config.rpc_port}`;
  const chain = defineChain({id: config.chain_id, name: `BSC local ${name}`,
    nativeCurrency: {name: 'BNB', symbol: 'BNB', decimals: 18}, rpcUrls: {default: {http: [rpc]}}});
  const client = createPublicClient({chain, transport: http(rpc, {timeout: 180000}), pollingInterval: 500});
  assert.equal(await client.getChainId(), config.chain_id);
  assert.equal((await client.getBlock({blockNumber: 0n})).hash, manifest.genesis_hash);
  assert.equal(base.genesisHash, manifest.genesis_hash);
  const info = manifest.accounts[0];
  const files = fs.readdirSync(path.join(env, info.keystore)).filter(x => x.startsWith('UTC--'));
  assert.equal(files.length, 1);
  const eoa = await Wallet.fromEncryptedJson(fs.readFileSync(path.join(env, info.keystore, files[0]), 'utf8'),
    fs.readFileSync(path.join(env, info.password_file), 'utf8').trim());
  const owner = privateKeyToAccount(eoa.privateKey);
  assert.equal(owner.address.toLowerCase(), info.address.toLowerCase());
  const wallet = createWalletClient({chain, account: owner, transport: http(rpc)});
  const bundler = createBundlerClient({chain, client, pollingInterval: 500,
    transport: http(`http://127.0.0.1:${alto.port}`, {timeout: 180000, retryCount: 0})});
  assert.equal(base.entryPointVersion, profile.entryPointVersion, 'Incompatible EntryPoint version');
  const dir = path.join(env, 'wallet', profile.id);
  fs.mkdirSync(dir, {recursive: true, mode: 0o700});
  return {name, env, dir, profile, chain, client, wallet, bundler, manifest, config, base,
    fee: {type: 'legacy', gasPrice: BigInt(config.gas_price_wei)}};
}

async function wait(ctx, hash) {
  const receipt = await ctx.client.waitForTransactionReceipt({hash, timeout: 180000});
  assert.equal(receipt.status, 'success', `Transaction reverted: ${hash}`);
  return receipt;
}

export function artifact(name) { return read(path.join(ROOT, `contract/out/${name}.sol/${name}.json`)); }

export async function deployment(ctx) {
  if (ctx.profile.probe) await ctx.profile.probe(ctx);
  const p = path.join(ctx.dir, 'deployment.json');
  const d = fs.existsSync(p) ? read(p) : {genesisHash: ctx.manifest.genesis_hash, chainId: ctx.chain.id,
    entryPoint: ctx.base.entryPoint, profile: ctx.profile.id, contractVersion: ctx.profile.contractVersion,
    algorithm: ctx.profile.algorithm, transactions: {}, codeHashes: {}};
  assert.equal(d.genesisHash, ctx.manifest.genesis_hash);
  assert.equal(d.entryPoint, ctx.base.entryPoint);
  assert.equal(d.profile, ctx.profile.id);
  assert.equal(d.contractVersion, ctx.profile.contractVersion);
  ctx.deployment = d;
  // Resolve each stage after the previous stage has populated its address.
  for (let stage = 0; stage < ctx.profile.contracts(ctx).length; stage++) {
    const [label, name, args] = ctx.profile.contracts(ctx)[stage];
    const a = artifact(name);
    if (d[label]) {
      const code = await ctx.client.getCode({address: d[label]});
      assert(code && keccak256(code) === d.codeHashes[label], `${label} deployment changed`);
      const tx = await ctx.client.getTransaction({hash: d.transactions[label]});
      assert.equal(tx.input, encodeDeployData({abi: a.abi, bytecode: a.bytecode.object, args}),
        `${name} build changed; use a new profile/environment instead of silently reusing old contracts`);
    } else {
      const receipt = await wait(ctx, await ctx.wallet.deployContract({abi: a.abi, bytecode: a.bytecode.object, args, ...ctx.fee}));
      assert(receipt.contractAddress);
      d[label] = receipt.contractAddress;
      d.transactions[label] = receipt.transactionHash;
      d.codeHashes[label] = keccak256(await ctx.client.getCode({address: d[label]}));
      write(p, d);
      console.log(`Deployed ${name}: ${d[label]}`);
    }
  }
  ctx.deployment = d;
  return d;
}

function identityPath(ctx, id) {
  assert.match(id, /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/);
  return path.join(ctx.dir, 'accounts', `${id}.json`);
}

// Generate keys only at runtime. Never use deterministic demo seeds or an ECDSA owner.
async function newIdentity(ctx) {
  const d = ctx.deployment || await deployment(ctx);
  const id = randomBytes(12).toString('hex');
  const {publicKey, secretKey} = await ctx.profile.keygen();
  const salt = BigInt(bytesToHex(randomBytes(32)));
  const address = await ctx.profile.getAddress(ctx, publicKey, salt);
  const identity = {id, profile: ctx.profile.id, contractVersion: ctx.profile.contractVersion,
    genesisHash: d.genesisHash, entryPoint: d.entryPoint, factory: d.factory,
    address, salt: salt.toString(), publicKey, secretKey};
  write(identityPath(ctx, id), identity);
  return identity;
}

export function loadIdentity(ctx, id) {
  const identity = read(identityPath(ctx, id));
  assert.equal(identity.id, id);
  return identity;
}

export async function smartAccount(ctx, identity) {
  if (!ctx.deployment) await deployment(ctx);
  assert.equal(identity.profile, ctx.profile.id, 'Wallet profile mismatch');
  assert.equal(identity.contractVersion, ctx.profile.contractVersion, 'Contract version mismatch');
  return ctx.profile.smartAccount(ctx, identity);
}

export async function prepare(ctx, identity, call) {
  const smart = await smartAccount(ctx, identity);
  const fee = BigInt(ctx.config.gas_price_wei);
  const operation = await ctx.bundler.prepareUserOperation({account: smart, calls: [call], maxFeePerGas: fee, maxPriorityFeePerGas: fee});
  operation.signature = await smart.signUserOperation(operation);
  return operation;
}

export async function submit(ctx, operation) {
  const expected = getUserOperationHash({chainId: ctx.chain.id, entryPointAddress: ctx.deployment.entryPoint,
    entryPointVersion: ctx.profile.entryPointVersion, userOperation: operation});
  assert.equal(await ctx.profile.onchainOperationHash(ctx, operation), expected, 'Wallet / EntryPoint hash mismatch');
  const hash = await ctx.bundler.sendUserOperation(operation);
  assert.equal(hash, expected);
  const receipt = await ctx.bundler.waitForUserOperationReceipt({hash, timeout: 180000});
  const outer = await ctx.client.getTransactionReceipt({hash: receipt.receipt.transactionHash});
  const result = {userOperationHash: hash, transactionHash: receipt.receipt.transactionHash,
    success: receipt.success, transactionStatus: receipt.receipt.status,
    blockHash: receipt.receipt.blockHash, blockNumber: receipt.receipt.blockNumber,
    actualGasUsed: receipt.actualGasUsed, actualGasCost: receipt.actualGasCost,
    outerTransactionGas: outer.gasUsed, outerGasPrice: outer.effectiveGasPrice,
    outerTransactionCost: outer.gasUsed * outer.effectiveGasPrice,
    verificationGasLimit: operation.verificationGasLimit,
    signatureBytes: (operation.signature.length - 2) / 2,
    packedUserOperationBytes: (ctx.profile.encodedOperation(operation).length - 2) / 2};
  write(path.join(ctx.dir, 'receipts', `${hash}.json`), result);
  assert.equal(receipt.success, true, `UserOperation execution failed: ${hash}; receipt saved`);
  assert.equal(receipt.receipt.status, 'success');
  assert.equal(outer.status, 'success');
  return result;
}

export async function createAccount(ctx) {
  const identity = await newIdentity(ctx);
  // Local faucet funding is separate from authorization. Account pays EntryPoint costs.
  await wait(ctx, await ctx.wallet.sendTransaction({to: identity.address, value: parseEther('2'), ...ctx.fee}));
  const operation = await prepare(ctx, identity, {to: identity.address, value: 0n});
  const receipt = await submit(ctx, operation);
  assert(await ctx.client.getCode({address: identity.address}));
  assert.equal(await ctx.profile.checkPublicKey(ctx, identity.address), identity.publicKey);
  return {id: identity.id, address: identity.address, publicKey: identity.publicKey, ...receipt};
}

export async function transfer(ctx, id, to, amount) {
  const identity = loadIdentity(ctx, id);
  assert.notEqual(identity.address.toLowerCase(), to.toLowerCase(), 'Use a separate recipient for a balance-checked transfer');
  assert(amount > 0n, 'Amount must be positive');
  const before = await ctx.client.getBalance({address: to});
  const operation = await prepare(ctx, identity, {to, value: amount});
  const receipt = await submit(ctx, operation);
  const after = await ctx.client.getBalance({address: to});
  assert.equal(after - before, amount, 'Recipient must be passive; balance delta must match');
  return {id, from: identity.address, to, amountWei: amount, ...receipt};
}
