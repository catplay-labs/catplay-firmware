FILESEXTRAPATHS:append := "${THISDIR}/files:"

SRC_URI += "file://woo.dts"

DT_FILES:woo = "woo.dts"
DT_FILES:woo-recov = "woo.dts"

inherit devicetree_c2a

DT_INCLUDE:append = " \
    ${STAGING_KERNEL_DIR}/arch/${ARCH}/boot/dts \
    ${STAGING_KERNEL_DIR}/arch/${ARCH}/boot/dts/allwinner \
"

C2A_DTC_INTS:append = " CARLINKIT_UBOOT_OFFSET_HEX CARLINKIT_UBOOT_SIZE_HEX CARLINKIT_UBOOT_CSF_OFFSET_HEX CARLINKIT_UBOOT_CSF_SIZE_HEX "
C2A_DTC_INTS:append = " CARLINKIT_UBOOT_ENV_OFFSET_HEX CARLINKIT_UBOOT_ENV_SIZE_HEX"
COMPATIBLE_MACHINE = "|woo-recov"
