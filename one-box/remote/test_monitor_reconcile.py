"""Offline evidence accounting; no RPC or service operations."""
import unittest
from monitor_reconcile import reconcile


def h(n):
    return '0x' + format(n, '064x')


def block(n=10, digest=None, transactions=None, userops=2):
    txs = transactions or [h(1), h(2)]
    return {'number': n, 'hash': digest or h(n + 100), 'txCount': len(txs),
            'txHashes': txs, 'txHashesTruncated': False, 'userOps': userops}


def sample(*blocks, complete=True):
    return {'blocks': list(blocks), 'coverage': {'complete': complete}}


def bench(*rows, truncated=False):
    return {'dataset': 'test', 'run': 'one', 'progress': {'inclusions': list(rows),
            'inclusionTruncated': truncated}}


def receipt(n=10, digest=None, tx=None, ops=1, count=1):
    return {'blockNumber': n, 'blockHash': digest or h(n + 100),
            'transactions': [tx or h(1)], 'userOps': ops, 'count': count}


class ReconcileTests(unittest.TestCase):
    def test_failed_bench_does_not_reuse_stale_receipts(self):
        stale = {**bench(receipt()), 'error': 'STALE_SAMPLE'}
        out = reconcile({'a': sample(block())}, {'a': stale})
        self.assertEqual(out['blocks'][0]['benchConfirmed'], 0)
        self.assertEqual(out['status'], 'partial')

    def test_replicas_not_summed_and_shared_outer_tx_deduplicated(self):
        b = block()
        out = reconcile({'a': sample(b), 'b': sample(b)},
                        {'a': bench(receipt()), 'b': bench(receipt())})
        row = out['blocks'][0]
        self.assertEqual(row['chainTransactions'], 2)
        self.assertEqual(row['benchTransactions'], 1)
        self.assertEqual(row['benchUserOps'], 2)
        self.assertEqual(row['benchConfirmed'], 2)
        self.assertEqual(row['matchedTransactions'], 1)
        self.assertEqual(out['status'], 'matched_observed_receipts')

    def test_missing_late_receipt_or_block_is_partial_not_loss(self):
        out = reconcile({'a': sample(block())}, {'a': bench(receipt(n=11))})
        self.assertEqual(out['status'], 'partial')
        self.assertIn('block_not_in_reference_window', out['blocks'][-1]['reasons'])
        self.assertNotIn('lost', str(out))
        late = reconcile({'a': sample(block())}, {'a': bench()})
        self.assertEqual(late['blocks'][0]['status'], 'unattributed')

    def test_tx_hash_truncation_never_claims_complete_alignment(self):
        b = block(); b['txHashesTruncated'] = True
        out = reconcile({'a': sample(b)}, {'a': bench(receipt(tx=h(3)))})
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['blocks'][0]['unmatchedReceiptTransactions'], 1)
        self.assertIn('chain_tx_hashes_incomplete', out['blocks'][0]['reasons'])

    def test_unknown_userop_log_count_stays_unknown(self):
        b = block(userops=None)
        out = reconcile({'a': sample(b)}, {'a': bench(receipt())})
        self.assertIsNone(out['blocks'][0]['chainUserOps'])
        self.assertEqual(out['status'], 'partial')

    def test_bounded_window_and_truncated_bench(self):
        blocks = [block(n) for n in range(200)]
        out = reconcile({'a': sample(*blocks)}, {'a': bench(receipt(n=199), truncated=True)})
        self.assertEqual(len(out['blocks']), 128)
        self.assertIn('comparison_window_truncated', out['reasons'])
        self.assertIn('bench_window_truncated', out['reasons'])

    def test_different_same_height_hashes_are_hints_not_stop_decisions(self):
        out = reconcile({'a': sample(block()), 'b': sample(block(digest=h(99)))}, {})
        self.assertEqual(out['reorgHints'][0]['blockNumber'], 10)
        self.assertEqual(out['reorgHints'][0]['hashCount'], 2)
        self.assertNotIn('stop', out)

    def test_duplicate_bench_row_not_counted_twice(self):
        out = reconcile({'a': sample(block())}, {'a': bench(receipt(), receipt())})
        self.assertEqual(out['blocks'][0]['benchUserOps'], 1)
        self.assertIn('duplicate_bench_block_evidence', out['reasons'])

    def test_no_private_payload_exported(self):
        row = receipt(); row.update(secretKey='private fixture', signed='signed fixture')
        out = reconcile({'a': sample(block())}, {'a': bench(row)})
        self.assertNotIn('private fixture', str(out))
        self.assertNotIn('signed fixture', str(out))

    def test_malformed_or_absent_data_is_partial(self):
        out = reconcile({'a': {'error': 'timeout'}}, {'a': {'progress': {}}})
        self.assertEqual(out['status'], 'partial')
        self.assertIsNone(out['reference'])
        self.assertEqual(out['blocks'], [])


if __name__ == '__main__':
    unittest.main()
