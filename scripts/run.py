"""Score a Yukon submission with the pinned rocq-init-e verifier, which runs unchanged.

submission/ is the verifier package of docs/SUBMISSIONS.md: rom.bin, claim.json, Solution.v and
optional Rocq helper sources. verifier/verify.py checks the operator seal written by
scripts/setup.sh, compiles the package in Bubblewrap and recursively kernel-checks the fixed
Certificate. A score is written only after it prints VERIFIED and writes a verified result.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
SUBMISSION = ROOT / "submission"
WORK = ROOT / ".artifacts" / "yukon"
SWITCH = "rocq-init-e"
# Yukon scores are doubles and come back with 15 significant digits, so this sentinel reads back as
# 9007199254740990, as does every value from FINITE_LIMIT up. A finite K must stay below FINITE_LIMIT
# to remain distinguishable from infinity.
INFINITY_SCORE = 2**53 - 1
FINITE_LIMIT = 9_007_199_254_740_985
# Per-stage limit of upstream's accepted baseline replay. Its kernel stage exceeded the default
# 10,800 s and passed within 21,600 s (docs/PORT-STATUS.md).
TIMEOUT_SECONDS = 21_600
HEX64 = re.compile(r"[0-9a-f]{64}")


def run_verifier(output: Path) -> dict:
    command = ["opam", "exec", f"--switch={SWITCH}", "--", "python3", str(ROOT / "verifier" / "verify.py"),
               str(SUBMISSION), "--output", str(output), "--timeout", str(TIMEOUT_SECONDS)]
    process = subprocess.run(command, stdout=subprocess.PIPE, text=True, cwd=ROOT,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    print(process.stdout, end="")
    if process.returncode != 0 or process.stdout.splitlines()[-1:] != ["VERIFIED"]:
        return {"status": "rejected", "returncode": process.returncode}
    try:
        result = json.loads((output / "result.json").read_text())
    except (OSError, ValueError):
        return {"status": "failed", "reason": "verifier wrote no readable result.json"}
    if not isinstance(result, dict):
        return {"status": "failed", "reason": "malformed result.json"}
    return dict(result, status="verified")


def claim_of(result: dict) -> str | int:
    """The verified score literal, `infinity` or `finite N`, as the claim."""
    literal = result.get("score_literal")
    if literal == "infinity":
        return "infinity"
    match = re.fullmatch(r"finite (0|[1-9][0-9]*)", literal) if isinstance(literal, str) else None
    if match is None:
        raise SystemExit(f"Unrecognized verified score literal: {literal!r}")
    return int(match[1])


def main() -> None:
    score_path = ROOT / "scripts" / "score.json"
    score_path.unlink(missing_ok=True)
    if sys.platform != "linux":
        raise SystemExit("Verification requires Linux and Bubblewrap")
    work = WORK / uuid.uuid4().hex
    work.mkdir(parents=True)
    report = run_verifier(work / "replay")
    print(json.dumps(report, sort_keys=True))
    if (report.get("status") != "verified" or report.get("verified") is not True
            or report.get("kernel_exit") != 0):
        raise SystemExit(f"Verifier did not accept the submission: {report.get('status')}")
    if not all(isinstance(report.get(key), str) and HEX64.fullmatch(report[key])
               for key in ("rom_sha256", "seal_sha256")):
        raise SystemExit("Verified result lacks the ROM or seal SHA-256")
    claim = claim_of(report)
    if claim == "infinity":
        score = INFINITY_SCORE
    elif claim < FINITE_LIMIT:
        score = claim
    else:
        raise SystemExit("K exceeds Yukon's exact integer range")
    contract = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()
    metrics = {"claim": claim, "finite": claim != "infinity", "prover": "rocq", "verification": "rocqchk",
               "romSha256": report["rom_sha256"], "romBytes": report.get("rom_bytes"),
               "sealSha256": report["seal_sha256"], "axioms": report.get("axioms"),
               "contractCommit": contract}
    temporary = score_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"score": score, "metrics": metrics}) + "\n")
    temporary.replace(score_path)


if __name__ == "__main__":
    main()
