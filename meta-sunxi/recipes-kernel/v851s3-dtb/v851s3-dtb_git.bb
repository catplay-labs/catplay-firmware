SUMMARY = "CatPlay V851S3 device tree"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${V851S3_DTS};beginline=1;endline=1;md5=fcab174c20ea2e2bc0be64b493708266"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
V851S3_DTS = "v851s3-aic8800d80.dts"
V851S3_DTS:v553 = "v553-aic8800d80.dts"
SRC_URI = "file://${V851S3_DTS}"

DT_FILES = "${V851S3_DTS}"
inherit devicetree_c2a

DT_INCLUDE:append = " \
 ${STAGING_KERNEL_DIR}/arch/arm/boot/dts \
 ${STAGING_KERNEL_DIR}/arch/arm/boot/dts/allwinner \
"
COMPATIBLE_MACHINE = "(v851s3-aic8800d80)"
