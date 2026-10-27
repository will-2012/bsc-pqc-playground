import sys
sys.dont_write_bytecode = True
import importlib.util
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('bench',Path(__file__).with_name('run.py'))
bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)

class BenchmarkChecks(TestCase):
    def data(self):
        cfg={'rounds':2,'samples':2,'sign_count':32,'verify_count':256};rows=[]
        for round_ in range(2):
            for index,name in enumerate(bench.NAMES):
                sk,pk,low,high=bench.SIZES[index];order=(index-round_+6)%6
                rows.append(dict(kind='check',round=round_,order=order,algorithm=name,private_key_bytes=sk,public_key_bytes=pk,signature_min_bytes=low,signature_max_bytes=high,vectors=32,checks_per_vector=5,key_setup_cpu_ns=1000))
                for operation in ['sign','verify']:
                    count=cfg[operation+'_count']
                    for sample in range(2):
                        offset=sample*32 if operation=='sign' else 32
                        ns=(index+1)*(1000 if operation=='sign' else 2000)*count
                        rows.append(dict(kind='timing',round=round_,order=order,algorithm=name,operation=operation,sample=sample,iterations=count,message_start_index=(round_*64+offset)%4096,cpu_ns=ns,wall_ns=ns*2,process_cpu_ns=ns,signature_observed_min_bytes=low,signature_observed_max_bytes=high))
        return {'config':cfg,'rows':rows}
    def test_counts_normalized_and_separate_baselines(self):
        summary=bench.aggregate(self.data())
        self.assertEqual(summary[0]['sign']['cpu_us'],1)
        self.assertEqual(summary[0]['verify']['cpu_us'],2)
        self.assertEqual(summary[3]['sign']['ecdsa_ratio'],4)
        self.assertEqual(summary[3]['verify']['ecdsa_ratio'],4)
    def test_different_message_index_rejected(self):
        data=self.data();data['rows'][-1]['message_start_index']+=1
        with self.assertRaises(AssertionError):bench.aggregate(data)
    def test_missing_batch_rejected(self):
        data=self.data();data['rows'].pop()
        with self.assertRaises(AssertionError):bench.aggregate(data)
    def test_wrong_falcon_encoding_size_rejected(self):
        data=self.data();data['rows'][5]['public_key_bytes']=897
        with self.assertRaises(AssertionError):bench.aggregate(data)
    def test_cross_platform_request_rejected(self):
        with patch.object(bench.sys,'platform','darwin'),patch.object(bench.platform,'machine',return_value='arm64'):
            with self.assertRaises(RuntimeError):bench.detect_profile('linux-x86_64')
    def test_unvalidated_linux_backend_never_silently_runs(self):
        with patch.object(bench.sys,'platform','linux'),patch.object(bench.platform,'machine',return_value='x86_64'):
            with self.assertRaises(RuntimeError):bench.detect_profile('auto')

if __name__=='__main__':main()
