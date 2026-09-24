LETUX_BRANCH = "old-ingenic-x1600-v4"
SRCREV = "96d2b0b0ddcc50a690d942812e5fa79275a55813"
PACKAGE_ARCH = "${MACHINE_ARCH}"
PROVIDES = "virtual/bootloader"

LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRC_URI = "git://github.com/goldelico/letux-uboot.git;branch=${LETUX_BRANCH};protocol=https"

# U-Boot is configured per board by do_compile below; it is not an Autotools
# project and has no configure script.
inherit deploy

LETUX_UBOOT_BOARDS ?= ""
LETUX_UBOOT_BUILD_TARGETS = "spl/u-boot-spl.bin"

TOOLCHAIN:forcevariable = "gcc"

DEPENDS = "libgcc"
PACKAGE_ARCH = "${MACHINE_ARCH}"

do_compile() {
    boards="$(printf '%s' "${LETUX_UBOOT_BOARDS}" | tr -d '[:space:]')"
    [ -z "${boards}" ] && { bbwarn "LETUX_UBOOT_BOARDS is empty, skipping"; return 0; }

    build_targets="$(printf '%s' "${LETUX_UBOOT_BUILD_TARGETS}" | tr -d '[:space:]')"
    [ -z "${build_targets}" ] && { bbwarn "LETUX_UBOOT_BUILD_TARGETS is empty, skipping"; return 0; }

    unset LDFLAGS CFLAGS

    libgcc_a="$(${CC} -print-libgcc-file-name)"
    [ -f "${libgcc_a}" ] || bbfatal "libgcc.a not found: ${libgcc_a}"
    libgcc_dir="$(dirname "${libgcc_a}")"

    for board in ${LETUX_UBOOT_BOARDS}; do
        O="${B}/${board}"
        mkdir -p "${O}"

        oe_runmake -C "${S}" O="${O}" CROSS_COMPILE=${TARGET_PREFIX} distclean
        oe_runmake -C "${S}" O="${O}" CROSS_COMPILE=${TARGET_PREFIX} "${board}_config"

        for target in ${LETUX_UBOOT_BUILD_TARGETS}; do
            oe_runmake -C "${S}" O="${O}" CROSS_COMPILE=${TARGET_PREFIX} \
                USE_PRIVATE_LIBGCC="${libgcc_dir}" \
                "${O}/${target}"
        done

        bbnote "Artifacts for ${board}:"
        find "${O}" -maxdepth 3 -type f | sort | while IFS= read -r f; do
            bbnote "  ${f}"
        done
    done
}

do_install() {
    install -d "${D}${libdir}"

    for board in ${LETUX_UBOOT_BOARDS}; do
        O="${B}/${board}"

        for target in ${LETUX_UBOOT_BUILD_TARGETS}; do
            src="${O}/${target}"
            [ -f "${src}" ] || bbfatal "Missing build artifact: ${src}"

            filename="$(basename "${target}")"
            install -Dm 0644 "${src}" "${D}${libdir}/${PN}/${board}_${filename}"
        done
    done
}

do_deploy() {
    install -d "${DEPLOYDIR}"

    for board in ${LETUX_UBOOT_BOARDS}; do
        O="${B}/${board}"

        for target in ${LETUX_UBOOT_BUILD_TARGETS}; do
            src="${O}/${target}"
            [ -f "${src}" ] || bbfatal "Missing build artifact for deploy: ${src}"

            filename="$(basename "${target}")"
            install -m 0644 "${src}" "${DEPLOYDIR}/${board}_${filename}"
        done
    done
}

addtask deploy after do_compile before do_build
