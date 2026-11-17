FILESEXTRAPATHS:prepend := "${THISDIR}/files/v851s3:${THISDIR}/files:"

# Both sunxi ports use one upstream revision and the same source patchset.
C2A_UBOOT_GIT_URI = "git://github.com/u-boot/u-boot.git;protocol=https;branch=main"
C2A_UBOOT_SRCREV = "3cb2cbbc75aa7fe2391f662607d6b2f46c1292a7"
C2A_UBOOT_VERSION = "2027.01+git"
C2A_UBOOT_REPLACED_URI = "git://source.denx.de/u-boot/u-boot.git;protocol=https;branch=master;tag=v${PV} file://CVE-2026-33243.patch"

SRC_URI:remove:v851s = "${C2A_UBOOT_REPLACED_URI}"
SRC_URI:remove:v821 = "${C2A_UBOOT_REPLACED_URI} file://shared/0041-spl-zlib-o3.patch"
SRC_URI:prepend:v851s = "${C2A_UBOOT_GIT_URI} "
SRC_URI:prepend:v821 = "${C2A_UBOOT_GIT_URI} "
SRCREV:v851s = "${C2A_UBOOT_SRCREV}"
SRCREV:v821 = "${C2A_UBOOT_SRCREV}"
PV:v851s = "${C2A_UBOOT_VERSION}"
PV:v821 = "${C2A_UBOOT_VERSION}"
SRC_URI_RISCV:v821 = ""

C2A_UBOOT_COMMON_FILES = "${C2A_UBOOT_SHARED_FILES} ${C2A_UBOOT_V851_FILES} ${C2A_UBOOT_V821_FILES}"
SRC_URI:append:v851s = " ${C2A_UBOOT_COMMON_FILES}"
SRC_URI:append:v821 = " ${C2A_UBOOT_COMMON_FILES}"

C2A_UBOOT_SHARED_FILES = " \
    file://shared/mips/0069-mips-spl-fit-fdt-mapping.patch \
    file://shared/mips/0070-mips-spl-falcon-linux-handoff.patch \
    file://shared/mips/0072-mips-x1600-DMA-bridge-drain.patch \
    file://shared/spi/0073-spi-mem-nodm-allow-controller-operation-sizing.patch \
    file://shared/0043-spl-fit-verify-image-hashes.patch \
    file://shared/0001-sunxi-egon-max-size.patch \
    file://shared/0002-sunxi-riscv-egon-build.patch \
    file://shared/0003-uboot-boot-failure-hooks.patch \
    file://shared/riscv/0004-riscv-fix-single-hart-initialization.patch \
    file://shared/0005-timer-without-cpu-device.patch \
    file://shared/0006-v821-spif-controller.patch \
    file://shared/0007-spi-nor-puya-support.patch \
    file://shared/0008-spl-fix-cyclic-gd-move.patch \
    file://shared/riscv/0009-riscv-spl-lto-global-data.patch \
    file://shared/riscv/0010-riscv-bootm-opensbi.patch \
    file://shared/0011-image-initrd-high-word-size.patch \
    file://shared/riscv/0012-andes-l2-enable-dm-events.patch \
    file://shared/riscv/0013-riscv-phase-aware-sbi-reset.patch \
    file://shared/riscv/0014-riscv-spl-dtb-alignment.patch \
    file://shared/nand/0030-v851s-gigadevice-clone.patch \
    file://shared/nand/0038-spinand-spl-nodm-poc.patch \
    file://shared/0041-spl-zlib-o3.patch \
    file://shared/0044-spl-crc32-slicing4.patch \
    file://shared/nor/0060-spi-nor-spl-manufacturers.patch \
    file://shared/spl/0071-tpl-legacy-uimage-loader.patch \
"

C2A_UBOOT_V851_FILES = " \
    file://v851s/0015-v851s-soc-selection.patch \
    file://v851s/0016-v851s-clocks.patch \
    file://v851s/0017-v851s-pinmux.patch \
    file://v851s/0018-v851s-soc-dts.patch \
    file://board/0019-v851s-board-dts.patch \
    file://board/sun8i-v851s-wooboobox-t2.dts \
    file://watchdog/0020-v851s-enable-wdt-dt.patch \
    file://spi/0021-spi-sunxi-configure-mode-independently.patch \
    file://spi/0022-spi-sunxi-keep-bootrom-module-clock.patch \
    file://spi/0023-spi-sunxi-use-hardware-rx-only.patch \
    file://spi/0024-spi-sunxi-release-cs-on-fifo-timeout.patch \
    file://spi/0025-spi-sunxi-add-single-lane-spi-mem.patch \
    file://spi/0026-spi-sunxi-add-programmable-sampling.patch \
    file://spi/0027-spi-sunxi-add-sun20i-d1-variant.patch \
    file://dram/0028-v851s-dram-parameters.patch \
    file://dram/0029-ram-sunxi-follow-V85x-boot0-remapping-and-pad-hold-policy.patch \
    file://fel/0031-sunxi-fel-preserve-r4.patch \
    file://spl/0032-sunxi-spinand-boot-device.patch \
    file://spl/0033-sunxi-spl-force-boot-device.patch \
    file://dram/0034-v851s-mbus-enable-ce-master.patch \
    file://fel/0035-sunxi-arm-fel-api-poc.patch \
    file://fel/0036-v851s-fel-recovery-flow.patch \
    file://watchdog/0037-sunxi-spl-watchdog-dt.patch \
    file://spl/0039-sunxi-spinand-spl-loader.patch \
    file://spl/0040-v851s-fel-spinand-linux-stage.patch \
    file://spl/0042-v851s-spl-dcache-poc.patch \
    file://v851s_defconfig \
    file://v851s_defconfig_fel \
    file://v851s_defconfig_staging \
    file://v851s_defconfig_staging_proper \
"


C2A_UBOOT_V821_FILES = " \
    file://v821/soc/0045-sunxi-common-soc-selection.patch \
    file://v821/cpu/0046-v821-riscv-startup.patch \
    file://v821/cpu/0047-sunxi-fel-recovery.patch \
    file://v821/cpu/0048-sunxi-board-utils.patch \
    file://v821/ccu/0049-v821-clock-reset-controller.patch \
    file://v821/pinctrl/0050-v821-pinmux.patch \
    file://v821/soc/0051-v821-soc-dts.patch \
    file://v821/watchdog/0052-v821-reset.patch \
    file://v821/watchdog/0053-sunxi-v821-dm-watchdog.patch \
    file://v821/board/0054-wooboobox-v821b-board.patch \
    file://v821/dram/0055-ram-sunxi-use-sun20i-driver-for-v821.patch \
    file://v821/cpu/0057-andes-l2-v821-revision-prefetch.patch \
    file://v821/egon/0058-v821-egon-1k-alignment.patch \
    file://v821/v821_defconfig \
    file://v821/v821_defconfig_fel \
    file://v821/v821_defconfig_staging \
    file://v821/v821_defconfig_staging_proper \
"

SRC_URI:append:v553 = " file://v553_overlay"

V851_UBOOT_SOC_OVERLAY = ""
V851_UBOOT_SOC_OVERLAY:v553 = "${UNPACKDIR}/v553_overlay"

# Apply the SoC fragment last, after each artifact's boot-flow fragments.
v851_apply_soc_overlay() {
    if [ -n "${V851_UBOOT_SOC_OVERLAY}" ]; then
        "${S}/scripts/kconfig/merge_config.sh" -m -O "$1" \
            "$1/.config" "${V851_UBOOT_SOC_OVERLAY}"
        oe_runmake -C "${S}" O="$1" CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    fi
}

UBOOT_MACHINE:v851s = "v851s_defconfig"
UBOOT_ARCH:v851s = "arm"

python c2a_uboot_install_common_files() {
    import pathlib
    import shutil
    unpack = pathlib.Path(d.getVar("UNPACKDIR"))
    source = pathlib.Path(d.getVar("S"))
    for name in ("v851s_defconfig", "v821/v821_defconfig"):
        shutil.copyfile(unpack / name, source / "configs" / pathlib.Path(name).name)
    shutil.copyfile(
        unpack / "board/sun8i-v851s-wooboobox-t2.dts",
        source / "arch/arm/dts/sun8i-v851s-wooboobox-t2.dts",
    )
}

python do_patch:append:v851s() {
    bb.build.exec_func("c2a_uboot_install_common_files", d)
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
    v851_apply_soc_overlay "${B}/v851-main"
    grep -q '^# CONFIG_SPL_RAM_DEVICE is not set$' "${B}/v851-main/.config"
    grep -q '^# CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE is not set$' "${B}/v851-main/.config"
    grep -q '^CONFIG_SPL_SUNXI_SPI_NAND=y$' "${B}/v851-main/.config"
    grep -q '^CONFIG_EGON_MAX_SIZE=0x1e000$' "${B}/v851-main/.config"
    grep -Fq 'CONFIG_BOOTCOMMAND="bootm 0x41000000"' "${B}/v851-main/.config"
    oe_runmake -C "${S}" O="${B}/v851-main" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-Oz NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" -j"$jobs" u-boot.bin
    oe_runmake -C "${S}" O="${B}/v851-main" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-main/spl/sunxi-spl.bin" "${B}/${MACHINE}-u-boot-spl.egon.bin"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-fel" \
        "${B}/v851-fel/.config" "${UNPACKDIR}/v851s_defconfig_fel"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    v851_apply_soc_overlay "${B}/v851-fel"
    grep -q '^CONFIG_SPL_TEXT_BASE=0x28060$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_EGON_MAX_SIZE=0x18000$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE=y$' "${B}/v851-fel/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_BOARD=y$' "${B}/v851-fel/.config"
    oe_runmake -C "${S}" O="${B}/v851-fel" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-fel/spl/sunxi-spl.bin" "${B}/${MACHINE}-u-boot-fel.egon.bin"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-staging" \
        "${B}/v851-staging/.config" "${UNPACKDIR}/v851s_defconfig_fel" \
        "${UNPACKDIR}/v851s_defconfig_staging"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    v851_apply_soc_overlay "${B}/v851-staging"
    grep -q '^CONFIG_SPL_TEXT_BASE=0x28060$' "${B}/v851-staging/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE=y$' "${B}/v851-staging/.config"
    grep -q '^CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_SPI_NAND=y$' "${B}/v851-staging/.config"
    grep -q '^# CONFIG_SPL_SUNXI_FORCE_BOOT_DEVICE_BOARD is not set$' "${B}/v851-staging/.config"
    grep -q '^# CONFIG_SUNXI_FEL_RECOVERY is not set$' "${B}/v851-staging/.config"
    oe_runmake -C "${S}" O="${B}/v851-staging" CROSS_COMPILE="${TARGET_PREFIX}" \
        KCFLAGS=-O3 NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" -j"$jobs" spl/sunxi-spl.bin
    install -Dm0644 "${B}/v851-staging/spl/sunxi-spl.bin" "${B}/${MACHINE}-u-boot-staging.egon.bin"

    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" v851s_defconfig
    "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v851-staging-proper" \
        "${B}/v851-staging-proper/.config" \
        "${UNPACKDIR}/v851s_defconfig_staging_proper"
    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" olddefconfig
    v851_apply_soc_overlay "${B}/v851-staging-proper"
    grep -Fq 'CONFIG_BOOTCOMMAND="mtd read kernel 0x41000000 0 0x800000; bootm 0x41000000"' \
        "${B}/v851-staging-proper/.config"
    grep -q '^CONFIG_BOOTDELAY=-2$' "${B}/v851-staging-proper/.config"
    oe_runmake -C "${S}" O="${B}/v851-staging-proper" \
        CROSS_COMPILE="${TARGET_PREFIX}" KCFLAGS=-Oz NPROC=8 LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" -j"$jobs" u-boot.bin
    install -Dm0644 "${B}/v851-staging-proper/u-boot.bin" \
        "${B}/${MACHINE}-u-boot-staging-proper.bin"

    python3 - "${B}/v851-main/.config" "${B}/${MACHINE}-u-boot-spl.egon.bin" \
        "${B}/v851-fel/.config" "${B}/${MACHINE}-u-boot-fel.egon.bin" \
        "${B}/v851-staging/.config" "${B}/${MACHINE}-u-boot-staging.egon.bin" <<'PY'
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
    install -Dm0644 "${B}/${MACHINE}-u-boot-spl.egon.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-spl.egon.bin"
    install -Dm0644 "${B}/${MACHINE}-u-boot-fel.egon.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-fel.egon.bin"
    install -Dm0644 "${B}/${MACHINE}-u-boot-staging.egon.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-staging.egon.bin"
    install -Dm0644 "${B}/v851-main/u-boot.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.bin"
    install -Dm0644 "${B}/${MACHINE}-u-boot-staging-proper.bin" \
        "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.bin"
}

do_install:v851s() {
    install -d "${D}${libdir}/u-boot-v851s3"
    for image in ${MACHINE}-u-boot-spl.egon.bin \
                 ${MACHINE}-u-boot-fel.egon.bin \
                 ${MACHINE}-u-boot-staging.egon.bin; do
        install -m0644 "${B}/$image" "${D}${libdir}/u-boot-v851s3/$image"
    done
    install -m0644 "${B}/v851-main/u-boot.bin" \
        "${D}${libdir}/u-boot-v851s3/${MACHINE}-u-boot-proper.bin"
    install -m0644 "${B}/${MACHINE}-u-boot-staging-proper.bin" \
        "${D}${libdir}/u-boot-v851s3/${MACHINE}-u-boot-staging-proper.bin"
}

COMPATIBLE_MACHINE:append = "|v851s"
FILES:${PN}:append:v851s = " ${libdir}/u-boot-v851s3"

# V821 uses U-Boot for both flash SPL and FEL recovery.
UBOOT_MACHINE:v821 = "v821_defconfig"
UBOOT_ARCH:v821 = "riscv"
UBOOT_BINARY:v821 = "v821-main/spl/sunxi-spl.bin"
COMPATIBLE_MACHINE:append:v821 = "|woo|v821"

python do_patch:append:v821() {
    bb.build.exec_func("c2a_uboot_install_common_files", d)
}

do_configure:v821() {
    for variant in main fel staging staging-proper; do
        case "$variant" in
            fel) overlay=v821_defconfig_fel ;;
            main) overlay="" ;;
            staging-proper) overlay=v821_defconfig_staging_proper ;;
            staging) overlay=v821_defconfig_staging ;;
        esac
        # Merge boot-flow overrides before resolving Kconfig dependencies.
        install -Dm0644 "${S}/configs/v821_defconfig" "${B}/v821-$variant/.config"
        if [ -n "$overlay" ]; then
            "${S}/scripts/kconfig/merge_config.sh" -m -O "${B}/v821-$variant" \
                "${B}/v821-$variant/.config" "${UNPACKDIR}/v821/$overlay"
        fi
        oe_runmake -C "${S}" O="${B}/v821-$variant" olddefconfig
        # Proper must discover L2 again after relocation, even without EFI.
        for option in ANDES_L2_CACHE CACHE DM_EVENT EVENT SPL_DM SPL_OF_CONTROL SPL_CACHE; do
            grep -q "^CONFIG_$option=y$" "${B}/v821-$variant/.config"
        done
        ! grep -q '^CONFIG_\(SPL_\)\?SYS_[ID]CACHE_OFF=y$' "${B}/v821-$variant/.config"
    done
}

do_compile:v821() {
    unset LDFLAGS CFLAGS CPPFLAGS
    for variant in fel main staging; do
        oe_runmake -C "${S}" O="${B}/v821-$variant" KCFLAGS=-Oz \
            LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" spl/sunxi-spl.bin
    done
    for variant in main staging-proper; do
        oe_runmake -C "${S}" O="${B}/v821-$variant" KCFLAGS=-Oz \
            LTO_FINAL_LDFLAGS="-fuse-linker-plugin -flto=2 -no-pie" u-boot.bin u-boot-initial-env
    done
    python3 - "${B}" <<'PY'
import pathlib, re, struct, sys
root = pathlib.Path(sys.argv[1])
for variant, base, limit in [('fel', 0x02008000, 0x8000), ('main', 0x02000000, 0xb000), ('staging', 0x02000000, 0xb000)]:
    build = root / ('v821-' + variant)
    data = (build / 'spl/sunxi-spl.bin').read_bytes()
    config = dict(re.findall(r'^(CONFIG_\w+)=(.*)$', (build / '.config').read_text(), re.M))
    linkmap = (build / 'spl/u-boot-spl.map').read_text()
    def number(name):
        return int(config['CONFIG_' + name], 0)
    def address(name):
        matches = re.findall(r'^\s*(0x[0-9a-f]+)\s+' + name + r'(?:\s|$)', linkmap, re.M)
        assert len(matches) == 1, name
        return int(matches[0], 16)
    assert data[4:12] == b'eGON.BT0'
    assert struct.unpack_from('<I', data)[0] == 0x0600006f
    assert len(data) == struct.unpack_from('<I', data, 16)[0]
    assert number('EGON_MAX_SIZE') == limit
    assert len(data) % 1024 == 0 and len(data) <= limit
    check = bytearray(data)
    stored = struct.unpack_from('<I', check, 12)[0]
    struct.pack_into('<I', check, 12, 0x5f0a6c39)
    assert sum(struct.unpack('<%dI' % (len(check) // 4), check)) & 0xffffffff == stored
    assert config['CONFIG_RISCV_MMODE'] == 'y'
    assert config['CONFIG_SPL_RISCV_MMODE'] == 'y'
    assert number('SPL_TEXT_BASE') == base + 0x60
    assert base + len(data) <= address('__bss_start')
    dtb_offset = address('_image_binary_end') - base
    assert data[dtb_offset:dtb_offset + 4] == bytes.fromhex('d00dfeed'), (
        'SPL control DTB does not match the linked _image_binary_end')
    if variant == 'fel':
        assert config['CONFIG_WOOBOOBOX_V821B_SPL_FORCE_FEL'] == 'y'
        assert address('__bss_end') <= 0x02011000, 'FEL BSS overlaps the BootROM work area'
    else:
        assert config['CONFIG_SPL_FIT'] == 'y'
        assert config['CONFIG_SPL_LOAD_FIT'] == 'y'
        assert config['CONFIG_SPL_OPENSBI'] == 'y'
        assert number('SYS_SPI_U_BOOT_OFFS') == 0x40000
        assert config['CONFIG_SPL_LOAD_FIT_OPENSBI_OS_BOOT'] == 'y'
        offsets = (build / 'include/generated/generic-asm-offsets.h').read_text()
        gd_size = int(re.search(r'#define\s+GD_SIZE\s+(\d+)', offsets)[1])
        reserved = (number('SPL_STACK') - (1 << number('STACK_SIZE_SHIFT'))
                    - number('SPL_SYS_MALLOC_F_LEN') - gd_size) & ~15
        assert address('__bss_end') <= reserved, 'BSS overlaps GD / early allocator'
    print('Verified V821', variant, 'eGON:', len(data), 'bytes')
main_config = (root / 'v821-main/.config').read_text().splitlines()
staging_config = (root / 'v821-staging/.config').read_text().splitlines()
assert '# CONFIG_WOOBOOBOX_V821B_SPL_FORCE_FEL is not set' in main_config
assert '# CONFIG_WOOBOOBOX_V821B_SPL_FORCE_FEL is not set' in staging_config
assert '# CONFIG_SUNXI_FEL_STASH is not set' in main_config
assert '# CONFIG_SUNXI_FEL_STASH is not set' in staging_config
assert 'CONFIG_SUNXI_FEL_STASH=y' in (root / 'v821-fel/.config').read_text().splitlines()
assert main_config == staging_config, 'Staging must use the production NOR boot configuration'
config = (root / 'v821-main/.config').read_text()
assert 'CONFIG_RISCV_MMODE=y' in config
assert 'CONFIG_BOOTM_OPENSBI=y' in config
assert 'CONFIG_TEXT_BASE=0x82000000' in config
assert 'CONFIG_BOOTCOMMAND="bootm 0x81000000"' in config
environment = dict(line.split('=', 1) for line in
                   (root / 'v821-main/u-boot-initial-env').read_text().splitlines()
                   if '=' in line)
assert environment.get('bootcmd') == 'bootm 0x81000000', 'Board environment overrides recovery bootcmd'
assert environment.get('bootdelay') == '-2'
assert 'fdt_high' not in environment
assert 'initrd_high' not in environment
PY
}

do_install:v821() {
    install -Dm0644 "${B}/v821-fel/spl/sunxi-spl.bin" \
        "${D}${libdir}/u-boot-v821/${MACHINE}-u-boot-fel.egon.bin"
    install -Dm0644 "${B}/v821-main/spl/sunxi-spl.bin" \
        "${D}${libdir}/u-boot-v821/${MACHINE}-u-boot-spl.egon.bin"
    install -Dm0644 "${B}/v821-main/u-boot.bin" \
        "${D}${libdir}/u-boot-v821/${MACHINE}-u-boot-proper.bin"
}

do_deploy:v821() {
    for variant in fel main staging; do
        label=$variant
        [ "$variant" != main ] || label=spl
        install -Dm0644 "${B}/v821-$variant/spl/sunxi-spl.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-$label.egon.bin"
        install -Dm0644 "${B}/v821-$variant/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-$label.config"
        install -Dm0644 "${B}/v821-$variant/spl/u-boot-spl" "${DEPLOYDIR}/${MACHINE}-u-boot-$label.elf"
        install -Dm0644 "${B}/v821-$variant/spl/u-boot-spl.map" "${DEPLOYDIR}/${MACHINE}-u-boot-$label.map"
    done
    install -Dm0644 "${B}/v821-main/u-boot.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.bin"
    install -Dm0644 "${B}/v821-staging-proper/u-boot.bin" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.bin"
    install -Dm0644 "${B}/v821-staging-proper/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.config"
    install -Dm0644 "${B}/v821-staging-proper/u-boot.map" "${DEPLOYDIR}/${MACHINE}-u-boot-staging-proper.map"
    install -Dm0644 "${B}/v821-main/u-boot" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.elf"
    install -Dm0644 "${B}/v821-main/u-boot.map" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.map"
    install -Dm0644 "${B}/v821-main/.config" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.config"
    install -Dm0644 "${B}/v821-main/u-boot-initial-env" "${DEPLOYDIR}/${MACHINE}-u-boot-proper.env"
}
FILES:${PN}:append:v821 = " ${libdir}/u-boot-v821"
