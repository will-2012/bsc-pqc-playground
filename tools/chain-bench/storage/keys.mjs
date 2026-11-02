import fs from 'node:fs';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createHmac, randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../../erc4337-wallet/package.json', import.meta.url));
const {privateKeyToAccount} = require('viem/accounts');
export function openKeys(seedFile, algorithm, binary, parallel=4) {
  if (!fs.existsSync(seedFile)) fs.writeFileSync(seedFile,randomBytes(32),{mode:0o600,flag:'wx'});
  const master=fs.readFileSync(seedFile); if(master.length!==32)throw Error('Bad master seed');
  function seed(i) {return createHmac('sha512',master).update(`storage-bench-v1/${algorithm}/${i}`).digest().subarray(0,algorithm==='slh'?48:32);}
  const workers=[];
  if (algorithm!=='ecdsa') for(let i=0;i<parallel;i++) {
    const child=spawn(binary,[algorithm==='ml'?'ML-DSA-44':'SLH-DSA-SHA2-128s'],{stdio:['pipe','pipe','inherit']});
    const queue=[];let failed;
    createInterface({input:child.stdout}).on('line',line=>{const pending=queue.shift();if(!pending)return; if(!/^[0-9a-f]+$/.test(line))pending.reject(Error('Invalid public key output'));else pending.resolve('0x'+line);});
    const fail=e=>{failed=e;for(const q of queue.splice(0))q.reject(e);};
    child.on('error',fail);child.on('exit',code=>fail(Error(`Key generator exited: ${code}`)));
    workers.push({child,ask:s=>new Promise((resolve,reject)=>{if(failed)return reject(failed);queue.push({resolve,reject});child.stdin.write(s.toString('hex')+'\n');})});
  }
  return {seed,
    async batch(start,count) {return Promise.all(Array.from({length:count},(_,j)=>{
      const s=seed(start+j);
      return algorithm==='ecdsa'?privateKeyToAccount('0x'+s.toString('hex')).address:workers[j%workers.length].ask(s);
    }));},
    close(){master.fill(0);for(const w of workers)w.child.stdin.end();}
  };
}
