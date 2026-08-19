inherit cargo pkgconfig
do_compile[network] = "1"

CARGO_DISABLE_BITBAKE_VENDORING = "1"
CARGO_BUILD_FLAGS:remove = "--frozen"
INSANE_SKIP:${PN} += "already-stripped"
CARGO = "cargo"
CARGO_INSTALL_BENCHES ??= "0"
CARGO_BENCH_ARTIFACTS ??= "${B}/cargo-bench-artifacts.list"

RUSTFLAGS ?= ""

python __anonymous() {
    dbg = d.getVar("DEBUG_BUILD")

    if dbg != "1":
        d.appendVar("RUSTFLAGS", " -C codegen-units=1")
}

do_compile:prepend() {
    export RUSTFLAGS="${RUSTFLAGS}"
}

oe_c2a_cargo_collect_benches() {
    export RUSTFLAGS="${RUSTFLAGS}"
    export RUST_TARGET_PATH="${RUST_TARGET_PATH}"
    local json_out="${B}/cargo-bench-artifacts.jsonl"
    rm -f "$json_out" "${CARGO_BENCH_ARTIFACTS}"
    bbnote "${CARGO} build ${CARGO_BUILD_FLAGS} --benches --message-format=json-render-diagnostics"
    "${CARGO}" build ${CARGO_BUILD_FLAGS} --benches --message-format=json-render-diagnostics > "$json_out"
    python3 - "$json_out" "${CARGO_BENCH_ARTIFACTS}" <<'PY'
import json
import sys

json_path, out_path = sys.argv[1], sys.argv[2]
seen = set()

with open(out_path, "w", encoding="utf-8") as out_f:
    with open(json_path, "r", encoding="utf-8") as in_f:
        for line in in_f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("reason") != "compiler-artifact":
                continue
            target = obj.get("target") or {}
            kinds = target.get("kind") or []
            executable = obj.get("executable")
            name = target.get("name")
            if "bench" not in kinds or not executable or not name:
                continue
            key = (name, executable)
            if key in seen:
                continue
            seen.add(key)
            out_f.write(f"{name}|{executable}\n")
PY
    if [ -f "${CARGO_BENCH_ARTIFACTS}" ]; then
        while IFS='|' read -r bench_name bench_path; do
            [ -n "$bench_name" ] || continue
            bbwarn "c2a-rust-app: collected bench artifact name='${bench_name}' path='${bench_path}'"
        done < "${CARGO_BENCH_ARTIFACTS}"
    else
        bbwarn "c2a-rust-app: no bench artifact list generated at ${CARGO_BENCH_ARTIFACTS}"
    fi
}

do_compile:append() {
    bbwarn "c2a-rust-app: do_compile:append entered CARGO_INSTALL_BENCHES='${CARGO_INSTALL_BENCHES}' B='${B}'"
    if [ "${CARGO_INSTALL_BENCHES}" = "1" ]; then
        bbwarn "c2a-rust-app: bench collection enabled"
        oe_c2a_cargo_collect_benches
    else
        bbwarn "c2a-rust-app: bench collection disabled"
    fi
}

do_install:append() {
    bbwarn "c2a-rust-app: do_install:append entered CARGO_INSTALL_BENCHES='${CARGO_INSTALL_BENCHES}' artifact_list='${CARGO_BENCH_ARTIFACTS}'"
    if [ "${CARGO_INSTALL_BENCHES}" = "1" ] && [ -f "${CARGO_BENCH_ARTIFACTS}" ]; then
        while IFS='|' read -r bench_name bench_path; do
            [ -n "$bench_name" ] || continue
            [ -n "$bench_path" ] || continue
            bbwarn "c2a-rust-app: considering bench install name='${bench_name}' path='${bench_path}'"
            if [ -f "$bench_path" ] && [ -x "$bench_path" ]; then
                install -d "${D}${bindir}"
                install -m755 "$bench_path" "${D}${bindir}/${BPN}-bench-${bench_name}"
                bbwarn "c2a-rust-app: installed bench '${bench_name}' to ${D}${bindir}/${BPN}-bench-${bench_name}"
            else
                bbwarn "c2a-rust-app: skipping bench '${bench_name}', path missing or not executable: ${bench_path}"
            fi
        done < "${CARGO_BENCH_ARTIFACTS}"
    elif [ "${CARGO_INSTALL_BENCHES}" = "1" ]; then
        bbwarn "c2a-rust-app: bench install requested but artifact list missing: ${CARGO_BENCH_ARTIFACTS}"
    else
        bbwarn "c2a-rust-app: bench install disabled"
    fi
}

# Enable Rust libc time64 support (unstable for now)
export RUST_LIBC_UNSTABLE_MUSL_V1_2_3 = "1"
export RUST_LIBC_UNSTABLE_GNU_TIME_BITS = "64"

export CARGO_CFG_LIBC_UNSTABLE_GNU_TIME_BITS = "64"
export CARGO_CFG_LIBC_UNSTABLE_MUSL_V1_2_3 = "1"
