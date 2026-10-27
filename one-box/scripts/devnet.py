#!/usr/bin/env python3
"""One local devnet manager; templates are shared by all named environments."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
from string import Template
import subprocess
import sys
import time
import tomllib
import urllib.request

from genesis import generate

ROOT = Path(__file__).resolve().parents[2]
BOX = ROOT / "one-box"
RUNTIME = BOX / ".runtime"
BUILD = RUNTIME / "build"
FORKS = json.loads((ROOT / "one-box/forks.json").read_text())
STAKE_HUB = "0x0000000000000000000000000000000000002002"
MUTABLE = {"gas_limit", "gas_price_wei", "cache_mb", "rpc_port", "p2p_port"}


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(args, log=None, cwd=ROOT, timeout=300):
    args = list(map(str, args))
    if log:
        with log.open("a") as output:
            result = subprocess.run(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed; see {log}")
        return ""
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True, timeout=timeout).stdout


def validate(cfg):
    expected = set(read(ROOT / "one-box/config.json"))
    if set(cfg) != expected or any(type(v) is not int for v in cfg.values()):
        raise ValueError("Configuration must contain exactly the integer fields in one-box/config.json")
    if cfg["chain_id"] != 714:
        raise ValueError("This profile uses BSC Rialto system-contract bytecode: chain_id must be 714")
    if not 3 <= cfg["validators"] <= 7 or not 1 <= cfg["test_accounts"] <= 100:
        raise ValueError("Use 3–7 validators and 1–100 test accounts")
    if cfg["validator_balance_bnb"] < 20002 or cfg["test_balance_bnb"] <= 0:
        raise ValueError("Validators need >20001 BNB for registration; test balances must be positive")
    if cfg["gas_limit"] < 10_000_000 or cfg["cache_mb"] < 64 or cfg["gas_price_wei"] <= 0:
        raise ValueError("Invalid gas/cache budget")
    if not 45 <= cfg["passed_fork_delay_seconds"] < cfg["latest_fork_delay_seconds"]:
        raise ValueError("Allow at least 45s for early block forks; latest must follow passed forks")
    if cfg["breathe_seconds"] < 300:
        raise ValueError("breathe_seconds must be at least 300")
    ports = [cfg["rpc_port"] + 2*i for i in range(cfg["validators"])]
    ports += [cfg["p2p_port"] + i for i in range(cfg["validators"])]
    if len(ports) != len(set(ports)) or any(not 1024 <= p < 65536 for p in ports):
        raise ValueError("Port ranges overlap or are outside 1024–65535")


def doctor():
    if sys.version_info < (3, 11):
        raise RuntimeError("Python >=3.11 is required")
    for tool in ("git", "go", "forge", "cast", "node", "npm"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing dependency: {tool}")
    for source in ("bsc", "genesis-contracts"):
        if not (ROOT / "external" / source / ".git").exists():
            raise RuntimeError("Run git submodule update --init first")
    print("Dependencies and submodules: OK", flush=True)


def binary_info(binary):
    version = command([binary, "version"])
    if not re.search(r"Version: " + re.escape(FORKS["bsc_version"]) + r"(?:[-\s]|$)", version):
        raise RuntimeError("This devnet profile has been validated for BSC 1.8.1; adapt forks.json before upgrading versions")
    help_text = command([binary, "--help"])
    for name in ["passedforktime"] + FORKS["recent"]:
        if "--override." + name not in help_text:
            raise RuntimeError(f"Binary lacks fork override: {name}")
    return {"sha256": digest(binary), "version": version.strip()}


def build():
    doctor()
    BUILD.mkdir(parents=True, exist_ok=True)
    print("Building pinned BSC source...", flush=True)
    source = ROOT / "external/bsc"
    commit = command(["git", "rev-parse", "HEAD"], cwd=source).strip()
    # Upstream LocalEnv detects a .git directory, whereas a submodule has a .git
    # file. Pass the actual BSC commit rather than embedding the parent repo SHA.
    command(["go", "run", "build/ci.go", "install", "-git-commit", commit, "./cmd/geth"],
            BUILD / "build.log", source, timeout=1800)
    shutil.copy2(ROOT / "external/bsc/build/bin/geth", BUILD / "geth")
    info = binary_info(BUILD / "geth")
    info["source_commit"] = commit
    write(BUILD / "binary.json", info)
    print("Built one-box/.runtime/build/geth", flush=True)


def new_account(binary, directory, log):
    directory.mkdir(parents=True, exist_ok=True)
    password = directory / "password.txt"
    password.write_text(secrets.token_urlsafe(32) + "\n")
    os.chmod(password, 0o600)
    command([binary, "--datadir", directory, "account", "new", "--password", password], log)
    keyfile, = (directory / "keystore").glob("*")
    return command(["cast", "to-check-sum-address", "0x" + read(keyfile)["address"]]).strip()


def node_config(cfg, i, peers):
    values = dict(cfg, rpc_port=cfg["rpc_port"] + 2*i, p2p_port=cfg["p2p_port"] + i,
                  max_peers=cfg["validators"] + 2, peers=json.dumps(peers))
    return Template((ROOT / "one-box/templates/node.toml").read_text()).substitute(values)


def render(env, manifest, cfg):
    # Derive enodes from generated identities, updating only the endpoint ports.
    enodes = [re.sub(r"@.*", f'@127.0.0.1:{cfg["p2p_port"] + i}', n["enode"])
              for i, n in enumerate(manifest["nodes"])]
    for i in range(cfg["validators"]):
        (env / f"node{i}/config.toml").write_text(node_config(cfg, i, [e for j, e in enumerate(enodes) if i != j]))


def init(env, config_path):
    cfg = dict(config_path) if isinstance(config_path, dict) else read(config_path)
    validate(cfg)
    doctor()
    if env.exists():
        raise RuntimeError(f"Environment already exists: {env}. Use up/restart or explicit reset --yes")
    if not (BUILD / "geth").exists():
        build()
    env.mkdir(mode=0o700, parents=True)
    (env / "logs").mkdir()
    (env / "bin").mkdir()
    binary = env / "bin/geth"
    shutil.copy2(BUILD / "geth", binary)
    info = binary_info(binary)
    write(env / "config.json", cfg)
    log = env / "logs/init.log"
    nodes, accounts = [], []
    for i in range(cfg["validators"]):
        directory = env / f"node{i}"
        address = new_account(binary, directory, log)
        command([binary, "bls", "account", "new", "--datadir", directory,
                 "--blspassword", directory / "password.txt"], log)
        keyfile, = (directory / "bls/keystore").glob("*.json")
        pubkey = "0x" + read(keyfile)["pubkey"]
        proof = command([binary, "bls", "account", "generate-proof", "--datadir", directory,
                         "--blspassword", directory / "password.txt", "--chain-id", str(cfg["chain_id"]), address, pubkey])
        match = re.search(r"Proof: (0x[0-9a-fA-F]{192})", proof)
        if not match:
            raise RuntimeError("Cannot read BLS proof from geth")
        nodes.append({"address": address, "bls_public_key": pubkey, "bls_proof": match[1]})
        print(f"Generated validator {i}: {address}", flush=True)
    for i in range(cfg["test_accounts"]):
        address = new_account(binary, env / f"accounts/test{i}", log)
        accounts.append({"name": f"test{i}", "address": address, "keystore": f"accounts/test{i}/keystore",
                         "password_file": f"accounts/test{i}/password.txt"})
    work = env / "genesis-build"
    work.mkdir()
    print("Compiling local system contracts...", flush=True)
    genesis = generate(work, cfg, nodes, accounts)
    write(env / "genesis.json", genesis)
    (env / "base.toml").write_text(node_config(cfg, 0, []))
    command([binary, "init-network", "--init.dir", env, "--init.size", cfg["validators"],
             "--init.p2p-port", cfg["p2p_port"], "--init.evn-validator-register", "--config", env / "base.toml", env / "genesis.json"], log)
    all_enodes = set()
    for i in range(cfg["validators"]):
        path = env / f"node{i}/config.toml"
        text = "\n".join(line for line in path.read_text().splitlines() if "<nil>" not in line)
        all_enodes.update(tomllib.loads(text)["Node"]["P2P"]["StaticNodes"])
    for i, node in enumerate(nodes):
        node["enode"], = [e for e in all_enodes if e.endswith(f':{cfg["p2p_port"]+i}')]
    manifest = {"binary": info, "nodes": nodes, "accounts": accounts,
                "config": cfg, "genesis_contracts_commit": command(["git", "rev-parse", "HEAD"], cwd=ROOT / "external/genesis-contracts").strip()}
    render(env, manifest, cfg)
    for i in range(cfg["validators"]):
        command([binary, "--datadir", env / f"node{i}", "init", "--state.scheme", "path",
                 "--db.engine", "pebble", env / "genesis.json"], log)
    hashes = re.findall(r"Successfully wrote genesis state.*?hash=(0x[0-9a-f]+)", log.read_text())
    if len(hashes) != cfg["validators"] or len(set(hashes)) != 1:
        raise RuntimeError("Nodes were not initialized with the same genesis")
    manifest["genesis_hash"] = hashes[0]
    manifest["genesis_sha256"] = digest(env / "genesis.json")
    write(env / "environment.json", manifest)
    print(f"Initialized {env.name}; {len(accounts)} funded test accounts. Run ./tools/one-box up", flush=True)


def load(env):
    manifest = read(env / "environment.json")
    cfg = read(env / "config.json")
    validate(cfg)
    if digest(env / "genesis.json") != manifest["genesis_sha256"]:
        raise RuntimeError("Genesis was modified; refusing to open existing chain")
    if digest(env / "bin/geth") != manifest["binary"]["sha256"]:
        raise RuntimeError("Client binary changed outside upgrade command")
    for k in set(cfg) - MUTABLE:
        if cfg[k] != manifest["config"][k]:
            raise RuntimeError(f"{k} requires a new genesis: use reset")
    return manifest, cfg


def rpc(cfg, i, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{cfg["rpc_port"] + 2*i}', body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(f'{method}: {result["error"]}')
    return result["result"]


def process(env, i):
    pidfile = env / f"node{i}/pid.json"
    if not pidfile.exists():
        return None
    saved = read(pidfile)
    cmd = subprocess.run(["ps", "-p", str(saved["pid"]), "-o", "command="], capture_output=True, text=True).stdout
    if not cmd.strip():
        return None
    if str(env / "bin/geth") not in cmd or str(env / f"node{i}") not in cmd:
        raise RuntimeError(f"PID {saved['pid']} was reused; refusing to signal it")
    return saved["pid"]


def attach(env, i, expression):
    return command([env / "bin/geth", "attach", "--exec", expression, env / f"node{i}/geth.ipc"], timeout=15)


def wait_for(message, check, timeout=180):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, RuntimeError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            error = exc
        time.sleep(1)
    raise RuntimeError(f"Timed out: {message}; last error: {error}")


def free_ports(cfg, indices):
    for i in indices:
        for key, step in (("rpc_port", 2), ("p2p_port", 1)):
            with socket.socket() as sock:
                # Match geth's listener behavior: recently closed connections
                # in TIME_WAIT must not be mistaken for a running service.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", cfg[key] + i*step))
                except OSError:
                    raise RuntimeError(f"Port {cfg[key]+i*step} already occupied") from None


def snapshot(env, cfg):
    rows = []
    for i in range(cfg["validators"]):
        head = rpc(cfg, i, "eth_getBlockByNumber", ["latest", False])
        finalized = rpc(cfg, i, "eth_getBlockByNumber", ["finalized", False])
        rows.append({"node": i, "rpc": f'http://127.0.0.1:{cfg["rpc_port"]+2*i}',
                     "height": int(head["number"], 16), "timestamp": int(head["timestamp"], 16),
                     "peers": int(rpc(cfg, i, "net_peerCount"), 16),
                     "finalized": int(finalized["number"], 16) if finalized else 0})
    return rows


def check(env):
    manifest, cfg = load(env)
    rows = snapshot(env, cfg)
    common = hex(min(r["height"] for r in rows))
    hashes = [rpc(cfg, i, "eth_getBlockByNumber", [common, False])["hash"] for i in range(cfg["validators"])]
    if len(set(hashes)) != 1 or min(r["peers"] for r in rows) < 1:
        raise RuntimeError("Nodes do not agree or have missing peers")
    forks = read(env / "fork-times.json")
    if min(r["timestamp"] for r in rows) < forks["latest"] or min(r["finalized"] for r in rows) <= 0:
        raise RuntimeError("Waiting for latest fork and BLS finality")
    for i in range(cfg["validators"]):
        # PaymentLane is installed by the Jenner transition, not merely a config label.
        if rpc(cfg, i, "eth_getCode", ["0x0000000000000000000000000000000000002007", "latest"]) == "0x":
            raise RuntimeError("Jenner PaymentLane system contract is missing")
        if rpc(cfg, i, "eth_chainId") != hex(cfg["chain_id"]):
            raise RuntimeError("Wrong chain id")
        effective = json.loads(json.loads(attach(env, i, "JSON.stringify(admin.nodeInfo.protocols.eth.config)")))
        for fork in FORKS["passed"] + FORKS["recent"]:
            expected = forks["passed"] if fork in FORKS["passed"] + ["lorentz", "maxwell"] else forks["latest"]
            if effective.get(fork + "Time") != expected:
                raise RuntimeError(f"Node {i} has a different {fork} schedule")
    return {"nodes": rows, "common_height": int(common, 16), "common_hash": hashes[0],
            "latest_fork": FORKS["latest"], "genesis_hash": manifest["genesis_hash"]}


def register(env, manifest, cfg):
    receipts = read(env / "registrations.json") if (env / "registrations.json").exists() else {}
    for i, node in enumerate(manifest["nodes"]):
        address = node["address"]
        # An on-chain check makes retries safe even if the process stopped after send.
        data = command(["cast", "calldata", "consensusToOperator(address)", address]).strip()
        operator = rpc(cfg, 0, "eth_call", [{"to": STAKE_HUB, "data": data}, "latest"])
        if int(operator, 16):
            continue
        key = str(i)
        txhash = receipts.get(key, {}).get("tx_hash")
        if txhash:
            receipt = rpc(cfg, 0, "eth_getTransactionReceipt", [txhash])
            if receipt and receipt["status"] == "0x0":
                raise RuntimeError(f"Previous validator registration reverted: {txhash}")
        else:
            data = command(["cast", "calldata", "createValidator(address,bytes,bytes,(uint64,uint64,uint64),(string,string,string,string))",
                            address, node["bls_public_key"], node["bls_proof"], "(100,1000,100)",
                            f"(Val{i},local,local,local)"]).strip()
            txhash = rpc(cfg, i, "eth_sendTransaction", [{"from": address, "to": STAKE_HUB,
                         "value": hex(20001 * 10**18), "gas": hex(5_000_000),
                         "gasPrice": hex(cfg["gas_price_wei"]), "data": data}])
            receipts[key] = {"tx_hash": txhash}
            write(env / "registrations.json", receipts)
        receipt = wait_for("validator registration receipt", lambda: rpc(cfg, 0, "eth_getTransactionReceipt", [txhash]))
        if receipt["status"] != "0x1":
            raise RuntimeError(f"Validator {i} registration reverted: {txhash}")
        receipts[key]["block"] = int(receipt["blockNumber"], 16)
        write(env / "registrations.json", receipts)
        print(f"Registered validator {i}: {txhash}", flush=True)


def check_progress(env):
    before = wait_for("consistent node snapshot", lambda: check(env), 60)

    def advanced():
        after = check(env)
        for old, new in zip(before["nodes"], after["nodes"]):
            if new["height"] <= old["height"] or new["finalized"] <= old["finalized"]:
                return False
        return after

    return wait_for("blocks and BLS finality advancing on every node", advanced, 90)


def up(env):
    manifest, cfg = load(env)
    indices = [i for i in range(cfg["validators"]) if process(env, i) is None]
    applied = env / "applied-config.json"
    if len(indices) < cfg["validators"] and applied.exists() and read(applied) != cfg:
        raise RuntimeError("Configuration changed; use restart to apply it to every node")
    if indices:
        free_ports(cfg, indices)
    render(env, manifest, cfg)
    forkfile = env / "fork-times.json"
    if not forkfile.exists():
        now = int(time.time())
        write(forkfile, {"created_at": now, "passed": now + cfg["passed_fork_delay_seconds"],
                         "latest": now + cfg["latest_fork_delay_seconds"], "latest_fork": FORKS["latest"]})
    forks = read(forkfile)
    binary = env / "bin/geth"
    for i in indices:
        directory = env / f"node{i}"
        args = [binary, "--config", directory / "config.toml", "--datadir", directory,
                "--nodekey", directory / "geth/nodekey", "--vote", "--unlock", manifest["nodes"][i]["address"],
                "--miner.etherbase", manifest["nodes"][i]["address"], "--password", directory / "password.txt",
                "--blspassword", directory / "password.txt", "--allow-insecure-unlock", "--http",
                "--cache", cfg["cache_mb"], "--syncmode", "full", "--state.scheme", "path", "--db.engine", "pebble",
                "--nat", "none", "--netrestrict", "127.0.0.0/8", "--mev.grpc.disable",
                "--rialtohash", manifest["genesis_hash"], "--override.passedforktime", forks["passed"],
                "--override.breatheblockinterval", cfg["breathe_seconds"]]
        for fork in FORKS["recent"]:
            args += ["--override." + fork, forks["passed"] if fork in ("lorentz", "maxwell") else forks["latest"]]
        with (env / f"logs/node{i}.log").open("a") as output:
            child = subprocess.Popen(list(map(str, args)), cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        write(directory / "pid.json", {"pid": child.pid})
    print("Waiting for peers and matching genesis...", flush=True)
    def connected():
        return all(int(rpc(cfg, i, "net_peerCount"), 16) >= 1 and
                   rpc(cfg, i, "eth_getBlockByNumber", ["0x0", False])["hash"] == manifest["genesis_hash"]
                   for i in range(cfg["validators"]))
    wait_for("peer connections", connected, 60)
    heights = [int(rpc(cfg, i, "eth_blockNumber"), 16) for i in range(cfg["validators"])]
    if max(heights) == 0:
        attach(env, 1, "miner.start()")
        wait_for("first block propagation", lambda: all(int(rpc(cfg, i, "eth_blockNumber"), 16) >= 1 for i in range(cfg["validators"])), 45)
    for i in range(cfg["validators"]):
        attach(env, i, "miner.start()")
    print("Waiting for hardfork transitions through Jenner and BLS finality...", flush=True)
    wait_for("latest fork and finality", lambda: check(env), max(180, forks["latest"] - int(time.time()) + 120))
    register(env, manifest, cfg)
    report = check_progress(env)
    write(env / "health.json", report)
    write(env / "applied-config.json", cfg)
    print(json.dumps(report, indent=2), flush=True)


def down(env):
    from bundler import stop
    stop(env)
    manifest = read(env / "environment.json")
    pids = [(i, process(env, i)) for i in range(len(manifest["nodes"]))]
    for i, pid in pids:
        if pid:
            try:
                attach(env, i, "miner.stop()")
            except subprocess.SubprocessError:
                pass
    for _, pid in pids:
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    wait_for("graceful node shutdown", lambda: all(process(env, i) is None for i, _ in pids), 90)
    for i, _ in pids:
        (env / f"node{i}/pid.json").unlink(missing_ok=True)
    print("Stopped; keys and chain data preserved", flush=True)


def upgrade(env, candidate):
    from bundler import process as bundler_process, start as start_bundler
    resume_bundler = bool(bundler_process(env))
    manifest, _ = load(env)
    info = binary_info(candidate)
    # Preserve both binaries before touching any running process.
    staged = env / "bin/geth.next"
    shutil.copy2(candidate, staged)
    down(env)
    shutil.copy2(env / "bin/geth", env / "bin/geth.previous")
    staged.replace(env / "bin/geth")
    old = manifest["binary"]
    manifest["binary"] = info
    write(env / "environment.json", manifest)
    try:
        up(env)
        if resume_bundler:
            start_bundler(env)
    except Exception:
        down(env)
        shutil.copy2(env / "bin/geth.previous", env / "bin/geth")
        manifest["binary"] = old
        write(env / "environment.json", manifest)
        raise RuntimeError("Upgrade check failed. Previous binary restored; inspect logs before restarting. Database was not rolled back.") from None
    print("Client replacement verified; previous binary retained", flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="devnet", help="Local environment name")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("doctor", "build", "up", "down", "restart", "status", "check", "accounts"):
        sub.add_parser(name)
    for name in ("init", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, default=ROOT / "one-box/config.json" if name == "init" else None)
        if name == "reset":
            p.add_argument("--yes", action="store_true", help="Delete this environment, including its keys")
    p = sub.add_parser("config")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?", type=int)
    p = sub.add_parser("upgrade")
    p.add_argument("--binary", type=Path, required=True, help="Replacement geth, same supported hardfork profile")
    p = sub.add_parser("bundler", help="Build, deploy and manage Alto and the ERC-4337 demo")
    p.add_argument("operation", choices=["build", "deploy", "up", "down", "restart", "status", "test"])
    p.add_argument("--port", type=int, help="Set this environment's port with up, restart or deploy")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}", args.name) or args.name in ("build", "locks"):
        parser.error("Invalid or reserved environment name")
    env = RUNTIME / args.name
    RUNTIME.mkdir(exist_ok=True)
    locks = RUNTIME / "locks"
    locks.mkdir(exist_ok=True)
    lockname = "build-bundler" if args.action == "bundler" and args.operation == "build" else "build" if args.action == "build" else args.name
    with (locks / (lockname + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "doctor":
            doctor()
        elif args.action == "build":
            build()
        elif args.action == "init":
            init(env, args.config.resolve())
        elif args.action == "reset":
            if not args.yes:
                raise RuntimeError("reset deletes this environment's keys and database; pass --yes explicitly")
            source = args.config.resolve() if args.config else (env / "config.json" if (env / "config.json").exists() else ROOT / "one-box/config.json")
            cfg = read(source)
            validate(cfg)
            if env.exists():
                if (env / "environment.json").exists():
                    down(env)
                shutil.rmtree(env)
            init(env, cfg)
            up(env)
        elif args.action == "up":
            if not env.exists():
                init(env, ROOT / "one-box/config.json")
            up(env)
        elif args.action == "down":
            down(env)
        elif args.action == "restart":
            from bundler import process as bundler_process, start as start_bundler
            resume_bundler = bool(bundler_process(env))
            down(env)
            up(env)
            if resume_bundler:
                start_bundler(env)
        elif args.action == "bundler":
            import bundler
            operation = args.operation
            if args.port is not None and operation not in ("up", "restart", "deploy"):
                raise ValueError("--port is only valid with bundler up/restart/deploy")
            if operation == "build":
                bundler.build()
            elif operation in ("up", "restart", "deploy"):
                if operation == "restart":
                    bundler.stop(env)
                if not env.exists():
                    init(env, ROOT / "one-box/config.json")
                if any(process(env, i) is None for i in range(read(env / "config.json")["validators"])):
                    up(env)
                else:
                    check_progress(env)
                if args.port is not None:
                    if bundler.process(env):
                        raise RuntimeError("Use bundler restart --port to change a running bundler's port")
                    bundler.settings(env, args.port)
                if operation == "deploy":
                    bundler.run_script(env, "deploy")
                else:
                    bundler.start(env)
            elif operation == "down":
                bundler.stop(env)
            elif operation == "status":
                bundler.status(env)
            else:
                bundler.run_script(env, "test")
        elif args.action == "upgrade":
            upgrade(env, args.binary.resolve())
        elif args.action == "config":
            cfg = read(env / "config.json")
            if args.key is None:
                print(json.dumps(cfg, indent=2))
            else:
                if args.key not in MUTABLE or args.value is None:
                    raise ValueError("Mutable keys: " + ", ".join(sorted(MUTABLE)) + "; other fields require reset --config")
                cfg[args.key] = args.value
                validate(cfg)
                write(env / "config.json", cfg)
                print("Configuration saved; run ./tools/one-box restart to apply")
        elif args.action == "accounts":
            manifest, cfg = load(env)
            for role, records in (("validator", manifest["nodes"]), ("test", manifest["accounts"])):
                for i, record in enumerate(records):
                    amount = cfg["validator_balance_bnb"] if role == "validator" else cfg["test_balance_bnb"]
                    print(f"{role}{i}: {record['address']}  genesis={amount} BNB")
            print(f"Keystores and password files: {env}")
        elif args.action == "check":
            after = check_progress(env)
            write(env / "health.json", after)
            print(json.dumps(after, indent=2))
        elif args.action == "status":
            _, cfg = load(env)
            for i in range(cfg["validators"]):
                try:
                    print(json.dumps({"node": i, "pid": process(env, i), "height": rpc(cfg, i, "eth_blockNumber")}))
                except (OSError, RuntimeError):
                    print(json.dumps({"node": i, "pid": process(env, i), "rpc": "unavailable"}))
            if (env / "bundler/deployment.json").exists():
                from bundler import status as bundler_status
                bundler_status(env)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, BlockingIOError):
            exc = "Another command is already managing this environment; wait for it to finish"
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
