
C2A_FIRMWARE_STAGE = "linux-firmware firmware-rtl8733bs"
C2A_MODULES_STAGE = "rtl8733bs"
DRIVER_PATH = "drivers/extra/8733bs.ko"

FILES:${PN} = "\
    /lib/modules/*/kernel/${DRIVER_PATH} \
    /lib/firmware/rtl_bt/rtl8723fs_fw.bin \
    /lib/firmware/rtl_bt/rtl8723fs_config.bin \
"

require bundle.inc
require staging-kernel-modules.inc
require staging-linux-firmware.inc

PR = "r4"