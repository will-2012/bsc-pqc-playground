"""Bounded local observations; never export keys, RPC URLs or transaction payloads."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

USER_OP_TOPIC = "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"
SLASH_TOPIC = "0xddb6012116e51abf5436d956a4f0ebd927e92c576ff96d7918290c8782291e3e"
SLASH_ADDRESS = "0x0000000000000000000000000000000000001001"
MAX_BLOCKS = 64
RETAIN_BLOCKS = 256
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
HEX_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
PROGRESS = ("status", "algorithm", "action", "total", "dispatched", "attempted", "accepted",
            "confirmed", "failed", "skipped", "pending", "inflight", "targetQps",
            "workers", "maxInflight", "peakInflight", "slhBackend", "timings", "durationSeconds", "cancelled", "endReason", "startedAt", "updatedAt", "elapsedSeconds", "startLagMs", "acceptedPerSecond",
            "confirmedPerSecond", "cacheHits", "cacheMisses")


def number(value):
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)


def read_json(path):
    with Path(path).open() as stream:
        text = stream.read(4 * 1024 * 1024 + 1)
    if len(text) > 4 * 1024 * 1024:
        raise ValueError("record too large")
    return json.loads(text)


def header(value):
    if not isinstance(value, dict) or not HEX_HASH.fullmatch(value.get("hash", "")):
        raise ValueError("invalid header")
    stamp = value.get("milliTimestamp")
    if stamp is None:
        remainder = number(value.get("mixHash", "0x0"))
        if not 0 <= remainder < 1000:
            raise ValueError("unknown timestamp precision")
        stamp = number(value["timestamp"]) * 1000 + remainder
    difficulty = number(value.get("difficulty", "0x0"))
    return {"number": number(value["number"]), "hash": value["hash"],
            "parentHash": value["parentHash"], "timestampMs": number(stamp),
            "gasUsed": number(value["gasUsed"]), "gasLimit": number(value["gasLimit"]),
            "txCount": len(value["transactions"]),
            "txHashes": [x.lower() for x in value["transactions"][:256]
                         if isinstance(x, str) and HEX_HASH.fullmatch(x)],
            "txHashesTruncated": len(value["transactions"]) > 256 or any(
                not isinstance(x, str) or not HEX_HASH.fullmatch(x) for x in value["transactions"][:256]),
            "outOfTurn": True if difficulty == 1 else False if difficulty == 2 else None,
            "userOps": None, "slashEvents": None}


class Collector:
    def __init__(self, c, h, runtime):
        self.c, self.h, self.runtime = c, h, Path(runtime)
        # The validator binds its configured address, which need not include loopback.
        address = h['address']
        authority = f'[{address}]' if ':' in address else address
        self.url = f"http://{authority}:{h['ports']['rpc']}"
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.blocks = {}
        self.last = None
        self.cpu = None
        self.rpc_failures = 0
        self.resource_previous = {}
        self.component_previous = {}
        self.components = {}

    def _batch(self, calls):
        try:
            rows = self._request(calls)
            self.rpc_failures += sum(row is None for row in rows)
            return rows
        except Exception:
            self.rpc_failures += 1
            raise

    def _request(self, calls):
        payload = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params}
                   for i, (method, params) in enumerate(calls)]
        request = urllib.request.Request(self.url, json.dumps(payload).encode(),
                                         {"Content-Type": "application/json"})
        with self.http.open(request, timeout=2) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("RPC response too large")
        rows = json.loads(data)
        if not isinstance(rows, list):
            raise ValueError("invalid RPC batch")
        mapped = {row.get("id"): row for row in rows if isinstance(row, dict)}
        return [mapped.get(i, {}).get("result") if "error" not in mapped.get(i, {}) else None
                for i in range(len(calls))]

    def _entrypoint(self):
        for folder in ("bench", "bundler"):
            try:
                address = read_json(self.runtime / folder / "deployment.json")["entryPoint"]
                if re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
                    return address.lower()
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return None

    def _bench(self):
        result = {"run": None, "dataset": None, "progress": {}, "service": None}
        if "bench" not in self.h.get("roles", []):
            return result
        result["service"] = self.components.get("bench", {}).get("service")
        try:
            job = read_json(self.runtime / "load-job.json")
            if not all(isinstance(job.get(k), str) and NAME.fullmatch(job[k]) for k in ("run", "dataset")):
                return result
            result.update(run=job["run"], dataset=job["dataset"])
            progress = read_json(self.runtime / "load" / job["dataset"] / "runs" / job["run"] / "progress.json")
            result["progress"] = {k: progress[k] for k in PROGRESS
                                  if k in progress and isinstance(progress[k], (str, int, float, bool, type(None)))}
            evidence = progress.get("inclusions", [])
            clean, budget = [], 2048
            truncated = bool(progress.get("inclusionTruncated", False))
            if not isinstance(evidence, list):
                evidence, truncated = [], True
            if len(evidence) > 128:
                truncated = True
            for row in reversed(evidence[-128:]):
                if not isinstance(row, dict) or not HEX_HASH.fullmatch(str(row.get("blockHash", ""))):
                    truncated = True
                    continue
                if not all(isinstance(row.get(k), int) and row[k] >= 0 for k in ("blockNumber", "userOps", "count")):
                    truncated = True
                    continue
                transactions = row.get("transactions", [])
                if not isinstance(transactions, list):
                    truncated = True
                    continue
                selected = [x for x in transactions[:min(256, budget)] if isinstance(x, str) and HEX_HASH.fullmatch(x)]
                truncated |= len(selected) != len(transactions)
                budget -= len(selected)
                clean.append({k: row[k] for k in ("blockNumber", "blockHash", "userOps", "count")} | {"transactions": selected})
            result["progress"]["inclusions"] = list(reversed(clean))
            result["progress"]["inclusionTruncated"] = truncated
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return result

    def _rate(self, key, values):
        now = time.monotonic()
        previous = self.resource_previous.get(key)
        self.resource_previous[key] = (now, values)
        if previous and now > previous[0] and all(a >= b for a, b in zip(values, previous[1])):
            return [(a-b)/(now-previous[0]) for a, b in zip(values, previous[1])]
        return [None] * len(values)

    def _components(self):
        roles = [r for r in ("validator", "bundler", "bench") if r in self.h.get("roles", [])]
        result = {r: {"service": None, "cpuPct": None, "memoryBytes": None,
                      "ioReadBytesPerSecond": None, "ioWriteBytesPerSecond": None} for r in roles}
        units = {f"bsc-pqc-{self.c['name']}-{r}.service": r for r in roles}
        if not units:
            return result
        try:
            response = subprocess.run(["systemctl", "show", *units,
                "--property=Id,ActiveState,MainPID,CPUUsageNSec,MemoryCurrent,IOReadBytes,IOWriteBytes"],
                capture_output=True, text=True, timeout=1)
            if response.returncode:
                return result
            now = time.monotonic()
            for paragraph in response.stdout.strip().split("\n\n"):
                row = dict(line.split("=", 1) for line in paragraph.splitlines() if "=" in line)
                role = units.get(row.get("Id"))
                if role is None:
                    continue
                out = result[role]
                state = row.get("ActiveState")
                if state in ("active", "inactive", "failed", "activating", "deactivating"):
                    out["service"] = state
                def optional(key):
                    value = row.get(key, "")
                    return int(value) if value.isdigit() and int(value) < 2**64-1 else None
                out["memoryBytes"] = optional("MemoryCurrent")
                values = [optional(k) for k in ("CPUUsageNSec", "IOReadBytes", "IOWriteBytes")]
                previous = self.component_previous.get(role)
                pid = row.get("MainPID")
                if previous and pid == previous[1] and pid != "0" and now > previous[0]:
                    for i, key in enumerate(("cpuPct", "ioReadBytesPerSecond", "ioWriteBytesPerSecond")):
                        if values[i] is not None and previous[2][i] is not None and values[i] >= previous[2][i]:
                            value = (values[i]-previous[2][i])/(now-previous[0])
                            out[key] = value / 1e7 if i == 0 else value
                self.component_previous[role] = (now, pid, values)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return result

    def _resources(self):
        result = {"scope": "host", "cpuBusyPct": None, "iowaitPct": None, "stealPct": None,
                  "diskReadBytesPerSecond": None, "diskWriteBytesPerSecond": None,
                  "networkRxBytesPerSecond": None, "networkTxBytesPerSecond": None,
                  "load1": None, "memoryAvailableBytes": None, "memoryTotalBytes": None,
                  "diskFreeBytes": None, "diskTotalBytes": None}
        try:
            result["load1"] = os.getloadavg()[0]
        except (OSError, AttributeError):
            pass
        try:
            disk = shutil.disk_usage(self.runtime)
            result.update(diskFreeBytes=disk.free, diskTotalBytes=disk.total)
        except OSError:
            pass
        try:
            memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            result["memoryAvailableBytes"] = int(memory["MemAvailable"].split()[0]) * 1024
            result["memoryTotalBytes"] = int(memory["MemTotal"].split()[0]) * 1024
            values = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
            total, idle, iowait, steal = sum(values), values[3], values[4], values[7]
            if self.cpu and total > self.cpu[0]:
                delta = total - self.cpu[0]
                non_execution = idle - self.cpu[1] + iowait - self.cpu[2] + steal - self.cpu[3]
                result["cpuBusyPct"] = max(0, min(100, 100 * (1 - non_execution / delta)))
                result["iowaitPct"] = max(0, min(100, 100 * (iowait - self.cpu[2]) / delta))
                result["stealPct"] = max(0, min(100, 100 * (steal - self.cpu[3]) / delta))
            self.cpu = (total, idle, iowait, steal)
        except (OSError, ValueError, KeyError, IndexError):
            pass
        try:
            disks = [line.split() for line in Path("/proc/diskstats").read_text().splitlines()]
            disks = [v for v in disks if len(v) >= 10 and re.fullmatch(r"(?:nvme[0-9]+n[0-9]+|(?:sd|vd|xvd)[a-z]+)", v[2])]
            if disks:
                rates = self._rate("disk", [sum(int(v[5]) for v in disks)*512, sum(int(v[9]) for v in disks)*512])
                result["diskReadBytesPerSecond"], result["diskWriteBytesPerSecond"] = rates
        except (OSError, ValueError, IndexError):
            pass
        try:
            interfaces = []
            for line in Path("/proc/net/dev").read_text().splitlines():
                if ":" not in line:
                    continue
                name, counters = line.split(":", 1)
                if (Path("/sys/class/net") / name.strip() / "device").exists():
                    interfaces.append(list(map(int, counters.split())))
            if interfaces:
                rates = self._rate("network", [sum(v[0] for v in interfaces), sum(v[8] for v in interfaces)])
                result["networkRxBytesPerSecond"], result["networkTxBytesPerSecond"] = rates
        except (OSError, ValueError, IndexError):
            pass
        return result

    def collect(self):
        began = time.monotonic()
        failures_before = self.rpc_failures
        self.components = self._components()
        output = {"host": self.h["id"], "sampledAt": int(time.time() * 1000),
                  "head": None, "safe": None, "finalized": None, "peers": None,
                  "blocks": [], "coverage": {"complete": False, "from": None, "to": None},
                  "errors": [], "logErrors": [], "bench": self._bench(), "resources": self._resources(),
                  "components": self.components}
        try:
            if "validator" not in self.h.get("roles", []):
                return output
            # Fetch finality first: it must never be compared against an older sampled head.
            safe, finalized = self._batch([("eth_getBlockByNumber", [tag, False]) for tag in ("safe", "finalized")])
            # Batch execution is not atomic; fast finality can advance between its calls.
            def inconsistent(a, b):
                return a is not None and b is not None and (
                    number(b["number"]) > number(a["number"]) or
                    (number(a["number"]) == number(b["number"]) and a["hash"] != b["hash"]))
            if inconsistent(safe, finalized):
                safe = self._batch([("eth_getBlockByNumber", ["safe", False])])[0]
                if inconsistent(safe, finalized):
                    safe, finalized = None, None
                    output["errors"].append("finality_sample_inconsistent")
            for key, value in (("safe", safe), ("finalized", finalized)):
                if value is not None:
                    output[key] = {"number": number(value["number"]), "hash": value["hash"]}
                else:
                    output["errors"].append(key + "_unavailable")
            # Pool telemetry is optional: failure must not discard chain observations.
            output["txpool"] = None
            try:
                pool = self._batch([("txpool_status", [])])[0]
                counts = {key: number(pool[key]) for key in ("pending", "queued")}
                if any(value < 0 for value in counts.values()):
                    raise ValueError("negative pool count")
                output["txpool"] = counts
            except (KeyError, TypeError, ValueError, OSError):
                output["errors"].append("txpool_unavailable")
            raw, peers = self._batch([("eth_getBlockByNumber", ["latest", False]), ("net_peerCount", [])])
            current = header(raw)
            output["head"] = {k: current[k] for k in ("number", "hash", "parentHash", "timestampMs")}
            output["peers"] = number(peers) if peers is not None else None
            height = current["number"]
            first = self.last is None
            start = max(0, height - MAX_BLOCKS + 1) if first else max(0, min(self.last - 3, height - 3))
            end = min(height, start + MAX_BLOCKS - 1)
            rows = self._batch([("eth_getBlockByNumber", [hex(n), False]) for n in range(start, end + 1)])
            fetched = [header(row) for row in rows]
            if any(block["number"] != start + i for i, block in enumerate(fetched)):
                raise ValueError("block number mismatch")
            contiguous = all(a["hash"] == b["parentHash"] for a, b in zip(fetched, fetched[1:]))
            if not contiguous:
                output["errors"].append("inconsistent_block_range")
                return output
            previous = self.blocks.get(start - 1)
            head_changed = end == height and fetched[-1]["hash"] != current["hash"]
            if head_changed:
                output["errors"].append("head_changed_during_sample")
            deep_change = previous is not None and fetched[0]["parentHash"] != previous["hash"]
            if deep_change:
                self.blocks.clear()
                output["errors"].append("reorg_beyond_overlap")
            # Replace overlap and discard stale future blocks on a head regression/reorg.
            self.blocks = {n: b for n, b in self.blocks.items() if n < start}
            entry = self._entrypoint()
            addresses = [SLASH_ADDRESS] + ([entry] if entry else [])
            try:
                logs = self._batch([("eth_getLogs", [{"blockHash": b["hash"], "address": addresses,
                                                     "topics": [[USER_OP_TOPIC, SLASH_TOPIC]]}]) for b in fetched])
            except Exception:
                logs = [None] * len(fetched)
            for block, events in zip(fetched, logs):
                if not isinstance(events, list) or any(not isinstance(e, dict) or e.get("blockHash") != block["hash"]
                                                      or e.get("removed", False) or not isinstance(e.get("topics"), list)
                                                      or not e["topics"] or not isinstance(e.get("address"), str) for e in events):
                    output["logErrors"].append({"block": block["number"], "reason": "logs_unavailable_or_changed"})
                else:
                    block["slashEvents"] = sum(e.get("address", "").lower() == SLASH_ADDRESS and e.get("topics", [None])[0] == SLASH_TOPIC for e in events)
                    if entry:
                        block["userOps"] = sum(e.get("address", "").lower() == entry and e.get("topics", [None])[0] == USER_OP_TOPIC for e in events)
                self.blocks[block["number"]] = block
            self.last = end
            self.blocks = dict(sorted(self.blocks.items())[-RETAIN_BLOCKS:])
            budget = 4096
            for block in reversed(list(self.blocks.values())):
                if len(block["txHashes"]) > budget:
                    block["txHashes"] = block["txHashes"][:budget]
                    block["txHashesTruncated"] = True
                budget -= len(block["txHashes"])
            output["blocks"] = list(self.blocks.values())
            output["coverage"] = {"complete": not first and not deep_change and not head_changed and end == height,
                                  "from": min(self.blocks), "to": end}
            if end != height:
                output["errors"].append("catching_up")
            if not entry:
                output["errors"].append("entrypoint_unknown")
        except Exception:
            output["errors"].append("rpc_or_header_unavailable")
        finally:
            output["collectionMs"] = round((time.monotonic() - began) * 1000, 2)
            output["rpcFailures"] = self.rpc_failures - failures_before
        return output
