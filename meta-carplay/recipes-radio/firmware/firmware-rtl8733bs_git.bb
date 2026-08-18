SUMMARY = "Bluetooth firmware for Realtek RTL8723FS"
DESCRIPTION = "Bluetooth firmware and configuration for Realtek RTL8723FS / RTL8733BS"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRC_URI = "git://github.com/radxa/rtkbt.git;protocol=https;branch=main"

SRCREV = "72ef9b75374fdde945e0a19f6aba68e13d4d426d"

S = "${WORKDIR}/git"

FILES:${PN} = " \
    /lib/firmware/rtl_bt/rtl8723fs_fw.bin \
    /lib/firmware/rtl_bt/rtl8723fs_config.bin \
"

do_install() {
    install -Dm 0644 \
        ${S}/rtkbt-firmware/lib/firmware/rtlbt/rtl8723fs_fw \
        ${D}/lib/firmware/rtl_bt/rtl8723fs_fw.bin

    install -Dm 0644 \
        ${S}/rtkbt-firmware/lib/firmware/rtlbt/rtl8723fs_config \
        ${D}/lib/firmware/rtl_bt/rtl8723fs_config.bin
}

do_configure[noexec] = "1"
do_compile[noexec] = "1"
