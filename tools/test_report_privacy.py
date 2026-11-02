import unittest
from pathlib import Path
from report_privacy import ROOT, public_data, public_text

class ReportPrivacyTests(unittest.TestCase):
    def test_paths_and_host(self):
        value={'command':[str(ROOT/'tools/run.py'),str(Path.home()/'input.json')], 'host':'test-host', 'bytes':1234}
        self.assertEqual(public_data(value),{'command':['${REPO}/tools/run.py','${HOME}/input.json'],'host':'${HOST}','bytes':1234})
        self.assertEqual(value['bytes'],1234)

    def test_idempotent(self):
        value=public_text(str(ROOT/'docs/report.html'))
        self.assertEqual(public_text(value),value)
        self.assertEqual(public_data({'hash':'abcd','items':42}),{'hash':'abcd','items':42})

if __name__=='__main__':unittest.main()
