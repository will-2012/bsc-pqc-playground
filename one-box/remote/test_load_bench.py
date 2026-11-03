import unittest
from test_cluster import inventory
from load_bench import shards

class ShardingTests(unittest.TestCase):
    def test_duration_preserves_accounts_and_allows_count_cap(self):
        base = dict(action='transfer', algorithm='ecdsa', dataset='d', run='r', accounts=10000,
                    qps=100, workers=32, fund_wei='100', value_wei='1', duration=600)
        plan = shards(inventory(), count=None, **base)
        self.assertEqual(sum(s['accounts'] for _, s in plan), 10000)
        self.assertEqual(sum(s['count'] for _, s in plan), 60003)
        self.assertTrue(all(s['durationSeconds'] == 600 for _, s in plan))
        capped = shards(inventory(), count=100, **base)
        self.assertEqual(sum(s['count'] for _, s in capped), 100)
        for duration in (-1, float('nan'), float('inf'), 86401):
            with self.assertRaises(ValueError): shards(inventory(), count=None, **{**base, 'duration': duration})
        with self.assertRaises(ValueError):
            shards(inventory(), count=None, **{**base, 'action': 'prepare'})

    def test_max_inflight_is_per_host_and_optional(self):
        base = dict(action='transfer', algorithm='ecdsa', dataset='d', run='r', accounts=1000,
                    count=1000, qps=800, workers=8, fund_wei='100', value_wei='1')
        self.assertTrue(all(s['maxInflight'] == 8 for _, s in shards(inventory(), **base)))
        self.assertTrue(all(s['maxInflight'] == 256 for _, s in shards(inventory(), max_inflight=256, **base)))
        self.assertTrue(all(s['slhBackend'] == 'openssl' for _, s in shards(inventory(), slh_backend='openssl', **base)))
        with self.assertRaises(ValueError): shards(inventory(), slh_backend='unknown', **base)
        for value in (0, -1, 4097, 1.5):
            with self.assertRaises(ValueError): shards(inventory(), max_inflight=value, **base)

    def test_status_hides_receipt_details_but_preserves_private_record(self):
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from load_bench import execute
        evidence = [{'blockNumber': 12, 'transactions': ['receipt-hash']}]
        row = {'service': 'inactive', 'dataset': 'd', 'run': 'r',
               'progress': {'status': 'complete', 'confirmed': 3,
                            'inclusions': evidence, 'inclusionTruncated': False}}
        with tempfile.TemporaryDirectory() as folder:
            cluster = SimpleNamespace(c=inventory(), home=Path(folder), call=lambda *_: row)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                execute(cluster, SimpleNamespace(load_action='status'))
            self.assertNotIn('inclusions', output.getvalue())
            self.assertNotIn('receipt-hash', output.getvalue())
            self.assertIn('confirmed', output.getvalue())
            saved = json.loads((Path(folder) / 'load-results/latest.json').read_text())
            self.assertEqual(saved[0]['progress']['inclusions'], evidence)
            self.assertEqual(row['progress']['inclusions'], evidence)

    def test_total_and_rates_are_preserved(self):
        rows = shards(inventory(), action='create', algorithm='ml', dataset='ml', run='create1',
                      accounts=1000, count=1000, qps=100, workers=8, fund_wei='100', value_wei='1')
        self.assertEqual([s['count'] for h, s in rows], [334, 333, 333])
        self.assertEqual(sum(s['accounts'] for h, s in rows), 1000)
        self.assertAlmostEqual(sum(s['qps'] for h, s in rows), 100)
        self.assertTrue(all(s['workers'] == 8 for h, s in rows))

    def test_rejects_invalid_limits(self):
        base = dict(action='create', algorithm='ml', dataset='ml', run='create1',
                    accounts=1000, count=1000, qps=100, workers=8, fund_wei='100', value_wei='1')
        for patch in ({'qps': float('nan')}, {'accounts': 2}, {'run': '../x'}, {'workers': 0}):
            with self.assertRaises(ValueError):
                shards(inventory(), **{**base, **patch})

    def test_account_partition_is_stable_between_actions(self):
        base = dict(algorithm='slh', dataset='vaults', run='run', accounts=1000,
                    qps=100, workers=16, fund_wei='100', value_wei='1')
        created = shards(inventory(), action='create', count=1000, **base)
        transferred = shards(inventory(), action='transfer', count=10001, **base)
        self.assertEqual([s['accounts'] for _, s in created], [s['accounts'] for _, s in transferred])
        self.assertEqual(sum(s['count'] for _, s in transferred), 10001)

    def test_no_bench_is_rejected(self):
        c = inventory()
        for host in c['hosts']:
            host['roles'] = ['validator']
        with self.assertRaises(ValueError):
            shards(c, action='create', algorithm='ml', dataset='ml', run='r',
                   accounts=3, count=3, qps=100, workers=8, fund_wei='100', value_wei='1')


class HostLoadTests(unittest.TestCase):
    def test_job_cannot_change_while_active_and_status_excludes_secrets(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import agent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c = inventory()
            payload = {'config': c, 'host': c['hosts'][0], 'token': 'test',
                       'spec': {'dataset': 'keys', 'run': 'transfer'}}
            agent.write(root / 'owner.json', {'token': 'test'})
            agent.write(root / 'runtime/load-job.json', payload['spec'])
            agent.write(root / 'runtime/load/keys/runs/transfer/progress.json',
                        {'confirmed': 12, 'secretKey': 'do-not-export'})
            with patch.object(agent, 'base', return_value=root), patch.object(agent, 'property_value', return_value='active'):
                with self.assertRaisesRegex(ValueError, 'Stop bench'):
                    agent.perform('load-config', payload)
                status = agent.perform('load-status', payload)
                self.assertEqual(status['progress'], {'confirmed': 12})
                self.assertEqual(status['service'], 'active')

    def test_legacy_bench_cannot_bypass_unresolved_run(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import agent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c = inventory()
            payload = {'config': c, 'host': c['hosts'][0], 'token': 'test'}
            agent.write(root / 'owner.json', {'token': 'test'})
            agent.write(root / 'runtime/load/unresolved.json', {'dataset': 'a', 'run': 'r'})
            with patch.object(agent, 'base', return_value=root), patch.object(agent, 'property_value', return_value='inactive'):
                for action in ('load-clear', 'smoke'):
                    with self.assertRaisesRegex(ValueError, 'Reconcile unresolved load'):
                        agent.perform(action, payload)
