"""Score mapping and fail-closed checks for scripts/run.py, with the verifier mocked."""
from __future__ import annotations

import contextlib
import io
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

ROM_SHA = "a" * 64
SEAL_SHA = "b" * 64
RUNNER = {"name": "blacksmith-1", "cpuModel": "AMD EPYC 9454P 48-Core Processor", "cpuCount": 32}
# The fake clock advances by 3600.27 s per verifier run.
TIMING = {"verifySeconds": 3600.3, "verifyRunner": RUNNER}
# What scripts/run.py wrote before verifier timing was recorded.
LEGACY = {
    "infinity": '{"score": 9007199254740991, "metrics": {"claim": "infinity", "finite": false, "prover": "rocq", '
                f'"verification": "rocqchk", "romSha256": "{ROM_SHA}", "romBytes": 1028080, "sealSha256": "{SEAL_SHA}", '
                '"axioms": ["Stdlib.Logic.Classical_Prop.classic"], "contractCommit": "abc123"}}\n',
    1234: '{"score": 1234, "metrics": {"claim": 1234, "finite": true, "prover": "rocq", "verification": "rocqchk", '
          f'"romSha256": "{ROM_SHA}", "romBytes": 1028080, "sealSha256": "{SEAL_SHA}", '
          '"axioms": ["Stdlib.Logic.Classical_Prop.classic"], "contractCommit": "abc123"}}\n',
}


def result(literal="infinity", **changes):
    return dict({"verified": True, "rom_bytes": 1028080, "rom_sha256": ROM_SHA, "score_literal": literal,
                 "axioms": ["Stdlib.Logic.Classical_Prop.classic"], "kernel_exit": 0,
                 "seal_sha256": SEAL_SHA}, **changes)


class Harness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        self.score = self.root / "scripts" / "score.json"
        cpuinfo = self.root / "cpuinfo"
        cpuinfo.write_text(f"processor\t: 0\nmodel name\t: {RUNNER['cpuModel']}\n\nprocessor\t: 1\nmodel name\t: x\n")
        for p in [patch.object(run, "ROOT", self.root), patch.object(run, "SUBMISSION", self.root / "submission"),
                  patch.object(run, "WORK", self.root / ".artifacts" / "yukon"),
                  patch.object(run.sys, "platform", "linux"),
                  patch.object(run, "CPUINFO", cpuinfo), patch.object(run.os, "cpu_count", return_value=32),
                  patch.dict(run.os.environ, {"RUNNER_NAME": "blacksmith-1"}),
                  patch.object(run.time, "monotonic", side_effect=itertools.cycle([100.0, 3700.27]))]:
            p.start()
            self.addCleanup(p.stop)
        self.stderr = io.StringIO()
        redirect = contextlib.redirect_stderr(self.stderr)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def timing_logged(self):
        lines = [line for line in self.stderr.getvalue().splitlines() if line.startswith("Verifier timing: ")]
        return [json.loads(line.removeprefix("Verifier timing: ")) for line in lines]

    def verifier(self, written, returncode=0, stdout="VERIFIED\n"):
        def fake(command, **kwargs):
            self.assertEqual(command[:5], ["opam", "exec", "--switch=rocq-init-e", "--", "python3"])
            self.assertEqual(command[5:7], [str(self.root / "verifier" / "verify.py"), str(self.root / "submission")])
            self.assertEqual(command[9:], ["--timeout", "21600"])
            output = Path(command[command.index("--output") + 1])
            self.assertFalse(output.exists())
            output.mkdir()
            if written is not None:
                (output / "result.json").write_text(written if isinstance(written, str) else json.dumps(written))
            return subprocess.CompletedProcess(command, returncode, stdout=stdout)
        return patch.object(run.subprocess, "run", fake)

    def run_main(self, written, **kwargs):
        with self.verifier(written, **kwargs), patch.object(run.subprocess, "check_output", return_value="abc123\n"):
            run.main()
        return json.loads(self.score.read_text())

    def rejected(self, written, **kwargs):
        self.score.write_text("stale")
        with self.assertRaises(SystemExit):
            self.run_main(written, **kwargs)
        self.assertFalse(self.score.exists())

    def test_infinity_scores_the_exact_infinity_pair(self):
        scored = self.run_main(result())
        self.assertEqual((scored["score"], scored["scoreExact"]), (1e300, "1" + "0" * 300))
        self.assertEqual(scored["metrics"], {
            "claim": "infinity", "finite": False, "prover": "rocq", "verification": "rocqchk",
            "romSha256": ROM_SHA, "romBytes": 1028080, "sealSha256": SEAL_SHA,
            "axioms": ["Stdlib.Logic.Classical_Prop.classic"], "contractCommit": "abc123", **TIMING})

    def test_score_carries_minutes_and_timing_is_appended(self):
        # 3600.3 s rounds up to 61 minutes, below K's digits.
        for K, legacy in LEGACY.items():
            self.run_main(result("infinity" if K == "infinity" else f"finite {K}"))
            text = self.score.read_text()
            score = '1e+300, "scoreExact": "1' + "0" * 300 + '"' if K == "infinity" else \
                f'{K * 10_000 + 61}, "scoreExact": "{K * 10_000 + 61}"'
            expected = legacy.replace(f'"score": {2**53 - 1 if K == "infinity" else K},', f'"score": {score},', 1)
            self.assertEqual(text, expected[:-len("}}\n")] + ", " + json.dumps(TIMING)[1:] + "}\n")
            self.assertTrue(text.startswith(f'{{"score": {score}, "metrics": '))
            scored = json.loads(text)
            self.assertEqual(scored["score"], float(scored["scoreExact"]))

    def test_verify_seconds_is_positive_float(self):
        with patch.object(run.time, "monotonic", side_effect=[5.0, 5.26]):
            metrics = self.run_main(result("finite 7"))["metrics"]
        self.assertIsInstance(metrics["verifySeconds"], float)
        self.assertEqual(metrics["verifySeconds"], 0.3)
        self.assertNotIn("verifyCpuSeconds", metrics)

    def test_runner_info(self):
        self.assertEqual(run.runner(), RUNNER)
        with patch.object(run, "CPUINFO", self.root / "missing"), patch.dict(run.os.environ, {"RUNNER_NAME": ""}):
            self.assertEqual(run.runner(), {"cpuCount": 32})

    def test_finite_k_is_scored_with_its_minutes(self):
        largest = run.time_score.LARGEST_K
        for K in [0, 123, 2**53 - 1, 10**121, largest]:
            scored = self.run_main(result(f"finite {K}"))
            self.assertEqual((scored["scoreExact"], scored["metrics"]["claim"], scored["metrics"]["finite"]),
                             (str(K * 10_000 + 61), K, True))
            self.assertEqual(scored["score"], float(scored["scoreExact"]))
        self.assertEqual(self.run_main(result("finite 123"))["score"], 1_230_061)
        self.assertEqual(self.run_main(result(f"finite {10**121}"))["score"], 1e125)

    def test_k_beyond_299_digits_fails_closed(self):
        for K in [run.time_score.LARGEST_K + 1, 10**300]:
            self.rejected(result(f"finite {K}"))

    def test_malformed_score_literal_fails_closed(self):
        for literal in ["finite -1", "finite 01", "finite 1.5", "finite", "7", "Infinity", 7, None]:
            self.rejected(result(literal))

    def test_rejection_and_incomplete_runs_delete_stale_score(self):
        self.rejected(None, returncode=1, stdout="REJECTED: verification stage failed\n")
        self.rejected(result(), returncode=1)
        self.rejected(result(), stdout="")
        self.rejected(result(), stdout="VERIFIED\nextra\n")
        self.rejected(None)
        self.rejected("not json")
        self.rejected("[]")
        self.assertEqual(self.timing_logged(), [TIMING] * 7)

    def test_unverified_result_fails_closed(self):
        for written in [result(verified=False), result(verified="true"), result(kernel_exit=1),
                        result(kernel_exit=None), result(rom_sha256="A" * 64), result(rom_sha256=None),
                        result(seal_sha256="b" * 63)]:
            self.rejected(written)

    def test_non_linux_fails_closed(self):
        self.score.write_text("stale")
        with patch.object(run.sys, "platform", "darwin"), self.assertRaises(SystemExit), \
                patch.object(run.subprocess, "run", side_effect=AssertionError("verifier ran")):
            run.main()
        self.assertFalse(self.score.exists())
        self.assertEqual(self.timing_logged(), [])


if __name__ == "__main__":
    unittest.main()
