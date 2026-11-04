#!/usr/bin/env python3
"""Recompute published pricing, validate sample coverage and render freshness."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import run
from report import render


def verify(path):
    data = json.loads(Path(path).read_text())
    for name, source in data['sourceSnapshots'].items():
        assert hashlib.sha256(source['content'].encode()).hexdigest() == source['sha256'], name
    assert data['sourceSnapshots']['pricing_test.go']['content'] == data['measuredBenchmarkSource']
    for result in data['runs']:
        rows = result['rows']
        assert result['rounds'] >= 5 and len(rows) == result['rounds'] * len(run.CASES)
        assert len({(r['round'], r['name']) for r in rows}) == len(rows)
        for round_ in range(1, result['rounds'] + 1):
            assert {r['name'] for r in rows if r['round'] == round_} == set(run.CASES)
        for row in rows:
            assert row['iterations'] > 0
            assert all(math.isfinite(v) and v >= 0 for v in row['metrics'].values())
            assert row['metrics']['cpu-ns/op'] > 0
            for family, gas in [('ML', 10000), ('SLH', 60000)]:
                if row['name'].startswith('Pricing/' + family + '_'):
                    assert row['metrics']['gas/op'] == gas
        assert result['summary'] == run.summarize(rows)
        assert result['pricing'] == run.pricing(result['summary'])
        assert result['runnerSha256'] == data['sourceSnapshots']['run.py']['sha256']
        assert hashlib.sha256(result['checks'].encode()).hexdigest() == result['checksSha256']
        assert result['checks'].endswith('PASS\n') and 'FAIL' not in result['checks']
    builds = list(data['builds'].values())
    assert builds[0]['runSourceSha256'] == builds[1]['runSourceSha256']
    assert builds[0]['mlLibrarySha256'] != builds[1]['mlLibrarySha256']
    assert data['reusableBuildVerification']['correctnessBothPassed']
    # Publication must not include connection details or private workspace paths.
    serialized = json.dumps(data)
    for pattern in [r'/(?:Users|home|server|mnt)/|\.codex/|tsh (?:ssh|login)',
                    r'\b(?:10\.\d+|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b',
                    r'arn:aws:|\bi-[0-9a-f]{8,17}\b|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY']:
        assert not re.search(pattern, serialized), 'Private information in published data'
    html = Path(path).with_name('report.html')
    assert html.read_text() == render(data), 'Stale pricing HTML'
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    data = verify(args.results)
    print('PASS: samples, clocks, quotes, sources, correctness logs, privacy and HTML; '
          + str(sum(len(r['rows']) for r in data['runs'])) + ' groups')
