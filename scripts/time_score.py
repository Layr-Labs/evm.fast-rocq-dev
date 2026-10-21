"""Verification time in the score: scoreExact = K * 10^4 + verification minutes.

The track opts in to Yukon's exact scores (`"exactScores": true` in benchmark.json): Yukon compares
scoreExact as an exact decimal, and score is the double nearest it. K sits in the high digits, so a
smaller K always wins and time only breaks ties at equal K, at any size of K.
scripts/run.py scores every run this way with its own minutes. In official runs the benchmark
workflow then runs this file on the runner host, outside any verifier sandbox, against the current
record: at equal K a submission must verify at least 5% faster than the record read when it
finishes; otherwise it takes the record's exact score, ties, and is not promoted, since Yukon
promotes strict improvements only. Infinity is left as run.py wrote it. If the record cannot be
read, the time digits are 9999, which never wins a tie.

A record without currentBestScoreExact was scored before the track opted in. Its K is then read
from currentBestMetrics.claim, and at equal K a submission that is not 5% faster gets 9999 minutes
instead of a copy, which cannot beat the record either.
"""
from __future__ import annotations

import http.client
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SCORE = ROOT / "scripts" / "score.json"
DEFAULT_API = "https://api.yukon.org"
SCALE = 10_000
SLOWEST = SCALE - 1
MARGIN = 0.95
# Yukon exact scores have at most 299 integer digits, so K * 10^4 + minutes must stay below 10^299.
LARGEST_K = (10**299 - 1) // SCALE
# Above every finite score; 1e300 is the double nearest 10^300, as Yukon requires.
INFINITY = {"score": 1e300, "scoreExact": "1" + "0" * 300}
RECORD_EXACT = re.compile(r"0|[1-9][0-9]{0,300}")
NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)


def encode(k: int, minutes: int) -> int:
    return k * SCALE + minutes


def exact_score(exact: int) -> dict:
    """score.json's score and scoreExact for an exact integer, with score == float(scoreExact)."""
    text = str(exact)
    return {"score": exact if exact <= 2**53 else float(text), "scoreExact": text}


def minutes(seconds: float) -> int:
    return min(math.ceil(seconds / 60), SLOWEST)


def timed_score(k: int, seconds: float, record: dict | None) -> dict:
    """The official score for a verified finite K, given the benchmark's current record."""
    if record is None:
        return exact_score(encode(k, SLOWEST))
    own = exact_score(encode(k, minutes(seconds)))
    best = record.get("currentBestMetrics") or {}
    exact = record.get("currentBestScoreExact")
    if exact is None:
        if best.get("claim") != k:
            return own
        tie = exact_score(encode(k, SLOWEST))
    elif isinstance(exact, str) and RECORD_EXACT.fullmatch(exact):
        if int(exact) // SCALE != k:
            return own
        tie = exact_score(int(exact))
    else:
        print(f"::warning::Unreadable currentBestScoreExact {exact!r}; scoring with 9999 minutes", file=sys.stderr)
        return exact_score(encode(k, SLOWEST))
    record_seconds = best.get("verifySeconds")
    if isinstance(record_seconds, (int, float)) and seconds <= record_seconds * MARGIN:
        return own
    return tie


def get_json(url: str) -> dict:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except NETWORK_ERRORS:
            if attempt == 2:
                raise
            time.sleep(10)
    raise AssertionError("unreachable")


def current_record(api: str, repository: str, track: str) -> dict | None:
    """The open Yukon benchmark for this repository and track, or None if it cannot be read."""
    source = f"https://github.com/{repository}".lower()
    try:
        matches = [benchmark["id"] for benchmark in get_json(f"{api}/api/benchmarks")["benchmarks"]
                   if benchmark["sourceUrl"].lower().removesuffix(".git") == source
                   and benchmark.get("trackName") == track and benchmark.get("status") == "open"]
        if len(matches) != 1:
            print(f"::warning::Expected one open benchmark for {source} track {track} at {api}, found "
                  f"{len(matches)}; scoring with 9999 minutes", file=sys.stderr)
            return None
        return get_json(f"{api}/api/benchmarks/{matches[0]}")["benchmark"]
    except (*NETWORK_ERRORS, KeyError, TypeError, AttributeError) as exc:
        print(f"::warning::Could not read the current record from {api}: {exc}; scoring with 9999 minutes",
              file=sys.stderr)
        return None


def main() -> None:
    result = json.loads(SCORE.read_text())
    k = result["metrics"]["claim"]
    if k == "infinity":
        return
    [track] = json.loads((ROOT / "benchmark.json").read_text())["tracks"]
    record = current_record(os.environ.get("YUKON_API_URL") or DEFAULT_API, os.environ["GITHUB_REPOSITORY"],
                            track["name"])
    result.update(timed_score(k, result["metrics"]["verifySeconds"], record))
    print(f"Timed score: {result['scoreExact']}", file=sys.stderr)
    temporary = SCORE.with_suffix(".tmp")
    temporary.write_text(json.dumps(result) + "\n")
    temporary.replace(SCORE)


if __name__ == "__main__":
    main()
