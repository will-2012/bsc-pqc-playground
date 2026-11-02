"""Independent Linux ML44 + SLH128s client. Does not change v1–v7 builders."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from devnet import ROOT, BOX, command, read, write, digest, binary_info, doctor
import precompile

WORK = BOX / ".runtime/build/cluster"


def inputs_manifest():
    paths = [
        Path(__file__),
        BOX / "patches/bsc-slh-v2.patch",
        BOX / "patches/bsc-pq-v3.patch",
    ]
    for version in ("v2", "v4"):
        paths.extend(p for p in (BOX / "precompile" / version).rglob("*") if p.is_file())
    paths.append(ROOT / "tools/crypto-bench/testdata/standard.tsv")
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def prepare_sources():
    commit = precompile.prepare(WORK)
    source = WORK / "bsc"
    log = WORK / "build.log"
    command(["patch", "--batch", "-p1", "-i", BOX / "patches/bsc-pq-v3.patch"], log, source)
    spec = importlib.util.spec_from_file_location("sources", ROOT / "tools/crypto-bench/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CACHE.mkdir(parents=True, exist_ok=True)
    ml = WORK / "mldsa"
    if ml.exists():
        shutil.rmtree(ml)
    shutil.copytree(
        module.source("mldsa", read(BOX / "precompile/v4/dependency.json")),
        ml,
        ignore=shutil.ignore_patterns(".git", "build"),
    )
    # Portable C avoids tying a release to the build host's CPU extensions.
    old = os.environ.get("CFLAGS")
    os.environ["CFLAGS"] = "-O3 -fno-fast-math"
    try:
        for targets in [["test/build/libmldsa44.a"], ["run_func_44", "run_kat_44"]]:
            command(
                ["make", "-j4", "CC=cc", "OPT=0", "AUTO=0", *targets],
                log,
                ml,
                timeout=900,
            )
    finally:
        if old is None:
            os.environ.pop("CFLAGS", None)
        else:
            os.environ["CFLAGS"] = old
    package = source / "crypto/pqmldsa"
    shutil.copytree(BOX / "precompile/v4/native", package)
    shutil.copytree(ml / "mldsa", package / "backend")
    shutil.copy2(ml / "test/build/libmldsa44.a", package / "libmldsa44.a")
    vm = source / "core/vm"
    s = (BOX / "precompile/v4/mldsa_v4.go").read_text().split("func v4Precompiles")[0]
    s = s.replace('\n\t"maps"', "")
    (vm / "mldsa_v4.go").write_text(
        s
        + """func v3Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
 r := v2Precompiles(cfg, rules, base)
 // v2 is gated on PQTime in this isolated build.
 if r[slhV2Address] != nil { r[mldsaV4Address] = &mldsaV4{} } else { delete(r,mldsaV4Address) }
 return r
}
func v4Precompiles(cfg *params.ChainConfig,rules params.Rules,base PrecompiledContracts) PrecompiledContracts { return v3Precompiles(cfg,rules,base) }
"""
    )
    p = vm / "slh_v2.go"
    s = p.read_text().replace("rules.IsJenner &&", "rules.IsJenner && rules.IsPQ &&")
    # Always clone: removal of an inactive ML entry must not mutate caller maps.
    s = s.replace("if !active && base[slhV2Address] == nil {\n\t\treturn base\n\t}", "")
    p.write_text(s)
    (vm / "cluster_disabled.go").write_text("""//go:build !pqprecompile
package vm
import "github.com/ethereum/go-ethereum/params"
func v3Precompiles(c *params.ChainConfig,r params.Rules,b PrecompiledContracts) PrecompiledContracts{return b}
""")
    # Reuse vectors, bad encodings, gas-boundary and activation tests; omit Falcon checks only.
    p = vm / "slh_v2_test.go"
    s = (
        p.read_text()
        .replace("constructed := NewEVM", "full.PQTime = &zero\n constructed := NewEVM")
        .replace(
            "evm := &EVM",
            "activation := uint64(0)\n cfg.PQTime = &activation\n rules.IsPQ = true\n evm := &EVM",
        )
    )
    # Existing gate tests construct Rules directly and now also need IsPQ.
    s = s.replace(
        "rules := params.Rules{IsInBSC: true, IsJenner: true}",
        "rules := params.Rules{IsInBSC: true, IsJenner: true, IsPQ: true}",
    )
    p.write_text(s)
    for name in [
        "mldsa_v4_test.go",
        "mldsa_v4_bench_test.go",
        "mldsa_v4_fuzz_test.go",
        "mldsa_v4_disabled_test.go",
    ]:
        s = (BOX / "precompile/v4" / name).read_text()
        s = s.replace(" || (r[falconV3Address] != nil) != want", "").replace(
            " || (evm.precompiles[falconV3Address] != nil) != want", ""
        )
        (vm / name).write_text(s)
    shutil.copy2(ROOT / "tools/crypto-bench/testdata/standard.tsv", vm / "testdata/mldsa_v4.tsv")
    wallet = source / "cmd/pq-wallet"
    wallet.mkdir()
    s = (
        (BOX / "precompile/v4/wallet/main.go")
        .read_text()
        .replace('\n\t"github.com/ethereum/go-ethereum/crypto/pqfalcon"', "")
    )
    start = s.index('\tif r.Algorithm == "falcon"')
    end = s.index("\n\tout :=", start)
    s = (
        s[:start]
        + '\tif r.Algorithm != "" && r.Algorithm != "mldsa" { panic("unsupported algorithm") }'
        + s[end:]
    )
    (wallet / "main.go").write_text(s)
    command(
        [
            "gofmt",
            "-w",
            *vm.glob("*v[24]*.go"),
            vm / "cluster_disabled.go",
            wallet / "main.go",
        ],
        log,
        source,
    )
    return commit


def build():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise RuntimeError("Build on the Linux target architecture; macOS artifacts must not be deployed")
    doctor()
    missing = [tool for tool in ("cc", "make", "patch", "pnpm") if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing build tools: " + ", ".join(missing))
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError("Update Python 3.11 to a patch release with tarfile.data_filter")
    node_version = tuple(map(int, command(["node", "--version"]).strip().lstrip("v").split(".")))
    if node_version < (20, 19, 0):
        raise RuntimeError("Node.js >=20.19 is required")
    if command(["pnpm", "--version"]).strip() != "8.15.4":
        raise RuntimeError("Install the repository-pinned pnpm 8.15.4")
    # A failed rebuild must not leave stale metadata marking old binaries as current.
    (WORK / "binary.json").unlink(missing_ok=True)
    commit = prepare_sources()
    source, log = WORK / "bsc", WORK / "build.log"
    package = source / "crypto/pqmldsa"
    command(
        [
            "go",
            "test",
            "-mod=readonly",
            "-tags",
            "pqprecompile",
            "./core/vm",
            "-run",
            "Test(SLHV2|MLDSAV4)",
            "-count=1",
        ],
        log,
        source,
        timeout=900,
    )
    command(
        [
            "go",
            "test",
            "-mod=readonly",
            "./core/vm",
            "-run",
            "Test(SLHV2Disabled|MLDSAV4Disabled)",
            "-count=1",
        ],
        log,
        source,
        timeout=900,
    )
    command(
        [
            "go",
            "test",
            "-mod=readonly",
            "-tags",
            "pqprecompile",
            "./core/vm",
            "-run",
            "^$",
            "-fuzz",
            "^FuzzMLDSAV4$",
            "-fuzztime=10s",
            "-parallel=1",
        ],
        log,
        source,
        timeout=300,
    )
    command(
        ["go", "test", "-mod=readonly", "./params", "-count=1"],
        log,
        source,
        timeout=600,
    )
    for output, pkg in [("geth", "./cmd/geth"), ("pq-wallet", "./cmd/pq-wallet")]:
        command(
            [
                "go",
                "build",
                "-mod=readonly",
                "-tags",
                "pqprecompile",
                "-trimpath",
                "-o",
                WORK / output,
                pkg,
            ],
            log,
            source,
            timeout=1800,
        )

    def helper(v):
        return json.loads(subprocess.check_output([WORK / "pq-wallet"], input=json.dumps(v).encode()))

    k = helper({"action": "keygen"})
    m = "00" * 32
    sig = helper({"action": "sign", "secretKey": k["secretKey"], "message": m})["signature"]
    write(WORK / "mldsa-public.json", {"input": k["publicKey"] + m + sig})
    del k
    info = binary_info(WORK / "geth")
    info["version"] = "\n".join(
        line
        for line in info["version"].splitlines()
        if line.startswith(("Version:", "Architecture:", "Go Version:", "Operating System:"))
    )
    write(
        WORK / "binary.json",
        {
            **info,
            "source_commit": commit,
            "platform": platform.system(),
            "arch": platform.machine(),
            "inputs": inputs_manifest(),
            "go_version": command(["go", "version"]).strip(),
            "compiler": command(["cc", "--version"]).splitlines()[0],
            "algorithms": ["ML-DSA-44", "SLH-DSA-SHA2-128s"],
            "ml_backend": "portable C OPT=0 AUTO=0",
            "helper_sha256": digest(WORK / "pq-wallet"),
            "library_sha256": digest(package / "libmldsa44.a"),
        },
    )
