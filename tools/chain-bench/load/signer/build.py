"""Build optional native SLH signers for the load client."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
DEFAULT = ROOT / "one-box/.runtime/build/bsc-pqc-slh-signer"


def build(output=None, backend="openssl"):
    if backend in ("avx2", "avx512"):
        return build_simd(output or DEFAULT.with_name(f"bsc-pqc-slh-{backend}-signer"), backend)
    if backend != "openssl":
        raise ValueError("Unknown SLH signer backend")
    output = Path(output or DEFAULT)
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


def supports_simd(backend):
    feature = {"avx2": "avx2", "avx512": "avx512f"}[backend]
    return (platform.system() == "Linux" and platform.machine() == "x86_64"
            and {"avx2", feature} <= set(Path("/proc/cpuinfo").read_text().split()))


def build_simd(output, backend):
    if not supports_simd(backend):
        raise RuntimeError(f"SLH {backend} signer requires a compatible Linux x86_64 CPU")
    compiler = shutil.which("c++")
    if not compiler:
        raise RuntimeError("SLH SIMD signer requires a C++ compiler")
    output = Path(output)
    work = output.parent / f"slh-{backend}-build"
    work.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    lock = json.loads((here / "parallel.json").read_text())[backend]
    flags = "-O3 -std=c++17 -mavx2 -mrdrnd -fno-fast-math"
    if backend == "avx512":
        flags += " -mavx512f -DBSC_PQC_AVX512"
    identity = {"upstream": lock, "compiler": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
                "flags": flags, "threads_per_signer": 1,
                "files": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                          for name in ("parallel.cpp", "build.py", "check.mjs", "client.mjs")},
                "wallet_lock_sha256": hashlib.sha256((ROOT / "erc4337-wallet/package-lock.json").read_bytes()).hexdigest()}
    stamp = output.with_suffix(".json")
    if output.exists() and stamp.exists():
        saved = json.loads(stamp.read_text())
        if saved.get("inputs") == identity and saved.get("binary_sha256") == hashlib.sha256(output.read_bytes()).hexdigest():
            return
    spec = importlib.util.spec_from_file_location("slh_avx2_sources", ROOT / "tools/crypto-bench/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CACHE = work
    source = module.source("parallel-slh", lock)
    env = {k: v for k, v in os.environ.items() if k not in ("CFLAGS", "CPPFLAGS", "LDFLAGS", "CC", "CXX")}
    with (work / "build.log").open("a") as log:
        def run(args, cwd):
            subprocess.run(list(map(str, args)), cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                           check=True, env=env, timeout=600)
        run(["make", "-B", "-j2", "slh-dsa-fast.a", "test_slh_dsa", "CPP=" + compiler, "CFLAGS=" + flags], source)
        run([source / "test_slh_dsa", "testkeygen", "testsign", "sha512", "context", "-q"], source)
        temp = output.with_suffix(".tmp")
        run([compiler, *flags.split(), "-I", source, here / "parallel.cpp",
             source / "slh-dsa-fast.a", "-lpthread", "-o", temp], ROOT)
        run(["npm", "ci", "--ignore-scripts"], ROOT / "erc4337-wallet")
        run(["node", here / "check.mjs", temp], ROOT)
        temp.replace(output)
    stamp.write_text(json.dumps({"inputs": identity, "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--backend", choices=("openssl", "avx2", "avx512"), default="openssl")
    args = parser.parse_args()
    build(args.output, args.backend)
    print('SLH signer built and cross-verified')
