import unittest
import json
import tempfile
from pathlib import Path
import verify
import run


class PricingTests(unittest.TestCase):
    def test_parse_actual_go_format_and_reject_missing_cpu(self):
        text = 'BenchmarkPricing/ML_Valid 12345 84000 ns/op 85000 cpu-ns/op 10000 gas/op 83000 thread-cpu-ns/op 18.00 vectors 32 B/op 1 allocs/op\nPASS\n'
        row = run.parse(text, 'Pricing/ML_Valid')
        self.assertEqual(row['iterations'], 12345)
        self.assertEqual(row['metrics']['gas/op'], 10000)
        for bad in [text.replace('cpu-ns/op', 'wrong', 1), text.replace('85000 cpu', '-1 cpu'), text.replace('85000 cpu', 'nan cpu')]:
            with self.assertRaises(ValueError):
                run.parse(bad, 'Pricing/ML_Valid')
        with self.assertRaises(ValueError):
            run.parse(text, 'Pricing/SLH_Valid')

    def test_quote_uses_slowest_verifier_and_excludes_signing(self):
        summary = {'PricingECDSA/ECRecover': {'medianUs': 30},
                   'Pricing/ML_Valid': {'medianUs': 15, 'maxRoundUs': 16},
                   'Pricing/ML_WrongMessage': {'medianUs': 17, 'maxRoundUs': 20},
                   'Pricing/SLH_Valid': {'medianUs': 400, 'maxRoundUs': 420},
                   'PricingSign/SLH': {'medianUs': 400000, 'maxRoundUs': 500000}}
        result = run.pricing(summary)
        self.assertEqual(result['ML']['worstPath'], 'Pricing/ML_WrongMessage')
        self.assertEqual(result['ML']['rounded1000Gas'], 3000)
        self.assertEqual(result['SLH']['rounded1000Gas'], 53000)
        self.assertEqual(result['SLH']['rounded10000Gas'], 60000)
        self.assertAlmostEqual(result['ML']['validCpuRatio'], 0.5)

    def test_process_and_thread_cpu_remain_distinct(self):
        rows = [dict(name=n, metrics={'cpu-ns/op': 1000, 'ns/op': 1200, 'thread-cpu-ns/op': 900, 'B/op': 32, 'allocs/op': 1}) for n in run.CASES]
        s = run.summarize(rows)['PricingECDSA/ECRecover']
        self.assertEqual((s['medianUs'], s['threadMedianUs'], s['wallMedianUs']), (1.0, 0.9, 1.2))

    def test_public_samples_recompute_and_tampering_is_rejected(self):
        path = Path(__file__).resolve().parents[3] / 'docs/chain-bench/pricing/linux-x86_64/results.json'
        verify.verify(path)
        data = json.loads(path.read_text())
        data['runs'][0]['rows'][0]['metrics']['cpu-ns/op'] *= 2
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'results.json'
            target.write_text(json.dumps(data))
            with self.assertRaises(AssertionError):
                verify.verify(target)


if __name__ == '__main__':
    unittest.main()
