#!/usr/bin/env python3
"""Offline reconciliation of the published crypto and v6 chain reports."""
import contextlib
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
CHAIN = DOCS / 'chain-bench'
ARCHIVE = CHAIN / 'v6'
CASES = [
    ('SLH-128s v2', 'slh-reference.json', 'SLHV2Valid'),
    ('Falcon-512 v3', 'falcon-reference.json', 'FalconV3Valid'),
    ('ML-DSA-44 v4', 'mldsa44-reference.json', 'MLDSAV4Valid'),
    ('ML-DSA-65 v5', 'mldsa65-reference.json', 'MLDSAV5Valid'),
    ('SLH-128f v6', 'results.json', 'SLHV6Valid'),
]


def read(path):
    return json.loads(path.read_text())


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    crypto = load_module('crypto_report', ROOT / 'tools/crypto-bench/run.py')
    render = load_module('chain_report', ROOT / 'tools/chain-bench/report.py')
    with contextlib.redirect_stdout(io.StringIO()):
        data = crypto.verify(DOCS / 'crypto-bench/macos-arm64/results.json')
    # The historical renderer may change; timed code and dependency pins may not.
    for name in ['main.go', 'native.c', 'native.h', 'mldsa.c', 'falcon.c', 'go.mod', 'sources.json']:
        assert sha(ROOT / 'tools/crypto-bench' / name) == data['source_snapshot'][name]['sha256'], name
    old_runner = data['source_snapshot']['run.py']['content']
    current_runner = (ROOT / 'tools/crypto-bench/run.py').read_text()
    assert old_runner.split('NOTES=[')[0] == current_runner.split('NOTES=[')[0], 'crypto build or aggregation changed'
    assert old_runner.split('def main():', 1)[1] == current_runner.split('def main():', 1)[1], 'crypto orchestration changed'
    native = read(ARCHIVE / 'precompile.json')
    assert native['rounds'] == 5 and len(native['rows']) == 125
    for name, summary in native['summary'].items():
        rows = [r for r in native['rows'] if r['name'] == name]
        assert len(rows) == 5 and {r['round'] for r in rows} == set(range(1, 6)), name
        for field, source in [('medianUs', 'nsPerOp'), ('wallMedianUs', 'wallNsPerOp'), ('threadMedianUs', 'threadNsPerOp')]:
            assert summary[field] == statistics.median(r[source] for r in rows) / 1000, name
        assert summary['maxUs'] == max(r['nsPerOp'] for r in rows) / 1000, name
    baseline = native['summary']['SLHV2ECRecover']['medianUs']
    for family, prefix in [('SLH','SLHV2'), ('SLHF','SLHV6'), ('Falcon','FalconV3'), ('MLDSA','MLDSAV4'), ('MLDSA65','MLDSAV5')]:
        price = native['pricing'][family]
        maximum = max(r['nsPerOp'] / 1000 for r in native['rows']
                      if r['name'].startswith(prefix) and not r['name'].endswith(('Sign', 'ECRecover')))
        assert price['worstMeasuredUs'] == maximum
        raw = math.ceil((3000 * maximum / baseline * 1.25) / 1000) * 1000
        assert price['recommendedGas'] == raw and price['selectedGas'] == math.ceil(raw / 10000) * 10000
    text = (CHAIN / 'README.md').read_text()
    hashes = set()
    traces = {item['profile']: item for item in read(ARCHIVE / 'native-gas.json')['checks']}
    family_names = ['SLH', 'Falcon', 'MLDSA', 'MLDSA65', 'SLHF']
    for (label, filename, name), family in zip(CASES, family_names):
        case = read(ARCHIVE / filename)
        price = native['pricing'][family]
        cpu_row = f"| {label.rsplit(' ', 1)[0]} | {native['summary'][name]['medianUs']:,.2f} us | {price['worstMeasuredUs']:,.2f} us | {price['selectedGas']:,} gas |"
        assert cpu_row in text, label
        trace = traces[case['profile']]
        assert int(trace['gasUsed']) == price['selectedGas']
        assert trace['result'] == '0x' + '00' * 31 + '01'
    for name, summary_name in [('SLH-DSA-SHA2-128s', 'SLHV2Valid'), ('SLH-DSA-SHA2-128f', 'SLHV6Valid')]:
        item = next(item for item in data['summary'] if item['algorithm'] == name)
        assert f"{item['verify']['cpu_us']:,.2f} / {native['summary'][summary_name]['medianUs']:,.2f} us" in text, name

    for label, filename, _ in CASES:
        case = read(ARCHIVE / filename)
        assert case['count'] == len(case['rows']) == 3
        for operation, title in [('created', '创建账户'), ('transferred', '已部署账户转账')]:
            values = [int(row[operation]['outerTransactionGas']) for row in case['rows']]
            expected = f'| {label} | {title} | {statistics.mean(values):,.0f} gas | {statistics.mean(values)/21000:.2f} 倍 | {min(values):,}–{max(values):,} gas |'
            assert expected in text, (label, title)
            for row in case['rows']:
                item = row[operation]
                floor = 21000 + 10 * (int(item['calldataZeroBytes']) + 4 * (int(item['calldataBytes']) - int(item['calldataZeroBytes'])))
                assert int(item['calldataFloorGas']) == floor <= int(item['outerTransactionGas'])
                if label.startswith('SLH') and operation == 'transferred':
                    assert floor == int(item['outerTransactionGas'])
                hashes.add(item['transactionHash'])
        for tx in case.get('ecdsaBaseline', []):
            assert int(tx['gasUsed']) == 21000
            hashes.add(tx['transactionHash'])
    load = read(ARCHIVE / 'load.json')
    for result in load['results']:
        assert result['operations'] == len(result['rows']) == 40
        outer = {row['transactionHash'] for row in result['rows']}
        assert len(outer) == result['uniqueOuterTransactions']
        hashes.update(outer)
    assert [r['uniqueOuterTransactions'] for r in load['results']] == [5, 10]
    env = read(ARCHIVE / 'environment.json')
    recorded = env['receiptAudit']
    assert recorded['allSuccessfulAndCanonical'] and recorded['validators'] == 3
    assert len(hashes) == recorded['uniqueOuterTransactions'] == 48
    assert hashes == set(recorded['signedTransactionBytes'])
    assert max(recorded['signedTransactionBytes'].values()) == recorded['maxSignedTransactionBytes'] <= 131072
    for name, expected in env['sourceSha256'].items():
        if not name.endswith('.md') and name != 'tools/chain-bench/report.py':
            assert sha(ROOT / name) == expected, f'measured source changed: {name}'
    for artifact in ARCHIVE.glob('*.json'):
        def public(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    assert key.lower() not in {'privatekey', 'secretkey', 'password', 'mnemonic', 'executor-private-keys', 'utility-private-key'}, (artifact, key)
                    public(item)
            elif isinstance(value, list):
                for item in value:
                    public(item)
        public(read(artifact))
    # Render to a temporary directory to check freshness without modifying reports.
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)
        crypto.DOCS = target
        crypto.render(data)
        assert (target / 'comparison.html').read_bytes() == (DOCS / 'crypto-bench/comparison.html').read_bytes(), 'stale crypto HTML'
        shutil.copy(CHAIN / 'README.md', target / 'README.md')
        (target / 'v6').mkdir()
        for name in ['results.json', 'slh-reference.json', 'precompile.json']:
            shutil.copy(ARCHIVE / name, target / 'v6' / name)
        render.REPORT = target
        with contextlib.redirect_stdout(io.StringIO()):
            render.main()
        assert (target / 'report.html').read_bytes() == (CHAIN / 'report.html').read_bytes(), 'stale chain HTML'
    for report in [DOCS / 'crypto-bench/comparison.html', CHAIN / 'report.html']:
        for href in re.findall(r'href="([^"]+)"', report.read_text()):
            if not href.startswith(('https:', 'http:', '#')):
                assert (report.parent / href.split('#')[0]).exists(), href
    print('PASS: crypto statistics, 125 native groups, pricing, gas/floor, 80 operations / 48 transactions, source pins, public data and both HTML reports')


if __name__ == '__main__':
    main()
