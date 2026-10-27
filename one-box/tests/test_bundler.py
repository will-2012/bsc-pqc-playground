"""Bundler configuration isolation and process ownership guards."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "one-box/scripts"))
import bundler  # noqa: E402


class BundlerTests(unittest.TestCase):
    def test_template_has_no_keys_and_preserves_validation(self):
        config = json.loads((ROOT / "one-box/bundler.json").read_text())
        self.assertTrue(config["safe-mode"])
        self.assertFalse(config["deploy-simulations-contract"])
        self.assertNotIn("executor-private-keys", config)
        self.assertNotIn("utility-private-key", config)

    def test_ports_are_persisted_per_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary)
            config = bundler.settings(env, 14338)
            self.assertEqual(config["port"], 14338)
            self.assertEqual(bundler.settings(env)["port"], 14338)
            self.assertEqual(json.loads((ROOT / "one-box/bundler.json").read_text())["port"], 14337)
            with self.assertRaises(ValueError):
                bundler.settings(env, 0)

    def test_pq_profile_is_local_and_preserves_safe_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary)
            config = bundler.settings(env, pq=True)
            self.assertTrue(config["safe-mode"])
            self.assertFalse(config["deploy-simulations-contract"])
            self.assertEqual(config["max-bundle-size"], 1)
            self.assertEqual(config["simulation-verification-gas-limit"], "16000000")
            self.assertEqual(bundler.settings(env)["timeout"], 180000)
            self.assertNotIn("simulation-verification-gas-limit", json.loads((ROOT / "one-box/bundler.json").read_text()))

    def test_stop_never_signals_an_unrelated_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary)
            (env / "bundler").mkdir()
            (env / "bundler/pid.json").write_text('{"pid": 12345}')
            with patch.object(bundler.subprocess, "run", return_value=SimpleNamespace(stdout="python unrelated.py")):
                with patch.object(bundler.os, "kill") as kill:
                    with self.assertRaises(RuntimeError):
                        bundler.stop(env)
                    kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
