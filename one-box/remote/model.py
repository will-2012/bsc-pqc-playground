"""Private inventory validation and deterministic service identities."""

import ipaddress
import json
import re
from pathlib import Path

ROLES = {"validator", "bundler", "bench"}
PORTS = {"rpc": 29545, "p2p": 33311, "metrics": 29660, "pprof": 29661, "bundler": 24337}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    c = json.loads(Path(path).read_text())
    validate(c)
    return c


def validate(c):
    require(c.get("schema") == 1, "Unsupported inventory schema")
    root = c.get("deploy_root")
    require(isinstance(root, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", root)
            and ".." not in Path(root).parts and root != "/",
            "Set deploy_root to the existing deployment parent in private inventory")
    require(
        re.fullmatch(r"[a-z][a-z0-9-]{0,23}", c.get("name", "")),
        "Invalid environment name",
    )
    require(isinstance(c.get("hosts"), list) and c["hosts"], "hosts must be a nonempty list")
    require(
        isinstance(c.get("shared_controller"), str)
        and re.fullmatch(r"/[A-Za-z0-9_./-]+", c["shared_controller"]),
        "Invalid shared controller path",
    )
    require(
        ".." not in Path(c["shared_controller"]).parts and c["shared_controller"] != "/",
        "Unsafe shared path",
    )
    ids = set()
    instances = set()
    addresses = set()
    for h in c["hosts"]:
        require(
            re.fullmatch(r"[a-z][a-z0-9-]{0,23}", h.get("id", "")),
            "Invalid logical host ID",
        )
        require(h["id"] not in ids, "Duplicate logical host")
        ids.add(h["id"])
        require(
            re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", h.get("instance_id", "")),
            "Set a real private instance ID in inventory",
        )
        require(
            h["instance_id"] not in instances,
            "One inventory entry per physical instance",
        )
        instances.add(h["instance_id"])
        require(
            re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", h.get("region", "")),
            "Invalid AWS region",
        )
        ip = ipaddress.ip_address(h["address"])
        require(
            ip.version == 4
            and not ip.is_loopback
            and not ip.is_unspecified
            and not ip.is_multicast
            and not ip.is_link_local,
            "Use a reachable IPv4 address",
        )
        require(h["address"] not in addresses, "Duplicate host address")
        addresses.add(h["address"])
        require(
            h.get("arch") in ("x86_64", "aarch64"),
            "Supported architectures: x86_64 / aarch64",
        )
        require(
            set(h.get("roles", [])) <= ROLES and h.get("roles") and len(set(h["roles"])) == len(h["roles"]),
            "Invalid roles",
        )
        require(
            re.fullmatch(r"/[A-Za-z0-9_./-]+", h.get("shared", ""))
            and ".." not in Path(h["shared"]).parts
            and h["shared"] != "/",
            "Invalid remote shared path",
        )
        for runtime in ("python", "node"):
            value = h.get(runtime, "python3" if runtime == "python" else "node")
            require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./-]+", value)
                    and ".." not in Path(value).parts
                    and ("/" not in value or value.startswith("/")),
                    "Invalid runtime executable")
        require(set(h.get("ports", {})) == set(PORTS), "Specify all five service ports")
        values = list(h["ports"].values())
        require(
            all(type(p) is int and 1024 <= p <= 65535 for p in values) and len(values) == len(set(values)),
            "Ports overlap or out of range",
        )
    vals = [h for h in c["hosts"] if "validator" in h["roles"]]
    require(3 <= len(vals) <= 7, "Need 3–7 validators")
    require(any("bundler" in h["roles"] for h in c["hosts"]), "Need a bundler")
    for h in c["hosts"]:
        if "rpc_node" in h:
            require(
                any(v["id"] == h["rpc_node"] for v in vals),
                "rpc_node must name a validator",
            )
        if "bundler" in h["roles"]:
            require(
                any(v["id"] == h.get("rpc_node") for v in vals),
                "Bundler rpc_node must name a validator",
            )
        if "bench" in h["roles"]:
            require(
                any(b["id"] == h.get("bundler_node") and "bundler" in b["roles"] for b in c["hosts"]),
                "Bench bundler_node must name a bundler",
            )
    require(
        type(c.get("min_free_gib")) is int and c["min_free_gib"] >= 10,
        "Specify min_free_gib >=10",
    )
    require(
        c.get("bench", {}).get("version") in ("v4", "v7"),
        "Bench version must be v4 or v7",
    )
    require(
        type(c["bench"].get("accounts")) is int and 1 <= c["bench"]["accounts"] <= 100,
        "Bench accounts must be 1..100 per batch",
    )
    require(
        type(c["bench"].get("rounds")) is int and 1 <= c["bench"]["rounds"] <= 100000,
        "Specify finite bench rounds",
    )
    require(
        all(type(c.get(k)) is int and c[k] > 0 for k in ("cache_mb", "gas_limit", "gas_price_wei")),
        "Invalid chain resource settings",
    )
    require(
        type(c.get("jenner_delay")) is int and c["jenner_delay"] >= 300,
        "jenner_delay must be >=300 seconds",
    )
    require(
        type(c.get("pq_delay")) is int and c["pq_delay"] > c["jenner_delay"],
        "PQ must follow Jenner",
    )
    require(len(c["hosts"]) <= 99, "At most 99 funded hosts are supported")
    require(
        c["cache_mb"] >= 64 and c["gas_limit"] >= 10000000,
        "Cache or gas budget is too small",
    )
    require(c["name"] != "build", "Environment name is reserved")
    if "min_available_mib" in c:
        require(
            type(c["min_available_mib"]) is int and c["min_available_mib"] > 0,
            "Invalid memory threshold",
        )
    require(
        any("bench" in h["roles"] for h in c["hosts"]),
        "Need a bench client for acceptance",
    )
    return c


def base(c):
    return Path(c["deploy_root"]) / c["name"]


def unit(c, role):
    return f"bsc-pqc-{c['name']}-{role}.service"


def by_id(c, identity):
    return next(h for h in c["hosts"] if h["id"] == identity)


def rpc_url(c, identity):
    h = by_id(c, identity)
    address = h["address"] if ":" not in h["address"] else "[" + h["address"] + "]"
    return f"http://{address}:{h['ports']['rpc']}"
