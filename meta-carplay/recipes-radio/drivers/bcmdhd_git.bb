SUMMARY = "Broadcom vendor driver for BCM4335/4354/4358"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRCBRANCH = "5.15.170"
BCMDHD_SRC = "git://github.com/CoreELEC/ap6xxx-aml;protocol=https"

SRC_URI = " \
    ${BCMDHD_SRC};branch=${SRCBRANCH} \
    file://0001-fixes.patch \
    file://0002-fixes.patch \
"

SRCREV = "b2541e247f88e84873041cad9d2605aa4202d352"

S = "${UNPACKDIR}/${BP}/bcmdhd.101.10.591.x"
B = "${WORKDIR}/build"

inherit module
require module-extra.inc

INSANE_SKIP:${PN} += "buildpaths"

EXTRA_OEMAKE += "KERNELDIR=${STAGING_KERNEL_BUILDDIR} -C ${STAGING_KERNEL_BUILDDIR} M=${S}"
EXTRA_OEMAKE += "CONFIG_BCMDHD=m"
