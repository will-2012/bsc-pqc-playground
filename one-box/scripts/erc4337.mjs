import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {Wallet} from 'ethers';
import {
  createPublicClient, createWalletClient, defineChain, encodeFunctionData,
  http, keccak256, parseAbi, parseEther,
} from 'viem';
import {generatePrivateKey, privateKeyToAccount} from 'viem/accounts';
import {createBundlerClient, getUserOperationHash} from 'viem/account-abstraction';
import {toSimpleSmartAccount} from 'permissionless/accounts';

process.umask(0o077);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [action, name = 'devnet'] = process.argv.slice(2);
assert.match(name, /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/);
const env = path.join(root, 'one-box/.runtime', name);
const stateDir = path.join(env, 'bundler');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const write = (p, data) => {
  const temporary = `${p}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(data, (_, v) => typeof v === 'bigint' ? v.toString() : v, 2)}\n`, {mode: 0o600});
  fs.renameSync(temporary, p);
};
const manifest = read(path.join(env, 'environment.json'));
const config = read(path.join(env, 'config.json'));
const rpcUrl = `http://127.0.0.1:${config.rpc_port}`;
const chain = defineChain({
  id: config.chain_id, name: `BSC local ${name}`,
  nativeCurrency: {name: 'BNB', symbol: 'BNB', decimals: 18},
  rpcUrls: {default: {http: [rpcUrl]}},
});
const client = createPublicClient({chain, transport: http(rpcUrl), pollingInterval: 500});
assert.equal(await client.getChainId(), config.chain_id);
assert.equal((await client.getBlock({blockNumber: 0n})).hash, manifest.genesis_hash);
fs.mkdirSync(stateDir, {recursive: true, mode: 0o700});

// Decrypt the generated devnet test keystore in memory, never in CLI arguments.
const ownerInfo = manifest.accounts[0];
const keyDir = path.join(env, ownerInfo.keystore);
const keyFiles = fs.readdirSync(keyDir).filter(x => x.startsWith('UTC--'));
assert.equal(keyFiles.length, 1);
const ownerWallet = await Wallet.fromEncryptedJson(
  fs.readFileSync(path.join(keyDir, keyFiles[0]), 'utf8'),
  fs.readFileSync(path.join(env, ownerInfo.password_file), 'utf8').trim(),
);
const owner = privateKeyToAccount(ownerWallet.privateKey);
assert.equal(owner.address.toLowerCase(), ownerInfo.address.toLowerCase());
const wallet = createWalletClient({chain, account: owner, transport: http(rpcUrl)});
const fee = {type: 'legacy', gasPrice: BigInt(config.gas_price_wei)};

const secretsPath = path.join(stateDir, 'private.json');
const deploymentPath = path.join(stateDir, 'deployment.json');
if (!fs.existsSync(secretsPath)) {
  assert(!fs.existsSync(deploymentPath), 'Deployment exists but bundler keys are missing; refusing to replace its identity');
  write(secretsPath, {genesisHash: manifest.genesis_hash,
    executor: generatePrivateKey(), utility: generatePrivateKey(), recipient: generatePrivateKey()});
}
const keys = read(secretsPath);
assert.equal(keys.genesisHash, manifest.genesis_hash);
const executor = privateKeyToAccount(keys.executor);
const utility = privateKeyToAccount(keys.utility);
const recipient = privateKeyToAccount(keys.recipient);
const deployment = fs.existsSync(deploymentPath) ? read(deploymentPath) : {
  genesisHash: manifest.genesis_hash, chainId: chain.id, entryPointVersion: '0.7',
  owner: owner.address, executor: executor.address, utility: utility.address,
  recipient: recipient.address, transactions: {}, codeHashes: {},
};
assert.equal(deployment.genesisHash, manifest.genesis_hash);
assert.equal(deployment.owner.toLowerCase(), owner.address.toLowerCase());
assert.equal(deployment.executor.toLowerCase(), executor.address.toLowerCase());
assert.equal(deployment.utility.toLowerCase(), utility.address.toLowerCase());
assert.equal(deployment.recipient.toLowerCase(), recipient.address.toLowerCase());

async function wait(hash) {
  const receipt = await client.waitForTransactionReceipt({hash, timeout: 90000});
  assert.equal(receipt.status, 'success', `Transaction reverted: ${hash}`);
  return receipt;
}

function artifact(name) {
  return read(path.join(root, `contract/out/${name}.sol/${name}.json`));
}

async function deploy(label, abi, bytecode, args = []) {
  if (deployment[label]) {
    const code = await client.getCode({address: deployment[label]});
    assert(code && code !== '0x', `Missing ${label}; use a fresh devnet or inspect deployment record`);
    assert.equal(keccak256(code), deployment.codeHashes[label], `${label} code changed`);
    return deployment[label];
  }
  const hash = await wallet.deployContract({abi, bytecode, args, ...fee});
  const receipt = await wait(hash);
  const address = receipt.contractAddress;
  assert(address, `No address for ${label}`);
  deployment[label] = address;
  deployment.transactions[label] = hash;
  deployment.codeHashes[label] = keccak256(await client.getCode({address}));
  write(deploymentPath, deployment);
  console.log(`Deployed ${label}: ${address}`);
  return address;
}

async function setup() {
  const entry = artifact('EntryPoint');
  const ep = await deploy('entryPoint', entry.abi, entry.bytecode.object);
  const factory = artifact('SimpleAccountFactory');
  await deploy('factory', factory.abi, factory.bytecode.object, [ep]);
  const counter = artifact('Counter');
  await deploy('counter', counter.abi, counter.bytecode.object);
  const source = path.join(root, 'one-box/.runtime/build/alto/src/esm/types/contracts/PimlicoEntryPointSimulations.js');
  const simulations = await import(pathToFileURL(source).href);
  await deploy('simulation', simulations.PimlicoEntryPointSimulationsAbi,
    simulations.pimlicoEntrypointSimulationsV7DeployBytecode);
  for (const account of [executor, utility]) {
    if (await client.getBalance({address: account.address}) < parseEther('1')) {
      await wait(await wallet.sendTransaction({to: account.address, value: parseEther('10'), ...fee}));
    }
  }
  const publicConfig = path.join(stateDir, 'config.json');
  const bundlerConfig = read(fs.existsSync(publicConfig) ? publicConfig : path.join(root, 'one-box/bundler.json'));
  assert.equal(bundlerConfig['safe-mode'], true);
  assert.equal(bundlerConfig['deploy-simulations-contract'], false);
  write(publicConfig, bundlerConfig);
  Object.assign(bundlerConfig, {
    'network-name': `bsc-${name}`, 'rpc-url': rpcUrl, entrypoints: ep,
    'entrypoint-simulation-contract-v7': deployment.simulation,
    'executor-private-keys': keys.executor, 'utility-private-key': keys.utility,
  });
  write(path.join(stateDir, 'alto.json'), bundlerConfig);
  console.log(`EntryPoint v0.7 ready; source-built Alto uses generated executor ${executor.address}`);
}

async function test() {
  assert(deployment.entryPoint, 'Deploy the contracts first');
  const bundlerConfig = read(path.join(stateDir, 'alto.json'));
  const transport = http(`http://127.0.0.1:${bundlerConfig.port}`);
  const probe = createBundlerClient({chain, client, transport});
  const supported = await probe.getSupportedEntryPoints();
  assert(supported.some(a => a.toLowerCase() === deployment.entryPoint.toLowerCase()));
  const runPath = path.join(stateDir, 'run-state.json');
  const index = BigInt(fs.existsSync(runPath) ? read(runPath).nextIndex : 0);
  write(runPath, {nextIndex: (index + 1n).toString()});
  const smart = await toSimpleSmartAccount({
    client, owner, factoryAddress: deployment.factory,
    entryPoint: {address: deployment.entryPoint, version: '0.7'}, index, nonceKey: 0n,
  });
  assert(!await client.getCode({address: smart.address}), 'Expected a new counterfactual smart account');
  await wait(await wallet.sendTransaction({to: smart.address, value: parseEther('2'), ...fee}));
  const bundler = createBundlerClient({chain, client, account: smart, pollingInterval: 500,
    transport});
  const before = await client.getBalance({address: recipient.address});
  const amount = parseEther('0.01');
  const gas = {maxFeePerGas: BigInt(config.gas_price_wei), maxPriorityFeePerGas: BigInt(config.gas_price_wei)};
  const operation = await bundler.prepareUserOperation({account: smart, calls: [{to: recipient.address, value: amount}], ...gas});
  assert(operation.verificationGasLimit > 0n);
  operation.signature = await smart.signUserOperation(operation);
  let wrongSignatureRejected = false;
  try {
    const operationHash = getUserOperationHash({chainId: chain.id, entryPointAddress: deployment.entryPoint,
      entryPointVersion: '0.7', userOperation: operation});
    const wrong = await utility.signMessage({message: {raw: operationHash}});
    await bundler.sendUserOperation({...operation, signature: wrong});
  } catch (error) {
    assert.match(error.shortMessage || error.message, /signature|AA24/i);
    wrongSignatureRejected = true;
  }
  assert(wrongSignatureRejected, 'Wrong-owner signature was accepted');
  const hash = await bundler.sendUserOperation(operation);
  assert.equal(hash, getUserOperationHash({chainId: chain.id, entryPointAddress: deployment.entryPoint,
    entryPointVersion: '0.7', userOperation: operation}));
  const transfer = await bundler.waitForUserOperationReceipt({hash, timeout: 120000});
  assert.equal(transfer.success, true);
  assert.equal(transfer.receipt.from.toLowerCase(), executor.address.toLowerCase());
  assert.equal(await client.getBalance({address: recipient.address}) - before, amount);
  assert(await client.getCode({address: smart.address}), 'Account was not deployed');

  const counterAbi = parseAbi(['function count() view returns (uint256)',
    'function lastCaller() view returns (address)', 'function increment()']);
  const countBefore = await client.readContract({address: deployment.counter, abi: counterAbi, functionName: 'count'});
  const counterOp = await bundler.prepareUserOperation({account: smart, calls: [{to: deployment.counter,
    data: encodeFunctionData({abi: counterAbi, functionName: 'increment'})}], ...gas});
  counterOp.signature = await smart.signUserOperation(counterOp);
  const counterHash = await bundler.sendUserOperation(counterOp);
  const counterReceipt = await bundler.waitForUserOperationReceipt({hash: counterHash, timeout: 120000});
  assert.equal(counterReceipt.success, true);
  let replayRejected = false;
  try { await bundler.sendUserOperation(counterOp); }
  catch (error) {
    assert.match(error.shortMessage || error.message, /nonce|AA25/i);
    replayRejected = true;
  }
  assert(replayRejected, 'Old nonce was accepted');
  const nodes = [];
  for (let i = 0; i < config.validators; i++) {
    const node = createPublicClient({chain, transport: http(`http://127.0.0.1:${config.rpc_port + 2*i}`), pollingInterval: 500});
    const tr = await node.waitForTransactionReceipt({hash: transfer.receipt.transactionHash});
    const cr = await node.waitForTransactionReceipt({hash: counterReceipt.receipt.transactionHash});
    assert.equal(tr.status, 'success');
    assert.equal(cr.status, 'success');
    const count = await node.readContract({address: deployment.counter, abi: counterAbi, functionName: 'count', blockNumber: cr.blockNumber});
    const caller = await node.readContract({address: deployment.counter, abi: counterAbi, functionName: 'lastCaller', blockNumber: cr.blockNumber});
    assert.equal(count, countBefore + 1n);
    assert.equal(caller.toLowerCase(), smart.address.toLowerCase());
    nodes.push({node: i, transferBlockHash: tr.blockHash, counterBlockHash: cr.blockHash, count, caller});
  }
  assert.equal(new Set(nodes.map(n => n.transferBlockHash)).size, 1);
  assert.equal(new Set(nodes.map(n => n.counterBlockHash)).size, 1);
  const result = {checkedAt: new Date().toISOString(), genesisHash: manifest.genesis_hash,
    entryPoint: deployment.entryPoint, entryPointVersion: '0.7', accountIndex: index, smartAccount: smart.address,
    executor: executor.address, createdByFirstUserOperation: true, wrongSignatureRejected, replayRejected,
    transfer: {userOperationHash: hash, transactionHash: transfer.receipt.transactionHash,
      amountWei: amount, actualGasUsed: transfer.actualGasUsed, outerTransactionGas: transfer.receipt.gasUsed},
    counter: {userOperationHash: counterHash, transactionHash: counterReceipt.receipt.transactionHash}, nodes};
  write(path.join(stateDir, 'result.json'), result);
  console.log(JSON.stringify(result, (_, v) => typeof v === 'bigint' ? v.toString() : v, 2));
}

try {
  if (action === 'deploy') await setup();
  else if (action === 'test') await test();
  else throw new Error('Expected deploy or test');
} catch (error) {
  // Do not dump the wallet or generated Alto configuration on failure.
  console.error(error.shortMessage || error.message);
  process.exitCode = 1;
}
