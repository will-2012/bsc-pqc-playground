import unittest
from test_cluster import inventory
from load_bench import shards

class ShardingTests(unittest.TestCase):
    def test_direct_and_signer_options_are_explicit_and_validated_before_remote_calls(self):
        import contextlib, io, json, tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from load_bench import execute
        with tempfile.TemporaryDirectory() as folder:
            gas_file = Path(folder) / 'gas.json'
            gas = dict(callGasLimit='30000', verificationGasLimit='300000', preVerificationGas='150000', outerGasLimit='750000')
            gas_file.write_text(json.dumps(gas))
            calls = []
            cluster = SimpleNamespace(c=inventory(), state={'ready': True},
                call=lambda h, action, **kw: calls.append((action, kw)))
            base = dict(load_action='transfer', algorithm='ml', dataset='d', run='r', accounts=3,
                count=3, qps=3, workers=1, max_inflight=1, cache_accounts=1,
                fund_wei='100', value_wei='1', duration=0, slh_backend='noble',
                submission='direct', direct_gas_file=gas_file, ml_backend='persistent')
            with contextlib.redirect_stdout(io.StringIO()):
                execute(cluster, SimpleNamespace(**base))
            specs = [kw['spec'] for action, kw in calls if action == 'load-config']
            self.assertEqual(len(specs), 3)
            self.assertTrue(all(s['submission'] == 'direct' and s['mlBackend'] == 'persistent' and s['directGas'] == gas for s in specs))
            for changes in ({'algorithm': 'ecdsa'}, {'ml_backend': 'unknown'}, {'direct_gas_file': None}, {'load_action': 'create'}):
                calls.clear()
                with self.assertRaises(ValueError):
                    execute(cluster, SimpleNamespace(**{**base, **changes}))
                self.assertEqual(calls, [])

    def test_random_recipient_pool_is_shared_without_key_material(self):
        import json
        import tempfile
        import contextlib
        import io
        from pathlib import Path
        from types import SimpleNamespace
        from load_bench import execute
        addresses = ['0x' + format(i, '040x') for i in (1, 2, 3)]
        with tempfile.TemporaryDirectory() as folder:
            pool = Path(folder) / 'addresses.json'
            pool.write_text(json.dumps(addresses))
            calls = []
            cluster = SimpleNamespace(c=inventory(), state={'ready': True},
                call=lambda h, action, **kw: calls.append((action, kw)))
            args = SimpleNamespace(load_action='transfer', algorithm='ml', dataset='d', run='r',
                accounts=3, count=3, qps=3, workers=1, max_inflight=1, cache_accounts=1,
                fund_wei='100', value_wei='1', duration=0, slh_backend='noble', recipients_file=pool)
            with contextlib.redirect_stdout(io.StringIO()):
                execute(cluster, args)
            specs = [kw['spec'] for action, kw in calls if action == 'load-config']
            self.assertEqual(len(specs), 3)
            self.assertTrue(all(s['recipientAddresses'] == addresses for s in specs))
            calls.clear()
            pool.write_text(json.dumps([addresses[0], addresses[0]]))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                execute(cluster, args)
            self.assertEqual(calls, [])

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
        self.assertTrue(all(s['slhBackend'] == 'avx2' for _, s in shards(inventory(), slh_backend='avx2', **base)))
        self.assertTrue(all(s['slhBackend'] == 'avx512' for _, s in shards(inventory(), slh_backend='avx512', **base)))
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
    def test_native_signer_is_checked_before_job_is_written(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        import agent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c = inventory()
            spec = {'dataset': 'keys', 'run': 'prepare', 'algorithm': 'slh', 'slhBackend': 'avx512'}
            payload = {'config': c, 'host': c['hosts'][0], 'token': 'test', 'spec': spec}
            agent.write(root / 'owner.json', {'token': 'test'})
            runner = root / 'current/tools/chain-bench/load/run.mjs'
            runner.parent.mkdir(parents=True); runner.touch()
            signer = root / 'current/one-box/.runtime/build/bsc-pqc-slh-avx512-signer'
            with patch.object(agent, 'base', return_value=root), patch.object(agent, 'property_value', return_value='inactive'):
                with self.assertRaisesRegex(ValueError, 'Build release'):
                    agent.perform('load-config', payload)
                signer.parent.mkdir(parents=True); signer.touch(); signer.chmod(0o700)
                with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                    with self.assertRaisesRegex(ValueError, 'cannot run'):
                        agent.perform('load-config', payload)
                self.assertFalse((root / 'runtime/load-job.json').exists())
                with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as probe:
                    agent.perform('load-config', payload)
                    self.assertEqual(probe.call_args.kwargs['input'], b'')
                self.assertEqual(agent.read(root / 'runtime/load-job.json'), spec)

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
