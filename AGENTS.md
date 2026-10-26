# Agent instructions

- The challenge is described in [README.md](README.md) and the submission protocol in
  [docs/SUBMISSIONS.md](docs/SUBMISSIONS.md). The fixed statement is `Certificate` in
  `theories/InitE/Challenge.v`, resolved through `theories/InitE/VerificationCore.v`. Those
  upstream documents are the authority for the challenge; this file covers the Yukon harness.
- `benchmark.json` is schema v2 with one track, `rocq`. Pass `--track rocq` to `yukon setup`,
  `yukon run` and `yukon submit`. `yukon switch` changes only Yukon's track selection; it keeps
  your Git branch, `HEAD`, index and worktree.
- Yukon submissions on the `rocq` track replace only `submission/`, which is the verifier
  package itself:
  - `rom.bin`: the submitted bytes, 1 byte to 128 MiB.
  - `claim.json`: exactly `{"K":123}` (a nonnegative integer) or `{"K":"infinity"}`, at most 1 MiB.
  - `Solution.v`: exports `submission : Submission` and a proved `certificate` of the exact type
    in docs/SUBMISSIONS.md.
  - Optional helper `.v` sources, in subdirectories with ASCII identifier names.

  Anything else (compiled files, symlinks, other names or extensions) is rejected. Sources are
  loaded as `Candidate.Solution` and `Candidate.<path>`. `verifier/policy.py` rejects these words
  anywhere outside comments and strings, even as identifiers: `Notation`, `Infix`, `Reserved`,
  `Declare`, `Redirect`, `Extraction`, `Separate`, `Recursive`, `Cd`, `Include`, `Plugin`,
  `Export`. It also rejects unqualified `Load` and `Add`/`Remove` `LoadPath` or `ML Path`.
  The baseline package in `submission/` is the one `scripts/make_baseline_submission.py`
  produces for the software-precompile guest (1,028,080-byte ROM, SHA-256 `44b6a6b8…69d04b`,
  claim `infinity`). Its `Solution.v`
  reuses the trusted `InitE.Baseline` library. Your sources may import Stdlib and any trusted
  `InitE` or `Galette` module.
- Changes outside `submission/` are dropped from Yukon submissions.

## Size limits

- Yukon archives are limited to 25 MiB compressed, 512 MiB expanded and 10,000 entries. Yukon
  commits the submission to Git, and GitHub rejects files over 100 MiB, so `rom.bin` is
  effectively capped below the verifier's 128 MiB. The baseline package is 1,028,844 bytes
  (about 261 KB as tar.gz).

## Score

- `scripts/run.py` scores the certified step bound `K` from `claim.json` with its verification
  time: `scoreExact = K * 10^4 + verification minutes` (rounded up, at most 9999), so a smaller
  `K` always wins and time only breaks ties at equal `K`; lower is better. Ties do not improve
  the incumbent.
- The track opts in to Yukon's exact scores (`"exactScores": true` in `benchmark.json`).
  `score.json` carries `scoreExact`, a decimal string Yukon compares exactly, and `score`, the
  double nearest it (a plain integer up to 2^53). Minutes count at every size of `K`. `K` must be
  below 10^295, since an exact score holds at most 299 integer digits; larger claims are rejected.
- Official runs then apply the record rule (`scripts/time_score.py`, run by the workflow on the
  host): at equal `K`, read from the record's `currentBestScoreExact`, a submission must verify
  at least 5% faster than the `verifySeconds` of the record read when it finishes; otherwise it
  takes the record's exact score and ties. If the record cannot be read, the minutes are 9999
  and the step logs a warning. A record with no exact score (scored before the opt-in) is matched
  by its `metrics.claim`, and an equal `K` that is not 5% faster gets 9999 minutes.
  `YUKON_API_URL` (repository variable, default `https://api.yukon.org`) names the API. Local runs
  skip this step, so their minutes are only a rough indicator.
- `{"K":"infinity"}` is scored as `scoreExact` `1` followed by 300 zeros (10^300) with `score`
  `1e300`, above every finite score. Infinity still requires a finite matching Halt for every
  covered input. The initial submission claims infinity, so any accepted finite `K` improves it.
- `K = floor(scoreExact / 10^4)`, with the verification minutes in the last four digits.
- A score is written only when the verifier prints `VERIFIED` and writes a verified
  `result.json`; otherwise `scripts/run.py` exits nonzero and writes no score. `metrics` records
  the claim, the ROM SHA-256 and size, the operator seal SHA-256, the kernel axioms and the
  harness commit.
- `metrics.verifySeconds` is the verifier's wall time, and `metrics.verifyRunner` describes the
  runner.

## Verification

`scripts/run.py` runs `verifier/verify.py submission --timeout 21600` unchanged in the
`rocq-init-e` opam switch. The verifier checks the operator seal (trusted sources, objects and
the Rocq launcher), captures the ROM and claim as protected literals, and compiles your sources
in Bubblewrap with no network and read-only trusted libraries. It then checks that your
`certificate` has the fixed type for those literals and recursively kernel-checks it with
`rocq check`. Only the five `Stdlib.Logic` foundations listed in `verifier/policy.py` are
allowed as axioms. Each stage is limited to 21,600 s and 112 GiB of address space. Upstream's
software-guest baseline passed the verifier in 2 h 20 min.

Run `bash scripts/setup.sh`, then `python3 scripts/run.py` (what `yukon setup --track rocq` and
`yukon run --track rocq` do), on Linux. Bubblewrap must be allowed to create user, PID and
network namespaces; on Ubuntu 24.04 this needs
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`. Setup installs the recorded
toolchain (OCaml 5.2.1, Dune 3.24.2, Rocq 9.3.0, `rocq-stdlib` 9.2.0) in the `rocq-init-e` opam
switch. It checks out the pinned submodules and runs the reference audit, import checks and
verifier tests. It then builds the trusted library with `dune build -j 8` (the previous guest's
build peaked at 30.3 GiB RSS in one file), kernel-checks it, seals it and runs the negative CLI
cases. The cold build takes hours. Do not claim verification without a successful run.

## Notes

- Put reproducible context in the Yukon submission note. Remove secrets and private data first.
- Yukon handles submission pull requests and promotions; do not merge them by hand.
