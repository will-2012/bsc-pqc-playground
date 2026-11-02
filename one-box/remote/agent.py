#!/usr/bin/env python3
"""Runs via SSM on a configured host. Never selects processes by name."""

import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from model import base, unit, rpc_url, require, validate

SYSTEMD_DIR = Path("/etc/systemd/system")


def read(p):
    return json.loads(Path(p).read_text())


def write(p, v):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, indent=2))
    tmp.chmod(0o600)
    tmp.replace(p)


def run(args):
    return subprocess.check_output(list(map(str, args)), stderr=subprocess.STDOUT, text=True)


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def property_value(service, name):
    return run(["systemctl", "show", service, "-p", name]).strip().partition("=")[2]


def rpc(url, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(url, body, {"Content-Type": "application/json"}),
        timeout=15,
    ) as r:
        d = json.load(r)
    if "error" in d:
        raise RuntimeError("RPC returned an error")
    return d["result"]


def services(c, h):
    return [unit(c, r) for r in ("bench", "bundler", "validator") if r in h["roles"]]


def check_owner(c, token):
    p = base(c) / "owner.json"
    require(p.exists() and read(p).get("token") == token, "Environment ownership mismatch")
    require(not base(c).is_symlink(), "Environment directory must not be a symlink")


def check_service(c, role):
    service = unit(c, role)
    state = property_value(service, "LoadState")
    if state == "not-found":
        return False
    expected = str(base(c) / "current/one-box/remote/agent.py")
    require(
        state == "loaded" and expected in property_value(service, "ExecStart"),
        "Refusing to operate on a foreign service",
    )
    return True


def stop_role(c, role):
    if check_service(c, role):
        run(["systemctl", "stop", unit(c, role)])


def precheck(c, h, token):
    result = {"host": h["id"], "checks": {}}
    checks = result["checks"]
    checks["platform"] = platform.system() == "Linux" and platform.machine() == h["arch"]
    checks["python"] = sys.version_info >= (3, 11) and hasattr(tarfile, "data_filter")
    checks["privilege"] = os.geteuid() == 0
    checks["systemd"] = shutil.which("systemctl") is not None
    checks["clock"] = abs(time.time() - c["_checked_at"]) < 120
    disk = Path("/server/will")
    while not disk.exists():
        disk = disk.parent
    checks["disk"] = shutil.disk_usage(disk).free >= c["min_free_gib"] * 2**30
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if ":" in line:
            mem[line.split(":")[0]] = int(line.split()[1])
    checks["memory"] = mem.get("MemAvailable", 0) >= c.get("min_available_mib", 2048) * 1024
    checks["directory"] = os.access(disk, os.W_OK) and not Path("/server/will").is_symlink()
    owner = base(c) / "owner.json"
    owned = owner.exists() and read(owner).get("token") == token
    checks["ownership"] = not base(c).exists() or owned
    for role in h["roles"]:
        state = property_value(unit(c, role), "LoadState")
        command = property_value(unit(c, role), "ExecStart")
        checks["service_" + role] = state == "not-found" or (
            owned and str(base(c) / "current/one-box/remote/agent.py") in command
        )
    # This check is intentionally read-only; no temporary remote listeners are started.
    for key in (["rpc", "p2p", "metrics", "pprof"] if "validator" in h["roles"] else []) + (
        ["bundler"] if "bundler" in h["roles"] else []
    ):
        owner_role = "bundler" if key == "bundler" else "validator"
        active = (
            owned
            and subprocess.run(["systemctl", "is-active", "--quiet", unit(c, owner_role)]).returncode == 0
        )
        ok = True
        if not active:
            for kind in ([socket.SOCK_STREAM, socket.SOCK_DGRAM] if key == "p2p" else [socket.SOCK_STREAM]):
                try:
                    with socket.socket(socket.AF_INET6 if ":" in h["address"] else socket.AF_INET, kind) as s:
                        s.bind(
                            (
                                "::" if ":" in h["address"] else "0.0.0.0",
                                h["ports"][key],
                            )
                        )
                except OSError:
                    ok = False
        checks["port_" + key] = ok
    if any(r in h["roles"] for r in ("bench", "bundler")):
        try:
            version = tuple(int(n) for n in run(["node", "--version"]).strip().lstrip("v").split("."))
            checks["node"] = version >= (20, 19, 0)
        except Exception:
            checks["node"] = False
    # Unstarted remote endpoints cannot be meaningfully TCP-probed yet.
    result["network"] = "PENDING: peer and RPC reachability verified after startup"
    result["ok"] = all(checks.values())
    return result


def service_text(c, role, command):
    return f"""[Unit]
Description=bsc-pqc {c['name']} {role}
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory={base(c)}/current
ExecStart={command}
KillSignal=SIGTERM
KillMode=control-group
TimeoutStopSec=300
UMask=0077
LimitNOFILE=65536
Restart=no
StandardOutput=null
StandardError=null
[Install]
WantedBy=multi-user.target
"""


def install(c, h, p):
    b = base(c)
    token = p["token"]
    require(not b.is_symlink(), "Environment directory must not be a symlink")
    if b.exists():
        check_owner(c, token)
    else:
        b.mkdir(parents=True, mode=0o700)
        write(b / "owner.json", {"token": token})
    for role in h["roles"]:
        if check_service(c, role):
            require(
                property_value(unit(c, role), "ActiveState") in ("inactive", "failed"),
                "Stop services before installing a release",
            )
    archive = Path(p["archive"])
    require(digest(archive) == p["sha256"], "Release hash mismatch")
    release = b / "releases" / p["sha256"][:20]
    if not (release / ".complete").exists():
        if release.exists():
            shutil.rmtree(release)
        release.mkdir(parents=True, mode=0o700)
        try:
            with tarfile.open(archive) as t:
                t.extractall(release, filter="data")
            (release / ".complete").touch()
        except Exception:
            shutil.rmtree(release)
            raise
    target = release / "app"
    metadata = read(target / "one-box/.runtime/build/v4/binary.json")
    require(
        metadata["platform"] == "Linux" and metadata["arch"] == platform.machine(),
        "Wrong release architecture",
    )
    require(
        hashlib.sha256((target / "one-box/.runtime/build/v4/pq-wallet").read_bytes()).hexdigest()
        == metadata["helper_sha256"],
        "Helper checksum mismatch",
    )
    executable = target / "one-box/.runtime/build/v4/geth"
    require(digest(executable) == metadata["sha256"], "Client checksum mismatch")
    runtime = b / "runtime"
    new = not (runtime / ".identity-ready").exists()
    old = b / "current"
    if old.exists() and not new:
        previous = read(old / "one-box/.runtime/build/v4/binary.json")
        require(
            previous["source_commit"] == metadata["source_commit"],
            "Upstream version migration requires a new environment",
        )
        require(
            previous.get("contracts_sha256") == metadata.get("contracts_sha256"),
            "Contract artifacts changed; refuse implicit migration",
        )
        if old.resolve() != target.resolve():
            write(
                b / "previous.json",
                {"target": str(old.resolve()), "operation": p.get("operation")},
            )
    if new:
        overlay = Path(p["overlay"])
        require(digest(overlay) == p["overlay_sha256"], "Identity bundle hash mismatch")
        staging = b / "runtime.new"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        with tarfile.open(overlay) as t:
            t.extractall(staging, filter="data")
        require((staging / "environment.json").is_file(), "Incomplete identity bundle")
        (staging / ".identity-ready").touch()
        if runtime.exists():
            shutil.rmtree(runtime)
        staging.replace(runtime)
    write(
        b / "host.json",
        {"config": c, "host": h, "token": token, "node": shutil.which("node")},
    )
    envlink = target / "one-box/.runtime" / c["name"]
    if not envlink.exists():
        envlink.symlink_to(runtime, target_is_directory=True)
    (runtime / "bin").mkdir(exist_ok=True)
    # Use a branded binary filename, leaving historical releases available.
    shutil.copy2(executable, runtime / "bin/bsc-pqc-validator")
    current = b / "current"
    link = b / "current.next"
    link.unlink(missing_ok=True)
    link.symlink_to(target, target_is_directory=True)
    link.replace(current)
    (b / "logs").mkdir(exist_ok=True)
    write(runtime / "remote.json", p["remote"])
    if "validator" in h["roles"]:
        node = runtime / "node"
        if not (node / "initialized").exists():
            run(
                [
                    runtime / "bin/bsc-pqc-validator",
                    "--datadir",
                    node,
                    "init",
                    "--state.scheme",
                    "path",
                    "--db.engine",
                    "pebble",
                    runtime / "genesis.json",
                ]
            )
            (node / "initialized").touch()
        write(node / "launch.json", p["validator_args"])
    for role in h["roles"]:
        # No shell parsing of inventory values in a unit.
        cmd = f"{sys.executable} {b}/current/one-box/remote/agent.py serve {b}/host.json {role}"
        (SYSTEMD_DIR / unit(c, role)).write_text(service_text(c, role, cmd))
    run(["systemctl", "daemon-reload"])
    return {"host": h["id"], "installed": True, "new": new}


def serve(path, role):
    d = read(path)
    c = d["config"]
    h = d["host"]
    b = base(c)
    root = b / "current"
    runtime = b / "runtime"
    check_owner(c, d["token"])
    fd = os.open(b / "logs" / f"{role}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    if role == "validator":
        args = read(runtime / "node/launch.json")
        os.execv(str(runtime / "bin/bsc-pqc-validator"), args)
    if role == "bundler":
        for key in list(os.environ):
            if key.startswith(("ALTO_", "SENTRY_", "OTEL_")):
                os.environ.pop(key)
        os.environ["BSC_PQC_BIND"] = h["address"]
        os.environ.update(
            ALTO_LOCAL_PRECOMPILE_V2="1",
            ALTO_LOCAL_PRECOMPILE_V4="1",
            DOTENV_CONFIG_PATH="/dev/null",
            ALTO_LOCAL_TRACE_TIMEOUT="180000ms",
            ALTO_LOCAL_RPC_TIMEOUT="180000",
        )
        # Set process title before loading Alto; both systemd and ps show bsc-pqc.
        os.execv(
            d["node"],
            [
                d["node"],
                str(root / "one-box/remote/bundler.mjs"),
                str(runtime / "bundler/alto.json"),
            ],
        )
    require(role == "bench", "Unknown role")
    bench = c["bench"]
    for _ in range(bench["rounds"]):
        subprocess.run(
            [
                d["node"],
                str(root / "one-box/remote/bench.mjs"),
                c["name"],
                str(bench["accounts"]),
                "--version",
                bench["version"],
                "--ecdsa-baseline",
            ],
            check=True,
            cwd=root,
        )


def perform(action, p):
    c = p["config"]
    h = p["host"]
    token = p["token"]
    if action == "precheck":
        result = precheck(c, h, token)
    elif action == "install":
        result = install(c, h, p)
    elif action == "reset" and not base(c).exists():
        result = {"reset": True, "alreadyAbsent": True}
    else:
        check_owner(c, token)
        b = base(c)
        if action in ("start", "stop", "restart", "status"):
            roles = p.get("roles", h["roles"])
            require(set(roles) <= set(h["roles"]), "Role not on this host")
            for role in roles:
                if action == "stop":
                    stop_role(c, role)
                elif action != "status":
                    require(check_service(c, role), "Service is not installed")
                    run(["systemctl", action, unit(c, role)])
            result = {r: property_value(unit(c, r), "ActiveState") for r in roles}
        elif action == "rpc":
            result = rpc(p["url"], p["method"], p.get("params"))
        elif action == "bootstrap":
            root = b / "current"
            run(["node", root / "one-box/scripts/erc4337.mjs", "deploy", c["name"]])
            result = read(b / "runtime/bundler/deployment.json")
        elif action == "configure":
            write(
                b / "host.json",
                {"config": c, "host": h, "token": token, "node": shutil.which("node")},
            )
            if "validator" in h["roles"]:
                (b / "runtime/node/config.toml").write_text(p["node_config"])
                write(b / "runtime/node/launch.json", p["validator_args"])
            cfg = read(b / "runtime/config.json")
            cfg.update(
                cache_mb=c["cache_mb"],
                gas_limit=c["gas_limit"],
                gas_price_wei=c["gas_price_wei"],
            )
            write(b / "runtime/config.json", cfg)
            result = {"configured": True}
        elif action == "rollback":
            previous = read(b / "previous.json") if (b / "previous.json").exists() else {}
            if previous.get("operation") != p.get("operation") or not previous.get("target"):
                return {"unchanged": True}
            old = Path(previous["target"])
            require(
                old.is_relative_to(b / "releases") and old.is_dir(),
                "Invalid rollback target",
            )
            for role in h["roles"]:
                stop_role(c, role)
            shutil.copy2(
                old / "one-box/.runtime/build/v4/geth",
                b / "runtime/bin/bsc-pqc-validator",
            )
            (b / "current").unlink()
            (b / "current").symlink_to(old, target_is_directory=True)
            result = {"binaryRestored": True, "databaseRolledBack": False}
        elif action == "bench-config":
            dest = b / "runtime/bench"
            write(dest / "deployment.json", p["deployment"])
            cfg = read(dest / "config.json") if (dest / "config.json").exists() else {}
            write(dest / "config.json", {**cfg, **p["bundler_config"]})
            result = {"configured": True}
        elif action == "smoke":
            for version in ["v4", "v7"]:
                run(
                    [
                        "node",
                        b / "current/tools/chain-bench/common/run.mjs",
                        c["name"],
                        "1",
                        "--version",
                        version,
                        "--ecdsa-baseline",
                    ]
                )
            result = {"passed": ["v4", "v7"]}
        elif action == "bundler-ready":
            bench = "bench" in h["roles"]
            deployment = read(
                b / ("runtime/bench/deployment.json" if bench else "runtime/bundler/deployment.json")
            )
            endpoint = (
                read(b / "runtime/remote.json")["bundler"]
                if bench
                else f"http://{h['address']}:{h['ports']['bundler']}"
            )
            deadline = time.monotonic() + 60
            while True:
                try:
                    entries = rpc(endpoint, "eth_supportedEntryPoints")
                    require(
                        deployment["entryPoint"].lower() in [v.lower() for v in entries],
                        "Wrong EntryPoint",
                    )
                    break
                except (OSError, RuntimeError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2)
            result = {"ready": True}
        elif action == "health":
            url = rpc_url(c, h["id"])
            block = rpc(url, "eth_getBlockByNumber", ["latest", False])
            finalized = rpc(url, "eth_getBlockByNumber", ["finalized", False])
            effective = json.loads(
                json.loads(
                    run(
                        [
                            b / "runtime/bin/bsc-pqc-validator",
                            "attach",
                            "--exec",
                            "JSON.stringify(admin.nodeInfo.protocols.eth.config)",
                            b / "runtime/node/geth.ipc",
                        ]
                    )
                )
            )
            result = {
                "host": h["id"],
                "forks": effective,
                "height": int(block["number"], 16),
                "hash": block["hash"],
                "root": block["stateRoot"],
                "timestamp": int(block["timestamp"], 16),
                "peers": int(rpc(url, "net_peerCount"), 16),
                "genesis": rpc(url, "eth_getBlockByNumber", ["0x0", False])["hash"],
                "finalized": ({k: finalized[k] for k in ("number", "hash")} if finalized else None),
            }
        elif action == "reset":
            for role in ("bench", "bundler", "validator"):
                if role in h["roles"]:
                    stop_role(c, role)
                    (SYSTEMD_DIR / unit(c, role)).unlink(missing_ok=True)
            run(["systemctl", "daemon-reload"])
            shutil.rmtree(b)
            result = {"reset": True}
        elif action == "collect":
            # Whitelist public benchmark records; never export runtime config or logs.
            rows = []
            for f in (b / "runtime/wallet").glob("*/bench-*.json"):
                data = read(f)
                rows.append(
                    {
                        "checkedAt": data["checkedAt"],
                        "chainId": data["chainId"],
                        "genesisHash": data["genesisHash"],
                        "algorithm": data["algorithm"],
                        "mode": data["mode"],
                        "profile": data["profile"],
                        "count": data["count"],
                        "rows": data["rows"],
                        "ecdsaBaseline": data.get("ecdsaBaseline", []),
                    }
                )
            dest = Path(p["output"])
            write(
                dest,
                {
                    "node": h["id"],
                    "bench": c["bench"],
                    "release": read(b / "current/one-box/.runtime/build/v4/binary.json"),
                    "runs": rows,
                },
            )
            result = {"runs": len(rows)}
        else:
            raise ValueError("Unknown action")
    return result


def main():
    os.umask(0o077)
    if sys.argv[1] == "serve":
        serve(sys.argv[2], sys.argv[3])
        return
    action, request = sys.argv[1], Path(sys.argv[2])
    payload = read(request)
    validate(payload["config"])
    if action == "precheck":
        result = perform(action, payload)
    else:
        folder = Path("/server/will/.bsc-pqc-locks")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (folder / (payload["config"]["name"] + ".lock")).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = perform(action, payload)
    # Avoid SSM's stdout truncation for block responses and deployment metadata.
    response = request.parent / "response.json"
    write(response, result)
    print(json.dumps({"response_sha256": digest(response)}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Detailed failures stay beside the private request, never in console output.
        import traceback

        try:
            error = Path(sys.argv[2]).parent / "error.log"
            with error.open("a") as f:
                traceback.print_exc(file=f)
            error.chmod(0o600)
        except Exception:
            pass
        print("bsc-pqc operation failed; inspect private host logs", file=sys.stderr)
        sys.exit(1)
