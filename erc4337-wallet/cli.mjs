import {context, createAccount, deployment, transfer} from './wallet.mjs';
import {isAddress, parseEther} from 'viem';
import {parseArgs} from 'node:util';
import {DEFAULT_PROFILE} from './profiles/index.mjs';

try {
  const {values, positionals} = parseArgs({allowPositionals: true, options: {profile: {type: 'string', default: DEFAULT_PROFILE}}});
  const [action, name = 'devnet', id, to, amount] = positionals;
  if (!['deploy', 'create', 'transfer', 'test'].includes(action)) throw new Error('Usage: wallet deploy|create|test <env>; wallet transfer <env> <id> <to> <BNB> [--profile <name>]');
  if (positionals.length > (action === 'transfer' ? 5 : 2)) throw new Error('Unexpected extra arguments');
  if (action === 'transfer' && (!id || !isAddress(to) || !amount)) throw new Error('Expected account id, recipient address and amount in BNB');
  const ctx = await context(name, values.profile);
  let result;
  if (action === 'test') {
    result = await (await import('./test.mjs')).test(ctx);
  } else {
    const d = await deployment(ctx);
    if (action === 'deploy') result = d;
    else if (action === 'create') result = await createAccount(ctx);
    else result = await transfer(ctx, id, to, parseEther(amount));
  }
  console.log(JSON.stringify(result, (_, v) => typeof v === 'bigint' ? v.toString() : v, 2));
} catch (error) {
  console.error(error.shortMessage || error.message);
  process.exitCode = 1;
}
