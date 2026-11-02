import unittest
from measure import parse_inspect
class InspectTests(unittest.TestCase):
    def test_units_and_count(self):
        rows=parse_inspect('| Key-Value store | Path trie account nodes | 1.25 MiB | 100 |\n| Key-Value store | Storage snapshot | 0.00 B | 0 |')
        self.assertEqual(rows[0]['approxBytes'],1310720)
        self.assertEqual(rows[1]['items'],0)
    def test_reject_incomplete(self):
        with self.assertRaises(ValueError): parse_inspect('failed to open database')
if __name__=='__main__': unittest.main()
