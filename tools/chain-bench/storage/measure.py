#!/usr/bin/env python3
"""Offline per-node snapshot; usable unchanged on macOS and Linux nodes."""
import argparse, datetime, hashlib, json, platform, re, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from report_privacy import public_data, public_text


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def allocated(path):
    if not Path(path).exists():
        return 0
    return int(subprocess.check_output(['du', '-sk', str(path)], text=True).split()[0]) * 1024


def parse_inspect(text):
    rows = []
    for line in re.sub(r'\x1b\[[0-9;]*m', '', text).splitlines():
        if not line.startswith('|'):
            continue
        cells = [s.strip() for s in line.strip().strip('|').split('|')]
        if len(cells) != 4:
            continue
        match = re.fullmatch(r'([\d.]+)\s*(B|KiB|MiB|GiB|TiB)', cells[2])
        if match and cells[0] and cells[3].isdigit():
            rows.append({'store': cells[0], 'category': cells[1], 'sizeDisplay': cells[2],
                         'approxBytes': float(match[1]) * 1024 ** ['B','KiB','MiB','GiB','TiB'].index(match[2]),
                         'items': int(cells[3])})
    if not any(r['category'] == 'Path trie account nodes' for r in rows):
        raise ValueError('Missing path trie categories in inspect output')
    return rows


def measure(binary, datadir, output, node, count):
    binary, datadir, output = map(Path, (binary, datadir, output))
    output.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary.resolve()), '--datadir', str(datadir.resolve()), '--state.scheme', 'path', '--db.engine', 'pebble', 'db', 'inspect']
    before = allocated(datadir / 'geth/chaindata')
    compact=cmd[:-2]+['db','compact']
    if shutil.disk_usage(datadir).free < max(30*2**30,before):
        raise RuntimeError('Insufficient free space for offline compaction')
    with (output / f'{node}-compact.txt').open('w') as f:
        subprocess.run(compact,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (output / f'{node}-inspect.txt').open('w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    for log in [output / f'{node}-compact.txt', output / f'{node}-inspect.txt']:
        log.write_text(public_text(log.read_text()))
    text = (output / f'{node}-inspect.txt').read_text()
    result = {'schema': 1, 'node': node, 'accounts': count,
              'measuredAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'host': platform.node(), 'platform': platform.platform(),
              'binarySha256': digest(binary), 'command': cmd, 'compactCommand': compact,
              'physicalBytes': {'datadir': allocated(datadir), 'chaindataBeforeCompact': before,
                                'chaindata': allocated(datadir/'geth/chaindata'),
                                'ancient': allocated(datadir/'geth/chaindata/ancient')},
              'freeBytes': shutil.disk_usage(datadir).free, 'inspect': parse_inspect(text),
              'note': 'KV sizes are logical key+value bytes rounded by geth, not compressed physical sizes. Ancient is included in chaindata; do not add twice.'}
    result = public_data(result)
    (output / f'{node}.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    return result

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', required=True); p.add_argument('--datadir', required=True)
    p.add_argument('--output', required=True); p.add_argument('--node', required=True)
    p.add_argument('--accounts', type=int, required=True)
    a = p.parse_args(); measure(a.binary,a.datadir,a.output,a.node,a.accounts)
