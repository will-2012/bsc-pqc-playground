import unittest
from test_cluster import inventory
from load_bench import shards

class ShardingTests(unittest.TestCase):
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
