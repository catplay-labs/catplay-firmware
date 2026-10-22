SUMMARY = "Wi-Fi firmware for Broadcom"
DESCRIPTION = "Wi-Fi firmware for Broadcom"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

FILESEXTRAPATHS:append := "${THISDIR}/brcm:"

SRC_URI = "\
    file://4335/BCM4335A0.hcd \
    file://4335/fw_bcm4335_ag_apsta.bin \
    \
    file://4354/bcm4350.hcd \
    file://4354/fw_bcm4354a1_ag_apsta.bin \
    \
    file://4358/BCM4358A3.hcd \
    file://4358/fw_bcm4358_ag_apsta.bin \
"

PACKAGES = "${PN}-bcm4335 ${PN}-bcm4354 ${PN}-bcm4358"

FILES:${PN}-bcm4335 = " \
  /lib/firmware/brcm/BCM4335A0.hcd \
  /lib/firmware/brcm/fw_bcm4335_ag_apsta.bin \
"

FILES:${PN}-bcm4354 = " \
  /lib/firmware/brcm/bcm4350.hcd \
  /lib/firmware/brcm/fw_bcm4354a1_ag_apsta.bin \
"

FILES:${PN}-bcm4358 = " \
  /lib/firmware/brcm/BCM4358A3.hcd \
  /lib/firmware/brcm/fw_bcm4358_ag_apsta.bin \
"

do_install() {
    install -Dm 644 ${UNPACKDIR}/4335/BCM4335A0.hcd ${D}/lib/firmware/brcm/BCM4335A0.hcd
    install -Dm 644 ${UNPACKDIR}/4335/fw_bcm4335_ag_apsta.bin ${D}/lib/firmware/brcm/fw_bcm4335_ag_apsta.bin

    install -Dm 644 ${UNPACKDIR}/4354/bcm4350.hcd ${D}/lib/firmware/brcm/bcm4350.hcd
    install -Dm 644 ${UNPACKDIR}/4354/fw_bcm4354a1_ag_apsta.bin ${D}/lib/firmware/brcm/fw_bcm4354a1_ag_apsta.bin

    install -Dm 644 ${UNPACKDIR}/4358/BCM4358A3.hcd ${D}/lib/firmware/brcm/BCM4358A3.hcd
    install -Dm 644 ${UNPACKDIR}/4358/fw_bcm4358_ag_apsta.bin ${D}/lib/firmware/brcm/fw_bcm4358_ag_apsta.bin
}


