"""Score mapping and fail-closed checks for scripts/run.py, with the verifier mocked."""
from __future__ import annotations

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


def result(literal="infinity", **changes):
    return dict({"verified": True, "rom_bytes": 950344, "rom_sha256": ROM_SHA, "score_literal": literal,
                 "axioms": ["Stdlib.Logic.Classical_Prop.classic"], "kernel_exit": 0,
                 "seal_sha256": SEAL_SHA}, **changes)


class Harness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        self.score = self.root / "scripts" / "score.json"
        for p in [patch.object(run, "ROOT", self.root), patch.object(run, "SUBMISSION", self.root / "submission"),
                  patch.object(run, "WORK", self.root / ".artifacts" / "yukon"),
                  patch.object(run.sys, "platform", "linux")]:
            p.start()
            self.addCleanup(p.stop)

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

    def test_infinity_scores_max_safe_integer(self):
        scored = self.run_main(result())
        self.assertEqual(scored["score"], 2**53 - 1)
        self.assertEqual(scored["metrics"], {
            "claim": "infinity", "finite": False, "prover": "rocq", "verification": "rocqchk",
            "romSha256": ROM_SHA, "romBytes": 950344, "sealSha256": SEAL_SHA,
            "axioms": ["Stdlib.Logic.Classical_Prop.classic"], "contractCommit": "abc123"})

    def test_finite_k_is_the_score(self):
        for K in [0, 123, run.FINITE_LIMIT - 1]:
            scored = self.run_main(result(f"finite {K}"))
            self.assertEqual((scored["score"], scored["metrics"]["claim"], scored["metrics"]["finite"]),
                             (K, K, True))

    def test_k_at_rounding_limit_fails_closed(self):
        for K in [run.FINITE_LIMIT, 2**53 - 1, 2**64]:
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


if __name__ == "__main__":
    unittest.main()
