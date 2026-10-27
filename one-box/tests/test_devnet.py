"""Guards for configuration and destructive command boundaries.

Run with: python3 -m unittest discover -s one-box/tests -v
The live chain is verified separately with ./tools/one-box check.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "one-box/scripts"))
from devnet import MUTABLE, RUNTIME, free_ports, node_config, validate  # noqa: E402
from genesis import rlp  # noqa: E402
import devnet  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "one-box/config.json").read_text())

    def test_distinct_local_port_ranges(self):
        validate(self.cfg)
        self.cfg["p2p_port"] = self.cfg["rpc_port"]
        with self.assertRaises(ValueError):
            validate(self.cfg)

    def test_v2_is_an_explicit_optional_extension(self):
        validate(self.cfg)
        self.assertNotIn("pq_precompile", self.cfg)
        self.cfg["pq_precompile"] = 1
        validate(self.cfg)
        self.assertNotIn("pq_precompile", MUTABLE)
        self.cfg["pq_precompile"] = 2
        with self.assertRaises(ValueError):
            validate(self.cfg)

    def test_v2_health_rejects_a_client_without_the_precompile(self):
        with patch.object(devnet, "rpc", return_value="0x"):
            with self.assertRaisesRegex(RuntimeError, "missing the v2 SLH precompile"):
                devnet.check_precompile(self.cfg, 0)
        with patch.object(devnet, "rpc", return_value="0x" + "00" * 31 + "01"):
            devnet.check_precompile(self.cfg, 0)

    def test_chain_and_key_changes_require_new_genesis(self):
        self.assertFalse({"chain_id", "validators", "test_accounts", "test_balance_bnb"} & MUTABLE)
        self.cfg["chain_id"] = 56
        with self.assertRaises(ValueError):
            validate(self.cfg)

    def test_enough_funds_to_register(self):
        self.cfg["validator_balance_bnb"] = 20001
        with self.assertRaises(ValueError):
            validate(self.cfg)

    def test_templates_are_local_and_do_not_embed_secrets(self):
        import tomllib
        config = tomllib.loads(node_config(self.cfg, 2, []))
        self.assertEqual(config["Node"]["HTTPHost"], "127.0.0.1")
        self.assertEqual(config["Node"]["P2P"]["ListenAddr"], "127.0.0.1:32313")
        self.assertNotIn("admin", config["Node"]["HTTPModules"])
        self.assertNotIn("password", json.dumps(config).lower())

    def test_bad_reset_does_not_create_environment(self):
        path = RUNTIME / "test-reset-guard"
        self.assertFalse(path.exists())
        result = subprocess.run([str(ROOT / "tools/one-box"), "--name", "test-reset-guard", "reset"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--yes", result.stderr)
        self.assertFalse(path.exists())

    def test_configuration_rejects_unknown_fields(self):
        config = copy.deepcopy(self.cfg)
        config["private_key"] = 1
        with self.assertRaises(ValueError):
            validate(config)

    def test_real_listener_is_rejected_by_port_check(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.cfg["rpc_port"] = listener.getsockname()[1]
            with self.assertRaises(RuntimeError):
                free_ports(self.cfg, [0])

    def test_invalid_reset_config_preserves_existing_files(self):
        RUNTIME.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="guard-", dir=RUNTIME) as folder:
            env = Path(folder)
            marker = env / "keep.txt"
            marker.write_text("existing data")
            self.cfg["validators"] = 0
            config = env / "bad-config.json"
            config.write_text(json.dumps(self.cfg))
            result = subprocess.run([str(ROOT / "tools/one-box"), "--name", env.name, "reset", "--yes", "--config", str(config)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "existing data")


class RlpTests(unittest.TestCase):
    def test_standard_vectors(self):
        for value, expected in [(b"dog", "83646f67"), ([b"cat", b"dog"], "c88363617483646f67"),
                                (0, "80"), (1024, "820400"), ([], "c0")]:
            self.assertEqual(rlp(value).hex(), expected)


class HealthTests(unittest.TestCase):
    def test_progress_retries_transient_startup_snapshot(self):
        before = {"nodes": [{"height": 10, "finalized": 9}]}
        after = {"nodes": [{"height": 12, "finalized": 11}]}
        with patch.object(devnet, "check", side_effect=[RuntimeError("peer reconnect"), before, after]):
            with patch.object(devnet.time, "sleep"):
                self.assertEqual(devnet.check_progress(None), after)


class GitExclusionTests(unittest.TestCase):
    def test_runtime_and_key_paths_are_ignored_and_untracked(self):
        paths = ["one-box/.runtime/devnet/node0/password.txt", "one-box/.runtime/build/geth",
                 "one-box/.runtime/devnet/bundler/private.json", "one-box/.runtime/devnet/bundler/alto.json",
                 "one-box/runtime/test.json", "one-box/keystore/test.json", "one-box/nodekey",
                 "one-box/password.txt", "one-box/private/test.json", "one-box/secrets/test.json",
                 "one-box/test.key", "one-box/test.pem"]
        for path in paths:
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
            self.assertEqual(result.returncode, 0, path)
        tracked = subprocess.check_output(["git", "ls-files", "--", "one-box"], cwd=ROOT, text=True).splitlines()
        for path in tracked:
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
            self.assertNotEqual(result.returncode, 0, f"Secret/runtime path is tracked: {path}")


if __name__ == "__main__":
    unittest.main()
