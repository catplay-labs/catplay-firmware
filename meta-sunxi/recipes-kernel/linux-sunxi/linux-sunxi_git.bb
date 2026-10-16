SUMMARY = "Mainline Linux kernel for sunxi platforms"
DESCRIPTION = "Mainline Linux kernel from Linus Torvalds' tree for sunxi platforms"

inherit kernel-c2a-base

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

PR = "r7"

SRC_URI = "git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git;branch=${KBRANCH};protocol=https"
SRC_URI:append = " file://crypto/0001-crypto-remove-forced-entropy-collection-that-adds-0.5s-lag-to-system-boot.patch"
SRC_URI:append = " file://crypto/0002-crypto-af_alg-allow-async-AES-CTR-provider.patch"
SRC_URI:append = " file://crypto/0003-crypto-af_alg-restore-skcipher-splice-zero-copy.patch"
SRC_URI:append = " file://drivers/net/usb/0001-cdc-ncm-fix-linking-with-generic-CarPlay-Headunits.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0001-phy-sun4i-usb-add-new-PHY-register-map.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0002-phy-sun4i-usb-guard-PHY_CTL_VBUSVLDEXT.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0003-phy-sun4i-usb-skip-squelch-detection-for-VCBUS.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0004-phy-sun4i-usb-support-sun300i-V821.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0005-phy-sun4i-usb-apply-explicit-VCBUS-roles-synchronously.patch"
SRC_URI:append = " file://drivers/phy/allwinner/0006-phy-sun4i-usb-program-the-V821-PHY-PLL-and-tuning.patch"
SRC_URI:append = " file://init/0001-initrd-support-directly-mounted-EROFS-images.patch"
SRC_URI:append = " file://drivers/spi/0001-spi-add-sunxi-SPIF-controller-support.patch"
SRC_URI:append = " file://drivers/mtd/spi-nor/0001-mtd-spi-nor-add-XTX-flash-support.patch"
SRC_URI:append = " file://drivers/pinctrl/sunxi/0001-pinctrl-sunxi-add-V821-pin-controllers.patch"
SRC_URI:append = " file://drivers/clocksource/0001-clocksource-add-V821-high-speed-timer-support.patch"
SRC_URI:append = " file://drivers/cache/0001-cache-backport-generic-Andes-LLC-refactor.patch"
SRC_URI:append = " file://arch/riscv/errata/0001-riscv-andes-add-legacy-MMU-erratum.patch"
SRC_URI:append = " file://drivers/clk/sunxi-ng/0001-clk-sunxi-ng-ccu_mp-stop-divider-search-after-overfl.patch"
SRC_URI:append = " file://drivers/clk/sunxi-ng/0002-clk-sunxi-ng-add-linear-P-divider-mode-to-ccu_mp.patch"
SRC_URI:append = " file://drivers/clk/sunxi-ng/0003-clk-sunxi-ng-backport-vendor-ccu_nm-PLL-extensions.patch"
SRC_URI:append = " file://drivers/clk/sunxi-ng/0004-clk-sunxi-ng-add-complete-V821-CCU-support.patch"
SRC_URI:append = " file://drivers/usb/musb/0001-usb-musb-sunxi-support-optional-clocks-and-resets.patch"
SRC_URI:append = " file://drivers/usb/musb/0002-usb-musb-sunxi-add-V821-platform-support.patch"
SRC_URI:append = " file://drivers/usb/musb/0003-usb-musb-sunxi-expose-userspace-role-switch.patch"
SRC_URI:append = " file://drivers/usb/musb/0004-usb-musb-sunxi-hand-off-V821-PHY-to-linked-host-HCDs.patch"
SRC_URI:append = " file://drivers/usb/0003-usb-log-host-enumeration-failures-to-dmesg.patch"
SRC_URI:append = " file://drivers/usb/host/0003-usb-host-quiesce-platform-HCDs-before-remove.patch"
SRC_URI:append = " file://drivers/nvmem/0001-nvmem-sunxi-sid-add-V821-USB-PHY-calibration.patch"
SRC_URI:append = " file://arch/riscv/boot/dts/allwinner/sun300i-v821b.dtsi;subdir=."
SRC_URI:append = " file://drivers/soc/sunxi/0001-soc-allwinner-finish-V821-platform-integration.patch"
SRC_URI:append = " file://drivers/mtd/0001-mtd-add-read-only-bad-block-aware-mtdblock-device.patch"
SRC_URI:append = " file://drivers/crypto/allwinner/sun4i-ss/0112-crypto-sun4i-ss-add-v821-ctr-support.patch"
SRC_URI:append = " file://drivers/crypto/allwinner/sun4i-ss/0113-crypto-sun4i-ss-expose-algorithms-to-userspace.patch"
SRC_URI:append = " file://drivers/crypto/allwinner/sun4i-ss/0114-crypto-sun4i-ss-queue-skciphers-through-crypto-engine.patch"
SRC_URI:append = " file://drivers/crypto/allwinner/sun4i-ss/0115-crypto-sun4i-ss-add-dmaengine-aes-path.patch"
SRC_URI:append = " file://drivers/crypto/allwinner/sun4i-ss/0116-crypto-sun4i-ss-register-sun300i-v821-variant.patch"
SRC_URI:append = " file://drivers/char/hw_random/0102-hw-random-add-v821-sunxi-trng.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0001-dma-sun6i-add-V821-dma-v108-variant.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0002-dma-sun6i-add-v108-mbus-enable-clock.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0003-dma-sun6i-enable-v108-io-speedup.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0004-dma-sun6i-route-v108-shared-irqs-to-cpu.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0005-dma-sun6i-use-v108-sdram-drq-zero.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0006-dma-sun6i-log-v108-timeout-state.patch"
SRC_URI:append = " file://drivers/dma/sun6i/0007-dma-sun6i-handle-partial-v108-irq-bank.patch"
SRC_URI:append = " file://drivers/mmc/host/0001-mmc-sunxi-support-optional-MBUS-clock.patch"
SRC_URI:append = " file://drivers/mmc/host/0002-mmc-sunxi-add-explicit-maximum-descriptor-length-quirk.patch"
SRC_URI:append = " file://drivers/mmc/host/0004-mmc-sunxi-allow-controller-specific-DMA-trigger-levels.patch"
SRC_URI:append = " file://drivers/mmc/host/0005-mmc-sunxi-add-V821-SMHC-v5p3x-support.patch"
SRC_URI:append = " file://drivers/bluetooth/0001-rtl.patch"

DEPENDS += " \
    coreutils-native \
"

do_install_v821_dtsi() {
    install -Dm0644 "${UNPACKDIR}/arch/riscv/boot/dts/allwinner/sun300i-v821b.dtsi" "${S}/arch/riscv/boot/dts/allwinner/sun300i-v821b.dtsi"
}
addtask install_v821_dtsi after do_kernel_checkout before do_patch

# PV is defined in kernel-c2a-base and uses LINUX_VERSION.
LINUX_VERSION = "7.2"

KBRANCH = "master"
SRCREV = "8d3ae59288f1e7d58d76558a6ee96d533bc5019f"

# The Yocto kconfiglib shipped with this layer stack may not understand newer
# Kconfig syntax used by current mainline kernels.
do_kernel_configcheck[noexec] = "1"

LOCALVERSION = "-sunxi"

inherit kernel-clang-c2a
inherit kernel-deploy-extras-c2a
inherit kernel-deploy-modules-to-sysroot-c2a
inherit kernel-firmware-stage-c2a
inherit kernel-firmware-conflicts-c2a
inherit kernel-extra-config-c2a
inherit kernel-broken-version-c2a

DEPENDS:append = " u-boot-tools-native"
