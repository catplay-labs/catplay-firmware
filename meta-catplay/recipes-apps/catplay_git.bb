SUMMARY = "CatPlay - implementation of CarPlay protocol"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit catplay-src-bundle-local

RDEPENDS:${PN} += "catplay-g-iphone"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

S = "${WORKDIR}"

CARGO_SRC_DIR = "c2a/catplay_c2a"
#EXCLUDE_FROM_SHLIBS = "1"

# pixman
DEPENDS += "upx-native ffmpeg-mini libsdl2 alsa-lib libusb1 x264 libopus libopusenc libfdk-aac pixman libyuv dbus openssl-slim libjpeg-turbo"

PACKAGE_ARCH:ingenic-x1600 = "ingenic-x1600"

CATPLAY_DEBUG_BUILD ?= "1"
CATPLAY_DEBUG_SYMBOLS ?= "0"

DEBUG_BUILD = "${CATPLAY_DEBUG_BUILD}"

RUSTFLAGS = " -C link-arg=-Wl,--gc-sections "
RUSTFLAGS:append:armv7e = " -C link-arg=-no-pie"
#RUSTFLAGS:append:mipsarch = " -C target-feature=+strict-align"

# Fix static openssl linking
RUSTFLAGS:append:mipsel = " -C link-arg=-latomic"
RUSTFLAGS:append:riscv32 = " -C link-arg=-latomic"

INHIBIT_PACKAGE_STRIP = "${CATPLAY_DEBUG_SYMBOLS}"
inherit c2a-rust-app

require catplay-svc.inc
require ffmpeg-hack.inc
