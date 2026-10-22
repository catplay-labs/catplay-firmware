FILESEXTRAPATHS:prepend := "${THISDIR}/files/v851s3:"

SRC_URI:remove:v851s = "git://source.denx.de/u-boot/u-boot.git;protocol=https;branch=master;tag=v${PV} file://CVE-2026-33243.patch"
SRC_URI:prepend:v851s = "git://github.com/u-boot/u-boot.git;protocol=https;branch=master "
SRCREV:v851s = "ece349ade2973e220f524ce59e59711cc919263f"
PV:v851s = "2026.07"

SRC_URI:append:v851s = " \
    file://v851s/0001-v851s-soc-selection.patch \
    file://v851s/0002-v851s-clocks.patch \
    file://v851s/0003-v851s-pinmux.patch \
    file://v851s/0004-v851s-soc-dts.patch \
    file://board/0005-v851s-board-dts.patch \
    file://board/sun8i-v851s-wooboobox-t2.dts \
    file://watchdog/0006-v851s-enable-wdt-dt.patch \
    file://spi/0001-spi-sunxi-configure-mode-independently.patch \
    file://spi/0002-spi-sunxi-keep-bootrom-module-clock.patch \
    file://spi/0003-spi-sunxi-use-hardware-rx-only.patch \
    file://spi/0004-spi-sunxi-release-cs-on-fifo-timeout.patch \
    file://spi/0005-spi-sunxi-add-single-lane-spi-mem.patch \
    file://spi/0006-spi-sunxi-add-programmable-sampling.patch \
    file://spi/0007-spi-sunxi-add-sun20i-d1-variant.patch \
    file://dram/0002-v851s-dram-parameters.patch \
    file://nand/0003-v851s-gigadevice-clone.patch \
    file://fel/0004-sunxi-fel-preserve-r4.patch \
    file://spl/0004-sunxi-spinand-boot-device.patch \
    file://spl/0005-sunxi-spl-force-boot-device.patch \
    file://dram/0007-v851s-mbus-enable-ce-master.patch \
    file://hooks/0008-uboot-boot-failure-hooks.patch \
    file://fel/0009-sunxi-arm-fel-api-poc.patch \
    file://fel/0010-v851s-fel-recovery-flow.patch \
    file://watchdog/0011-sunxi-spl-watchdog-dt.patch \
    file://egon/0012-sunxi-egon-max-size.patch \
    file://nand/0013-spinand-spl-nodm-poc.patch \
    file://spl/0014-sunxi-spinand-spl-loader.patch \
    file://spl/0015-v851s-fel-spinand-linux-stage.patch \
    file://v851s/0016-v851s-spl-cpu-pll-888mhz.patch \
    file://spl/0017-spl-zlib-o3.patch \
    file://spl/0018-v851s-spl-dcache-poc.patch \
    file://spl/0019-spl-fit-verify-image-hashes.patch \
    file://spl/0020-spl-crc32-slicing4.patch \
    file://v851s_defconfig \
    file://v851s_defconfig_fel \
    file://v851s_defconfig_staging \
    file://v851s_defconfig_staging_proper \
"

UBOOT_MACHINE:v851s = "v851s_defconfig"
UBOOT_ARCH:v851s = "arm"

python do_patch:append:v851s() {
    import os
    import shutil
    shutil.copyfile(
        os.path.join(d.getVar("UNPACKDIR"), "v851s_defconfig"),
        os.path.join(d.getVar("S"), "configs", "v851s_defconfig"),
    )
    shutil.copyfile(
        os.path.join(d.getVar("UNPACKDIR"), "board", "sun8i-v851s-wooboobox-t2.dts"),
        os.path.join(d.getVar("S"), "arch", "arm", "dts", "sun8i-v851s-wooboobox-t2.dts"),
    )
}

# The flash image is linked for BootROM's 0x20000 eGON load address. FEL and
# staging variants run their complete eGON in place at 0x28000 instead.
do_compile:v851s() {
    set -eu
    jobs="${PARALLEL_MAKE:-8}"

    rm -rf "${B}/v851-main" "${B}/v851-fel" "${B}/v851-staging" \
        "${B}/v851-staging-proper"
    mkdir -p "${B}/v851-main" "${B}/v851-fel" "${B}/v851-staging" \
        "${B}/v851-staging-proper"
    oe_runmake -C "${S}" O="${B}/v851-main" CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    grep -q '^# CONFIG_SPL_RAM_DEVICE is not set$' "${B}/v851-main/.config"
    grep -q '^# CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE is not set$' "${B}/v851-main/.config"
    grep -q '^CONFIG_SPL_SUNXI_SPI_NAND=y$' "${B}/v851-main/.config"
    grep -q '^CONFIG_EGON_MAX_SIZE=0x1e000$' "${B}/v851-main/.config"
    grep -Fq 'CONFIG_BOOTCOMMAND="bootm 0x41000000"' "${B}/v851-main/.config"
    oe_runmake -C "${S}" O="${B}/v851-main" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-Oz NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=8 -no-pie" -j"$jobs" u-boot.bin
    oe_runmake -C "${S}" O="${B}/v851-main" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=8 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-main/spl/sunxi-spl.bin" "${B}/v851s3-u-boot-spl.egon.bin"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-fel" \
        "${B}/v851-fel/.config" "${UNPACKDIR}/v851s_defconfig_fel"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    grep -q '^CONFIG_SPL_TEXT_BASE=0x28060$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_EGON_MAX_SIZE=0x18000$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE=y$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_BOARD=y$' "${B}/v851-fel/.config"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=8 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-fel/spl/sunxi-spl.bin" "${B}/v851s3-u-boot-fel.egon.bin"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-staging" \
        "${B}/v851-staging/.config" "${UNPACKDIR}/v851s_defconfig_fel" \
        "${UNPACKDIR}/v851s_defconfig_staging"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    grep -q '^CONFIG_SPL_TEXT_BASE=0x28060$' "${B}/v851-staging/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE=y$' "${B}/v851-staging/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_SPI_NAND=y$' "${B}/v851-staging/.config"
    grep -q '^# CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_BOARD is not set$' "${B}/v851-staging/.config"
    grep -q '^# CONFIG_SUNXI_FEL_RECOVERY is not set$' "${B}/v851-staging/.config"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=8 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-staging/spl/sunxi-spl.bin" "${B}/v851s3-u-boot-staging.egon.bin"

    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-staging-proper" \
        "${B}/v851-staging-proper/.config" \
        "${UNPACKDIR}/v851s_defconfig_staging_proper"
    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    grep -Fq 'CONFIG_BOOTCOMMAND="mtd read kernel 0x41000000 0 0x800000; bootm 0x41000000"' \
        "${B}/v851-staging-proper/.config"
    grep -q '^CONFIG_BOOTDELAY=-2$' "${B}/v851-staging-proper/.config"
    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" KCFLAGS=-Oz NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=8 -no-pie" -j"$jobs" u-boot.bin
    install -Dm0644 "${B}/v851-staging-proper/u-boot.bin" \
        "${B}/v851s3-u-boot-staging-proper.bin"

    python3 - "${B}/v851-main/.config" "${B}/v851s3-u-boot-spl.egon.bin" \
        "${B}/v851-fel/.config" "${B}/v851s3-u-boot-fel.egon.bin" \
        "${B}/v851-staging/.config" "${B}/v851s3-u-boot-staging.egon.bin" <<'PY'
import pathlib, struct, sys
for config_name, name in zip(sys.argv[1::2], sys.argv[2::2]):
    config = pathlib.Path(config_name).read_text()
    limit = int(next(line.split('=', 1)[1] for line in config.splitlines()
                     if line.startswith('CONFIG_EGON_MAX_SIZE=')), 0)
    data = pathlib.Path(name).read_bytes()
    if data[4:12] != b'eGON.BT0':
        raise SystemExit(f'{name}: missing eGON header')
    declared = struct.unpack_from('<I', data, 16)[0]
    if declared != len(data) or declared % 512 or declared > limit:
        raise SystemExit(f'{name}: invalid eGON length {declared} (file {len(data)}, limit {limit})')
    check = bytearray(data)
    stored = struct.unpack_from('<I', check, 12)[0]
    struct.pack_into('<I', check, 12, 0x5f0a6c39)
    if sum(struct.unpack_from('<I', check, offset)[0]
           for offset in range(0, declared, 4)) & 0xffffffff != stored:
        raise SystemExit(f'{name}: invalid eGON checksum')
PY

}

do_deploy:v851s() {
    install -Dm0644 "${B}/v851s3-u-boot-spl.egon.bin" "${DEPLOYDIR}/v851s3-u-boot-spl.egon.bin"
    install -Dm0644 "${B}/v851s3-u-boot-fel.egon.bin" "${DEPLOYDIR}/v851s3-u-boot-fel.egon.bin"
    install -Dm0644 "${B}/v851s3-u-boot-staging.egon.bin" "${DEPLOYDIR}/v851s3-u-boot-staging.egon.bin"
    install -Dm0644 "${B}/v851-main/u-boot.bin" "${DEPLOYDIR}/v851s3-u-boot-proper.bin"
    install -Dm0644 "${B}/v851s3-u-boot-staging-proper.bin" \
        "${DEPLOYDIR}/v851s3-u-boot-staging-proper.bin"
}

do_install:v851s() {
    install -d "${D}${libdir}/u-boot-v851s3"
    for image in v851s3-u-boot-spl.egon.bin \
                 v851s3-u-boot-fel.egon.bin \
                 v851s3-u-boot-staging.egon.bin; do
        install -m0644 "${B}/$image" "${D}${libdir}/u-boot-v851s3/$image"
    done
    install -m0644 "${B}/v851-main/u-boot.bin" \
        "${D}${libdir}/u-boot-v851s3/v851s3-u-boot-proper.bin"
    install -m0644 "${B}/v851s3-u-boot-staging-proper.bin" \
        "${D}${libdir}/u-boot-v851s3/v851s3-u-boot-staging-proper.bin"
}

COMPATIBLE_MACHINE:append = "|v851s"
FILES:${PN}:append:v851s = " ${libdir}/u-boot-v851s3"
