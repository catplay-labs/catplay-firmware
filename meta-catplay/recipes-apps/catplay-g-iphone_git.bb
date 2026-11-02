SUMMARY = "Kernel driver for CatPlay"
DESCRIPTION = "Kernel driver for CatPlay"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

# Rust application changes must not invalidate this kernel module's sstate.
CATPLAY_SRC_BUNDLE_NAME = "catplay-g-iphone.tar.gz"
inherit catplay-src-bundle-local

inherit module

MODULE_NAME = "g_iphone"
EXTRA_OEMAKE += "-C ${STAGING_KERNEL_DIR} M=${S} KCFLAGS=-O2"

FILESEXTRAPATHS:prepend := "${THISDIR}/src:"

S = "${UNPACKDIR}/usb/catplay_iap2_usb_host/g_iphone"
