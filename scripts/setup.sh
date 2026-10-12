#!/usr/bin/env bash
# Trusted setup following docs/SUBMISSIONS.md for a fresh reviewed checkout: the recorded opam
# toolchain, pinned submodules, reference audit, import checks and verifier unit tests, then the
# coherent build, operator kernel checks, seal and real negative CLI cases. Nothing under
# submission/ is read, compiled or loaded here; verifier/verify.py handles it in Bubblewrap.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "$(uname -s)" == Linux ]] || { echo 'The verifier requires Linux and Bubblewrap.' >&2; exit 1; }
switch=rocq-init-e
# ocaml/opam-repository commit providing the package versions recorded in docs/SUBMISSIONS.md.
opam_repository_rev=74bf8da7ffea3971801da0d3540eb43f26d60fbd
packages=(ocaml-base-compiler.5.2.1 dune.3.24.2 rocq-core.9.3.0 rocq-runtime.9.3.0
  rocq-prover.meta.1 rocq-stdlib.9.2.0 zarith.1.14)
logs="${PWD}/.artifacts/setup"
export OPAMYES=1 PYTHONDONTWRITEBYTECODE=1
mkdir -p "${logs}"
switch_exec=(opam exec --switch="${switch}" --)
stamp() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
in_switch() { "${switch_exec[@]}" "$@"; }
# Retain each heavy step's complete log, wall time and peak RSS.
timed() {
  local name="$1"; shift
  stamp "start ${name}"
  /usr/bin/time -v -o "${logs}/${name}.time" "$@" 2>&1 | tee "${logs}/${name}.log"
  stamp "end ${name}: $(grep -E 'Elapsed|Maximum resident' "${logs}/${name}.time" | tr -s ' \t' ' ')"
}
seal_ok() {
  in_switch python3 -c 'import sys; sys.path.insert(0, "verifier"); import verify; verify.check_seal()'
}

if command -v apt-get >/dev/null 2>&1; then
  apt_packages=(bubblewrap build-essential bzip2 curl git libgmp-dev m4 opam patch pkg-config
    python3 rsync time unzip)
  if ! dpkg -s "${apt_packages[@]}" >/dev/null 2>&1; then
    sudo=(); [[ "$(id -u)" == 0 ]] || sudo=(sudo)
    ${sudo[@]+"${sudo[@]}"} apt-get update -qq
    ${sudo[@]+"${sudo[@]}"} apt-get install -y --no-install-recommends "${apt_packages[@]}"
  fi
fi

# The Bubblewrap options every verifier stage uses. Ubuntu 24.04 blocks them while
# kernel.apparmor_restrict_unprivileged_userns=1; fail now rather than after the build.
if ! bwrap --unshare-all --die-with-parent --new-session --clearenv --ro-bind /usr /usr \
    --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp /usr/bin/true; then
  echo 'Bubblewrap cannot create user, PID and network namespaces on this host.' >&2
  echo 'On Ubuntu 24.04: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0' >&2
  exit 1
fi

git submodule update --init --recursive
# The audit and import scripts run `rtk proxy git ...`; proxy mode is a plain passthrough.
command -v rtk >/dev/null 2>&1 || export PATH="${PWD}/scripts/bin:${PATH}"

if ! opam switch list --short 2>/dev/null | grep -qx "${switch}"; then
  repo="${PWD}/.artifacts/opam-repository"
  repo_name="opam-repository-${opam_repository_rev:0:12}"
  rm -rf "${repo}"
  git init -q "${repo}"
  git -C "${repo}" fetch -q --depth 1 https://github.com/ocaml/opam-repository.git "${opam_repository_rev}"
  git -C "${repo}" checkout -q --detach FETCH_HEAD
  test "$(git -C "${repo}" rev-parse HEAD)" = "${opam_repository_rev}"
  if [[ ! -f "${OPAMROOT:-${HOME}/.opam}/config" ]]; then
    opam init --bare --no-setup --disable-sandboxing "${repo_name}" "${repo}"
  fi
  opam switch create "${switch}" --repositories="${repo_name}=${repo}" "${packages[0]}"
fi
opam install --switch="${switch}" "${packages[@]:1}"
installed="$(opam list --switch="${switch}" --installed --short --columns=package)"
for package in "${packages[@]}"; do
  grep -qx "${package}" <<<"${installed}" || { echo "switch ${switch} lacks ${package}" >&2; exit 1; }
done
in_switch rocq --version | grep -F 'version 9.3.0'
test "$(in_switch ocamlc -version)" = 5.2.1
test "$(in_switch dune --version)" = 3.24.2

python3 scripts/audit_reference.py --self-test > "${logs}/reference-audit.json"
python3 scripts/import_baseline.py --check
python3 scripts/import_baseline_metadata.py --check
python3 -m unittest discover -s verifier -p 'test_*.py' -v

# A restored default-branch cache is reused only if it still matches its seal exactly.
if ! seal_ok 2>/dev/null; then
  rm -f .artifacts/verifier-seal.json
  timed build "${switch_exec[@]}" dune build -j 1
  for module in InitE.InitE.VerificationCore InitE.Baseline.BaselineCertificate; do
    timed "kernel-${module##*.}" "${switch_exec[@]}" rocq check -bytecode-compiler yes -silent -o \
      -Q _build/default/galette-theories Galette -Q _build/default/theories InitE "${module}"
    # The verifier's own context policy: the five permitted axioms and no unsafe sections.
    python3 -c 'import sys; sys.path.insert(0, "verifier"); from policy import check_kernel_context
print(check_kernel_context(open(sys.argv[1]).read()))' "${logs}/kernel-${module##*.}.log"
  done
  in_switch python3 scripts/seal_verifier.py
  rm -rf "${logs}/negative-replay"
  timed negative-cli "${switch_exec[@]}" python3 scripts/test_verifier_cli.py --output "${logs}/negative-replay"
fi
seal_ok
stamp 'setup complete; seal verified'
