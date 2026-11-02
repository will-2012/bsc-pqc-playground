"""Transport and guard tests: no AWS or service mutations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import monitor


class MonitorTests(unittest.TestCase):
    def test_invalid_snapshot_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            reader = monitor.SharedSamples(temp, [{'id': 'a'}], 's', 15)
            target = Path(temp) / 'a.json'
            for value in ([], None, {'host': 'a', 'session': 's', 'sequence': '1'},
                          {'host': 'a', 'session': 's', 'sequence': True}):
                target.write_text(json.dumps(value))
                self.assertEqual(reader.read(1)['a']['error'], 'MISSING_SAMPLE')
            target.write_text(' ' * (4 * 1024 * 1024 + 1))
            self.assertEqual(reader.read(2)['a']['error'], 'MISSING_SAMPLE')

    def test_shared_samples_expire_on_unchanged_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            reader = monitor.SharedSamples(temp, [{'id': 'a'}], 'session', 15)
            self.assertEqual(reader.read(0)['a']['error'], 'MISSING_SAMPLE')
            target = Path(temp) / 'a.json'
            monitor.write(target, {'host': 'a', 'session': 'session', 'sequence': 1})
            self.assertNotIn('error', reader.read(1)['a'])
            self.assertEqual(reader.read(17)['a']['error'], 'STALE_SAMPLE')
            monitor.write(target, {'host': 'a', 'session': 'session', 'sequence': 2})
            self.assertNotIn('error', reader.read(18)['a'])
            monitor.write(target, {'host': 'a', 'session': 'old', 'sequence': 3})
            self.assertEqual(reader.read(19)['a']['error'], 'MISSING_SAMPLE')

    def test_lease_only_renews_for_new_control(self):
        with patch('monitor.time.monotonic', return_value=0):
            lease = monitor.Lease(45)
            lease.update({'heartbeat': 1})
        with patch('monitor.time.monotonic', return_value=50):
            lease.update({'heartbeat': 1})
            self.assertTrue(lease.expired())
            lease.update({'heartbeat': 2})
            self.assertFalse(lease.expired())
            lease.update({'heartbeat': 3, 'stopBench': True})
            lease.update({'heartbeat': 4, 'stopBench': False})
            self.assertTrue(lease.stop)

    def test_stop_matches_run_and_only_targets_bench(self):
        with tempfile.TemporaryDirectory() as temp:
            c = {'deploy_root': temp, 'name': 'test'}
            h = {'roles': ['validator', 'bundler', 'bench']}
            job = Path(temp) / 'test/runtime/load-job.json'
            target = {'dataset': 'a', 'run': 'r'}
            monitor.write(job, {'dataset': 'a', 'run': 'new'})
            with patch('agent.check_owner'), patch('monitor.subprocess.run') as call:
                self.assertEqual(monitor.request_stop(c, h, 'token', target), 'JOB_CHANGED')
                call.assert_not_called()
                monitor.write(job, target)
                call.return_value.returncode = 0
                call.return_value.stdout = str(Path(temp) / 'test/current/one-box/remote/agent.py')
                with patch('monitor.service_state', return_value='active'):
                    self.assertEqual(monitor.request_stop(c, h, 'token', target), 'STOP_REQUESTED')
                self.assertEqual(call.call_args.args[0], ['systemctl', '--no-block', 'stop', 'bsc-pqc-test-bench.service'])

    def test_stop_drain_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            c = {'deploy_root': temp, 'name': 'test'}
            target = {'dataset': 'a', 'run': 'r'}
            monitor.write(Path(temp) / 'test/runtime/load-job.json', target)
            with patch('agent.check_owner'), patch('monitor.service_state', return_value='deactivating'), \
                 patch('monitor.subprocess.run') as call:
                self.assertEqual(monitor.request_stop(c, {'roles': ['bench']}, 't', target), 'DRAINING')
                call.assert_not_called()

    def test_writes_private_files(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'latest.json'
            scratch = p.with_suffix('.tmp')
            scratch.touch(mode=0o644)
            scratch.chmod(0o644)
            monitor.write(p, {'sequence': 1})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(p.read_text()), {'sequence': 1})
