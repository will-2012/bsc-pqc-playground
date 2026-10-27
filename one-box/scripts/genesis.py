"""Build a local genesis from pinned upstream system-contract sources.

Only the copy under .runtime is patched. No default upstream funded accounts
are carried into the generated chain.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "external/genesis-contracts"


def rlp(value):
    if isinstance(value, list):
        payload = b"".join(map(rlp, value))
        offset = 192
    else:
        if isinstance(value, int):
            value = value.to_bytes((value.bit_length() + 7) // 8, "big")
        payload, offset = value, 128
        if len(payload) == 1 and payload[0] < 128:
            return payload
    size = len(payload)
    if size < 56:
        return bytes([offset + size]) + payload
    length = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([offset + 55 + len(length)]) + length + payload


def patch(work, contract, pattern, replacement, count=1):
    path = work / "contracts" / (contract + ".sol")
    text, n = re.subn(pattern, lambda _: replacement, path.read_text(), count=count)
    if n != count:
        raise RuntimeError(f"Upstream source changed: {contract}: {pattern}")
    path.write_text(text)


def parameter(work, contract, name, value):
    patch(work, contract, re.escape(name) + r"\s*=[^;]*;", f"{name} = {value};")


def prepare_sources(work, cfg, validators, protector):
    shutil.copytree(SOURCE / "contracts", work / "contracts")
    # Avoid building upstream tests and scripts; production contracts need OZ only.
    (work / "foundry.toml").write_text(
        '[profile.default]\nsrc="contracts"\ntest="unused-test"\n'
        'script="unused-script"\nlibs=["node_modules"]\noptimizer=true\n'
        'optimizer_runs=200\nbytecode_hash="none"\n'
    )
    # Keep dependency resolution reproducible using the upstream lockfile.
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(SOURCE / name, work / name)
    def run(args):
        with (work / "build.log").open("a") as output:
            result = subprocess.run(args, cwd=work, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"System-contract build failed: {work / 'build.log'}")

    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"])
    records = []
    for node in validators:
        addr = bytes.fromhex(node["address"][2:])
        records.append([addr, addr, addr, 2_000_000_000_000, bytes.fromhex(node["bls_public_key"][2:])])
    package = rlp([0, records]).hex()
    parameter(work, "System", "uint16 public constant bscChainID", hex(cfg["chain_id"]))
    parameter(work, "BSCValidatorSet", "bytes public constant INIT_VALIDATORSET_BYTES", f'hex"{package}"')
    parameter(work, "BSCValidatorSet", "uint256 public constant INIT_BURN_RATIO", "1000")
    patch(work, "BSCValidatorSet", r"for \(uint256 i; i < validatorSetPkg\.validatorSet\.length; \+\+i\)",
          "ValidatorExtra memory validatorExtra;\n        for (uint256 i; i < validatorSetPkg.validatorSet.length; ++i)")
    patch(work, "BSCValidatorSet", r"currentValidatorSet\.push\(validatorSetPkg.validatorSet\[i\]\);",
          "validatorExtraSet.push(validatorExtra);\n            validatorExtraSet[i].voteAddress = validatorSetPkg.voteAddrs[i];\n            currentValidatorSet.push(validatorSetPkg.validatorSet[i]);")
    patch(work, "BSCValidatorSet", r"alreadyInit = true;", "turnLength = 16;\n        alreadyInit = true;")
    patch(work, "BSCValidatorSet", r"public onlyCoinbase onlyZeroGasPrice \{",
          "public onlyCoinbase onlyZeroGasPrice { if (block.number < 300) return;")
    patch(work, "BSCValidatorSet",
          r"handleSynPackage\(\s*uint8,\s*bytes calldata msgBytes\s*\) external override onlyInit onlyCrossChainContract initValidatorExtraSet",
          "handleSynPackage(uint8, bytes calldata msgBytes) external override onlyInit initValidatorExtraSet")
    for name, value in [("MISDEMEANOR_THRESHOLD", 50), ("FELONY_THRESHOLD", 150), ("INIT_FELONY_SLASH_SCOPE", 60)]:
        parameter(work, "SlashIndicator", "uint256 public constant " + name, str(value))
    patch(work, "SlashIndicator", r"alreadyInit = true;", "enableMaliciousVoteSlash = true;\n        alreadyInit = true;")
    parameter(work, "StakeHub", "uint256 public constant BREATHE_BLOCK_INTERVAL", f'{cfg["breathe_seconds"]} seconds')
    for name, value in [("maxElectedValidators", str(cfg["validators"])), ("unbondPeriod", "2 minutes"),
                        ("downtimeJailTime", "2 minutes"), ("felonyJailTime", "3 minutes")]:
        patch(work, "StakeHub", re.escape(name) + r" = .*;", f"{name} = {value};")
    for contract in ("StakeHub", "BSCGovernor", "TokenRecoverPortal"):
        patch(work, contract, r"__Protectable_init_unchained\(.*\);", f"__Protectable_init_unchained({protector});")
    for name, value in [("INIT_VOTING_DELAY", "0"), ("INIT_VOTING_PERIOD", "2 minutes / BLOCK_INTERVAL"),
                        ("INIT_MINIMAL_DELAY", "1 minutes")]:
        contract = "BSCTimelock" if name == "INIT_MINIMAL_DELAY" else "BSCGovernor"
        parameter(work, contract, "uint256 private constant " + name, value)
    parameter(work, "BSCGovernor", "uint64 private constant INIT_MIN_PERIOD_AFTER_QUORUM", "uint64(1 minutes / BLOCK_INTERVAL)")
    parameter(work, "TokenRecoverPortal", "string public constant SOURCE_CHAIN_ID", '"Binance-Chain-Ganges"')
    parameter(work, "TokenHub", "uint256 public constant LOCK_PERIOD_FOR_TOKEN_RECOVER", "300 seconds")
    patch(work, "GovHub", r"handleSynPackage\(\s*uint8,\s*bytes calldata msgBytes\s*\) external override onlyCrossChainContract",
          "handleSynPackage(uint8, bytes calldata msgBytes) external override")
    patch(work, "SystemReward", r"numOperator = 2;",
          "operators[VALIDATOR_CONTRACT_ADDR] = true;\n        operators[SLASH_CONTRACT_ADDR] = true;\n        numOperator = 4;")
    run(["forge", "build"])


def generate(work, cfg, validators, accounts):
    prepare_sources(work, cfg, validators, accounts[0]["address"])
    template = (SOURCE / "genesis-template.json").read_text()
    contracts = {
        "validatorContract": "BSCValidatorSet", "systemRewardContract": "SystemReward",
        "slashContract": "SlashIndicator", "tendermintLightClient": "TendermintLightClient",
        "tokenHub": "TokenHub", "relayerHub": "RelayerHub", "relayerIncentivize": "RelayerIncentivize",
        "govHub": "GovHub", "tokenManager": "TokenManager", "crossChain": "CrossChain",
        "staking": "Staking", "stakeHub": "StakeHub", "stakeCredit": "StakeCredit",
        "governor": "BSCGovernor", "govToken": "GovToken", "timelock": "BSCTimelock",
        "tokenRecoverPortal": "TokenRecoverPortal",
    }
    values = {"chainId": str(cfg["chain_id"]), "extraData": "0x" + "00" * 32 +
              "".join(v["address"][2:] for v in validators) + "00" * 65,
              "initLockedBNBOnTokenHub": "0"}
    for key, contract in contracts.items():
        artifact = json.loads((work / f"out/{contract}.sol/{contract}.json").read_text())
        values[key] = artifact["deployedBytecode"]["object"]
    template = re.sub(r"\{% for v in initHolders %\}.*?\{% endfor %\}", "", template, flags=re.S)
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    result = json.loads(template)
    # Discard every upstream fixture account, including hard-coded funded holders.
    result["alloc"] = {a: v for a, v in result["alloc"].items() if "code" in v}
    for role, balance in [(validators, cfg["validator_balance_bnb"]), (accounts, cfg["test_balance_bnb"])]:
        for account in role:
            result["alloc"][account["address"]] = {"balance": hex(balance * 10**18)}
    result["timestamp"] = hex(int(time.time()) - 1)
    result["gasLimit"] = hex(cfg["gas_limit"])
    result["config"]["parlia"] = {"period": 3, "epoch": 200}
    forks = json.loads((ROOT / "one-box/forks.json").read_text())
    for fork in forks["passed"] + forks["recent"]:
        result["config"][fork + "Time"] = 0  # First up applies persisted relative-time overrides.
    result["config"]["depositContractAddress"] = "0x" + "00" * 20
    result["config"]["blobSchedule"] = {
        f: {"target": 3, "max": 6, "baseFeeUpdateFraction": 3338477} for f in ("cancun", "prague", "osaka")
    }
    return result
