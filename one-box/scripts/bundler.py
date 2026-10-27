"""Build and manage the source-built Alto instance for a named devnet."""
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tarfile
import urllib.request

from devnet import ROOT, BOX, BUILD, RUNTIME, command, digest, load, read, wait_for, write

SOURCE = ROOT / "external/alto"
WORK = BUILD / "alto"
CLI = WORK / "src/esm/cli/alto.js"


def settings(env, port=None, pq=False):
    path = env / "bundler/config.json"
    config = read(path) if path.exists() else read(BOX / "bundler.json")
    # Removed upstream options are silently ignored by Alto 0.0.17.
    config.pop("max-bundle-size", None)
    config.pop("max-bundle-wait", None)
    if pq:
        config.update(read(BOX / "pq-bundler.json"))
    if port is not None:
        config["port"] = port
    if type(config["port"]) is not int or not 1024 <= config["port"] < 65536:
        raise ValueError("Invalid bundler port")
    if config.get("safe-mode") is not True or config.get("deploy-simulations-contract") is not False:
        raise ValueError("Keep safe-mode enabled and use the explicitly deployed simulation contract")
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, config)
    return config


def process(env):
    pidfile = env / "bundler/pid.json"
    if not pidfile.exists():
        return None
    saved = read(pidfile)
    text = subprocess.run(["ps", "-p", str(saved["pid"]), "-o", "command="], capture_output=True, text=True).stdout
    if not text.strip():
        return None
    if str(CLI) not in text or str(env / "bundler/alto.json") not in text:
        raise RuntimeError("Bundler PID belongs to another process; refusing to signal it")
    return saved["pid"]


def build():
    if not (SOURCE / ".git").exists():
        raise RuntimeError("Initialize the Alto submodule first")
    if not shutil.which("pnpm"):
        raise RuntimeError("pnpm is required; Alto pins pnpm 8.15.4 in packageManager")
    if command(["git", "status", "--porcelain"], cwd=SOURCE).strip():
        raise RuntimeError("Commit changes inside the Alto submodule before building its fixed source snapshot")
    for pidfile in RUNTIME.glob("*/bundler/pid.json"):
        if process(pidfile.parents[1]):
            raise RuntimeError("Stop all local bundlers before rebuilding their shared binary")
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=SOURCE)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(WORK, filter="data")
    log = BUILD / "alto-build.log"
    patch = BOX / "patches/alto-localhost.patch"
    command(["patch", "--batch", "--forward", "-p1", "-i", patch], log, WORK)
    timeout_patch = BOX / "patches/alto-timeout.patch"
    command(["patch", "--batch", "--forward", "-p1", "-i", timeout_patch], log, WORK)
    slh_patch = BOX / "patches/alto-slh-v2.patch"
    command(["patch", "--batch", "--forward", "-p1", "-i", slh_patch], log, WORK)
    falcon_patch = BOX / "patches/alto-falcon-v3.patch"
    command(["patch", "--batch", "--forward", "-p1", "-i", falcon_patch], log, WORK)
    # Do not run upstream prepare or the unrelated e2e workspaces.
    command(["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"], log, WORK, timeout=900)
    command(["pnpm", "--filter", "@pimlico/alto", "build:esm"], log, WORK, timeout=300)
    # Same ESM package marker as upstream's release workflow.
    package = read(WORK / "src/package.json")
    package["type"] = "module"
    write(WORK / "src/package.json", package)
    for path in (BOX, ROOT / "contract", ROOT / "erc4337-wallet"):
        command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], log, path, timeout=600)
    command(["forge", "build"], log, ROOT / "contract", timeout=300)
    write(BUILD / "alto.json", {
        "source_commit": command(["git", "rev-parse", "HEAD"], cwd=SOURCE).strip(),
        "version": package["version"], "localhost_patch_sha256": digest(patch),
        "timeout_patch_sha256": digest(timeout_patch),
        "slh_v2_patch_sha256": digest(slh_patch),
        "falcon_v3_patch_sha256": digest(falcon_patch),
    })
    print("Built Alto from submodule and compiled ERC-4337 contracts", flush=True)


def run_script(env, action):
    if not CLI.exists():
        build()
    if not (BOX / "node_modules/viem").exists() or not (ROOT / "contract/out/EntryPoint.sol/EntryPoint.json").exists():
        raise RuntimeError("Run ./tools/one-box bundler build first")
    args = ["node", BOX / "scripts/erc4337.mjs", action, env.name]
    result = subprocess.run(list(map(str, args)), cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"ERC-4337 {action} failed; see output above")


def rpc(env, method):
    config = read(env / "bundler/alto.json")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": []}).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{config["port"]}', body,
                                    {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(f"Bundler RPC error: {result['error']}")
    return result["result"]


def start(env):
    _, chain_config = load(env)
    if process(env):
        status(env)
        return
    config = settings(env)
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", config["port"]))
    run_script(env, "deploy")
    child_env = {k: v for k, v in os.environ.items()
                 if not k.startswith(("ALTO_", "SENTRY_", "OTEL_"))}
    child_env["DOTENV_CONFIG_PATH"] = os.devnull
    if chain_config.get("pq_precompile"):
        child_env["ALTO_LOCAL_PRECOMPILE_V2"] = "1"
    if chain_config.get("pq_precompile") == 3:
        child_env["ALTO_LOCAL_PRECOMPILE_V3"] = "1"
    if config.get("timeout"):
        child_env["ALTO_LOCAL_TRACE_TIMEOUT"] = f'{config["timeout"]}ms'
        child_env["ALTO_LOCAL_RPC_TIMEOUT"] = str(config["timeout"])
    with (env / "logs/alto.log").open("a") as output:
        child = subprocess.Popen(["node", str(CLI), "run", "--config", str(env / "bundler/alto.json")],
                                 cwd=WORK, env=child_env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    write(env / "bundler/pid.json", {"pid": child.pid})
    try:
        def ready():
            if child.poll() is not None:
                raise RuntimeError(f"Alto exited; see {env / 'logs/alto.log'}")
            addresses = rpc(env, "eth_supportedEntryPoints")
            return read(env / "bundler/deployment.json")["entryPoint"].lower() in [a.lower() for a in addresses]
        wait_for("Alto EntryPoint RPC", ready, 60)
    except Exception:
        stop(env)
        raise
    status(env)


def stop(env):
    pid = process(env)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        wait_for("Alto shutdown", lambda: process(env) is None, 30)
        print("Bundler stopped; keys and deployment records preserved", flush=True)
    (env / "bundler/pid.json").unlink(missing_ok=True)


def status(env):
    pid = process(env)
    result = {"pid": pid, "rpc": "unavailable"}
    if pid:
        config = read(env / "bundler/alto.json")
        result.update(rpc=f'http://127.0.0.1:{config["port"]}', entryPoints=rpc(env, "eth_supportedEntryPoints"))
    print(json.dumps(result, indent=2), flush=True)
