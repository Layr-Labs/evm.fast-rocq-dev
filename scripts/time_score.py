"""Encode verification time in the official score: score = K * 10^4 + verification minutes.

scripts/run.py scores the verified K alone. The benchmark workflow then runs this file on the
runner host, outside any verifier sandbox, and moves K into the high digits so a smaller K always
wins and time only breaks ties at equal K. At equal K a submission must verify at least 5% faster
than the record read when it finishes; otherwise it takes the record's score, ties, and is not
promoted, since Yukon promotes strict improvements only. Infinity is left as run.py wrote it. If
the record cannot be read, the time digits are 9999, which never wins a tie.
"""
from __future__ import annotations

import http.client
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SCORE = ROOT / "scripts" / "score.json"
DEFAULT_API = "https://api.yukon.org"
SCALE = 10_000
SLOWEST = SCALE - 1
MARGIN = 0.95
# Yukon reads scores back with 15 significant digits. From this K the minutes would round into K,
# so larger bounds carry no time and equal K stay tied.
UNTIMED_K = 10**11
# As in scripts/run.py: every score from here up reads back from Yukon as infinity.
FINITE_LIMIT = 9_007_199_254_740_985
NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)


def encode(k: int, minutes: int) -> int:
    # From K of about 9e11 the score would read back as infinity; all such bounds tie just below it.
    return min(k * SCALE + (minutes if k < UNTIMED_K else 0), FINITE_LIMIT - 1)


def timed_score(k: int, seconds: float, record: dict | None) -> int:
    """The official score for a verified finite K, given the benchmark's current record."""
    if record is None:
        return encode(k, SLOWEST)
    own = encode(k, min(math.ceil(seconds / 60), SLOWEST))
    best = record.get("currentBestMetrics") or {}
    if best.get("claim") != k:
        return own
    record_seconds = best.get("verifySeconds")
    if isinstance(record_seconds, (int, float)) and seconds <= record_seconds * MARGIN:
        return own
    return int(record["currentBestScore"])


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
    result["score"] = timed_score(k, result["metrics"]["verifySeconds"], record)
    print(f"Timed score: {result['score']}", file=sys.stderr)
    temporary = SCORE.with_suffix(".tmp")
    temporary.write_text(json.dumps(result) + "\n")
    temporary.replace(SCORE)


if __name__ == "__main__":
    main()
