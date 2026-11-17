# Native USB recovery plus separate NOR loader artifacts for staging tests.
SRC_URI:remove:ingenic-x1600 = "${C2A_UBOOT_REPLACED_URI}"
SRC_URI:prepend:ingenic-x1600 = "${C2A_UBOOT_GIT_URI} "
SRCREV:ingenic-x1600 = "${C2A_UBOOT_SRCREV}"
PV:ingenic-x1600 = "${C2A_UBOOT_VERSION}"
SRC_URI:append:ingenic-x1600 = " ${C2A_UBOOT_COMMON_FILES} file://x1600/cpu/0059-mips-x1600-platform-and-ROM-entry.patch file://x1600/dram/0060-mips-x1600-InnoPHY-DDR-init.patch file://x1600/spi/0061-spi-ingenic-shared-PIO-DMA-SFC.patch file://x1600/spl/0062-mips-x1600-NOR-TPL-and-SPL-Linux-boot.patch file://x1600/board/0063-mips-x1600-AX1800-board.patch file://x1600/watchdog/0064-watchdog-ingenic-early-and-DM-driver.patch file://x1600/timer/0065-timer-ingenic-X1600-OST.patch file://x1600/clk/0066-clk-ingenic-X1600-SFC.patch file://x1600/pinctrl/0067-pinctrl-ingenic-X1600-SFC.patch file://x1600/x1600_ax1800_defconfig file://x1600/x1600_ax1800_defconfig_fel file://x1600/x1600_ax1800_defconfig_staging file://x1600/x1600_ax1800_defconfig_staging_proper file://x1600/mk-nor-image.py"
COMPATIBLE_MACHINE:append:ingenic-x1600 = "|x1600"
DEPENDS:append:ingenic-x1600 = " openssl-native"
UBOOT_MACHINE:ingenic-x1600 = "x1600_ax1800_defconfig"
UBOOT_ARCH:ingenic-x1600 = "mips"
UBOOT_BINARY:ingenic-x1600 = "u-boot.bin"

python do_patch:append:ingenic-x1600() {
    import pathlib
    import shutil
    unpack = pathlib.Path(d.getVar("UNPACKDIR")) / "x1600"
    source = pathlib.Path(d.getVar("S")) / "configs"
    for name in ("x1600_ax1800_defconfig",):
        shutil.copyfile(unpack / name, source / name)
}

do_configure:ingenic-x1600() {
    for variant in main fel staging staging-proper; do
        case "$variant" in
            main) output="${B}/flash"; overlay="" ;;
            fel) output="${B}"; overlay=x1600_ax1800_defconfig_fel ;;
            staging) output="${B}/staging"; overlay=x1600_ax1800_defconfig_staging ;;
            staging-proper) output="${B}/staging-proper"; overlay=x1600_ax1800_defconfig_staging_proper ;;
        esac
        install -Dm0644 "${S}/configs/x1600_ax1800_defconfig" "$output/.config"
        if [ -n "$overlay" ]; then
            "${S}/scripts/kconfig/merge_config.sh" -m -O "$output" \
                "$output/.config" "${UNPACKDIR}/x1600/$overlay"
        fi
        oe_runmake -C "${S}" O="$output" olddefconfig
    done
}

do_compile:ingenic-x1600() {
    unset LDFLAGS CFLAGS CPPFLAGS
    oe_runmake -C "${S}" O="${B}" tpl/u-boot-tpl.bin u-boot.bin
    test $(stat -c %s "${B}/tpl/u-boot-tpl.bin") -le 20480
    oe_runmake -C "${S}" O="${B}/staging-proper" u-boot.bin
    oe_runmake -C "${S}" O="${B}/staging" tpl/u-boot-tpl.bin
    test $(stat -c %s "${B}/staging/tpl/u-boot-tpl.bin") -le 20480
    cd "${B}"

    # Keep the USB recovery build separate from the DRAM-linked NOR SPL.
    oe_runmake -C "${S}" O="${B}/flash" tpl/u-boot-tpl.bin spl/u-boot-spl.bin
    test $(stat -c %s "${B}/flash/tpl/u-boot-tpl.bin") -le 20480
    ./tools/mkimage -A mips -O u-boot -T firmware -C none \
        -a 0x81800000 -e 0x81800000 -n "X1600 flash SPL" \
        -d "${B}/flash/spl/u-boot-spl.bin" "${B}/flash/spl.uImage"
    test $(stat -c %s "${B}/flash/spl.uImage") -le 110592
    ${PYTHON} "${UNPACKDIR}/x1600/mk-nor-image.py" \
        --tpl "${B}/flash/tpl/u-boot-tpl.bin" \
        --spl "${B}/flash/spl.uImage" \
        --output "${B}/flash/u-boot-flash.bin"
}

do_install:ingenic-x1600() {
    install -d "${D}${datadir}/u-boot-x1600"
    install -m0644 "${B}/tpl/u-boot-tpl.bin" "${D}${datadir}/u-boot-x1600/"
    install -m0644 "${B}/u-boot.bin" "${D}${datadir}/u-boot-x1600/u-boot-proper.bin"
    install -m0644 "${B}/staging-proper/u-boot.bin" "${D}${datadir}/u-boot-x1600/u-boot-staging-proper.bin"
    install -d "${D}${datadir}/u-boot-x1600/flash"
    install -m0644 "${B}/flash/spl.uImage" "${B}/flash/u-boot-flash.bin" "${D}${datadir}/u-boot-x1600/flash/"
}

do_deploy:ingenic-x1600() {
    install -Dm0644 "${B}/tpl/u-boot-tpl.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-tpl.bin"
    install -Dm0644 "${B}/u-boot.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.bin"
    install -Dm0644 "${B}/staging-proper/u-boot.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.bin"
    install -m0644 "${B}/tpl/u-boot-tpl.map" "${DEPLOYDIR}/${MACHINE}-u-boot-tpl.map"
    install -m0644 "${B}/staging-proper/u-boot.map" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.map"
    install -m0644 "${B}/staging-proper/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.config"
    install -m0644 "${B}/u-boot.map" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.map"
    install -m0644 "${B}/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-recovery.config"
    install -m0644 "${B}/staging/tpl/u-boot-tpl.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-flash-tpl.bin"
    install -m0644 "${B}/flash/spl.uImage" "${DEPLOYDIR}/${MACHINE}-u-boot-flash-spl.uImage"
    install -m0644 "${B}/flash/u-boot-flash.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-flash.bin"
    install -m0644 "${B}/flash/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-flash.config"
    install -m0644 "${B}/staging/tpl/u-boot-tpl.map" "${DEPLOYDIR}/${MACHINE}-u-boot-flash-tpl.map"
    install -m0644 "${B}/flash/spl/u-boot-spl.map" "${DEPLOYDIR}/${MACHINE}-u-boot-flash-spl.map"
}
FILES:${PN}:append:ingenic-x1600 = " ${datadir}/u-boot-x1600"
