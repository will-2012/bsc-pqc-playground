#!/usr/bin/env python3
"""Check an existing devnet and transfer 0.01 BNB using a generated test key."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "one-box/scripts"))
from devnet import RUNTIME, check_progress, command, load, rpc, wait_for, write  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="devnet")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}", args.name):
        parser.error("Invalid environment name")
    env = RUNTIME / args.name
    manifest, cfg = load(env)
    health = check_progress(env)
    sender = manifest["accounts"][0]
    # Send to another generated account, or a validator if only one test account.
    receiver = (manifest["accounts"][1:] or manifest["nodes"])[0]["address"]
    before = int(rpc(cfg, 0, "eth_getBalance", [receiver, "latest"]), 16)
    keystore, = (env / sender["keystore"]).glob("*")
    receipt = json.loads(command([
        "cast", "send", receiver, "--value", "0.01ether", "--legacy", "--gas-price", str(cfg["gas_price_wei"]),
        "--rpc-url", f'http://127.0.0.1:{cfg["rpc_port"]}', "--keystore", keystore,
        "--password-file", env / sender["password_file"], "--json",
    ], timeout=90))
    if receipt["status"] != "0x1":
        raise RuntimeError("Transfer reverted")
    txhash = receipt["transactionHash"]
    wait_for("transfer visible on all validators", lambda: all(
        rpc(cfg, i, "eth_getTransactionReceipt", [txhash]) for i in range(cfg["validators"])))
    hashes = {rpc(cfg, i, "eth_getTransactionReceipt", [txhash])["blockHash"] for i in range(cfg["validators"])}
    if len(hashes) != 1:
        raise RuntimeError("Validators disagree on transfer block")
    if len(manifest["accounts"]) > 1 and int(rpc(cfg, 0, "eth_getBalance", [receiver, "latest"]), 16) - before != 10**16:
        raise RuntimeError("Recipient balance did not increase by 0.01 BNB")
    result = {"health": health, "transfer_hash": txhash, "transfer_gas": int(receipt["gasUsed"], 16),
              "all_validators_agree": True}
    write(env / "smoke-result.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
