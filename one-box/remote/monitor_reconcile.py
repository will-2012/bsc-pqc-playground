"""Bounded receipt-to-block evidence matching, not a claim of transaction loss.

Inputs use collector snapshots and Bench progress. No RPC or retained state;
replicas are never counted as independent chain traffic.
"""
import re

HASH = re.compile(r"0x[0-9a-fA-F]{64}")
MAX_BLOCKS = 128
MAX_TX_HASHES = 256


def valid_hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def reconcile(samples, benches):
    """Compare observed confirmed receipts to one node's recent canonical view.

    ``samples`` maps validator aliases to collector records. ``benches`` maps
    Bench aliases to {dataset,run,progress}. Unknown/truncated/late observations
    remain partial. UserOperation counts are sums across independent Bench
    clients, while shared outer transaction hashes are deduplicated.
    """
    candidates = []
    height_hashes = {}
    for host, sample in samples.items():
        if not isinstance(sample, dict) or sample.get("error"):
            continue
        raw = sample.get("blocks")
        if not isinstance(raw, list):
            continue
        blocks = {}
        for block in raw[-256:]:
            if not isinstance(block, dict) or not counter(block.get("number")) or not valid_hash(block.get("hash")):
                continue
            digest = block["hash"].lower()
            blocks[digest] = block
            height_hashes.setdefault(block["number"], set()).add(digest)
        if blocks:
            complete = sample.get("coverage", {}).get("complete") is True
            candidates.append((complete, max(b["number"] for b in blocks.values()), str(host), blocks))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    reference = candidates[0] if candidates else None
    chain = reference[3] if reference else {}
    reasons = set()
    if reference is None or not reference[0]:
        reasons.add("chain_coverage_incomplete")
    evidence = {}
    workloads = set()
    if not benches:
        reasons.add("bench_evidence_unavailable")
    for host, bench in benches.items():
        if not isinstance(bench, dict) or bench.get('error'):
            reasons.add("bench_evidence_unavailable")
            continue
        progress = bench.get("progress") or {}
        if not isinstance(progress, dict):
            reasons.add("bench_evidence_unavailable")
            continue
        workload = (bench.get("dataset"), bench.get("run"))
        if not all(isinstance(x, str) and x for x in workload):
            reasons.add("bench_identity_unavailable")
        else:
            workloads.add(workload)
        rows = progress.get("inclusions")
        if not isinstance(rows, list):
            reasons.add("bench_evidence_unavailable")
            continue
        if progress.get("inclusionTruncated") or len(rows) > MAX_BLOCKS:
            reasons.add("bench_window_truncated")
        seen = set()
        for row in rows[-MAX_BLOCKS:]:
            if not isinstance(row, dict) or not valid_hash(row.get("blockHash")) or not all(
                    counter(row.get(k)) for k in ("blockNumber", "userOps", "count")):
                reasons.add("invalid_bench_evidence")
                continue
            digest = row["blockHash"].lower()
            if digest in seen:
                reasons.add("duplicate_bench_block_evidence")
                continue
            seen.add(digest)
            txs = row.get("transactions")
            if not isinstance(txs, list) or not all(valid_hash(tx) for tx in txs):
                reasons.add("invalid_bench_evidence")
                continue
            if len(txs) > MAX_TX_HASHES:
                reasons.add("bench_window_truncated")
            merged = evidence.setdefault(digest, {"number": row["blockNumber"], "transactions": set(),
                                                 "userOps": 0, "count": 0})
            if merged["number"] != row["blockNumber"]:
                reasons.add("inconsistent_bench_block_number")
                continue
            merged["transactions"].update(tx.lower() for tx in txs[:MAX_TX_HASHES])
            merged["userOps"] += row["userOps"]
            merged["count"] += row["count"]
    if len(workloads) > 1:
        reasons.add("mixed_bench_runs")
    keys = set(chain) | set(evidence)
    ordered = sorted(keys, key=lambda key: ((chain.get(key) or evidence[key])["number"], key))
    if len(ordered) > MAX_BLOCKS:
        reasons.add("comparison_window_truncated")
    result = []
    for digest in ordered[-MAX_BLOCKS:]:
        block, observed = chain.get(digest), evidence.get(digest)
        row_reasons = []
        chain_txs = block.get("txCount") if block else None
        chain_ops = block.get("userOps") if block else None
        hashes = block.get("txHashes") if block else None
        hashes_known = isinstance(hashes, list) and all(valid_hash(tx) for tx in hashes)
        truncated = bool(block and block.get("txHashesTruncated"))
        transactions = observed["transactions"] if observed else set()
        known = set(tx.lower() for tx in hashes[:4096]) if hashes_known else set()
        matched = len(transactions & known) if hashes_known else None
        unmatched = len(transactions - known) if hashes_known else None
        if observed:
            if block is None:
                row_reasons.append("block_not_in_reference_window")
            elif observed["number"] != block["number"]:
                row_reasons.append("inconsistent_block_number")
            if not hashes_known or truncated:
                row_reasons.append("chain_tx_hashes_incomplete")
            if unmatched:
                row_reasons.append("receipt_hashes_not_yet_matched")
            if not counter(chain_txs) or not counter(chain_ops):
                row_reasons.append("chain_counts_unavailable")
            else:
                if len(transactions) > chain_txs or observed["userOps"] > chain_ops:
                    row_reasons.append("counts_not_yet_aligned")
            if not transactions and observed["count"]:
                row_reasons.append("receipt_tx_hashes_missing")
        result.append({"blockNumber": (block or observed)["number"], "blockHash": digest,
                       "chainTransactions": chain_txs if counter(chain_txs) else None,
                       "chainUserOps": chain_ops if counter(chain_ops) else None,
                       "benchTransactions": len(transactions),
                       "benchUserOps": observed["userOps"] if observed else 0,
                       "benchConfirmed": observed["count"] if observed else 0,
                       "matchedTransactions": matched, "unmatchedReceiptTransactions": unmatched,
                       "status": "partial" if row_reasons else "matched" if observed else "unattributed",
                       "reasons": row_reasons})
    if any(row["status"] == "partial" for row in result):
        reasons.add("receipt_alignment_partial")
    hints = [{"blockNumber": height, "hashCount": len(hashes),
              "note": "different hashes observed; may be reorg or asynchronous node views"}
             for height, hashes in sorted(height_hashes.items()) if len(hashes) > 1][-MAX_BLOCKS:]
    return {"reference": reference[2] if reference else None,
            "scope": "recent observed receipts; not full-run or finalized accounting",
            "status": "partial" if reasons else "matched_observed_receipts" if evidence else "no_receipt_evidence",
            "reasons": sorted(reasons), "blocks": result, "reorgHints": hints}
