SUMMARY = "CatPlay V851S3 device tree"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://v851s3-aic8800d80.dts;beginline=1;endline=1;md5=fcab174c20ea2e2bc0be64b493708266"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI = "file://v851s3-aic8800d80.dts"

DT_FILES = "v851s3-aic8800d80.dts"
inherit devicetree_c2a

DT_INCLUDE:append = " \
 ${STAGING_KERNEL_DIR}/arch/arm/boot/dts \
 ${STAGING_KERNEL_DIR}/arch/arm/boot/dts/allwinner \
"
COMPATIBLE_MACHINE = "(v851s3-aic8800d80)"
