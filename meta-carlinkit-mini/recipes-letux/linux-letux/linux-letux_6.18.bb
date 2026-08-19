SUMMARY = "Letux Kernel by Goldelico"
DESCRIPTION = "Letux Kernel by Goldelico is a project which maintains high-quality support for Ingenic X1600, otherwise unsupported by mainline"

inherit kernel-c2a-base

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

PR = "r0"

SRC_URI = "git://github.com/goldelico/letux-kernel.git;branch=${KBRANCH};protocol=https"

DEPENDS += " \
    coreutils-native \
"
# file://0006-Change-Os-to-more-aggressive-Oz-for-CONFIG_CC_OPTIMIZE_FOR_SIZE.patch
SRC_URI:append = " \
    file://0001-mtd-add-support-for-dev-mtdblock_bbt_ro-driver.patch \
    file://0002-mtd-spi-nand-add-support-for-CD5F1GM7UE-a-GigaDevice-clone-with-VID-PID-equal-to-0xC.patch \
    file://0003-x1600-add-SFC-driver.patch \
    file://0004-cdc-ncm-fix-linking-with-generic-CarPlay-Headunits.patch \
    file://0005-Fix-boot-crash-in-Falcon-mode-U-Boot-SPL-uzImage-self-decompressor-kernel-jump.patch \
    file://0007-mtd-spi-nor-Add-support-for-xt25f128b-chip.patch \
    file://0008-x1600-add-support-for-early-printk-on-UART2.patch \
    file://0009-x1600-add-missing-SFC-clock-definition.patch \
    file://0010-initrd-add-support-for-erofs.patch \
    file://0011-x1600-add-missing-HWRNG-clock-definition-and-driver-support.patch \
    file://0012-mtd-spi-nor-always-print-Manufacturer-and-device-ID.patch \
    file://0013-jz4740_mmc-fix-broken-compat-with-aic8800.patch \
    file://0014-bluetooth-add-basic-HCI-driver-for-AIC8800.patch \
    file://0015-crypto-remove-forced-entropy-collection-that-adds-0.5s-lag-to-system-boot.patch \
    file://0016-x1600-add-syscon-support.patch \
    file://0017-spi.patch \
    file://0018-leds-add-driver-for-SPI-driven-WorldSemi-WS2812B-RGB-LEDs.patch \
    file://0019-phy-ingenic-usb-use-optimal-flags-for-forced-role-switching-without-VBUS-and-ID-pins.patch \
    file://0020-phy-ingenic-usb-fix-incorrect-PHY-tuning.patch \
    file://0021-dwc2-actually-communicate-requested-usb-role-switch-to-the-PHY.patch \
    file://0022-dwc2-remove-3x100ms-overhead-when-booting-with-USB_DR_MODE_OTG-and-Ingenic-X1600.patch \
    file://0023-dwc2-log-failed-dwc2_hsotg_core_connect-when-binding-gadget.patch \
    file://0024-mips-x1600-log-early-CP0-Count-as-boot-time-estimate.patch \
    file://0025-mips-x1600-add-AES-clock-references.patch \
    file://0026-crypto-ingenic-add-AES-accelerator-driver.patch \
    file://0027-mips-genex-use-literal-immediates-for-r4k-wait-skipover.patch \
    file://0028-mips-ingenic-drain-bridge-after-dma-cache-sync.patch \
    file://0029-jz4740_mmc-reduce-poll-irq-timeout-budget.patch \
    file://0030-dwc2-drop-dwc2_hcd_start-50ms-delay.patch \
    file://0031-usb-core-set-USB_PORT_QUIRK_OLD_SCHEME-and-USB_PORT_QUIRK_FAST_ENUM-quirks-on-dwc2-root-hub.patch \
    file://0032-usb-hub-skip-OTG-root-hub-debounce-during-B-host-activation.patch \
    file://0033-clk-ingenic-x1600-couple-cpu-and-l2-dividers.patch \
    file://0034-clk-ingenic-x1600-fix-SADC-gate-bit.patch \
    file://0035-hwrng-ingenic-gate-DTRNG-clock-around-requests.patch \
    file://0036-i2c-jz4780-gate-clock-around-transfers.patch \
    file://0037-dmaengine-jz4780-gate-controller-clock-around-transf.patch \
"

# PV is defined in the base in linux-imx.inc file and uses the LINUX_VERSION definition
# required by kernel-yocto.bbclass.
#
# LINUX_VERSION define should match to the kernel version referenced by SRC_URI and
# should be updated once patchlevel is merged.
#
# Linux kernel stable 6.18.36 changelog:
# https://cdn.kernel.org/pub/linux/kernel/v6.x/ChangeLog-6.18.36
LINUX_VERSION = "6.18.36"

KBRANCH = "letux-6.18.y"
SRCREV = "a3d00c045d89b4944df82a7648c382c3a9cb1d3c"

# The Yocto kconfiglib shipped with this layer stack does not understand the
# Kconfig "transitional" property used by Linux 6.18, so the audit parser fails
# before it can report real config mismatches.
do_kernel_configcheck[noexec] = "1"

LOCALVERSION = "-letux"

inherit kernel-clang-c2a
inherit kernel-deploy-extras-c2a
inherit kernel-deploy-modules-to-sysroot-c2a
inherit kernel-firmware-stage-c2a
inherit kernel-firmware-conflicts-c2a
inherit kernel-extra-config-c2a
inherit kernel-broken-version-c2a

DEPENDS:append = " u-boot-tools-native"
