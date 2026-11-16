SUMMARY = "Open-source OpenSBI firmware for Allwinner V821"
DESCRIPTION = "Builds upstream OpenSBI with an Andes A27L2 V821 platform override."
HOMEPAGE = "https://github.com/riscv-software-src/opensbi"
LICENSE = "BSD-2-Clause"
LIC_FILES_CHKSUM = "file://COPYING.BSD;md5=42dd9555eb177f35150cf9aa240b61e5"

inherit deploy

PROVIDES += "virtual/opensbi"
DEPENDS += "gzip-native"

SRC_URI = "git://github.com/riscv-software-src/opensbi.git;branch=master;protocol=https \
           file://0001-platform-generic-add-Allwinner-V821-support.patch \
           file://0002-opensbi-optimize.patch \
           file://0003-opensbi-timer-hazards.patch \
           file://0004-opensbi-legacy-brandy-fw-base-ldS.patch \
"
SRCREV = "337c23dd66b821ac04a0f7bea313e7e7b30ecc49"
PV = "1.7+git${SRCPV}"
PR = "r3"

B = "${WORKDIR}/build"

OPENSBI_INSTALL_NAME = "opensbi-v821.bin"

PACKAGE_ARCH = "${MACHINE_ARCH}"
COMPATIBLE_MACHINE = "(woo|woo-recov)"

do_configure[noexec] = "1"

EXTRA_OEMAKE = " \
    PLATFORM=generic \
    PLATFORM_DEFCONFIG=v821_defconfig \
    PLATFORM_RISCV_XLEN=32 \
    PLATFORM_RISCV_ABI=ilp32d \
    PLATFORM_RISCV_ISA=rv32imafdc_zicsr_zifencei \
    OPENSBI_OPTIMIZE='-Os -fomit-frame-pointer -foptimize-sibling-calls -Wno-error=unused -Wno-unused-function -Wno-unused-variable' \
    FW_TEXT_START=0x80fc0000 \
    CROSS_COMPILE=${TARGET_PREFIX} \
    O=${B} \
"
EXTRA_OEMAKE:append:toolchain-clang = " LLVM=y"

do_compile() {
    oe_runmake -C "${S}"
}

do_install() {
    install -d "${D}${nonarch_base_libdir}/firmware/sunxi-opensbi"
    install -m 0644 "${B}/platform/generic/firmware/fw_dynamic.bin" \
        "${D}${nonarch_base_libdir}/firmware/sunxi-opensbi/${OPENSBI_INSTALL_NAME}"
}

FILES:${PN} += "${nonarch_base_libdir}/firmware/sunxi-opensbi"

do_deploy() {
    install -d "${DEPLOYDIR}/sunxi-opensbi"
    install -m 0644 "${B}/platform/generic/firmware/fw_dynamic.bin" \
        "${DEPLOYDIR}/sunxi-opensbi/${OPENSBI_INSTALL_NAME}"
    gzip -9 -c "${B}/platform/generic/firmware/fw_dynamic.bin" \
        > "${DEPLOYDIR}/sunxi-opensbi/${OPENSBI_INSTALL_NAME}.gz"
}

addtask do_deploy before do_build after do_install
