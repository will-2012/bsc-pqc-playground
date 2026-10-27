#!/usr/bin/env python3
"""Compare actual Go precompile Run paths; derive a local experimental gas quote."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "one-box/scripts"))
from precompile import SOURCE, WORK  # noqa: E402
from devnet import command, read, write, digest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    if args.rounds < 5:
        parser.error("Use at least five rounds")
    if not (SOURCE / "core/vm/slh_v2.go").exists():
        parser.error("Run ./tools/one-box build --v2 first")
    for path in ROOT.glob("one-box/.runtime/*/node*/pid.json"):
        pid = read(path)["pid"]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        parser.error("Stop local devnets before native performance measurement")
    binary = WORK / "vm.test"
    log = WORK / "native-bench.txt"
    command(["go", "test", "-mod=readonly", "-tags", "pqprecompile", "-buildvcs=false", "-trimpath", "-c", "-o", binary, "./core/vm"],
            WORK / "build.log", SOURCE, timeout=600)
    names = ["SLHV2ECRecover", "SLHV2Valid", "SLHV2Invalid", "SLHV2ZeroSignature"]
    rows = []
    environment = dict(os.environ, GOMAXPROCS="1", GOGC="100")
    environment.pop("GOMEMLIMIT", None)
    environment.pop("GODEBUG", None)
    with log.open("w") as output:
        for round_ in range(args.rounds):
            for i in range(len(names)):
                name = names[(round_ + i) % len(names)]
                result = subprocess.run([str(binary), "-test.run=^$", f"-test.bench=^Benchmark{name}$",
                    "-test.benchtime=1s", "-test.count=1", "-test.cpu=1", "-test.benchmem"],
                    cwd=SOURCE / "core/vm", env=environment, capture_output=True, text=True, check=True)
                output.write(f"ROUND {round_+1}\n" + result.stdout)
                match = re.search(rf"Benchmark{name}(?:-\d+)?\s+(\d+)\s+([^\n]+)", result.stdout)
                if not match:
                    raise RuntimeError(f"Cannot parse benchmark {name}")
                iterations, line = match.groups()
                metrics = {unit: float(value) for value, unit in re.findall(r"([\d.]+)\s+([\w/-]+)", line)}
                rows.append(dict(round=round_+1, name=name, iterations=int(iterations), nsPerOp=metrics["cpu-ns/op"],
                                 wallNsPerOp=metrics["ns/op"], threadNsPerOp=metrics["thread-cpu-ns/op"],
                                 bytesPerOp=int(metrics["B/op"]), allocsPerOp=int(metrics["allocs/op"])))
                print(f'round {round_+1}: {name} CPU {metrics["cpu-ns/op"]/1000:.2f} us, wall {metrics["ns/op"]/1000:.2f} us', flush=True)
    summary = {name: {"medianUs": statistics.median(r["nsPerOp"] for r in rows if r["name"] == name)/1000,
                     "maxUs": max(r["nsPerOp"] for r in rows if r["name"] == name)/1000,
                     "wallMedianUs": statistics.median(r["wallNsPerOp"] for r in rows if r["name"] == name)/1000,
                     "threadMedianUs": statistics.median(r["threadNsPerOp"] for r in rows if r["name"] == name)/1000} for name in names}
    baseline = summary["SLHV2ECRecover"]["medianUs"]
    worst = max(summary[name]["maxUs"] for name in names[1:])
    price = math.ceil((3000 * worst / baseline * 1.25) / 1000) * 1000
    result = dict(checkedAt=datetime.now(timezone.utc).isoformat(), rounds=args.rounds, rows=rows, summary=summary,
                  measurement="process CPU including Go GC; thread CPU and wall recorded separately; GOMAXPROCS=1, GC enabled",
                  pricing={"ecRecoverGas": 3000, "worstMeasuredUs": worst, "margin": 1.25,
                           "recommendedGas": price, "roundingGas": 1000,
                           "selectedGas": math.ceil(price / 10000) * 10000,
                           "selection": "round empirical quote upward to 10000 gas; experimental price, not a proven worst-case bound",
                           "basis": "max measured SLH Run sample / median ECRECOVER Run * 3000 * 1.25"},
                  environment=json.loads(command(["go", "env", "-json", "GOOS", "GOARCH", "GOARM64", "CGO_ENABLED", "CGO_CFLAGS", "GOVERSION", "GOFLAGS"], cwd=SOURCE)),
                  fixtureSha256=digest(ROOT / "one-box/precompile/v2/testdata/slh_v2.json"),
                  sources={str(p.relative_to(ROOT)): digest(p) for p in (ROOT / "one-box/precompile/v2").rglob("*") if p.is_file()},
                  dependency=read(ROOT / "one-box/precompile/v2/dependency.json"),
                  logSha256=digest(log))
    write(WORK / "native-bench.json", result)
    print(json.dumps({"summary": summary, "pricing": result["pricing"]}, indent=2))


if __name__ == "__main__":
    main()
