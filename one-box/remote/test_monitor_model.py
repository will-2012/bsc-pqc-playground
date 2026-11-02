"""Deterministic monitor policy tests; no chain or SSM required."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor_model import MonitorModel, block_milliseconds


def block(number, ms=None, gas=50, **extra):
    return {"number": number, "hash": f"h{number}", "parentHash": f"h{number-1}",
            "timestampMs": number * 450 if ms is None else ms,
            "gasUsed": gas, "gasLimit": 100, "txCount": 2, "userOps": 3,
            "outOfTurn": False, "slashEvents": 0, **extra}


def snapshot(height, start=None, gas=50, **extra):
    start = height if start is None else start
    final = max(0, height - 2)
    safe = max(0, height - 1)
    return {"head": block(height), "safe": {"number": safe, "hash": f"h{safe}"},
            "finalized": {"number": final, "hash": f"h{final}"},
            "blocks": [block(i, gas=gas) for i in range(start, height + 1)],
            "coverage": {"complete": True}, **extra}


def bench(run="r1", accepted=0, confirmed=0, action="transfer", started=1):
    return {"dataset": "d1", "run": run, "progress": {
        "action": action, "startedAt": started, "updatedAt": accepted + confirmed + 1, "status": "running", "accepted": accepted,
        "confirmed": confirmed, "failed": 0, "targetQps": 100}}


class MonitorModelTests(unittest.TestCase):
    def model(self, **options):
        return MonitorModel({"warmup": 0, **options})

    def test_window_uses_highest_complete_node_like_receipt_comparison(self):
        model = self.model()
        model.observe({'a': snapshot(0), 'b': snapshot(0)}, 0)
        out = model.observe({'a': snapshot(20, 1), 'b': snapshot(22, 1)}, 10)
        self.assertEqual(out['window']['host'], 'b')
        self.assertEqual(out['window']['blocks'], 22)

    def test_millisecond_precision_not_seconds_quantization(self):
        self.assertEqual(block_milliseconds({"timestamp": "0x64", "mixHash": "0x1c2"}), 100450)
        with self.assertRaises(ValueError):
            block_milliseconds({"timestamp": 100, "mixHash": 1000})
        model = self.model()
        model.observe({"a": snapshot(1)}, 0)
        result = model.observe({"a": snapshot(23, 1)}, 10)
        self.assertEqual(result["window"]["intervalP95Ms"], 450)
        self.assertEqual(result["window"]["intervalP50Ms"], 450)
        self.assertEqual(result["window"]["blocks"], 22)
        json.dumps(result, allow_nan=False)

    def test_stable_full_blocks_are_saturation_not_stop(self):
        model = self.model()
        model.observe({"a": snapshot(0)}, -10)
        for index in range(5):
            result = model.observe({"a": snapshot(20 + index * 20, 1 + index * 20, gas=100)}, index * 10)
            self.assertEqual(result["status"], "HIGH_UTILIZATION")
            self.assertFalse(result["stop"])

    def test_replicas_are_not_counted_as_extra_blocks(self):
        sample = snapshot(20, 1)
        model = self.model()
        model.observe({h: snapshot(0) for h in ("a", "b", "c")}, 0)
        result = model.observe({"b": sample, "a": sample, "c": sample}, 10)
        self.assertEqual(result["window"]["host"], "c")
        self.assertEqual(result["window"]["blocks"], 20)
        self.assertEqual(result["window"]["transactions"], 40)
        self.assertEqual(result["window"]["userOps"], 60)

    def test_repeated_blocks_do_not_inflate_window(self):
        model = self.model()
        model.observe({"a": snapshot(10, 1)}, 0)
        result = model.observe({"a": snapshot(12, 9)}, 10)
        self.assertEqual(result["window"]["blocks"], 2)
        self.assertEqual(result["window"]["intervalCount"], 2)

    def test_warmup_and_consecutive_failures(self):
        model = MonitorModel({"warmup": 20, "consecutive": 3})
        for t in (0, 10, 20, 30):
            result = model.observe({"a": {"error": "timeout"}}, t)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertFalse(result["stop"])
        result = model.observe({"a": {"error": "timeout"}}, 40)
        self.assertTrue(result["stop"])
        self.assertEqual(result["stopReasons"], ["observation_unavailable"])

    def test_transient_unknown_does_not_accumulate_across_good_sample(self):
        model = self.model(consecutive=2)
        model.observe({"a": {"error": "timeout"}}, 0)
        model.observe({"a": snapshot(20, 1)}, 10)
        result = model.observe({"a": {"error": "timeout"}}, 20)
        self.assertFalse(result["stop"])

    def test_stalled_chain_requires_duration_and_repeated_windows(self):
        model = self.model(head_stall_seconds=20, finality_stall_seconds=20, consecutive=2)
        model.observe({"a": snapshot(20, 1)}, 0)
        self.assertFalse(model.observe({"a": snapshot(20, 20)}, 10)["stop"])
        self.assertFalse(model.observe({"a": snapshot(20, 20)}, 20)["stop"])
        result = model.observe({"a": snapshot(20, 20)}, 30)
        self.assertTrue(result["stop"])
        self.assertIn("a:head_stalled", result["stopReasons"])
        self.assertIn("a:finality_stalled", result["stopReasons"])

    def test_finality_can_stall_while_head_advances(self):
        model = self.model(finality_stall_seconds=10, finality_lag_blocks=1000, consecutive=2)
        for index in range(3):
            s = snapshot(20 + index * 20, 1 + index * 20)
            s["finalized"] = {"number": 18, "hash": "h18"}
            result = model.observe({"a": s}, index * 10)
        self.assertIn("a:finality_stalled", result["stopReasons"])
        self.assertNotIn("a:head_stalled", result["reasons"])

    def test_finalized_conflict_is_immediate_even_during_warmup(self):
        model = MonitorModel()
        left, right = snapshot(20, 1), snapshot(20, 1)
        right["finalized"]["hash"] = "conflict"
        result = model.observe({"a": left, "b": right}, 0)
        self.assertTrue(result["stop"])
        self.assertIn("finalized_hash_conflict", result["stopReasons"])
        self.assertTrue(model.observe({"a": snapshot(21, 21)}, 10)["stop"])

    def test_tip_difference_does_not_imply_finalized_conflict(self):
        result = self.model().observe({"a": snapshot(20, 1), "b": snapshot(21, 1)}, 0)
        self.assertFalse(result["stop"])
        self.assertNotIn("finalized_hash_conflict", result["reasons"])

    def test_missing_finality_is_unknown_not_zero(self):
        result = self.model().observe({"a": snapshot(20, 1, finalized=None)}, 0)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["window"])
        self.assertFalse(result["hosts"]["a"]["available"])

    def test_gap_is_unknown_not_zero_or_measured_window(self):
        model = self.model()
        model.observe({"a": snapshot(10, 1)}, 0)
        result = model.observe({"a": snapshot(20, 15)}, 10)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["window"])
        sample = snapshot(21, 21, coverage={"complete": False})
        result = model.observe({"a": sample}, 20)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_no_new_blocks_is_zero_count_but_not_zero_gas_ratio(self):
        model = self.model()
        model.observe({"a": snapshot(10, 1)}, 0)
        result = model.observe({"a": snapshot(10, 11)}, 10)
        self.assertEqual(result["window"]["blocks"], 0)
        self.assertIsNone(result["window"]["gasRatio"])
        self.assertIsNone(result["window"]["intervalP95Ms"])

    def test_backoff_proxy_is_counted_but_not_automatic_failure(self):
        sample = snapshot(3, 1)
        sample["blocks"][1]["outOfTurn"] = True
        model = self.model()
        model.observe({"a": snapshot(0)}, 0)
        result = model.observe({"a": sample}, 10)
        self.assertEqual(result["window"]["outOfTurn"], 1)
        self.assertFalse(result["stop"])

    def test_slash_event_is_immediate(self):
        sample = snapshot(3, 1)
        sample["blocks"][1]["slashEvents"] = 1
        model = MonitorModel()
        model.observe({"a": snapshot(0)}, 0)
        result = model.observe({"a": sample}, 10)
        self.assertTrue(result["stop"])
        self.assertIn("slash_event", result["stopReasons"])

    def test_rates_use_monotonic_window_not_cumulative_average(self):
        model = self.model()
        self.assertIsNone(model.observe({"a": snapshot(20, 1)}, 0, {"a": bench(accepted=100, confirmed=90)})["bench"]["acceptedQps"])
        result = model.observe({"a": snapshot(40, 21)}, 10, {"a": bench(accepted=130, confirmed=110)})
        self.assertEqual(result["bench"]["acceptedQps"], 3)
        self.assertEqual(result["bench"]["confirmedQps"], 2)
        self.assertEqual(result["bench"]["delta"]["confirmed"], 20)

    def test_run_switch_counter_reset_and_mixed_runs_do_not_make_rates(self):
        model = self.model()
        model.observe({"a": snapshot(20, 1)}, 0, {"a": bench(accepted=100, confirmed=90)})
        result = model.observe({"a": snapshot(40, 21)}, 10, {"a": bench(run="new", accepted=10, confirmed=5)})
        self.assertIsNone(result["bench"]["acceptedQps"])
        result = model.observe({"a": snapshot(60, 41)}, 20, {"a": bench(run="new", accepted=1, confirmed=0)})
        self.assertIsNone(result["bench"]["acceptedQps"])
        model.observe({"a": snapshot(80, 61)}, 30, {"a": bench(run="new", accepted=2), "b": bench(run="other", accepted=2)})
        result = model.observe({"a": snapshot(100, 81)}, 40, {"a": bench(run="new", accepted=4), "b": bench(run="other", accepted=4)})
        self.assertFalse(result["bench"]["complete"])
        self.assertIsNone(result["bench"]["acceptedQps"])

    def test_prepare_count_is_not_transaction_rate(self):
        model = self.model()
        model.observe({"a": snapshot(20, 1)}, 0, {"a": bench(action="prepare")})
        result = model.observe({"a": snapshot(40, 21)}, 10, {"a": bench(action="prepare", confirmed=100)})
        self.assertEqual(result["bench"]["delta"]["confirmed"], 100)
        self.assertIsNone(result["bench"]["confirmedQps"])

    def test_non_monotonic_controller_time_rejected(self):
        model = self.model()
        model.observe({"a": snapshot(20, 1)}, 5)
        with self.assertRaises(ValueError):
            model.observe({"a": snapshot(20, 1)}, 5)

    def test_missing_host_is_unknown_not_healthy_subset(self):
        model = self.model()
        model.observe({"a": snapshot(20, 1), "b": snapshot(20, 1)}, 0)
        result = model.observe({"a": snapshot(40, 21)}, 10)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["hosts"]["b"]["available"])

    def test_history_is_baseline_not_new_slash_or_load(self):
        sample = snapshot(20, 1)
        sample["blocks"][2]["slashEvents"] = 1
        result = self.model().observe({"a": sample}, 0)
        self.assertFalse(result["stop"])
        self.assertTrue(result["window"]["baseline"])
        self.assertEqual(result["window"]["blocks"], 0)

    def test_unknown_optional_events_do_not_destroy_block_measurements(self):
        model = self.model()
        model.observe({"a": snapshot(0)}, 0)
        sample = snapshot(20, 1)
        sample["blocks"][0].update(userOps=None, slashEvents=None, outOfTurn=None)
        result = model.observe({"a": sample}, 10)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["stop"])
        self.assertEqual(result["window"]["transactions"], 40)
        for key in ("userOps", "slashEvents", "outOfTurn"):
            self.assertIsNone(result["window"][key])

    def test_persistent_missing_slash_logs_requests_stop(self):
        model = self.model()
        model.observe({"a": snapshot(0)}, 0)
        for index in range(1, 4):
            sample = snapshot(index * 20, (index - 1) * 20 + 1)
            sample["blocks"][0]["slashEvents"] = None
            result = model.observe({"a": sample}, index * 10)
            self.assertEqual(result["stop"], index == 3)
        self.assertIn("observation_unavailable", result["stopReasons"])

    def test_repeated_bench_sample_does_not_reset_rate_baseline(self):
        model = self.model()
        first = bench(accepted=100, confirmed=90)
        first["sequence"] = 1
        model.observe({"a": snapshot(0)}, 0, {"a": first})
        repeat = model.observe({"a": snapshot(20, 1)}, 10, {"a": first})
        self.assertIsNone(repeat["bench"]["acceptedQps"])
        last = bench(accepted=140, confirmed=130)
        last["sequence"] = 2
        result = model.observe({"a": snapshot(40, 21)}, 20, {"a": last})
        self.assertEqual(result["bench"]["acceptedQps"], 2)
        self.assertEqual(result["bench"]["confirmedQps"], 2)

    def test_terminal_bench_progress_can_remain_frozen(self):
        model = self.model(consecutive=1)
        first = bench(accepted=100, confirmed=100)
        first["progress"]["status"] = "complete"
        model.observe({"a": snapshot(0)}, 0, {"a": first})
        result = model.observe({"a": snapshot(200, 1)}, 90, {"a": first})
        self.assertFalse(result["stop"])
        self.assertIsNone(result["bench"]["acceptedQps"])
        self.assertEqual(result["bench"]["unavailableHosts"], [])

    def test_live_stale_bench_and_dedicated_bench_errors_are_unknown(self):
        model = self.model(consecutive=1, bench_stale_seconds=10)
        first = bench(accepted=100, confirmed=90)
        model.observe({"a": snapshot(0)}, 0, {"bench-only": first})
        result = model.observe({"a": snapshot(20, 1)}, 10, {"bench-only": first})
        self.assertTrue(result["stop"])
        self.assertEqual(result["status"], "UNKNOWN")
        model = self.model(consecutive=2)
        model.observe({"a": snapshot(0)}, 0, {"bench-only": {"error": "timeout"}})
        result = model.observe({"a": snapshot(20, 1)}, 10, {"bench-only": {"error": "timeout"}})
        self.assertTrue(result["stop"])
        self.assertIn("bench-only:bench_observation_unavailable", result["reasons"])

    def test_backlog_is_hint_not_bottleneck_verdict_or_stop(self):
        model = self.model()
        model.observe({"a": snapshot(0)}, 0, {"a": bench(accepted=10, confirmed=10)})
        result = model.observe({"a": snapshot(20, 1)}, 10, {"a": bench(accepted=50, confirmed=20)})
        self.assertIn("BENCH_BACKLOG", result["reasons"])
        self.assertFalse(result["stop"])
        self.assertEqual(result["status"], "HEALTHY")

    def test_idle_bench_without_workload_is_not_missing_observation(self):
        result = self.model(consecutive=1).observe({"a": snapshot(0)}, 0,
            {"b": {"service": "inactive", "progress": {}}})
        self.assertFalse(result["stop"])

    def test_filtered_other_run_and_terminal_without_counters_are_idle(self):
        for row in ({"idle": True}, {"progress": {"status": "complete"}}, {"service": "inactive"}):
            result = self.model(consecutive=1).observe({"a": snapshot(0)}, 0, {"b": row})
            self.assertFalse(result["stop"])
            self.assertEqual(result["bench"]["unavailableHosts"], [])

    def test_threshold_validation(self):
        for opts in ({"typo": 30}, {"consecutive": 0}, {"consecutive": 1.5}, {"warmup": -1}, {"interval": float("nan")}, {"saturation_gas_ratio": 2}):
            with self.assertRaises(ValueError):
                MonitorModel(opts)


if __name__ == "__main__":
    unittest.main()
