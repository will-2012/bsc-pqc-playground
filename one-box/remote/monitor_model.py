"""Pure load-monitor analysis. No RPC, filesystem, service control or wall-clock access.

Input is a host-alias mapping of collector snapshots. ``now`` is controller
monotonic seconds. Missing/failed observations never stand in for zero load.
"""
from collections import OrderedDict
import math


DEFAULTS = {
    "interval": 10.0,
    "warmup": 30.0,
    "txpool_growth_seconds": 60.0,
    "txpool_growth_min": 100,
    "consecutive": 3,
    "bench_stale_seconds": 30.0,
    "expected_block_ms": 450.0,
    "head_stall_seconds": 30.0,
    "finality_stall_seconds": 30.0,
    "safe_stall_seconds": 30.0,
    "finality_lag_blocks": 64,
    "safe_lag_blocks": 64,
    "node_lag_blocks": 32,
    "saturation_gas_ratio": 0.90,
    "slow_block_factor": 3.0,
    "slow_block_fraction": 0.20,
}


def integer(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a counter")
    if isinstance(value, str):
        return int(value, 16 if value.startswith("0x") else 10)
    if isinstance(value, int):
        return value
    raise ValueError("integer required")


def block_milliseconds(block):
    """BSC subsecond headers store the 0..999 ms remainder in mixHash."""
    if "timestampMs" in block:
        return integer(block["timestampMs"])
    seconds = integer(block["timestamp"])
    remainder = integer(block["mixHash"])
    if not 0 <= remainder <= 999:
        raise ValueError("invalid millisecond remainder")
    return seconds * 1000 + remainder


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def block_window(blocks, previous, config):
    """Return one node's new-block window, never sum replicas as extra blocks."""
    seen = set()
    new = []
    for block in blocks:
        number = integer(block["number"])
        key = (number, block["hash"])
        if key in seen:
            continue
        seen.add(key)
        if previous and number <= integer(previous["number"]):
            continue
        new.append(block)
    new.sort(key=lambda block: integer(block["number"]))
    intervals = []
    continuity = True
    parent = previous
    for block in new:
        if parent:
            if (integer(block["number"]) != integer(parent["number"]) + 1
                    or block["parentHash"] != parent["hash"]):
                continuity = False
            else:
                elapsed = block_milliseconds(block) - block_milliseconds(parent)
                if elapsed <= 0:
                    continuity = False
                else:
                    intervals.append(elapsed)
        parent = block
    gas_used = sum(integer(block["gasUsed"]) for block in new)
    gas_limit = sum(integer(block["gasLimit"]) for block in new)
    if any(integer(b["gasUsed"]) < 0 or integer(b["gasLimit"]) <= 0
           or integer(b["gasUsed"]) > integer(b["gasLimit"]) for b in new):
        raise ValueError("invalid gas counters")
    def total(field):
        return sum(integer(b[field]) for b in new) if all(b.get(field) is not None for b in new) else None
    out_of_turn = sum(b["outOfTurn"] for b in new) if all(isinstance(b.get("outOfTurn"), bool) for b in new) else None
    slow = sum(ms > config["expected_block_ms"] * config["slow_block_factor"] for ms in intervals)
    return {
        "blocks": len(new), "transactions": total("txCount"), "userOps": total("userOps"),
        "slashEvents": total("slashEvents"), "outOfTurn": out_of_turn,
        "gasUsed": gas_used, "gasLimit": gas_limit,
        "gasRatio": gas_used / gas_limit if gas_limit else None,
        "intervalCount": len(intervals),
        "intervalMeanMs": sum(intervals) / len(intervals) if intervals else None,
        "intervalP50Ms": percentile(intervals, .50),
        "intervalP95Ms": percentile(intervals, .95),
        "intervalMaxMs": max(intervals) if intervals else None,
        "slowBlockFraction": slow / len(intervals) if intervals else None,
        "complete": continuity,
    }, parent


class MonitorModel:
    """Thresholds are experiment policy, not BSC consensus rules.

    Snapshot: head={number,hash,parentHash,timestampMs}, safe/finalized={number,hash},
    blocks=[{number,hash,parentHash,timestampMs,gasUsed,gasLimit,txCount,
    outOfTurn,userOps,slashEvents}], coverage={complete: bool}, error?: str.
    Optional bench={dataset,run,progress:{action,startedAt,accepted,confirmed,...}}.
    """
    def __init__(self, config=None):
        if config is not None and (not isinstance(config, dict) or set(config) - set(DEFAULTS)):
            raise ValueError("unknown monitor policy key or invalid policy")
        self.config = {**DEFAULTS, **(config or {})}
        for key in DEFAULTS:
            value = self.config[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError("invalid monitor threshold: " + key)
            if value < 0 or (key != "warmup" and value == 0):
                raise ValueError("invalid monitor threshold: " + key)
        if self.config["consecutive"] != int(self.config["consecutive"]):
            raise ValueError("consecutive must be an integer")
        if self.config["saturation_gas_ratio"] > 1 or self.config["slow_block_fraction"] > 1:
            raise ValueError("ratio must not exceed one")
        self.started = None
        self.last_now = None
        self.hosts = {}
        self.finalized_hashes = OrderedDict()
        self.streaks = {}
        self.stop_reasons = set()
        self.previous_benches = {}

    def _bench(self, benches, now):
        rows, identities, unavailable = {}, set(), []
        totals = {"accepted": 0, "confirmed": 0, "failed": 0}
        rates = {"acceptedQps": 0.0, "confirmedQps": 0.0}
        complete, target, backlog_growth = bool(benches), 0.0, False
        for host, row in benches.items():
            progress = row.get("progress") or {}
            identity = (row.get("dataset"), row.get("run"), progress.get("action"))
            identities.add(identity)
            try:
                if row.get("error"):
                    raise ValueError("collector_error")
                terminal = progress.get("status") in ("complete", "stopped", "failed")
                missing = any(progress.get(k) is None for k in (*totals, "updatedAt"))
                if row.get("idle") or (missing and (terminal or row.get("service") in ("inactive", "failed"))):
                    rows[host] = {"complete": False, "reason": "no_active_workload"}
                    self.previous_benches.pop(host, None)
                    complete = False
                    continue
                if any(v is None for v in identity):
                    if row.get("service") in ("inactive", "failed") and not progress:
                        rows[host] = {"complete": False, "reason": "no_workload"}
                        complete = False
                        continue
                    raise ValueError("missing workload identity")
                counters = {key: integer(progress[key]) for key in totals}
                updated = integer(progress["updatedAt"])
                if any(v < 0 for v in counters.values()):
                    raise ValueError("negative counter")
                qps = float(progress.get("targetQps", 0))
                if not math.isfinite(qps) or qps < 0:
                    raise ValueError("invalid target QPS")
                previous = self.previous_benches.get(host)
                current = {"identity": identity, "startedAt": progress.get("startedAt"),
                           "counters": counters, "now": now, "updatedAt": updated,
                           "sequence": row.get("sequence")}
                same = previous and previous["identity"] == identity and previous["startedAt"] == current["startedAt"]
                if same and (updated <= previous["updatedAt"] or
                             (current["sequence"] is not None and previous["sequence"] is not None
                              and current["sequence"] <= previous["sequence"])):
                    # Frozen terminal progress is expected. A repeated active sample
                    # is not a fresh zero-QPS observation and must not move baseline.
                    terminal = progress.get("status") in ("complete", "stopped", "failed")
                    stale = not terminal and now - previous["now"] >= self.config["bench_stale_seconds"]
                    rows[host] = {"complete": False, "reason": "terminal" if terminal else "repeated_sample", "stale": stale}
                    if stale:
                        unavailable.append(host)
                    complete = False
                    continue
                self.previous_benches[host] = current
                delta = {key: counters[key] - previous["counters"][key] for key in totals} if same else {}
                if not same or any(v < 0 for v in delta.values()):
                    complete = False
                    rows[host] = {"complete": False, "reason": "baseline_or_counter_reset"}
                    continue
                seconds = now - previous["now"]
                rows[host] = {"complete": True, **delta, "seconds": seconds,
                              "acceptedQps": delta["accepted"] / seconds,
                              "confirmedQps": delta["confirmed"] / seconds,
                              "backlogProxy": max(0, counters["accepted"] - counters["confirmed"])}
                backlog_growth |= identity[2] != "prepare" and delta["accepted"] > delta["confirmed"]
                for key in totals:
                    totals[key] += delta[key]
                for key in rates:
                    rates[key] += rows[host][key]
                target += qps
            except (KeyError, ValueError, TypeError):
                complete = False
                unavailable.append(host)
                self.previous_benches.pop(host, None)
                rows[host] = {"complete": False, "reason": "missing_or_invalid_observation"}
        # Missing configured collectors must be passed as {error: ...}. If a host
        # vanishes from an explicit bench set, still report that loss this window.
        for host in set(self.previous_benches) - set(benches):
            unavailable.append(host)
            del self.previous_benches[host]
        same_run = len(identities) == 1 and all(None not in identity for identity in identities)
        complete = complete and same_run
        action = next(iter(identities))[2] if same_run else None
        return {"complete": complete, "hosts": rows, "action": action, "unavailableHosts": unavailable,
                "hints": ["BENCH_BACKLOG"] if complete and backlog_growth else [],
                "delta": totals if complete else None,
                "acceptedQps": rates["acceptedQps"] if complete and action != "prepare" else None,
                "confirmedQps": rates["confirmedQps"] if complete and action != "prepare" else None,
                "targetQps": target if complete else None,
                "note": "receipt confirmations are not finality; backlog proxy is accepted minus successful receipts, not exact mempool size"}

    def _pool(self, sample, state, now):
        pool = sample.get("txpool")
        if (sample.get("error") or not isinstance(pool, dict) or any(
                type(pool.get(k)) is not int or pool[k] < 0 for k in ("pending", "queued"))):
            state.pop("pool", None)
            return {"available": False}
        total = pool["pending"] + pool["queued"]
        previous = state.get("pool")
        if previous and sample.get("sampledAt") is not None and previous.get("sampledAt") == sample["sampledAt"]:
            return {"available": False}
        delta = total - previous["total"] if previous else None
        continuous = previous and 0 < now - previous["time"] <= self.config["interval"] * 2
        if continuous and delta > 0:
            since = previous["since"] if previous["since"] is not None else previous["time"]
            baseline = previous["baseline"] if previous["since"] is not None else previous["total"]
        else:
            since, baseline = None, total
        seconds = now - since if since is not None else 0
        growth = total - baseline
        result = {"available": True, **pool, "total": total, "delta": delta,
                  "growthSeconds": seconds, "growth": growth,
                  "sustained": seconds >= self.config["txpool_growth_seconds"]
                               and growth >= self.config["txpool_growth_min"]}
        state["pool"] = {"total": total, "time": now, "since": since, "baseline": baseline,
                         "sampledAt": sample.get("sampledAt"), "result": result}
        return result

    def observe(self, samples, now, benches=None):
        if not isinstance(now, (int, float)) or not math.isfinite(now) or (self.last_now is not None and now <= self.last_now):
            raise ValueError("now must increase monotonically")
        if self.started is None:
            self.started = now
        self.last_now = now
        warmup = now - self.started < self.config["warmup"]
        faults, reasons, immediate = set(), set(), set()
        host_rows, windows, valid_heights, pools = {}, {}, {}, {}
        if not samples or set(self.hosts) - set(samples):
            faults.add("observation_unavailable")
            for host in set(self.hosts) - set(samples):
                reasons.add(host + ":observation_unavailable")
                host_rows[host] = {"available": False, "reason": "missing_host"}
                self.hosts[host].pop("pool", None)
        for host, sample in samples.items():
            state = self.hosts.setdefault(host, {})
            pool = self._pool(sample, state, now)
            pools[host] = pool
            if pool.get("sustained"):
                faults.add(host + ":txpool_growing")
            try:
                if sample.get("error"):
                    raise ValueError("collector_error")
                head = sample["head"]
                height = integer(head["number"])
                if height < 0 or not head.get("hash"):
                    raise ValueError("invalid_head")
                final = sample.get("finalized")
                safe = sample.get("safe")
                if final is None or safe is None:
                    raise ValueError("finality_unavailable")
                final_number, safe_number = integer(final["number"]), integer(safe["number"])
                if not final.get("hash") or not safe.get("hash"):
                    raise ValueError("finality_hash_unavailable")
                if not 0 <= final_number <= safe_number <= height:
                    raise ValueError("inconsistent_snapshot")
                # Same finalized height with a different hash is never a tip race.
                known = self.finalized_hashes.get(final_number)
                if known is not None and known != final["hash"]:
                    immediate.add("finalized_hash_conflict")
                self.finalized_hashes[final_number] = final["hash"]
                if len(self.finalized_hashes) > 4096:
                    self.finalized_hashes.popitem(last=False)
                if state.get("height") != height or state.get("head_hash") != head["hash"]:
                    if "height" in state and height <= state["height"]:
                        reasons.add(host + ":head_reorg_observed")
                    if "height" not in state or height > state["height"]:
                        state["head_progress"] = now
                if "finalized" not in state or final_number > state["finalized"]:
                    state["final_progress"] = now
                if "safe" not in state or safe_number > state["safe"]:
                    state["safe_progress"] = now
                if "finalized" in state and final_number < state["finalized"]:
                    faults.add(host + ":finality_regressed")
                state.update(height=height, head_hash=head["hash"], finalized=final_number, safe=safe_number)
                head_age = now - state["head_progress"]
                final_age = now - state["final_progress"]
                if head_age >= self.config["head_stall_seconds"]:
                    faults.add(host + ":head_stalled")
                if final_age >= self.config["finality_stall_seconds"]:
                    faults.add(host + ":finality_stalled")
                safe_age = now - state["safe_progress"]
                if safe_age >= self.config["safe_stall_seconds"]:
                    faults.add(host + ":safe_stalled")
                if height - safe_number > self.config["safe_lag_blocks"]:
                    faults.add(host + ":safe_lag")
                lag = height - final_number
                if lag > self.config["finality_lag_blocks"]:
                    faults.add(host + ":finality_lag")
                valid_heights[host] = height
                complete = sample.get("coverage", {}).get("complete") is True
                blocks = sample.get("blocks")
                if not isinstance(blocks, list):
                    raise ValueError("blocks_unavailable")
                baseline = state.get("last_block") is None
                # The initial ring is history, not work observed during this monitor.
                window, last = block_window([] if baseline else blocks,
                                            head if baseline else state["last_block"], self.config)
                window["baseline"] = baseline
                if last:
                    state["last_block"] = last
                complete = (complete and window["complete"] and last is not None
                            and integer(last["number"]) == height and last["hash"] == head["hash"])
                window["complete"] = complete
                if not complete:
                    faults.add("observation_unavailable")
                    reasons.add(host + ":block_coverage_gap")
                else:
                    windows[host] = window
                    if window["slowBlockFraction"] is not None and window["slowBlockFraction"] > self.config["slow_block_fraction"]:
                        faults.add(host + ":slow_blocks")
                if window["slashEvents"]:
                    immediate.add("slash_event")
                if window["blocks"] and window["slashEvents"] is None:
                    faults.add("observation_unavailable")
                    reasons.add(host + ":slash_observation_unavailable")
                host_rows[host] = {"available": True, "height": height, "safe": safe_number,
                                   "finalized": final_number, "finalizedLag": lag, "safeLag": height - safe_number,
                                   "safeStallSeconds": safe_age,
                                   "headStallSeconds": head_age, "finalityStallSeconds": final_age,
                                   "window": window}
            except (KeyError, ValueError, TypeError) as error:
                faults.add("observation_unavailable")
                reasons.add(host + ":observation_unavailable")
                host_rows[host] = {"available": False, "reason": str(error)}
                # Keep the prior cursor so a recovered overlapping ring can fill
                # the gap; missing samples are not a new history baseline.
        for host, row in host_rows.items():
            row["txpool"] = pools.get(host, {"available": False})
        if valid_heights:
            maximum = max(valid_heights.values())
            for host, height in valid_heights.items():
                host_rows[host]["nodeLag"] = maximum - height
                if maximum - height > self.config["node_lag_blocks"]:
                    faults.add(host + ":node_lag")
        bench = self._bench(benches if benches is not None else {h: s["bench"] for h, s in samples.items() if s.get("bench")}, now)
        if bench["unavailableHosts"]:
            faults.add("observation_unavailable")
            reasons.update(host + ":bench_observation_unavailable" for host in bench["unavailableHosts"])
        reasons.update(bench["hints"])
        for key in set(self.streaks) | faults:
            self.streaks[key] = self.streaks.get(key, 0) + 1 if key in faults and not warmup else 0
        self.stop_reasons.update(immediate)
        self.stop_reasons.update(key for key, count in self.streaks.items() if count >= self.config["consecutive"])
        reasons.update(faults)
        reasons.update(immediate)
        reference = max(windows, key=lambda host: (valid_heights[host], host), default=None)
        window = {"host": reference, **windows[reference]} if reference is not None else None
        if self.stop_reasons:
            status = "UNKNOWN" if self.stop_reasons == {"observation_unavailable"} else "UNSTABLE"
        elif "observation_unavailable" in faults:
            status = "UNKNOWN"
        elif warmup:
            status = "WARMUP"
        elif faults:
            status = "UNSTABLE"
        elif window and window["gasRatio"] is not None and window["gasRatio"] >= self.config["saturation_gas_ratio"]:
            status = "HIGH_UTILIZATION"
        else:
            status = "HEALTHY"
        return {"status": status, "stop": bool(self.stop_reasons), "stopReasons": sorted(self.stop_reasons),
                "reasons": sorted(reasons), "warmup": warmup, "hosts": host_rows,
                "window": window, "bench": bench, "streaks": dict(self.streaks)}
