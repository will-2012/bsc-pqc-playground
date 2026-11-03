"""Build a bench-only SLH signer using the same pinned OpenSSL as crypto-bench."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
DEFAULT = ROOT / "one-box/.runtime/build/bsc-pqc-slh-signer"


def build(output=DEFAULT):
    output = Path(output)
    work = output.parent / "slh-signer-build"
    work.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "tools/crypto-bench/sources.json").read_text())["openssl"]
    source = Path(__file__).with_name("slh.c")
    identity = {"openssl": lock["commit"], "archive_sha256": lock["sha256"],
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "build_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "check_sha256": hashlib.sha256(source.with_name("check.mjs").read_bytes()).hexdigest(),
                "wallet_lock_sha256": hashlib.sha256((ROOT / "erc4337-wallet/package-lock.json").read_bytes()).hexdigest(),
                "system": platform.system(), "arch": platform.machine(),
                "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
                "flags": "-O3; OpenSSL runtime CPU dispatch; static libcrypto"}
    stamp = output.with_suffix('.json')
    if output.exists() and stamp.exists():
        saved = json.loads(stamp.read_text())
        if saved.get('inputs') == identity and saved.get('binary_sha256') == hashlib.sha256(output.read_bytes()).hexdigest():
            return
    spec = importlib.util.spec_from_file_location("crypto_sources", ROOT / "tools/crypto-bench/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CACHE = work
    openssl = module.source("openssl", lock)
    targets = {("Darwin", "arm64"): "darwin64-arm64-cc", ("Linux", "x86_64"): "linux-x86_64",
               ("Linux", "aarch64"): "linux-aarch64"}
    target = targets.get((platform.system(), platform.machine()))
    if target is None:
        raise RuntimeError("Unsupported SLH signer platform")
    env = {k: v for k, v in os.environ.items() if k not in ("CFLAGS", "CPPFLAGS", "LDFLAGS", "CC", "CXX")}
    env["CC"] = "cc"
    with (work / "build.log").open('a') as log:
        def run(args, cwd):
            subprocess.run(list(map(str, args)), cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
        run(['perl', 'Configure', target, 'no-shared', 'no-module', 'no-apps', 'no-docs', '-O3'], openssl)
        run(['make', '-j4', 'build_libs'], openssl)
        temp = output.with_suffix('.tmp')
        run(['cc', '-O3', '-Wall', '-Wextra', '-Werror', '-I', openssl / 'include', source,
             openssl / 'libcrypto.a', '-lpthread', *(['-ldl'] if platform.system() == 'Linux' else []), '-o', temp], ROOT)
        run(['npm', 'ci', '--ignore-scripts'], ROOT / 'erc4337-wallet')
        run(['node', source.with_name('check.mjs'), temp], ROOT)
        temp.replace(output)
    stamp.write_text(json.dumps({'inputs': identity, 'binary_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2) + '\n')


if __name__ == '__main__':
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT)
    print('SLH signer built and cross-verified')
