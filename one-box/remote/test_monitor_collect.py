"""Collector tests use fake RPCs and private temporary data only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor_collect import Collector, header, USER_OP_TOPIC, SLASH_TOPIC, SLASH_ADDRESS

ENTRY = '0x' + '12' * 20

def block(n, salt=0):
    return {'number': hex(n), 'hash': '0x' + f'{n+salt:064x}',
            'parentHash': '0x' + f'{max(0,n-1)+salt:064x}',
            'timestamp': hex((n*450)//1000), 'milliTimestamp': hex(n*450),
            'gasUsed': hex(500), 'gasLimit': hex(1000), 'transactions': ['0xabc'], 'difficulty': '0x2'}


class Fake(Collector):
    def __init__(self, root):
        super().__init__({'name':'test'}, {'id':'ap-0', 'address':'127.0.0.1',
                         'ports':{'rpc':29545}, 'roles':['validator','bench','bundler']}, root)
        self.height = 100
        self.calls = []
        self.changed = {}
        self.bad_logs = False
        self.down = False
    def _components(self): return {}
    def _resources(self): return {}
    def _entrypoint(self): return ENTRY
    def _request(self, calls):
        self.calls.append(copy.deepcopy(calls))
        if self.down: raise OSError('private address must not escape')
        rows=[]
        for method, params in calls:
            if method == 'eth_getBlockByNumber':
                tag=params[0]
                n=self.height if tag=='latest' else self.height-2 if tag=='safe' else self.height-3 if tag=='finalized' else int(tag,16)
                rows.append(copy.deepcopy(self.changed.get(n, block(n))))
            elif method == 'net_peerCount': rows.append('0x2')
            elif method == 'eth_getLogs':
                bh=params[0]['blockHash']
                rows.append(None if self.bad_logs else [
                    {'blockHash':bh,'address':ENTRY,'topics':[USER_OP_TOPIC]},
                    {'blockHash':bh,'address':SLASH_ADDRESS,'topics':[SLASH_TOPIC]}])
            else: raise AssertionError(method)
        return rows


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.c=Fake(self.root)
    def test_bounded_initial_and_overlap(self):
        first=self.c.collect()
        self.assertFalse(first['coverage']['complete'])
        self.assertEqual(len(first['blocks']),64)
        self.assertEqual(first['head']['timestampMs'],45000)
        self.assertEqual(first['blocks'][-1]['userOps'],1)
        self.assertEqual(first['blocks'][-1]['slashEvents'],1)
        self.assertEqual([p[0] for _,p in self.c.calls[0]],['safe','finalized'])
        self.c.height=104
        second=self.c.collect()
        self.assertTrue(second['coverage']['complete'])
        header_calls=self.c.calls[-2]
        self.assertEqual([p[0] for _,p in header_calls],[hex(i) for i in range(97,105)])
        self.assertLessEqual(max(map(len,self.c.calls)),64)
    def test_failed_rpc_is_unknown_and_private(self):
        self.c.down=True
        value=self.c.collect()
        self.assertIsNone(value['head'])
        self.assertFalse(value['coverage']['complete'])
        self.assertEqual(value['rpcFailures'],1)
        self.assertNotIn('private address',json.dumps(value))
        self.assertIn('collectionMs',value)
    def test_logs_failure_never_becomes_zero(self):
        self.c.bad_logs=True
        value=self.c.collect()
        self.assertIsNone(value['blocks'][-1]['userOps'])
        self.assertIsNone(value['blocks'][-1]['slashEvents'])
        self.assertEqual(len(value['logErrors']),64)
    def test_long_gap_catches_up_in_bounded_batches(self):
        self.c.collect()
        self.c.height=400
        value=self.c.collect()
        self.assertFalse(value['coverage']['complete'])
        self.assertEqual(value['coverage']['to'],160)
        self.assertIn('catching_up',value['errors'])
        for _ in range(5): value=self.c.collect()
        self.assertTrue(value['coverage']['complete'])
        self.assertLessEqual(len(value['blocks']),256)
    def test_overlap_replaces_reorged_blocks(self):
        self.c.collect()
        for n in range(99,101):
            self.c.changed[n]=block(n,1000)
        self.c.changed[99]['parentHash']=block(98)['hash']
        value=self.c.collect()
        self.assertTrue(value['coverage']['complete'])
        self.assertEqual(value['blocks'][-1]['hash'],block(100,1000)['hash'])
    def test_missing_millisecond_field_uses_mix_hash(self):
        b=block(100);del b['milliTimestamp'];b['mixHash']='0x1c2';b['timestamp']='0x2d'
        self.assertEqual(header(b)['timestampMs'],45450)
        b['mixHash']='0x1000'
        with self.assertRaises(ValueError):header(b)
    def test_ipv6_rpc_authority(self):
        c=Collector({'name':'test'},{'id':'a','address':'::1','ports':{'rpc':29545},'roles':[]},self.root)
        self.assertEqual(c.url,'http://[::1]:29545')
    def test_bench_whitelist_and_bounded_evidence(self):
        (self.root/'load-job.json').write_text(json.dumps({'run':'r','dataset':'d'}))
        folder=self.root/'load/d/runs/r';folder.mkdir(parents=True)
        progress={'status':'running','secretKey':'must not escape','inclusionTruncated':False,
                  'inclusions':[{'blockNumber':1,'blockHash':block(1)['hash'],'transactions':[block(2)['hash']],
                                 'userOps':1,'count':1,'secret':'must not escape'}]}
        (folder/'progress.json').write_text(json.dumps(progress))
        value=self.c._bench()
        self.assertEqual(value['progress']['inclusions'][0]['count'],1)
        self.assertNotIn('must not escape',json.dumps(value))
    def test_wrong_hash_logs_are_unknown(self):
        original=self.c._request
        def request(calls):
            values=original(calls)
            if calls[0][0]=='eth_getLogs':values[0][0]['blockHash']=block(0)['hash']
            return values
        self.c._request=request
        value=self.c.collect()
        self.assertIsNone(value['blocks'][0]['userOps'])
        self.assertTrue(value['logErrors'])
    def test_component_rates_reset_on_pid_change(self):
        c=Collector({'name':'test'},{'id':'a','address':'127.0.0.1','ports':{'rpc':1},'roles':['bench']},self.root)
        def response(cpu,pid=10):
            return type('Result',(),{'returncode':0,'stdout':f'Id=bsc-pqc-test-bench.service\nActiveState=active\nMainPID={pid}\nCPUUsageNSec={cpu}\nMemoryCurrent=4096\nIOReadBytes=18446744073709551615\nIOWriteBytes=0\n'})()
        with patch('monitor_collect.subprocess.run',side_effect=[response(100),response(2000000100),response(2000000200,11)]),patch('monitor_collect.time.monotonic',side_effect=[1,3,5]):
            self.assertIsNone(c._components()['bench']['cpuPct'])
            second=c._components()['bench']
            self.assertEqual(second['cpuPct'],100)
            self.assertEqual(second['memoryBytes'],4096)
            self.assertIsNone(second['ioReadBytesPerSecond'])
            self.assertIsNone(c._components()['bench']['cpuPct'])
    def test_nonvalidator_never_calls_local_rpc(self):
        self.c.h["roles"] = ["bench", "bundler"]
        self.c.down = True
        value = self.c.collect()
        self.assertIsNone(value["head"])
        self.assertEqual(self.c.calls, [])
        self.assertEqual(value["rpcFailures"], 0)
        self.assertIn("bench", value)

    def test_finality_race_has_one_bounded_safe_reread(self):
        original = self.c._request
        def request(calls):
            values = original(calls)
            if len(calls) == 2 and calls[0][1] == ["safe", False]:
                values = [block(97), block(98)]
            return values
        self.c._request = request
        value = self.c.collect()
        self.assertEqual(value["safe"]["number"], 98)
        self.assertEqual(value["finalized"]["number"], 98)
        self.assertEqual(sum(c == [("eth_getBlockByNumber", ["safe", False])] for c in self.c.calls), 1)

    def test_unresolved_finality_race_is_unknown(self):
        original = self.c._request
        def request(calls):
            values = original(calls)
            if calls[0] == ("eth_getBlockByNumber", ["safe", False]):
                values[0] = block(95)
            return values
        self.c._request = request
        value = self.c.collect()
        self.assertIsNone(value["safe"])
        self.assertIsNone(value["finalized"])
        self.assertIn("finality_sample_inconsistent", value["errors"])

    def test_large_progress_is_read_but_public_evidence_is_bounded(self):
        (self.root/'load-job.json').write_text(json.dumps({'run':'r','dataset':'d'}))
        folder=self.root/'load/d/runs/r';folder.mkdir(parents=True)
        rows = [{'blockNumber':n,'blockHash':block(n)['hash'],
                 'transactions':[block(t)['hash'] for t in range(256)],'userOps':256,'count':256} for n in range(128)]
        payload = json.dumps({'inclusions':rows,'inclusionTruncated':False})
        self.assertGreater(len(payload),1024*1024)
        (folder/'progress.json').write_text(payload)
        value=self.c._bench()['progress']
        self.assertEqual(sum(len(b['transactions']) for b in value['inclusions']),2048)
        self.assertTrue(value['inclusionTruncated'])
        self.assertEqual(len(value['inclusions'][-1]['transactions']),256)

    def test_block_hash_evidence_is_globally_bounded(self):
        original=self.c._request
        def request(calls):
            values=original(calls)
            for value in values:
                if isinstance(value,dict) and 'transactions' in value:
                    value['transactions']=[block(i)['hash'] for i in range(300)]
            return values
        self.c._request=request
        for height in (100,160,220,280):
            self.c.height=height
            value=self.c.collect()
        self.assertEqual(sum(len(b['txHashes']) for b in value['blocks']),4096)
        self.assertTrue(value['blocks'][0]['txHashesTruncated'])
        self.assertEqual(value['blocks'][-1]['txCount'],300)
        self.assertEqual(len(value['blocks'][-1]['txHashes']),256)
        self.assertLess(len(json.dumps(value)),1024*1024)

    def test_host_cpu_disk_and_network_deltas(self):
        c=Collector({'name':'test'},{'id':'a','address':'127.0.0.1','ports':{'rpc':1},'roles':[]},self.root)
        cpu=iter(['cpu 10 0 5 85 0 0 0 0', 'cpu 20 0 10 150 10 0 0 10'])
        disk=iter(['259 0 nvme0n1 0 0 2 0 0 0 4 0 0 0 0',
                   '259 0 nvme0n1 0 0 6 0 0 0 12 0 0 0 0'])
        network=iter(['ens5: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0',
                      'ens5: 300 0 0 0 0 0 0 0 600 0 0 0 0 0 0 0'])
        def read(path,*args,**kwargs):
            return {'/proc/stat':lambda:next(cpu),'/proc/diskstats':lambda:next(disk),
                    '/proc/net/dev':lambda:next(network),
                    '/proc/meminfo':lambda:'MemAvailable: 8 kB\nMemTotal: 16 kB'}[str(path)]()
        with patch('monitor_collect.Path.read_text',read),patch('monitor_collect.Path.exists',return_value=True), \
             patch('monitor_collect.time.monotonic',side_effect=[1,1,3,3]):
            first=c._resources(); second=c._resources()
        self.assertIsNone(first['cpuBusyPct'])
        self.assertAlmostEqual(second['cpuBusyPct'],15)
        self.assertEqual(second['iowaitPct'],10)
        self.assertEqual(second['stealPct'],10)
        self.assertEqual(second['diskReadBytesPerSecond'],1024)
        self.assertEqual(second['diskWriteBytesPerSecond'],2048)
        self.assertEqual(second['networkRxBytesPerSecond'],100)
        self.assertEqual(second['networkTxBytesPerSecond'],200)
        self.assertEqual(second['memoryAvailableBytes'],8192)

    def test_counter_rate_reset_is_unknown(self):
        with patch('monitor_collect.time.monotonic',side_effect=[1,3,5]):
            self.assertEqual(self.c._rate('x',[100,200]),[None,None])
            self.assertEqual(self.c._rate('x',[300,600]),[100,200])
            self.assertEqual(self.c._rate('x',[0,0]),[None,None])

if __name__=='__main__':unittest.main()
