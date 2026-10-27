#!/usr/bin/env python3
"""Observe both native precompiles before/after the common PQ fork on all nodes."""
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'one-box/scripts'))
from devnet import read,rpc,write,BUILD,RUNTIME

def main():
    name=sys.argv[1] if len(sys.argv)>1 else 'devnet-v3'
    env=RUNTIME/name
    deadline=time.monotonic()+600
    before,after={},{}
    slh=read(ROOT/'one-box/precompile/v2/testdata/slh_v2.json')[0]['input']
    while time.monotonic()<deadline:
        try:
            cfg=read(env/'config.json');fork=read(env/'fork-times.json')['latest']+5
            falcon=read(BUILD/'v3/falcon-public.json')['input']
            for i in range(cfg['validators']):
                b=rpc(cfg,i,'eth_getBlockByNumber',['latest',False]);timestamp=int(b['timestamp'],16)
                results=[]
                for address,data in [('0101',slh),('0102',falcon)]:
                    results.append(rpc(cfg,i,'eth_call',[{'to':'0x'+'0'*36+address,'data':'0x'+data,'gas':hex(1000000)},b['number']]))
                expected='0x' if timestamp<fork else '0x'+'00'*31+'01'
                assert results==[expected,expected],(i,timestamp,fork,results)
                sample={'node':i,'block':b['number'],'blockHash':b['hash'],'timestamp':timestamp,'results':results}
                (before if timestamp<fork else after)[i]=sample
            if len(after)==cfg['validators']:
                assert len(before)==cfg['validators'],'Started too late to observe pre-fork state'
                write(env/'pq-fork-check.json',{'checkedAtUnix':int(time.time()),'pqTime':fork,'before':list(before.values()),'after':list(after.values())})
                print('PASS: all validators saw both precompiles absent before pqTime and valid after it',flush=True);return
        except (FileNotFoundError,ConnectionError,OSError) as e:
            pass
        time.sleep(.5)
    raise RuntimeError('PQ fork observation timed out')
if __name__=='__main__':main()
