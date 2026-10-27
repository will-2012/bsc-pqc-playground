#!/usr/bin/env python3
"""Observe native precompiles before/after the common PQ fork on all nodes."""
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'one-box/scripts'))
from devnet import read,rpc,write,BUILD,RUNTIME

def main(version=3):
    name=sys.argv[1] if len(sys.argv)>1 else f'devnet-v{version}'
    env=RUNTIME/name
    deadline=time.monotonic()+600
    before,after={},{}
    slh=read(ROOT/'one-box/precompile/v2/testdata/slh_v2.json')[0]['input']
    while time.monotonic()<deadline:
        try:
            cfg=read(env/'config.json');fork=read(env/'fork-times.json')['latest']+5
            falcon=read(BUILD/f'v{version}/falcon-public.json')['input']
            for i in range(cfg['validators']):
                b=rpc(cfg,i,'eth_getBlockByNumber',['latest',False]);timestamp=int(b['timestamp'],16)
                results=[]
                targets=[('0101',slh),('0102',falcon)]
                if version>=4:targets.append(('0103',read(BUILD/f"v{version}/mldsa{'44' if version>=5 else ''}-public.json")['input']))
                if version>=5:targets.append(('0104',read(BUILD/f'v{version}/mldsa-public.json')['input']))
                if version==6:targets.append(('0105',read(BUILD/'v6/slh-public.json')['input']))
                for address,data in targets:
                    results.append(rpc(cfg,i,'eth_call',[{'to':'0x'+'0'*36+address,'data':'0x'+data,'gas':hex(1000000)},b['number']]))
                expected='0x' if timestamp<fork else '0x'+'00'*31+'01'
                assert results==[expected]*len(targets),(i,timestamp,fork,results)
                sample={'node':i,'block':b['number'],'blockHash':b['hash'],'timestamp':timestamp,'results':results}
                (before if timestamp<fork else after)[i]=sample
            if len(after)==cfg['validators']:
                assert len(before)==cfg['validators'],'Started too late to observe pre-fork state'
                write(env/'pq-fork-check.json',{'checkedAtUnix':int(time.time()),'pqTime':fork,'before':list(before.values()),'after':list(after.values())})
                print('PASS: all validators saw all selected precompiles absent before pqTime and valid after it',flush=True);return
        except (FileNotFoundError,ConnectionError,OSError) as e:
            pass
        time.sleep(.5)
    raise RuntimeError('PQ fork observation timed out')
