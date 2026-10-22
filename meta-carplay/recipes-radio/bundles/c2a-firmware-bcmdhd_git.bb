C2A_FIRMWARE_STAGE = "linux-firmware firmware-brcm"
C2A_MODULES_STAGE = "bcmdhd"
DRIVER_PATH = "drivers/extra/bcmdhd_sdio.ko"
#DRIVER_HELPER_PATH = "drivers/extra/dhd_static_buf_sdio.ko"
DRIVER_HELPER_PATH = "drivers/extra/dhd_static_buf.ko"

PACKAGES =+ "${PN}-bcm4335 ${PN}-bcm4354 ${PN}-bcm4358"

require bundle.inc
require staging-kernel-modules.inc
require staging-linux-firmware.inc

INSANE_SKIP:${PN} += "buildpaths"

FILES:${PN} = "/lib/modules/*/kernel/${DRIVER_PATH} /lib/modules/*/kernel/${DRIVER_HELPER_PATH}"

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

RDEPENDS:${PN}-bcm4335 += "${PN}"
RDEPENDS:${PN}-bcm4354 += "${PN}"
RDEPENDS:${PN}-bcm4358 += "${PN}"
