#!/usr/bin/env python3
"""Control-plane CLI. All machine identity and SSM responses stay in ignored runtime."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import devnet
from model import load, require, base, by_id, rpc_url
from transport import SSM

ROOT = devnet.ROOT
HERE = Path(__file__).resolve().parent


@contextlib.contextmanager
def locked(name):
    # Kept outside the environment so reset cannot replace the locked inode.
    folder = devnet.RUNTIME / "clusters/.locks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / (name + ".lock")).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def copy_project_sources(root, destination):
    """Ship version-controlled source only; runtime dependencies are added explicitly."""
    prefixes = (
        "contract/src/",
        "erc4337-wallet/",
        "one-box/scripts/",
        "one-box/remote/",
        "one-box/precompile/v2/testdata/",
        "tools/chain-bench/common/",
        "tools/chain-bench/load/",
    )
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    for name in tracked:
        if not name or not (name.startswith(prefixes) or name == "tools/erc4337-wallet"):
            continue
        source, target = root / name, destination / name
        require(source.resolve().is_relative_to(root.resolve()), "Source link escapes repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    for required in ("one-box/remote/agent.py", "one-box/remote/load_bench.py",
                     "tools/chain-bench/load/core.mjs", "tools/chain-bench/load/run.mjs",
                     "tools/chain-bench/load/worker.mjs", "tools/chain-bench/load/inclusions.mjs",
                     "tools/chain-bench/load/transactions.mjs"):
        require((destination / required).is_file(),
                "Release source missing; commit all load runner files before packaging")



class Cluster:
    def __init__(self, c):
        self.c = c
        self.home = devnet.RUNTIME / "clusters" / c["name"]
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.statefile = self.home / "state.json"
        self.state = (
            devnet.read(self.statefile) if self.statefile.exists() else {"token": secrets.token_hex(24)}
        )
        # Older inventories omitted the deployment parent. Require it explicitly
        # in the new private config; remote ownership checks still guard access.
        previous = self.state.get("inventory")
        if previous is not None and "deploy_root" not in previous:
            previous["deploy_root"] = c["deploy_root"]
        self.ssm = SSM(self.home / "ssm")
        self.shared = Path(c["shared_controller"])
        self.save()

    def save(self):
        devnet.write(self.statefile, self.state)

    def call(self, h, action, **extra):
        packet = "bsc-pqc-" + self.c["name"] + "/" + uuid.uuid4().hex
        path = self.shared / packet
        path.mkdir(parents=True, mode=0o700)
        for file in (("agent.py", "model.py", "monitor.py", "monitor_collect.py")
                     if action == "monitor-start" else ("agent.py", "model.py")):
            shutil.copy2(HERE / file, path / file)
        payload = {
            "config": {**self.c, "_checked_at": int(time.time())},
            "host": h,
            "token": self.state["token"],
            **extra,
        }
        devnet.write(path / "request.json", payload)
        remote = Path(h["shared"]) / packet
        # No raw host values or keys embedded in command text; only a private request path.
        cmd = shlex.join([h.get("python", "python3"), str(remote / "agent.py"), action, str(remote / "request.json")])
        output = self.ssm.run(h, [cmd])
        receipt = json.loads(output)
        response = path / "response.json"
        require(
            response.is_file() and devnet.digest(response) == receipt["response_sha256"],
            "Missing or mismatched remote response",
        )
        return devnet.read(response)

    def precheck(self):
        require(shutil.which("aws"), "Install AWS CLI on the controller")
        require(
            self.shared.is_dir() and os.access(self.shared, os.W_OK),
            "Shared controller directory must exist and be writable",
        )
        ok = True
        for h in self.c["hosts"]:
            r = self.call(h, "precheck")
            failed = [k for k, v in r["checks"].items() if not v]
            print(h["id"] + ": " + ("PASS" if r["ok"] else "FAIL " + ", ".join(failed)))
            ok &= r["ok"]
        require(ok, "Precheck failed; no services changed")
        print(
            "PENDING: service-to-service reachability requires deployed listeners; deploy checks it before bench"
        )

    def assert_config(self):
        require(
            self.state.get("inventory") == self.c,
            "Inventory changed: use configure for mutable fields; topology requires a new environment",
        )

    def package(self):
        with locked("build"):
            return self._package()

    def _package(self):
        from build import WORK, inputs_manifest

        info = devnet.read(WORK / "binary.json")
        require(info.get("platform") == "Linux", "Deploy requires a Linux release")
        require(
            info.get("inputs") == inputs_manifest(),
            "Build inputs changed; rebuild before deployment",
        )
        require(
            devnet.digest(WORK / "geth") == info["sha256"]
            and devnet.digest(WORK / "pq-wallet") == info["helper_sha256"]
            and devnet.digest(WORK / "bsc-pqc-slh-signer") == info["slh_signer_sha256"],
            "Build artifact checksum mismatch",
        )
        for backend in ("avx2", "avx512"):
            if info.get(f"slh_{backend}_signer_sha256"):
                require(devnet.digest(WORK / f"bsc-pqc-slh-{backend}-signer") == info[f"slh_{backend}_signer_sha256"],
                        "SLH SIMD signer checksum mismatch")
        require(
            all(h["arch"] == info["arch"] for h in self.c["hosts"]),
            "First release requires hosts matching the Linux build architecture",
        )
        stage = self.home / "release-stage"
        if stage.exists():
            shutil.rmtree(stage)
        app = stage / "app"
        app.mkdir(parents=True)
        copy_project_sources(ROOT, app)
        for rel in ("contract/out", "erc4337-wallet/node_modules", "one-box/node_modules"):
            target = app / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / rel, target, symlinks=True)
        # Bench modules resolve bare imports from the release root.
        (app / "node_modules").symlink_to("erc4337-wallet/node_modules", target_is_directory=True)
        for name in ["bundler.json", "pq-bundler.json", "forks.json", "config.json"]:
            shutil.copy2(devnet.BOX / name, app / "one-box" / name)
        signer_dest = app / "one-box/.runtime/build"
        signer_dest.mkdir(parents=True, exist_ok=True)
        for file in ("bsc-pqc-slh-signer", "bsc-pqc-slh-signer.json"):
            shutil.copy2(WORK / file, signer_dest / file)
        for backend in ("avx2", "avx512"):
            if info.get(f"slh_{backend}_signer_sha256"):
                name = f"bsc-pqc-slh-{backend}-signer"
                for file in (name, name + ".json"):
                    shutil.copy2(WORK / file, signer_dest / file)
        dest = app / "one-box/.runtime/build/v4"
        dest.mkdir(parents=True)
        for file in ("geth", "binary.json", "pq-wallet", "mldsa-public.json"):
            shutil.copy2(WORK / file, dest / file)
        # Exclude upstream dev configs and test fixtures from the runtime release.
        for rel in (
            "src/esm",
            "src/node_modules",
            "src/package.json",
            "node_modules",
            "package.json",
            "LICENSE",
        ):
            source = devnet.BUILD / "alto" / rel
            target = app / "one-box/.runtime/build/alto" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        server = app / "one-box/.runtime/build/alto/src/esm/rpc/server.js"
        text = server.read_text()
        require(text.count('host: "127.0.0.1"') == 1, "Alto bind hook changed")
        server.write_text(text.replace('host: "127.0.0.1"', 'host: process.env.BSC_PQC_BIND || "127.0.0.1"'))
        contracts = hashlib.sha256()
        for f in sorted((app / "contract/out").rglob("*.json")):
            contracts.update(str(f.relative_to(app / "contract/out")).encode())
            contracts.update(f.read_bytes())
        metadata = app / "one-box/.runtime/build/v4/binary.json"
        devnet.write(
            metadata,
            {**devnet.read(metadata), "contracts_sha256": contracts.hexdigest()},
        )
        # A release must be self-contained; reject links back to the controller.
        for path in app.rglob("*"):
            if path.is_symlink():
                require(
                    not Path(os.readlink(path)).is_absolute()
                    and path.resolve().is_relative_to(app.resolve()),
                    "Release symlink escapes its directory",
                )
        archive = self.home / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(app, arcname="app")
        digest = devnet.digest(archive)
        dest = self.shared / ("bsc-pqc-" + self.c["name"]) / "releases" / digest
        dest.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(archive, dest / "release.tar.gz")
        return str(dest.relative_to(self.shared) / "release.tar.gz"), digest

    def prepare_seed(self):
        from build import WORK

        seed = self.home / "seed"
        self.state["inventory"] = self.c
        self.save()
        if seed.exists():
            require(
                self.state.get("prepared"),
                "Interrupted preparation: inspect private state and use explicit reset",
            )
            return seed
        cfg = devnet.read(devnet.BOX / "config.json")
        vals = [h for h in self.c["hosts"] if "validator" in h["roles"]]
        cfg.update(
            validators=len(vals),
            test_accounts=len(self.c["hosts"]) + 1,
            pq_precompile=4,
            cache_mb=self.c["cache_mb"],
            gas_limit=self.c["gas_limit"],
            gas_price_wei=self.c["gas_price_wei"],
        )
        # No local processes are started; init creates new identities, genesis and validates it.
        with (self.home / "prepare.log").open("a") as log, contextlib.redirect_stdout(
            log
        ), contextlib.redirect_stderr(log):
            devnet.init(seed, cfg, selected_binary=WORK / "geth")
        self.state.update(prepared=True, inventory=self.c)
        self.save()
        return seed

    def overlay(self, h):
        seed = self.home / "seed"
        manifest = devnet.read(seed / "environment.json")
        cfg = devnet.read(seed / "config.json")
        vals = [n for n in self.c["hosts"] if "validator" in n["roles"]]
        folder = self.home / "overlays" / h["id"]
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, mode=0o700)
        # Each host gets an independent funded sender; no shared faucet nonce between bench clients.
        index = self.c["hosts"].index(h)
        chosen = [manifest["accounts"][index], manifest["accounts"][-1]]
        manifest = {**manifest, "accounts": chosen}
        for a in chosen:
            rel = Path(a["keystore"]).parent
            shutil.copytree(seed / rel, folder / rel)
        cfg.update(
            cache_mb=self.c["cache_mb"],
            gas_limit=self.c["gas_limit"],
            gas_price_wei=self.c["gas_price_wei"],
            rpc_port=h["ports"]["rpc"],
        )
        devnet.write(folder / "environment.json", manifest)
        devnet.write(folder / "config.json", cfg)
        shutil.copy2(seed / "genesis.json", folder / "genesis.json")
        devnet.write(folder / "fork-times.json", self.state["forks"])
        rpc_node = h.get("rpc_node") or (h["id"] if "validator" in h["roles"] else vals[0]["id"])
        bundler_node = h.get("bundler_node") or (
            h["id"]
            if "bundler" in h["roles"]
            else next(x["id"] for x in self.c["hosts"] if "bundler" in x["roles"])
        )
        bh = by_id(self.c, bundler_node)
        remote = {
            "bench": "bench" in h["roles"],
            "rpc": rpc_url(self.c, rpc_node),
            "bundler": f"http://{bh['address']}:{bh['ports']['bundler']}",
            "validators": [rpc_url(self.c, rpc_node)],
        }
        # Only the packaged Alto release accepts the configured private bind address.
        bcfg = {
            **devnet.read(devnet.BOX / "bundler.json"),
            **devnet.read(devnet.BOX / "pq-bundler.json"),
            "port": h["ports"]["bundler"],
        }
        (folder / "bundler").mkdir()
        devnet.write(folder / "bundler/config.json", bcfg)
        args = []
        if "validator" in h["roles"]:
            i = vals.index(h)
            node = folder / "node"
            node.mkdir()
            for part in ["keystore", "bls", "password.txt"]:
                src = seed / f"node{i}" / part
                if src.is_dir():
                    shutil.copytree(src, node / part)
                else:
                    shutil.copy2(src, node / part)
            (node / "geth").mkdir()
            shutil.copy2(seed / f"node{i}/geth/nodekey", node / "geth/nodekey")
            peers = []
            for j, v in enumerate(vals):
                if j != i:
                    peers.append(
                        manifest["nodes"][j]["enode"].split("@")[0] + f"@{v['address']}:{v['ports']['p2p']}"
                    )
            text = devnet.node_config(
                {**cfg, "rpc_port": h["ports"]["rpc"], "p2p_port": h["ports"]["p2p"]},
                0,
                peers,
            )
            text = text.replace('HTTPHost = "127.0.0.1"', f'HTTPHost = "{h["address"]}"').replace(
                'ListenAddr = "127.0.0.1:', f'ListenAddr = "{h["address"]}:'
            )
            text = text.replace(
                'HTTPVirtualHosts = ["localhost", "127.0.0.1"]',
                'HTTPVirtualHosts = ["localhost", "127.0.0.1", "' + h["address"] + '"]',
            )
            (node / "config.toml").write_text(text)
            b = base(self.c) / "runtime"
            nd = b / "node"
            forks = self.state["forks"]
            addr = manifest["nodes"][i]["address"]
            args = list(
                map(
                    str,
                    [
                        b / "bin/bsc-pqc-validator",
                        "--config",
                        nd / "config.toml",
                        "--datadir",
                        nd,
                        "--nodekey",
                        nd / "geth/nodekey",
                        "--mine",
                        "--vote",
                        "--unlock",
                        addr,
                        "--miner.etherbase",
                        addr,
                        "--password",
                        nd / "password.txt",
                        "--blspassword",
                        nd / "password.txt",
                        "--allow-insecure-unlock",
                        "--http",
                        "--cache",
                        self.c["cache_mb"],
                        "--syncmode",
                        "full",
                        "--gcmode",
                        "full",
                        "--state.scheme",
                        "path",
                        "--db.engine",
                        "pebble",
                        "--nat",
                        "none",
                        "--mev.grpc.disable",
                        "--rialtohash",
                        manifest["genesis_hash"],
                        "--override.passedforktime",
                        forks["passed"],
                        "--override.breatheblockinterval",
                        cfg["breathe_seconds"],
                        "--override.pq",
                        forks["pq"],
                        "--metrics",
                        "--metrics.addr",
                        "127.0.0.1",
                        "--metrics.port",
                        h["ports"]["metrics"],
                        "--pprof",
                        "--pprof.addr",
                        "127.0.0.1",
                        "--pprof.port",
                        h["ports"]["pprof"],
                    ],
                )
            )
            for fork in devnet.FORKS["recent"]:
                args += [
                    "--override." + fork,
                    str(forks["passed"] if fork in ("lorentz", "maxwell") else forks["latest"]),
                ]
            if "rpc_evm_timeout_seconds" in self.c:
                args += ["--rpc.evmtimeout", str(self.c["rpc_evm_timeout_seconds"]) + "s"]
        archive = folder.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as t:
            for item in folder.iterdir():
                t.add(item, arcname=item.name)
        shared = self.shared / ("bsc-pqc-" + self.c["name"]) / "identities"
        shared.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = shared / (h["id"] + ".tar.gz")
        shutil.copy2(archive, target)
        return {
            "overlay": str(Path(h["shared"]) / target.relative_to(self.shared)),
            "overlay_sha256": devnet.digest(target),
            "remote": remote,
            "validator_args": args,
        }

    def deploy(self):
        self.precheck()
        if self.state.get("prepared"):
            self.assert_config()
        seed = self.prepare_seed()
        rel, digest = self.package()
        if "forks" not in self.state:
            now = int(time.time())
            self.state["forks"] = {
                "passed": now + self.c["jenner_delay"] - 120,
                "latest": now + self.c["jenner_delay"],
                "pq": now + self.c["pq_delay"],
            }
            self.save()
        require(
            not self.state.get("started") or self.state.get("installed"),
            "Incomplete startup state",
        )
        for h in self.c["hosts"]:
            # Retry only missing installs; never reinitialize a running node.
            if h["id"] in self.state.get("installed", []):
                continue
            self.state.setdefault("attempted", [])
            if h["id"] not in self.state["attempted"]:
                self.state["attempted"].append(h["id"])
            self.save()
            self.call(
                h,
                "install",
                archive=str(Path(h["shared"]) / rel),
                sha256=digest,
                **self.overlay(h),
            )
            self.state.setdefault("installed", []).append(h["id"])
            self.save()
            print(h["id"] + ": installed")
        if not self.state.get("started"):
            require(
                time.time() < self.state["forks"]["passed"] - 30,
                "Deployment exceeded fork preparation window; reset with a longer delay",
            )
        self.control("stop", roles=["bench", "bundler"])
        self.state["started"] = True
        self.state["ready"] = False
        self.save()
        self.control("start", roles=["validator"])
        self.check_chain()
        # Reuse idempotent StakeHub registration with SSM-routed RPC calls.
        vals = [h for h in self.c["hosts"] if "validator" in h["roles"]]
        original = devnet.rpc
        devnet.rpc = lambda cfg, i, m, params=None: self.call(
            vals[i],
            "rpc",
            url=rpc_url(self.c, vals[i]["id"]),
            method=m,
            params=params or [],
        )
        try:
            with (self.home / "register.log").open("a") as log, contextlib.redirect_stdout(log):
                devnet.register(
                    seed,
                    devnet.read(seed / "environment.json"),
                    devnet.read(seed / "config.json"),
                )
        finally:
            devnet.rpc = original
        self.start_bundlers()
        for h in self.c["hosts"]:
            if "bench" in h["roles"]:
                self.call(h, "smoke")
                print(h["id"] + ": v4/v7 create and transfer passed")
        self.check_load()
        self.state["ready"] = True
        self.save()
        print("Deployment verified. Bench services remain stopped; use load for distributed testing.")

    def control(self, action, roles=None):
        # Finish one role across the cluster before moving to the next.
        # A failed host must not prevent stop from reaching the remaining hosts.
        order = roles if roles is not None else ["bench", "bundler", "validator"]
        failures = []
        for role in order:
            for h in self.c["hosts"]:
                if role not in h["roles"]:
                    continue
                try:
                    result = self.call(h, action, roles=[role])
                    print(h["id"] + ": " + json.dumps(result))
                except Exception:
                    failures.append(h["id"] + "/" + role)
                    if action not in ("stop", "status"):
                        raise
        require(not failures, "Service action failed for: " + ", ".join(failures))

    def start_bundlers(self):
        # Bootstrap is idempotent; repair bench bindings after a partial deployment too.
        deployments = {}
        for h in self.c["hosts"]:
            if "bundler" in h["roles"]:
                print(h["id"] + ": preparing Bundler contracts/config", flush=True)
                deployments[h["id"]] = self.call(h, "bootstrap")
                self.call(h, "start", roles=["bundler"])
        for h in self.c["hosts"]:
            if "bench" in h["roles"]:
                bh = by_id(self.c, h["bundler_node"])
                self.call(h, "bench-config", deployment=deployments[bh["id"]],
                          bundler_config={"port": bh["ports"]["bundler"]})
        self.check_bundlers()

    def check_load(self):
        for h in self.c["hosts"]:
            if "bench" in h["roles"]:
                print(h["id"] + ": verifying ECDSA / ML / SLH prepare, create and transfer", flush=True)
                self.call(h, "load-smoke")
                print(h["id"] + ": ECDSA / ML / SLH load create and transfer passed")

    def check_bundlers(self):
        for h in self.c["hosts"]:
            if any(r in h["roles"] for r in ("bundler", "bench")):
                self.call(h, "bundler-ready")

    def check_chain(self):
        vals = [h for h in self.c["hosts"] if "validator" in h["roles"]]
        genesis = devnet.read(self.home / "seed/environment.json")["genesis_hash"]
        end = max(time.time() + 180, self.state["forks"]["pq"] + 180)
        before = None
        while time.time() < end:
            try:
                rows = [self.call(h, "health") for h in vals]
                require(all(r["genesis"] == genesis for r in rows), "Genesis mismatch")
                for r in rows:
                    require(
                        r["forks"].get("pqTime") == self.state["forks"]["pq"],
                        "PQTime mismatch",
                    )
                    for fork in devnet.FORKS["passed"] + devnet.FORKS["recent"]:
                        expected = (
                            self.state["forks"]["passed"]
                            if fork in devnet.FORKS["passed"] + ["lorentz", "maxwell"]
                            else self.state["forks"]["latest"]
                        )
                        require(
                            r["forks"].get(fork + "Time") == expected,
                            "Hardfork schedule mismatch",
                        )
                if before is None:
                    before = rows
                    time.sleep(2)
                    continue
                if all(
                    r["peers"]
                    and r["height"] > before[i]["height"]
                    and r["timestamp"] >= self.state["forks"]["pq"]
                    and r["finalized"]
                    and int(r["finalized"]["number"], 16) > 0
                    and int(r["finalized"]["number"], 16)
                    > int((before[i]["finalized"] or {}).get("number", "0x0"), 16)
                    for i, r in enumerate(rows)
                ):
                    height = hex(min(int(r["finalized"]["number"], 16) for r in rows))
                    blocks = [
                        self.call(
                            h,
                            "rpc",
                            url=rpc_url(self.c, h["id"]),
                            method="eth_getBlockByNumber",
                            params=[height, False],
                        )
                        for h in vals
                    ]
                    require(
                        len({(r["hash"], r["stateRoot"]) for r in blocks}) == 1,
                        "Nodes disagree at common height",
                    )
                    vectors = [
                        (
                            "0x0101",
                            devnet.read(devnet.BOX / "precompile/v2/testdata/slh_v2.json")[0]["input"],
                        ),
                        (
                            "0x0103",
                            devnet.read(devnet.BOX / ".runtime/build/cluster/mldsa-public.json")["input"],
                        ),
                    ]
                    for h in vals:
                        for addr, data in vectors:
                            out = self.call(
                                h,
                                "rpc",
                                url=rpc_url(self.c, h["id"]),
                                method="eth_call",
                                params=[
                                    {
                                        "to": "0x" + addr[2:].zfill(40),
                                        "data": "0x" + data,
                                        "gas": "0xf4240",
                                    },
                                    "latest",
                                ],
                            )
                            require(
                                out == "0x" + "00" * 31 + "01",
                                "PQ precompile check failed",
                            )
                    print("PASS: matching genesis/state, peers, finalized blocks and both PQ precompiles")
                    return
            except RuntimeError:
                pass
            time.sleep(5)
        raise RuntimeError("Chain health deadline exceeded; private logs retained")

    def reset(self, confirm):
        require(confirm == self.c["name"], "reset requires --confirm ENVIRONMENT_NAME")
        self.assert_config()
        for h in self.c["hosts"]:
            if h["id"] in set(self.state.get("installed", []) + self.state.get("attempted", [])):
                self.call(h, "reset")
                for key in ("installed", "attempted"):
                    if h["id"] in self.state.get(key, []):
                        self.state[key].remove(h["id"])
                self.save()
        # Preserve controller logs and identities as a private backup, never silently erase keys.
        backup = self.home.with_name(self.home.name + "-backup-" + str(int(time.time())))
        self.home.rename(backup)
        print(
            "Environment stopped and removed remotely; private controller backup retained. Run deploy to generate new identities."
        )

    def collect(self):
        out = self.home / "results"
        out.mkdir(exist_ok=True)
        for h in self.c["hosts"]:
            if "bench" not in h["roles"]:
                continue
            rel = Path("bsc-pqc-" + self.c["name"]) / "results" / (h["id"] + ".json")
            self.call(h, "collect", output=str(Path(h["shared"]) / rel))
            shutil.copy2(self.shared / rel, out / (h["id"] + ".json"))
        print("Collected whitelisted results in private cluster results directory")

    def configure(self):
        old = self.state.get("inventory")
        require(old, "Deploy first")
        mutable = {"cache_mb", "gas_limit", "gas_price_wei", "bench", "rpc_evm_timeout_seconds"}
        require(
            {k: v for k, v in old.items() if k not in mutable}
            == {k: v for k, v in self.c.items() if k not in mutable},
            "Only cache, gas, RPC timeout and bench settings can change; use new environment for topology/forks",
        )
        self.state["ready"] = False
        self.save()
        self.control("stop", roles=["bench", "bundler"])
        for h in self.c["hosts"]:
            if "validator" in h["roles"]:
                self.call(h, "stop", roles=["validator"])
            payload = self.overlay(h)
            text = (
                (self.home / "overlays" / h["id"] / "node/config.toml").read_text()
                if "validator" in h["roles"]
                else ""
            )
            self.call(
                h,
                "configure",
                validator_args=payload["validator_args"],
                node_config=text,
            )
            if "validator" in h["roles"]:
                self.call(h, "start", roles=["validator"])
        self.check_chain()
        self.start_bundlers()
        self.check_load()
        self.state["inventory"] = self.c
        self.state["ready"] = True
        self.save()
        print("Configuration applied; bench remains stopped")

    def upgrade(self):
        self.precheck()
        rel, digest = self.package()
        self.state["ready"] = False
        self.save()
        self.control("stop", roles=["bench", "bundler"])
        changed = []
        operation = uuid.uuid4().hex
        try:
            for h in self.c["hosts"]:
                print(h["id"] + ": installing release and restarting validator", flush=True)
                self.call(h, "stop", roles=[r for r in ["validator"] if r in h["roles"]])
                changed.append(h)
                self.call(
                    h,
                    "install",
                    operation=operation,
                    archive=str(Path(h["shared"]) / rel),
                    sha256=digest,
                    **self.overlay(h),
                )
                if "validator" in h["roles"]:
                    self.call(h, "start", roles=["validator"])
            self.check_chain()
            self.start_bundlers()
            self.check_load()
        except Exception:
            self.state["rollback"] = {}
            for h in reversed(changed):
                try:
                    self.state["rollback"][h["id"]] = self.call(h, "rollback", operation=operation)
                except Exception:
                    self.state["rollback"][h["id"]] = {"failed": True}
            self.state["ready"] = False
            self.save()
            raise RuntimeError(
                "Upgrade failed; rollback attempted, data preserved. Services may be stopped; inspect status and private errors before start"
            )
        self.state["ready"] = True
        self.state.pop("rollback", None)
        self.save()
        print("Upgrade verified; identities/data retained; bench remains stopped")


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description="Isolated bsc-pqc remote cluster over AWS SSM")
    p.add_argument(
        "command",
        choices=[
            "plan",
            "precheck",
            "build",
            "deploy",
            "status",
            "stop",
            "restart",
            "start",
            "reset",
            "bench",
            "collect",
            "load",
            "configure",
            "upgrade",
            "monitor",
        ],
    )
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--confirm")
    p.add_argument("--load-action", choices=["prepare", "create", "transfer", "reconcile", "status", "stop"], default="status")
    p.add_argument("--algorithm", choices=["ecdsa", "ml", "slh"], default="ml")
    p.add_argument("--dataset", default="accounts")
    p.add_argument("--run", default="run1")
    p.add_argument("--accounts", type=int, default=1000, help="Total accounts across all bench clients")
    p.add_argument("--count", type=int, help="Total operations; defaults to accounts")
    p.add_argument("--qps", type=float, default=100, help="Total target send rate across all bench clients")
    p.add_argument("--workers", type=int, default=8, help="Worker threads per bench client")
    p.add_argument("--ml-backend", choices=["process", "persistent"], default="process", help="ML signer process reuse; fresh signatures and local verification remain enabled")
    p.add_argument("--slh-backend", choices=["noble", "openssl", "avx2", "avx512"], default="noble", help="SLH signing backend; SIMD requires a compatible Linux x86_64 release")
    p.add_argument("--max-inflight", type=int, help="Outstanding operations per bench client; defaults to workers")
    p.add_argument("--cache-accounts", type=int, default=64, help="Cached accounts per worker; 0 disables caching")
    p.add_argument("--fund-wei", default="100000000000000000")
    p.add_argument("--value-wei", default="1")
    p.add_argument("--submission", choices=["bundler", "direct"], default="bundler", help="Direct is a chain-capacity control, without Bundler simulation")
    p.add_argument("--direct-gas-file", type=Path, help="Explicit gas limits for the direct transfer control")
    p.add_argument("--recipients-file", type=Path, help="Private JSON array of account-pool addresses; random transfers excluding self")
    p.add_argument("--interval", type=float, default=10, help="Monitor report interval in seconds")
    p.add_argument("--duration", type=float, default=0, help="Seconds: monitor lifetime or load create/transfer dispatch window; 0 disables limit")
    p.add_argument("--auto-stop", action="store_true", help="Stop only the explicitly bound load run on instability")
    p.add_argument("--monitor-dataset", help="Dataset protected by auto-stop")
    p.add_argument("--monitor-run", help="Run protected by auto-stop")
    p.add_argument("--monitor-policy", type=Path, help="Optional JSON threshold overrides")
    args = p.parse_args()
    c = load(args.config)
    # Require inventory to live outside tracked project files or in an ignored path.
    if args.config.resolve().is_relative_to(ROOT):
        require(
            subprocess.run(["git", "check-ignore", "-q", str(args.config.resolve())], cwd=ROOT).returncode
            == 0,
            "Inventory inside repository must be Git-ignored",
        )
    if args.command == "monitor":
        # Monitoring must not hold the lifecycle lock and block load/stop commands.
        with locked(c["name"]):
            cluster = Cluster(c)
            cluster.assert_config()
        with locked("monitor-" + c["name"]):
            from monitor import execute
            execute(cluster, args)
        return
    with locked(c["name"]):
        cluster = Cluster(c)
        if args.command in ("stop", "status", "reset") and cluster.state.get("inventory"):
            # Recovery operations target the recorded deployment even after editing config.
            cluster.c = cluster.state["inventory"]
            cluster.shared = Path(cluster.c["shared_controller"])
        if args.command == "plan":
            print(
                json.dumps(
                    {
                        "environment": c["name"],
                        "hosts": [
                            {
                                "id": h["id"],
                                "roles": h["roles"],
                                "arch": h["arch"],
                                "ports": h["ports"],
                            }
                            for h in c["hosts"]
                        ],
                    },
                    indent=2,
                )
            )
            return
        if args.command == "precheck":
            cluster.precheck()
            return
        if args.command == "build":
            from build import build
            import bundler

            with locked("build"), (cluster.home / "build.log").open("a") as log, contextlib.redirect_stdout(
                log
            ), contextlib.redirect_stderr(log):
                build()
                bundler.build()
                # Only the remote artifact listens on configured private interfaces.
                # Actual bind address is provided through a generated per-host launcher.
            print("Linux client, wallet helper, contracts and Alto built; logs remain private")
            return
        if args.command == "deploy":
            cluster.deploy()
            return
        if args.command == "reset":
            cluster.reset(args.confirm)
            return
        if args.command == "configure":
            cluster.configure()
            return
        cluster.assert_config()
        if args.command == "load":
            from load_bench import execute
            execute(cluster, args)
        elif args.command in ("status", "stop"):
            cluster.control(args.command)
        elif args.command in ("start", "restart"):
            cluster.control("stop", roles=["bench", "bundler"])
            cluster.control(args.command, roles=["validator"])
            cluster.check_chain()
            cluster.control("start", roles=["bundler"])
            cluster.check_bundlers()
        elif args.command == "bench":
            require(cluster.state.get("ready"), "Run successful deploy before bench")
            for h in cluster.c["hosts"]:
                if "bench" in h["roles"]:
                    cluster.call(h, "load-clear")
            cluster.check_chain()
            cluster.check_bundlers()
            cluster.control("start", roles=["bench"])
        elif args.command == "collect":
            cluster.collect()
        elif args.command == "upgrade":
            cluster.upgrade()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        logs = devnet.RUNTIME / "clusters"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (logs / "last-error.log").open("a") as f:
            traceback.print_exc(file=f)
        # Never print exception messages which can include private inventory or endpoints.
        print(
            "bsc-pqc command failed: "
            + type(e).__name__
            + ". Check the private runtime logs and configuration.",
            file=sys.stderr,
        )
        sys.exit(1)
