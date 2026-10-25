SUMMARY = "Carlinkit OTA lib - OTA utilities for Carlinkit IMX6UL devices"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

FILESEXTRAPATHS:prepend := "${THISDIR}:"
S = "${UNPACKDIR}"

SRC_URI += "\
    file://src/imx/mod.rs \
    file://src/imx/boot.rs \
    file://src/imx/hwid/hwid_read_legacy.rs \
    file://src/imx/hwid/hwid_read.rs \
    file://src/imx/hwid/mod.rs \
    file://src/imx/signer/env.rs \
    file://src/imx/signer/shellcode.rs \
    file://src/imx/signer/mod.rs \
    \
    file://src/mini/crc.rs \
    file://src/mini/md5.rs \
    file://src/mini/mod.rs \
    file://src/mini/ota_packer.rs \
    file://src/mini/ota.rs \
    file://src/mini/ota_nand_flasher.rs \
    \
    file://src/nostd/alloc.rs \
    file://src/nostd/args.rs \
    file://src/nostd/modules_info.rs \
    file://src/nostd/sanitizer.rs \
    file://src/nostd/small_fd.rs \
    file://src/nostd/mod.rs \
    \
    file://src/flash.rs \
    file://src/lib.rs \
    file://src/main.rs \
    file://src/modprobe_util.rs \
    file://src/system_util.rs \
    file://src/radio.rs \
    \
    file://src/boot/boot_persist.rs \
    file://src/boot/persist_config.rs \
    file://src/boot/catplay/catplay_boot.rs \
    file://src/boot/catplay/catplay_formatter.rs \
    file://src/boot/catplay/mod.rs \
    file://src/boot/hostapd/hostapd_boot.rs \
    file://src/boot/hostapd/hostapd_formatter.rs \
    file://src/boot/hostapd/hostapd_templates.rs \
    file://src/boot/hostapd/mod.rs \
    file://src/boot/boot_platform.rs \
    file://src/boot/boot_radio.rs \
    file://src/boot/boot_tail.rs \
    file://src/boot/boot_ultra.rs \
    file://src/boot/mod.rs \
    file://src/boot/recovery_gadget.rs \
    file://src/boot/sysctl.rs \
    file://src/boot/udhcpd/mod.rs \
    file://src/boot/udhcpd/udhcpd_boot.rs \
    file://src/boot/udhcpd/udhcpd_formatter.rs \
    \
    file://src/telnet/mod.rs \
    \
    file://src/web/mod.rs \
    file://src/web/props.rs \
    \
    file://build.rs \
    file://Cargo.toml \
    file://Cargo.lock \
"

#RUSTFLAGS = "-C link-args=-lc -C target-feature=+crt-static"
RUSTFLAGS:append:libc-musl = " -C link-arg=-static -C link-arg=-Wl,-Bstatic -C link-arg=-lc -C target-feature=+crt-static -C link-arg=-Wl,--gc-sections"
RUSTFLAGS:append = " -C link-arg=-lc"

# undefined symbol: __sync_synchronize
RUSTFLAGS:append:armv5 = " -C link-arg=-lgcc"
DEPENDS:append:armv5 = "gcc-runtime"

#DEBUG_BUILD = "1"
inherit c2a-rust-app
