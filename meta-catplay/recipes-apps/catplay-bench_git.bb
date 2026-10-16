SUMMARY = "CatPlay crypto benchmarks"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit catplay-src-bundle-local

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

S = "${UNPACKDIR}"

CARGO_SRC_DIR = "core/catplay_hap"
DEPENDS += "openssl-slim"

PACKAGE_ARCH:ingenic-x1600 = "ingenic-x1600"

CATPLAY_DEBUG_BUILD = "0"
CATPLAY_DEBUG_SYMBOLS = "0"

DEBUG_BUILD = "${CATPLAY_DEBUG_BUILD}"

RUSTFLAGS = " -C link-arg=-Wl,--gc-sections "

# Fix static openssl linking
RUSTFLAGS:append:mipsel = " -C link-arg=-latomic"
RUSTFLAGS:append:riscv32 = " -C link-arg=-latomic"

INHIBIT_PACKAGE_STRIP = "${CATPLAY_DEBUG_SYMBOLS}"
CARGO_INSTALL_BENCHES = "1"
inherit c2a-rust-app
