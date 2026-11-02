import json, tempfile, unittest
from pathlib import Path
from compare import generate, CATEGORIES

class ComparisonTests(unittest.TestCase):
    def test_totals_and_per_account_use_all_three_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';new=root/'new'
            def put(path,x):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(x))
            for folder,algo in [(old,'ecdsa'),(new,'slh-storage')]:
                put(folder/'manifest.json',{'binarySha256':'same','config':{},'historyPolicy':'full','platform':'same','status':'complete','groups':{algo:{'completed':[100]}}})
                for node in range(3):
                    for n in [0,100]:
                        factor=(node+1)*(1 if algo=='ecdsa' else 2)
                        rows=[{'store':'Key-Value store','category':c,'approxBytes':1000+n*factor,'items':10+n if c=='Account snapshot' else 10} for c in sorted({c for cats in CATEGORIES.values() for c in cats})]
                        put(folder/algo/str(n)/f'node{node}.json',{'inspect':rows,'physicalBytes':{'chaindata':1000+n*factor}})
            result=generate(old,new,new/'comparison.html')
            row=next(r for r in result['summary'] if r['algorithm']=='slh-storage')['metrics']['disk']
            self.assertEqual(row['threeNodesBytes'],1200)
            self.assertEqual(row['meanBytesPerAccount'],4)
            self.assertEqual(row['ecdsaRatio'],2)
            manifest=json.loads((new/'manifest.json').read_text());manifest['binarySha256']='different';put(new/'manifest.json',manifest)
            with self.assertRaisesRegex(ValueError,'binarySha256'):generate(old,new,new/'comparison.html')

if __name__=='__main__':unittest.main()
