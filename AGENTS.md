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
  produces (950,344-byte ROM, SHA-256 `22a1dd09…97d7cc`, claim `infinity`). Its `Solution.v`
  reuses the trusted `InitE.Baseline` library. Your sources may import Stdlib and any trusted
  `InitE` or `Galette` module.
- Changes outside `submission/` are dropped from Yukon submissions.

## Size limits

- Yukon archives are limited to 25 MiB compressed, 512 MiB expanded and 10,000 entries. Yukon
  commits the submission to Git, and GitHub rejects files over 100 MiB, so `rom.bin` is
  effectively capped below the verifier's 128 MiB. The baseline package is 951,108 bytes
  (243,852 bytes as tar.gz).

## Score

- `scripts/run.py` scores the certified step bound `K` from `claim.json` with its verification
  time: `score = K * 10^4 + verification minutes` (rounded up, at most 9999), so a smaller `K`
  always wins and time only breaks ties at equal `K`; lower is better. Ties do not improve the
  incumbent.
- Official runs then apply the record rule (`scripts/time_score.py`, run by the workflow on the
  host): at equal `K` a submission must verify at least 5% faster than the `verifySeconds` of the
  record read when it finishes; otherwise it takes the record's score and ties. If the record
  cannot be read, the minutes are 9999 and the step logs a warning. `YUKON_API_URL` (repository
  variable, default `https://api.yukon.org`) names the API. Local runs skip this step, so their
  minutes are only a rough indicator.
- `{"K":"infinity"}` is scored as `9007199254740991` (2^53 − 1). Infinity still requires a finite
  matching Halt for every covered input. The initial submission claims infinity, so any accepted
  finite `K` improves it.
- Yukon stores scores as doubles and returns them with 15 significant digits, so the infinity
  score reads back as `9007199254740990`, as does every value from `9007199254740985` up. A
  finite `K` of `900719925475` or more is rejected, since `K * 10^4` would read back as
  infinity. A `K` of 10^11 or more carries no minutes, since they would round into `K`, so equal
  bounds that large stay tied.
- Scores, as `yukon` prints them, read as `K = floor(score / 10^4)`, with the verification
  minutes in the last four digits.
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
baseline kernel stage took more than 3 hours.

Run `bash scripts/setup.sh`, then `python3 scripts/run.py` (what `yukon setup --track rocq` and
`yukon run --track rocq` do), on Linux. Bubblewrap must be allowed to create user, PID and
network namespaces; on Ubuntu 24.04 this needs
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`. Setup installs the recorded
toolchain (OCaml 5.2.1, Dune 3.24.2, Rocq 9.3.0, `rocq-stdlib` 9.2.0) in the `rocq-init-e` opam
switch. It checks out the pinned submodules and runs the reference audit, import checks and
verifier tests. It then builds the trusted library with `dune build -j 1` (upstream peaked at
30.3 GiB RSS), kernel-checks it, seals it and runs the negative CLI cases. The cold build takes
hours. Do not claim verification without a successful run.

## Notes

- Put reproducible context in the Yukon submission note. Remove secrets and private data first.
- Yukon handles submission pull requests and promotions; do not merge them by hand.
