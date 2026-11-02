"""Offline tests: no AWS calls, service changes or real network traffic."""

import copy
import io
import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model
import cluster
import agent
from transport import SSM


def inventory():
    c = json.loads((cluster.devnet.BOX / "templates/cluster.example.json").read_text())
    for i, h in enumerate(c["hosts"]):
        h["instance_id"] = "i-" + format(i + 1, "017x")
        # Documentation-only address range; never used for connections in these tests.
        h["address"] = str(__import__("ipaddress").IPv4Address(0xC0000201 + i))
    return c


class InventoryTests(unittest.TestCase):
    def test_valid_and_isolated_names(self):
        c = model.validate(inventory())
        self.assertEqual(str(model.base(c)), "/server/will/ap-pq")
        self.assertEqual(model.unit(c, "validator"), "bsc-pqc-ap-pq-validator.service")

    def test_bad_inventory_rejected(self):
        for mutate in [
            lambda c: c.update(name="../other"),
            lambda c: c["hosts"][1].update(instance_id=c["hosts"][0]["instance_id"]),
            lambda c: c["hosts"][0]["ports"].update(rpc=33311),
            lambda c: c["hosts"][0].update(shared="/"),
            lambda c: c["hosts"][0].update(rpc_node="missing"),
            lambda c: c.update(pq_delay=20),
            lambda c: c["hosts"][0].update(instance_id="$(anything)"),
            lambda c: c["bench"].update(rounds=0),
        ]:
            c = inventory()
            mutate(c)
            with self.assertRaises((ValueError, KeyError)):
                model.validate(c)

    def test_separate_roles(self):
        c = inventory()
        a = copy.deepcopy(c["hosts"][0])
        a.update(
            id="load",
            instance_id="i-00000000000000009",
            address=str(__import__("ipaddress").IPv4Address(0xC0000209)),
            roles=["bench"],
        )
        c["hosts"].append(a)
        model.validate(c)

    def test_service_compatibility(self):
        text = agent.service_text(inventory(), "validator", "/usr/bin/python3 /server/will/ap-pq/agent.py")
        self.assertNotIn("append:", text)  # Amazon Linux 2 systemd 219
        self.assertIn("KillMode=control-group", text)
        self.assertNotIn("bsc.service", text)

    def test_shell_values_rejected(self):
        c = inventory()
        c["shared_controller"] = "/mnt/a;rm"
        with self.assertRaises(ValueError):
            model.validate(c)


class RuntimeTests(unittest.TestCase):
    def test_explicit_runtimes(self):
        c = inventory()
        c["hosts"][0]["python"] = "/server/will/toolchain/python311/bin/python3.11"
        c["hosts"][0]["node"] = "/server/will/toolchain/node/bin/node"
        model.validate(c)
        c["hosts"][0]["python"] = "python3; id"
        with self.assertRaises(ValueError):
            model.validate(c)

    def test_digest_without_python311_api(self):
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder) / "test"
            f.write_bytes(b"abc")
            with patch.object(agent.hashlib, "file_digest", None):
                self.assertEqual(agent.digest(f), agent.hashlib.sha256(b"abc").hexdigest())


class SSMTests(unittest.TestCase):
    def test_wait_and_private_results(self):
        with tempfile.TemporaryDirectory() as temp:
            s = SSM(temp)
            responses = [
                {"Command": {"CommandId": "ticket"}},
                {"CommandInvocations": []},
                {"CommandInvocations": [{"Status": "InProgress"}]},
                {"CommandInvocations": [{"Status": "Success", "CommandPlugins": [{
                    "Status": "Success", "ResponseCode": 0, "Output": '{"ok":true}'
                }]}]},
            ]
            with patch.object(s, "aws", side_effect=responses) as aws, patch("transport.time.sleep"):
                self.assertEqual(s.run(inventory()["hosts"][0], ["command"]), '{"ok":true}')
                self.assertEqual(aws.call_count, 4)
            self.assertFalse(list(Path(temp).glob("*request.json")))

    def test_failure_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            s = SSM(temp)
            with patch.object(
                s,
                "aws",
                side_effect=[
                    {"Command": {"CommandId": "x"}},
                    {"CommandInvocations": [{"Status": "Failed"}]},
                    {
                        "Status": "Failed",
                        "ResponseCode": 1,
                        "StandardErrorContent": "PRIVATE",
                    },
                ],
            ):
                with self.assertRaisesRegex(RuntimeError, "ap-0: remote command failed") as caught:
                    s.run(inventory()["hosts"][0], ["command"])
                self.assertNotIn("PRIVATE", str(caught.exception))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = patch.object(cluster.devnet, "RUNTIME", Path(self.temp.name))
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.c = inventory()
        self.c["shared_controller"] = str(Path(self.temp.name) / "share")
        Path(self.c["shared_controller"]).mkdir()
        self.obj = cluster.Cluster(self.c)

    def test_token_persisted_for_precheck(self):
        other = cluster.Cluster(self.c)
        self.assertEqual(self.obj.state["token"], other.state["token"])

    def test_reset_requires_exact_environment(self):
        with self.assertRaises(ValueError):
            self.obj.reset("other")

    def test_changed_topology_not_configure(self):
        self.obj.state["inventory"] = copy.deepcopy(self.c)
        self.obj.c = copy.deepcopy(self.c)
        self.obj.c["hosts"][0]["ports"]["rpc"] += 1
        with self.assertRaisesRegex(ValueError, "Only cache"):
            self.obj.configure()

    def test_stop_order(self):
        with patch.object(self.obj, "call", return_value={}) as call, contextlib_redirect():
            self.obj.control("stop")
        self.assertEqual(
            [c.kwargs["roles"] for c in call.call_args_list],
            [[r] for r in ("bench", "bundler", "validator") for _ in range(3)],
        )

    def test_rpc_genesis_and_data_kept_on_upgrade_failure(self):
        self.obj.state["ready"] = True
        with patch.object(self.obj, "precheck"), patch.object(
            self.obj, "package", return_value=("file", "hash")
        ), patch.object(self.obj, "control"), patch.object(
            self.obj, "overlay", return_value={}
        ), patch.object(
            self.obj, "call", side_effect=[{}, {}, {}, RuntimeError("fail"), {}]
        ) as call:
            with self.assertRaises(RuntimeError):
                self.obj.upgrade()
        self.assertFalse(self.obj.state["ready"])
        self.assertTrue(any(x.args[1] == "rollback" for x in call.call_args_list))


def contextlib_redirect():
    return __import__("contextlib").redirect_stdout(io.StringIO())


class FailureBoundaryTests(unittest.TestCase):
    setUp = LifecycleTests.setUp

    def test_stop_continues_after_first_host_failure(self):
        with patch.object(
            self.obj, "call", side_effect=[RuntimeError("offline")] + [{}] * 8
        ) as call, contextlib_redirect():
            with self.assertRaisesRegex(ValueError, "ap-0/bench"):
                self.obj.control("stop")
        self.assertEqual(call.call_count, 9)

    def test_lock_precedes_state_load_and_survives_reset_path(self):
        with cluster.locked(self.c["name"]):
            with self.assertRaises(BlockingIOError):
                with cluster.locked(self.c["name"]):
                    pass
            backup = self.obj.home.with_name("backup")
            self.obj.home.rename(backup)
            with self.assertRaises(BlockingIOError):
                with cluster.locked(self.c["name"]):
                    pass

    def test_response_hash_detects_incomplete_or_wrong_shared_file(self):
        with patch.object(self.obj.ssm, "run", return_value='{"response_sha256":"wrong"}'):
            with self.assertRaisesRegex(ValueError, "remote response"):
                self.obj.call(self.c["hosts"][0], "health")

    def test_partial_reset_can_be_retried(self):
        self.obj.state.update(inventory=self.c, installed=["ap-0", "ap-1", "ap-2"])
        with patch.object(self.obj, "call", side_effect=[{}, RuntimeError("offline")]):
            with self.assertRaises(RuntimeError):
                self.obj.reset(self.c["name"])
        self.assertEqual(self.obj.state["installed"], ["ap-1", "ap-2"])
        with patch.object(self.obj, "call", return_value={}) as call, contextlib_redirect():
            self.obj.reset(self.c["name"])
        self.assertEqual([x.args[0]["id"] for x in call.call_args_list], ["ap-1", "ap-2"])


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / "environment"
        self.c = inventory()
        self.h = self.c["hosts"][0]
        self.patch = patch.object(agent, "base", return_value=self.env)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env.mkdir()
        agent.write(self.env / "owner.json", {"token": "test"})
        self.payload = {"config": self.c, "host": self.h, "token": "test"}

    def test_wrong_owner_prevents_any_service_change(self):
        with patch.object(agent, "run") as run:
            with self.assertRaisesRegex(ValueError, "ownership"):
                agent.perform("stop", {**self.payload, "token": "wrong"})
        run.assert_not_called()

    def test_foreign_service_cannot_be_stopped(self):
        with patch.object(agent, "property_value", side_effect=["loaded", "/old/geth"]), patch.object(
            agent, "run"
        ) as run:
            with self.assertRaisesRegex(ValueError, "foreign"):
                agent.perform("stop", {**self.payload, "roles": ["validator"]})
        run.assert_not_called()

    def test_bench_connection_does_not_overwrite_local_bundler(self):
        path = self.env / "runtime/bundler/deployment.json"
        agent.write(path, {"owner": "own-bundler"})
        agent.perform(
            "bench-config",
            {
                **self.payload,
                "deployment": {"owner": "other-bundler"},
                "bundler_config": {"port": 24337},
            },
        )
        self.assertEqual(agent.read(path), {"owner": "own-bundler"})
        self.assertEqual(
            agent.read(self.env / "runtime/bench/deployment.json"),
            {"owner": "other-bundler"},
        )

    def test_stale_rollback_receipt_is_ignored(self):
        agent.write(self.env / "previous.json", {"target": "invalid", "operation": "old"})
        with patch.object(agent, "run") as run:
            result = agent.perform("rollback", {**self.payload, "operation": "new"})
        self.assertEqual(result, {"unchanged": True})
        run.assert_not_called()

    def test_symlink_environment_rejected(self):
        import shutil

        shutil.rmtree(self.env)
        self.env.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "symlink"):
            agent.install(self.c, self.h, self.payload)


class TransportCancellationTests(unittest.TestCase):
    def test_poll_failure_requests_cancellation(self):
        with tempfile.TemporaryDirectory() as t:
            s = SSM(t)
            with patch.object(
                s,
                "aws",
                side_effect=[
                    {"Command": {"CommandId": "x"}},
                    RuntimeError("network"),
                    {},
                ],
            ) as aws:
                with self.assertRaises(RuntimeError):
                    s.run(inventory()["hosts"][0], ["cmd"])
            self.assertEqual(aws.call_args.args[0][:2], ["ssm", "cancel-command"])
            self.assertEqual(len(list(Path(t).glob("*command.json"))), 1)


class InstallationTests(unittest.TestCase):
    setUp = HostTests.setUp

    # Use an actual archive and filesystem; stub only host services and executable invocation.
    def make_release(self):
        import tarfile

        app = self.root / "bundle/app"
        binary = app / "one-box/.runtime/build/v4"
        binary.mkdir(parents=True)
        for name in ("geth", "pq-wallet"):
            (binary / name).write_bytes(b"test executable")
        agent.write(
            binary / "binary.json",
            {
                "platform": "Linux",
                "arch": "x86_64",
                "sha256": agent.digest(binary / "geth"),
                "helper_sha256": agent.digest(binary / "pq-wallet"),
                "source_commit": "fixed",
                "contracts_sha256": "fixed",
            },
        )
        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(app, arcname="app")
        seed = self.root / "seed"
        (seed / "node/geth").mkdir(parents=True)
        (seed / "node/geth/nodekey").write_text("original fixture identity")
        agent.write(seed / "environment.json", {"genesis_hash": "fixture"})
        agent.write(seed / "config.json", {})
        agent.write(seed / "genesis.json", {})
        overlay = self.root / "overlay.tar.gz"
        with tarfile.open(overlay, "w:gz") as t:
            for file in seed.iterdir():
                t.add(file, arcname=file.name)
        return {
            **self.payload,
            "archive": str(archive),
            "sha256": agent.digest(archive),
            "overlay": str(overlay),
            "overlay_sha256": agent.digest(overlay),
            "remote": {},
            "validator_args": [],
        }

    def test_init_interruption_preserves_identity_on_retry(self):
        p = self.make_release()
        units = self.root / "units"
        units.mkdir()
        with patch.object(agent, "SYSTEMD_DIR", units), patch.object(
            agent.platform, "machine", return_value="x86_64"
        ), patch.object(agent, "check_service", return_value=False):
            with patch.object(agent, "run", side_effect=RuntimeError("init failed")):
                with self.assertRaises(RuntimeError):
                    agent.install(self.c, self.h, p)
            self.assertFalse((self.env / "runtime/node/initialized").exists())
            key = self.env / "runtime/node/geth/nodekey"
            key.write_text("preserved after interruption")
            # Original overlay may be inaccessible on retry; existing identities must suffice.
            p["overlay"] = "missing"
            with patch.object(agent, "run", return_value=""):
                agent.install(self.c, self.h, p)
            self.assertEqual(key.read_text(), "preserved after interruption")
            self.assertTrue((self.env / "runtime/node/initialized").exists())
            self.assertTrue((self.env / "current").is_symlink())

    def test_active_service_blocks_replacing_binary(self):
        p = self.make_release()
        with patch.object(agent, "check_service", return_value=True), patch.object(
            agent, "property_value", return_value="active"
        ):
            with self.assertRaisesRegex(ValueError, "Stop services"):
                agent.install(self.c, self.h, p)
        self.assertFalse((self.env / "runtime").exists())

    def test_corrupted_release_rejected_before_extract(self):
        p = self.make_release()
        p["sha256"] = "bad"
        with patch.object(agent, "check_service", return_value=False):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                agent.install(self.c, self.h, p)
        self.assertFalse((self.env / "releases").exists())


class OverlayTests(unittest.TestCase):
    setUp = LifecycleTests.setUp

    def test_only_own_validator_and_sender_secrets_are_distributed(self):
        seed = self.obj.home / "seed"
        seed.mkdir()
        cfg = cluster.devnet.read(cluster.devnet.BOX / "config.json")
        nodes = []
        accounts = []
        for i in range(3):
            node = seed / f"node{i}"
            for folder in ("keystore", "bls", "geth"):
                (node / folder).mkdir(parents=True)
            (node / "keystore/key").write_text(f"fixture-validator-{i}")
            (node / "bls/key").write_text(f"fixture-vote-{i}")
            (node / "password.txt").write_text("fixture-password")
            (node / "geth/nodekey").write_text(f"fixture-node-{i}")
            nodes.append({"address": f"account-{i}", "enode": f"enode://public{i}@localhost:1"})
        for i in range(4):
            folder = seed / f"accounts/test{i}"
            (folder / "keystore").mkdir(parents=True)
            (folder / "keystore/key").write_text(f"fixture-sender-{i}")
            (folder / "password.txt").write_text("fixture-password")
            accounts.append(
                {
                    "address": f"account-{i}",
                    "keystore": f"accounts/test{i}/keystore",
                    "password_file": f"accounts/test{i}/password.txt",
                }
            )
        cluster.devnet.write(
            seed / "environment.json", {"nodes": nodes, "accounts": accounts, "genesis_hash": "fixture"}
        )
        cluster.devnet.write(seed / "config.json", cfg)
        cluster.devnet.write(seed / "genesis.json", {})
        self.obj.state["forks"] = {"passed": 100, "latest": 200, "pq": 300}
        self.obj.overlay(self.c["hosts"][1])
        folder = self.obj.home / "overlays/ap-1"
        self.assertEqual(sorted(p.name for p in (folder / "accounts").iterdir()), ["test1", "test3"])
        self.assertEqual((folder / "node/geth/nodekey").read_text(), "fixture-node-1")
        text = (folder / "node/config.toml").read_text()
        self.assertIn(self.c["hosts"][1]["address"], text)
        self.assertNotIn('HTTPHost = "127.0.0.1"', text)
        self.assertIn(self.c["hosts"][0]["address"], text)


class ChainAcceptanceTests(unittest.TestCase):
    setUp = LifecycleTests.setUp

    def test_static_chain_does_not_pass_health_check(self):
        self.obj.state["forks"] = {"passed": 0, "latest": 0, "pq": 0}
        forks = {f + "Time": 0 for f in cluster.devnet.FORKS["passed"] + cluster.devnet.FORKS["recent"]}
        forks["pqTime"] = 0
        row = {
            "genesis": "expected",
            "height": 10,
            "peers": 2,
            "timestamp": 10,
            "finalized": {"number": "0x5"},
            "forks": forks,
        }
        with patch.object(cluster.devnet, "read", return_value={"genesis_hash": "expected"}), patch.object(
            self.obj, "call", return_value=row
        ), patch("cluster.time.time", side_effect=[0, 1, 1000]), patch("cluster.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "health deadline"):
                self.obj.check_chain()

    def test_changed_fork_fails_before_benchmark(self):
        self.obj.state["forks"] = {"passed": 0, "latest": 0, "pq": 100}
        with patch.object(cluster.devnet, "read", return_value={"genesis_hash": "expected"}), patch.object(
            self.obj, "call", return_value={"genesis": "expected", "forks": {"pqTime": 101}}
        ):
            with self.assertRaisesRegex(ValueError, "PQTime mismatch"):
                self.obj.check_chain()


class ReleaseSourceTests(unittest.TestCase):
    def test_untracked_private_files_are_not_packaged(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            destination = Path(directory) / "release"
            destination.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in ("one-box/remote/agent.py", "erc4337-wallet/cli.mjs"):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("public source")
                subprocess.run(["git", "add", name], cwd=root, check=True)
            for name in ("erc4337-wallet/.env", "one-box/remote/private.json"):
                (root / name).write_text("fixture must not be shipped")
            cluster.copy_project_sources(root, destination)
            self.assertTrue((destination / "one-box/remote/agent.py").exists())
            self.assertFalse((destination / "erc4337-wallet/.env").exists())
            self.assertFalse((destination / "one-box/remote/private.json").exists())

    def test_tracked_link_cannot_read_outside_repository(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "one-box/remote").mkdir(parents=True)
            private = Path(directory) / "private"
            private.write_text("fixture")
            (root / "one-box/remote/agent.py").symlink_to(private)
            subprocess.run(["git", "add", "one-box/remote/agent.py"], cwd=root, check=True)
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                cluster.copy_project_sources(root, Path(directory) / "release")


if __name__ == "__main__":
    unittest.main()
