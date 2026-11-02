#!/usr/bin/env python3
import json, platform, subprocess
from pathlib import Path
from measure import digest
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OUT=ROOT/'one-box/.runtime/storage-bench';SSL=ROOT/'tools/crypto-bench/.runtime/src/openssl'
OUT.mkdir(parents=True,exist_ok=True)
if not (SSL/'libcrypto.a').exists():raise SystemExit('Build the pinned crypto-bench OpenSSL dependency first')
flags=['-O3']+(['-mcpu=apple-m1'] if platform.system()=='Darwin' and platform.machine()=='arm64' else [])
cmd=['cc',*flags,'-I'+str(SSL/'include'),str(HERE/'keygen.c'),str(SSL/'libcrypto.a')]
cmd+=(['-framework','Security','-framework','CoreFoundation'] if platform.system()=='Darwin' else ['-ldl','-pthread'])
cmd+=['-o',str(OUT/'keygen')];subprocess.run(cmd,check=True)
forge=['forge','build','--root',str(HERE),'--out',str(OUT/'contracts'),'--cache-path',str(OUT/'forge-cache')]
subprocess.run(forge,check=True)
(OUT/'build.json').write_text(json.dumps({'platform':platform.platform(),'commands':[cmd,forge],
 'keygenSha256':digest(OUT/'keygen'),'libcryptoSha256':digest(SSL/'libcrypto.a'),
 'opensslSource':json.loads((ROOT/'tools/crypto-bench/sources.json').read_text())['openssl']},indent=2)+'\n')
