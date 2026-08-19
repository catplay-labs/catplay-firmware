# Enable Rust libc time64 support (unstable for now)
export RUST_LIBC_UNSTABLE_MUSL_V1_2_3 = "1"
export RUST_LIBC_UNSTABLE_GNU_TIME_BITS = "64"

export CARGO_CFG_LIBC_UNSTABLE_GNU_TIME_BITS = "64"
export CARGO_CFG_LIBC_UNSTABLE_MUSL_V1_2_3 = "1"
