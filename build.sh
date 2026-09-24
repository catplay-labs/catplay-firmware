#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${BUILD_DIR:-${root_dir}/build}"
catplay_dir="${CATPLAY_DIR:-${root_dir}/.sources/catplay}"
catplay_url="${CATPLAY_REPO:-https://github.com/catplay-labs/catplay.git}"
machine="${MACHINE:-clk-mini-ultra-nor}"
distro="${DISTRO:-c2a-musl}"
target="${TARGET:-c2a-system-image}"

usage() {
    cat <<EOF
Usage: $0 [bitbake-target] [bitbake-options ...]

Defaults:
  MACHINE=${machine}
  DISTRO=${distro}
  target=${target}

Environment overrides:
  BUILD_DIR       Build directory (default: ${root_dir}/build)
  CATPLAY_DIR     Local CatPlay checkout (default: ${root_dir}/.sources/catplay)
  CATPLAY_REPO    CatPlay repository URL
  MACHINE         Yocto machine
  DISTRO          Yocto distro
  TARGET          Default BitBake target
  MIN_BUILD_FREE_GIB  Minimum free space for BUILD_DIR (default: 8)

Examples:
  $0
  $0 c2a-system-bundle
  MACHINE=clk-mini-ultra-nor-recov $0 c2a-system-bundle
  $0 -c cleansstate catplay

Notes on MACHINE and c2a-system-bundle:
  There are two related Carlinkit Mini Ultra machines, both built from the
  same shared.conf/defconfig:
    - clk-mini-ultra-nor       normal firmware image
    - clk-mini-ultra-nor-recov recovery image + host-side flashing tools

  Only clk-mini-ultra-nor-recov's machine conf declares the
  carlinkit-mini-flasher:do_deploy extra dependency for c2a-system-bundle,
  so it's the only one that (re)deploys tools/*.py (exploit.py, flash.py,
  recov.py, ...) into the bundle. If you change anything under
  meta-carlinkit-mini/recipes-bsp/carlinkit-mini-flasher/files/ and want
  those changes reflected in build/tmp-c2a/clk-mini-ultra-nor/tools/, build
  with:
    MACHINE=clk-mini-ultra-nor-recov $0 c2a-system-bundle
  Building c2a-system-bundle with the default MACHINE alone will not pick
  up tools/*.py changes.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -gt 0 && "$1" != -* ]]; then
    target="$1"
    shift
fi

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "[!] Required command not found: $1" >&2
        exit 1
    }
}

check_host_dependencies() {
  local tool
  local missing=()
  local host_tools=(
    ar as awk basename bash bunzip2 bzip2 cat chgrp chmod chown chrpath cmp
    comm cp cpio cpp cut date dd diff diffstat dirname du echo egrep env
    expand expr false fgrep file find flock g++ gawk gcc getconf getopt git
    grep gunzip gzip head hostname iconv id install ld ldd ln ls make md5sum
    mkdir mkfifo mknod mktemp mv nm objcopy objdump od patch perl pr printf
    pwd python3 pzstd ranlib readelf readlink realpath rm rmdir rpcgen sed
    seq sh sha1sum sha224sum sha256sum sha384sum sha512sum sleep sort split
    stat strings strip tail tar tee test touch tr true truncate uname uniq
    unzstd wc wget which xargs zstd xz
  )

  for tool in "${host_tools[@]}"; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
      missing+=("${tool}")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "[!] Missing Yocto host tools: ${missing[*]}" >&2
    echo "    Ubuntu/Debian hint: sudo apt-get install build-essential chrpath cpio diffstat file gawk python3 zstd" >&2
    echo "    Install the missing tools and run this script again." >&2
    exit 1
  fi
}

check_python_module() {
  if ! python3 -c "import $1" >/dev/null 2>&1; then
    echo "[!] Missing Python module for Yocto hash equivalence: $1" >&2
    echo "    Install it with: python3 -m pip install --user $1" >&2
    exit 1
  fi
}

check_build_space() {
  local available_kib
  local required_gib="${MIN_BUILD_FREE_GIB:-8}"
  local required_kib

  if ! [[ "${required_gib}" =~ ^[0-9]+$ ]]; then
    echo "[!] MIN_BUILD_FREE_GIB must be a whole number, got: ${required_gib}" >&2
    exit 1
  fi

  mkdir -p "${build_dir}"
  available_kib="$(df -Pk "${build_dir}" | awk 'NR == 2 { print $4 }')"
  required_kib=$((required_gib * 1024 * 1024))

  if [[ -z "${available_kib}" || ${available_kib} -lt ${required_kib} ]]; then
    echo "[!] Insufficient free space for Yocto build directory: ${build_dir}" >&2
    echo "    Required: at least ${required_gib} GiB free; available: $((available_kib / 1024 / 1024)) GiB." >&2
    echo "    Set BUILD_DIR to a larger volume, for example:" >&2
    exit 1
  fi
}

require_command git
require_command tar

echo "[*] Checking build disk space"
check_build_space

echo "[*] Initializing Yocto submodules"
git -C "${root_dir}" submodule sync --recursive
git -C "${root_dir}" submodule update --init --recursive

if [[ ! -f "${root_dir}/openembedded-core/meta/conf/bitbake.conf" ]]; then
  echo "[!] openembedded-core is not initialized correctly" >&2
  exit 1
fi

echo "[*] Checking Yocto host dependencies"
check_host_dependencies
# check_python_module websockets

if [[ ! -d "${catplay_dir}/.git" ]]; then
    mkdir -p "$(dirname "${catplay_dir}")"
    echo "[*] Cloning CatPlay from ${catplay_url}"
    git clone --depth 1 "${catplay_url}" "${catplay_dir}"
elif [[ "$(git -C "${catplay_dir}" rev-parse --show-toplevel)" != "${catplay_dir}" ]]; then
    echo "[!] CATPLAY_DIR is not the root of a CatPlay git checkout: ${catplay_dir}" >&2
    exit 1
fi

echo "[*] Creating CatPlay source bundle"
"${root_dir}/meta-catplay/scripts/create-src-bundle.sh" "${catplay_dir}"

mkdir -p "${build_dir}/conf"

cat > "${build_dir}/conf/bblayers.conf" <<EOF
# Generated by ${root_dir}/build.sh
LCONF_VERSION = "7"

BBLAYERS = " \\
  ${root_dir}/openembedded-core/meta \\
  ${root_dir}/meta-openembedded/meta-oe \\
  ${root_dir}/meta-openembedded/meta-filesystems \\
  ${root_dir}/meta-openembedded/meta-networking \\
  ${root_dir}/meta-openembedded/meta-multimedia \\
  ${root_dir}/meta-openembedded/meta-python \\
  ${root_dir}/meta-clang \\
  ${root_dir}/meta-freescale \\
  ${root_dir}/meta-sunxi \\
  ${root_dir}/meta-carplay \\
  ${root_dir}/meta-catplay \\
  ${root_dir}/meta-carlinkit \\
  ${root_dir}/meta-carlinkit-mini \\
"
EOF

cat > "${build_dir}/conf/auto.conf" <<EOF
# Generated by ${root_dir}/build.sh
MACHINE = "${machine}"
DISTRO = "${distro}"
LICENSE_FLAGS_ACCEPTED = "commercial"

# ftp.gnu.org (the primary archive) aggressively throttles/rate-limits parallel
# connections, which manifests as do_fetch tasks hanging for a long time.
# Try the ftpmirror.gnu.org redirector (auto-selects a fast nearby mirror)
# *before* falling back to the origin server.
GNU_MIRROR = "https://ftpmirror.gnu.org/gnu"
PREMIRRORS:prepend = " \\
  https://ftp.gnu.org/gnu/ https://ftpmirror.gnu.org/gnu/ \\
  ftp://ftp.gnu.org/gnu/ https://ftpmirror.gnu.org/gnu/ \\
"
MIRRORS:append = " \\
  https://ftp.gnu.org/gnu/ https://ftpmirror.gnu.org/gnu/ \\
"

# Reuse prebuilt Yocto artifacts when available instead of compiling them locally.
# BB_HASHSERVE_UPSTREAM = "wss://hashserv.yoctoproject.org/ws"
# SSTATE_MIRRORS = "file://.* https://sstate.yoctoproject.org/all/PATH;downloadfilename=PATH"
EOF

echo "[*] Starting BitBake: ${target}"
cd "${root_dir}"
# shellcheck disable=SC1091
set +u
source "${root_dir}/openembedded-core/oe-init-build-env" "${build_dir}" >/dev/null
set -u

# BitBake occasionally leaves a stamp pointing at a tmp/deploy/ artifact that
# no longer exists (e.g. after tmp/deploy/ was partially cleaned by hand, or
# a build was interrupted mid-task) - it then trusts the stamp, skips the
# task, and fails much later with a "license-file-missing" QA error or a
# FileNotFoundError copying a missing .ipk into the package feed. Both are
# harmless to retry: clearing the stale stamp forces BitBake to redo a cheap
# packaging step instead of trusting a promise it can't keep. See
# tools/fix-stale-deploy-artifacts.py for details.
max_attempts="${BUILD_RETRY_ATTEMPTS:-3}"
bitbake_log="$(mktemp -t catplay-bitbake-log.XXXXXX)"
trap 'rm -f "${bitbake_log}"' EXIT

attempt=1
while true; do
    set +e
    bitbake "${target}" "$@" 2>&1 | tee "${bitbake_log}"
    bitbake_rc="${PIPESTATUS[0]}"
    set -e

    if [[ "${bitbake_rc}" -eq 0 ]]; then
        exit 0
    fi

    if [[ "${attempt}" -ge "${max_attempts}" ]]; then
        echo "[!] BitBake failed after ${attempt} attempt(s), giving up" >&2
        exit "${bitbake_rc}"
    fi

    if ! python3 "${root_dir}/tools/fix-stale-deploy-artifacts.py" --build-dir "${build_dir}" --from-log "${bitbake_log}"; then
        echo "[!] BitBake failed (exit ${bitbake_rc}) with an error this script doesn't know how to recover from" >&2
        exit "${bitbake_rc}"
    fi

    attempt=$((attempt + 1))
    echo "[*] Retrying BitBake after clearing stale stamps (attempt ${attempt}/${max_attempts})"
done
