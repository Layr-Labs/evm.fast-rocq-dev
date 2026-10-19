"""Timed score encoding and record lookup for scripts/time_score.py, with Yukon's API mocked."""
from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import time_score  # noqa: E402

K = 41_250_000
# 23,037 s rounds up to 384 minutes.
RECORD = {"currentBestScore": K * 10_000 + 384, "currentBestMetrics": {"claim": K, "verifySeconds": 23_037.0}}
SOURCE = "https://github.com/Example/prover-dev"


def listing(*benchmarks: dict) -> dict:
    return {"benchmarks": [{"id": "b1", "sourceUrl": SOURCE, "trackName": "main", "status": "open"}, *benchmarks]}


class TimedScoreTest(unittest.TestCase):
    def test_a_new_k_encodes_its_own_minutes(self):
        self.assertEqual(time_score.timed_score(K - 1, 23_037.0, RECORD), (K - 1) * 10_000 + 384)
        self.assertEqual(time_score.timed_score(K - 1, 60.0, {"currentBestScore": 2**53 - 1,
                                                              "currentBestMetrics": {"claim": "infinity"}}),
                         (K - 1) * 10_000 + 1)

    def test_a_smaller_k_always_beats_a_faster_larger_k(self):
        self.assertLess(time_score.timed_score(K - 1, 1_000_000.0, None), time_score.encode(K, 0))

    def test_equal_k_needs_a_five_percent_speedup(self):
        self.assertEqual(time_score.timed_score(K, 23_037.0 * 0.95, RECORD), K * 10_000 + 365)
        self.assertLess(time_score.timed_score(K, 23_037.0 * 0.95, RECORD), RECORD["currentBestScore"])
        self.assertEqual(time_score.timed_score(K, 23_037.0 * 0.96, RECORD), RECORD["currentBestScore"])
        self.assertEqual(time_score.timed_score(K, 30_000.0, RECORD), RECORD["currentBestScore"])

    def test_equal_k_ties_when_the_record_has_no_time(self):
        record = {"currentBestScore": K * 10_000 + 9_999, "currentBestMetrics": {"claim": K}}
        self.assertEqual(time_score.timed_score(K, 1.0, record), record["currentBestScore"])

    def test_an_unread_record_gets_the_slowest_digits(self):
        self.assertEqual(time_score.timed_score(K, 1.0, None), K * 10_000 + 9_999)

    def test_minutes_are_capped(self):
        self.assertEqual(time_score.timed_score(K, 10**9, RECORD | {"currentBestMetrics": {}}), K * 10_000 + 9_999)

    def test_k_from_1e11_carries_no_time_so_it_reads_back_exactly(self):
        k = 2 * 10**11 + 7
        self.assertEqual(time_score.encode(k, 9_999), k * 10_000)
        self.assertEqual(int(float(f"{time_score.encode(k, 9_999):.15g}")) // 10_000, k)
        self.assertEqual(time_score.encode(10**11 - 1, 9_999), (10**11 - 1) * 10_000 + 9_999)
        self.assertEqual(int(float(f"{time_score.encode(10**11 - 1, 9_999):.15g}")), (10**11 - 1) * 10_000 + 9_999)
        record = {"currentBestScore": k * 10_000, "currentBestMetrics": {"claim": k, "verifySeconds": 20_000.0}}
        self.assertEqual(time_score.timed_score(k, 100.0, record), record["currentBestScore"])


class CurrentRecordTest(unittest.TestCase):
    def lookup(self, responses: list):
        with patch.object(time_score, "get_json", side_effect=responses) as get:
            record = time_score.current_record("https://api", "example/Prover-Dev", "main")
        return record, [call.args[0] for call in get.call_args_list]

    def test_reads_the_one_open_benchmark_for_this_repository_and_track(self):
        others = [{"id": "x1", "sourceUrl": SOURCE, "trackName": "other", "status": "open"},
                  {"id": "x2", "sourceUrl": SOURCE, "trackName": "main", "status": "paused"},
                  {"id": "x3", "sourceUrl": SOURCE + "-other", "trackName": "main", "status": "open"}]
        record, urls = self.lookup([listing(*others), {"benchmark": RECORD}])
        self.assertEqual(record, RECORD)
        self.assertEqual(urls, ["https://api/api/benchmarks", "https://api/api/benchmarks/b1"])

    def test_matches_a_git_suffix(self):
        record, _ = self.lookup([{"benchmarks": [{"id": "b1", "sourceUrl": SOURCE + ".git", "trackName": "main",
                                                  "status": "open"}]}, {"benchmark": RECORD}])
        self.assertEqual(record, RECORD)

    def test_no_single_match_or_an_api_failure_gives_none(self):
        self.assertIsNone(self.lookup([{"benchmarks": []}])[0])
        self.assertIsNone(self.lookup([listing({"id": "b2", "sourceUrl": SOURCE, "trackName": "main",
                                                "status": "open"})])[0])
        self.assertIsNone(self.lookup([OSError("unreachable")])[0])
        self.assertIsNone(self.lookup([{"error": "bad gateway"}])[0])
        self.assertIsNone(self.lookup([http.client.IncompleteRead(b"")])[0])

    def test_get_json_retries_truncated_and_malformed_responses(self):
        responses = [http.client.IncompleteRead(b""), io.BytesIO(b"<html>proxy</html>"), io.BytesIO(b'{"ok": 1}')]
        with patch("urllib.request.urlopen", side_effect=responses), patch("time.sleep") as sleep:
            self.assertEqual(time_score.get_json("https://api/x"), {"ok": 1})
        self.assertEqual(sleep.call_count, 2)
        with patch("urllib.request.urlopen", side_effect=OSError("down")), patch("time.sleep"):
            with self.assertRaises(OSError):
                time_score.get_json("https://api/x")


class MainTest(unittest.TestCase):
    def run_main(self, metrics: dict, record: dict | None, written: int | None = None) -> tuple[dict, list]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "benchmark.json").write_text(json.dumps({"tracks": [{"name": "main"}]}))
            score = root / "scripts" / "score.json"
            score.write_text(json.dumps({"score": metrics["claim"] if written is None else written, "metrics": metrics}))
            env = {"GITHUB_REPOSITORY": "Example/prover-dev", "YUKON_API_URL": "https://api-dev"}
            with patch.object(time_score, "ROOT", root), patch.object(time_score, "SCORE", score), \
                    patch.dict("os.environ", env), \
                    patch.object(time_score, "current_record", return_value=record) as lookup:
                time_score.main()
            return json.loads(score.read_text()), lookup.call_args_list

    def test_rewrites_the_score_and_keeps_the_metrics(self):
        metrics = {"claim": K - 5, "finite": True, "verifySeconds": 600.0}
        result, calls = self.run_main(metrics, RECORD)
        self.assertEqual(result, {"score": (K - 5) * 10_000 + 10, "metrics": metrics})
        self.assertEqual([call.args for call in calls], [("https://api-dev", "Example/prover-dev", "main")])

    def test_the_largest_k_reads_back_finite(self):
        self.assertEqual(time_score.LARGEST_K, 900_719_925_474)
        largest = time_score.encode(time_score.LARGEST_K, 9_999)
        self.assertLess(float(f"{largest:.15g}"), 9_007_199_254_740_990)
        self.assertGreaterEqual(float(f"{time_score.encode(time_score.LARGEST_K + 1, 0):.15g}"), 9_007_199_254_740_990)

    def test_infinity_skips_the_lookup_and_keeps_run_py_score(self):
        metrics = {"claim": "infinity", "finite": False, "verifySeconds": 1.0}
        result, calls = self.run_main(metrics, None, written=2**53 - 1)
        self.assertEqual(result, {"score": 2**53 - 1, "metrics": metrics})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
