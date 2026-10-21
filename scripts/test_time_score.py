"""Timed score encoding and record lookup for scripts/time_score.py, with Yukon's API mocked."""
from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import time_score  # noqa: E402

K = 41_250_000
# 23,037 s rounds up to 384 minutes.
RECORD = {"currentBestScore": K * 10_000 + 384, "currentBestScoreExact": str(K * 10_000 + 384),
          "currentBestMetrics": {"claim": K, "verifySeconds": 23_037.0}}
HUGE_K = 10**121 + 3
HUGE_RECORD = {"currentBestScore": 1e125, "currentBestScoreExact": str(HUGE_K * 10_000 + 384),
               "currentBestMetrics": {"claim": HUGE_K, "verifySeconds": 23_037.0}}
INFINITE_RECORD = {"currentBestScore": 1e300, "currentBestScoreExact": "1" + "0" * 300,
                   "currentBestMetrics": {"claim": "infinity"}}
SOURCE = "https://github.com/Example/prover-dev"
# Yukon's format for scoreExact: canonical decimal, at most 299 integer and 100 fraction digits.
CANONICAL = re.compile(r"-?(0|[1-9][0-9]{0,298})(\.[0-9]{0,99}[1-9])?")


def exact(value: int) -> dict:
    return {"score": value, "scoreExact": str(value)}


def listing(*benchmarks: dict) -> dict:
    return {"benchmarks": [{"id": "b1", "sourceUrl": SOURCE, "trackName": "main", "status": "open"}, *benchmarks]}


class ExactScoreTest(unittest.TestCase):
    def assertConsistent(self, scored: dict):
        self.assertEqual(scored["score"], float(scored["scoreExact"]))
        self.assertEqual(json.loads(json.dumps(scored))["score"], float(scored["scoreExact"]))

    def test_small_scores_stay_integers(self):
        self.assertEqual(time_score.exact_score(K * 10_000 + 384), exact(K * 10_000 + 384))
        self.assertEqual(time_score.exact_score(2**53), exact(2**53))
        self.assertIsInstance(time_score.exact_score(2**53)["score"], int)
        self.assertIsInstance(time_score.exact_score(2**53 + 1)["score"], float)

    def test_score_is_the_double_of_score_exact(self):
        for value in [0, 1, 2**53, 2**53 + 1, 9_007_199_254_740_993, HUGE_K * 10_000 + 61,
                      time_score.encode(time_score.LARGEST_K, 9_999)]:
            scored = time_score.exact_score(value)
            self.assertConsistent(scored)
            self.assertTrue(CANONICAL.fullmatch(scored["scoreExact"]))
            self.assertEqual(int(scored["scoreExact"]), value)
        self.assertEqual(time_score.exact_score(HUGE_K * 10_000 + 61)["score"], 1e125)

    def test_infinity_pair(self):
        self.assertEqual(time_score.INFINITY, {"score": 1e300, "scoreExact": "1" + "0" * 300})
        self.assertConsistent(time_score.INFINITY)
        self.assertEqual(int(time_score.INFINITY["scoreExact"]), 10**300)
        self.assertGreater(int(time_score.INFINITY["scoreExact"]), time_score.encode(time_score.LARGEST_K, 9_999))

    def test_the_largest_k_fills_299_integer_digits(self):
        self.assertEqual(time_score.LARGEST_K, 10**295 - 1)
        self.assertEqual(len(str(time_score.encode(time_score.LARGEST_K, 9_999))), 299)
        self.assertTrue(CANONICAL.fullmatch(str(time_score.encode(time_score.LARGEST_K, 9_999))))
        self.assertFalse(CANONICAL.fullmatch(str(time_score.encode(time_score.LARGEST_K + 1, 0))))


class TimedScoreTest(unittest.TestCase):
    def test_a_new_k_encodes_its_own_minutes(self):
        self.assertEqual(time_score.timed_score(K - 1, 23_037.0, RECORD), exact((K - 1) * 10_000 + 384))
        self.assertEqual(time_score.timed_score(K - 1, 60.0, INFINITE_RECORD), exact((K - 1) * 10_000 + 1))

    def test_a_smaller_k_always_beats_a_faster_larger_k(self):
        self.assertLess(int(time_score.timed_score(K - 1, 1_000_000.0, None)["scoreExact"]), time_score.encode(K, 0))
        self.assertLess(int(time_score.timed_score(HUGE_K - 1, 1_000_000.0, None)["scoreExact"]),
                        time_score.encode(HUGE_K, 0))

    def test_equal_k_needs_a_five_percent_speedup(self):
        self.assertEqual(time_score.timed_score(K, 23_037.0 * 0.95, RECORD), exact(K * 10_000 + 365))
        self.assertEqual(time_score.timed_score(K, 23_037.0 * 0.96, RECORD), exact(RECORD["currentBestScore"]))
        self.assertEqual(time_score.timed_score(K, 30_000.0, RECORD), exact(RECORD["currentBestScore"]))

    def test_huge_k_carries_minutes(self):
        scored = time_score.timed_score(HUGE_K, 23_037.0 * 0.95, HUGE_RECORD)
        self.assertEqual(scored, {"score": 1e125, "scoreExact": str(HUGE_K * 10_000 + 365)})
        self.assertLess(int(scored["scoreExact"]), int(HUGE_RECORD["currentBestScoreExact"]))
        self.assertEqual(time_score.timed_score(HUGE_K + 1, 60.0, HUGE_RECORD),
                         {"score": 1e125, "scoreExact": str((HUGE_K + 1) * 10_000 + 1)})

    def test_huge_k_ties_by_copying_the_exact_record(self):
        for seconds in [23_037.0 * 0.96, 30_000.0]:
            scored = time_score.timed_score(HUGE_K, seconds, HUGE_RECORD)
            self.assertEqual(scored, {"score": HUGE_RECORD["currentBestScore"],
                                      "scoreExact": HUGE_RECORD["currentBestScoreExact"]})

    def test_the_record_k_comes_from_the_exact_score(self):
        # Metrics that disagree with the exact score do not decide the record's K.
        record = HUGE_RECORD | {"currentBestMetrics": {"claim": K, "verifySeconds": 23_037.0}}
        self.assertEqual(time_score.timed_score(HUGE_K, 30_000.0, record)["scoreExact"],
                         HUGE_RECORD["currentBestScoreExact"])
        self.assertEqual(time_score.timed_score(K, 30_000.0, record), exact(K * 10_000 + 500))

    def test_equal_k_ties_when_the_record_has_no_time(self):
        record = {"currentBestScore": K * 10_000 + 9_999, "currentBestScoreExact": str(K * 10_000 + 9_999),
                  "currentBestMetrics": {"claim": K}}
        self.assertEqual(time_score.timed_score(K, 1.0, record), exact(K * 10_000 + 9_999))

    def test_a_record_without_an_exact_score_uses_its_claim_and_slowest_digits(self):
        legacy = {"currentBestScore": K * 10_000 + 384, "currentBestScoreExact": None,
                  "currentBestMetrics": {"claim": K, "verifySeconds": 23_037.0}}
        self.assertEqual(time_score.timed_score(K, 30_000.0, legacy), exact(K * 10_000 + 9_999))
        self.assertEqual(time_score.timed_score(K, 23_037.0 * 0.95, legacy), exact(K * 10_000 + 365))
        self.assertEqual(time_score.timed_score(K - 1, 30_000.0, legacy), exact((K - 1) * 10_000 + 500))
        baseline = {"currentBestScore": 2**53 - 1, "currentBestMetrics": {"claim": "infinity"}}
        self.assertEqual(time_score.timed_score(K, 60.0, baseline), exact(K * 10_000 + 1))

    def test_a_malformed_exact_record_gets_the_slowest_digits(self):
        for value in [123, "0123", "1e9", "-1", str(K * 10_000) + ".5", "9" * 302]:
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(time_score.timed_score(K, 1.0, RECORD | {"currentBestScoreExact": value}),
                                 exact(K * 10_000 + 9_999))

    def test_an_unread_record_gets_the_slowest_digits(self):
        self.assertEqual(time_score.timed_score(K, 1.0, None), exact(K * 10_000 + 9_999))
        self.assertEqual(time_score.timed_score(HUGE_K, 1.0, None),
                         {"score": 1e125, "scoreExact": str(HUGE_K * 10_000 + 9_999)})

    def test_minutes_are_capped(self):
        self.assertEqual(time_score.timed_score(K - 1, 10**9, RECORD), exact((K - 1) * 10_000 + 9_999))


class CurrentRecordTest(unittest.TestCase):
    def lookup(self, responses: list):
        with patch.object(time_score, "get_json", side_effect=responses) as get, patch("sys.stderr", io.StringIO()):
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
    def run_main(self, written: dict, record: dict | None) -> tuple[str, list]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "benchmark.json").write_text(json.dumps({"tracks": [{"name": "main", "exactScores": True}]}))
            score = root / "scripts" / "score.json"
            score.write_text(json.dumps(written) + "\n")
            env = {"GITHUB_REPOSITORY": "Example/prover-dev", "YUKON_API_URL": "https://api-dev"}
            with patch.object(time_score, "ROOT", root), patch.object(time_score, "SCORE", score), \
                    patch.dict("os.environ", env), patch("sys.stderr", io.StringIO()), \
                    patch.object(time_score, "current_record", return_value=record) as lookup:
                time_score.main()
            return score.read_text(), lookup.call_args_list

    def test_rewrites_the_score_and_keeps_the_metrics(self):
        metrics = {"claim": K - 5, "finite": True, "verifySeconds": 600.0}
        text, calls = self.run_main({**exact((K - 5) * 10_000 + 61), "metrics": metrics}, RECORD)
        self.assertEqual(json.loads(text), {**exact((K - 5) * 10_000 + 10), "metrics": metrics})
        self.assertTrue(text.startswith(f'{{"score": {(K - 5) * 10_000 + 10}, "scoreExact": "'))
        self.assertEqual([call.args for call in calls], [("https://api-dev", "Example/prover-dev", "main")])

    def test_huge_k_tie_writes_the_record_pair(self):
        metrics = {"claim": HUGE_K, "finite": True, "verifySeconds": 30_000.0}
        written = {**time_score.exact_score(HUGE_K * 10_000 + 500), "metrics": metrics}
        text, _ = self.run_main(written, HUGE_RECORD)
        result = json.loads(text)
        self.assertEqual(result, {"score": 1e125, "scoreExact": HUGE_RECORD["currentBestScoreExact"],
                                  "metrics": metrics})
        self.assertEqual(result["score"], float(result["scoreExact"]))

    def test_infinity_skips_the_lookup_and_keeps_run_py_score(self):
        metrics = {"claim": "infinity", "finite": False, "verifySeconds": 1.0}
        text, calls = self.run_main({**time_score.INFINITY, "metrics": metrics}, None)
        self.assertEqual(json.loads(text), {"score": 1e300, "scoreExact": "1" + "0" * 300, "metrics": metrics})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
