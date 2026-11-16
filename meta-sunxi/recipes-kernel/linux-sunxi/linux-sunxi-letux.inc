# Letux source: 4624961a42880c7fccf27120d67d3a8d42d82ea5 (Linux 7.2.15).
# X1600 support and local fixes, applied after the sunxi/carplay patch groups.
# Patch numbers follow application order; subsystem directories are retained.
SRC_URI:append = " \
    file://letux/arch/mips/0001-MIPS-Ingenic-support-XBurst-cache-management-and-SMP.patch \
    file://letux/arch/mips/0002-MIPS-Ingenic-wire-platform-initialization-into-the-generic-kernel.patch \
    file://letux/arch/mips/0003-MIPS-Ingenic-add-the-X1600-machine-option.patch \
    file://letux/drivers/clk/ingenic/0004-clk-ingenic-support-a-second-PLL-output-divider.patch \
    file://letux/drivers/clk/ingenic/0005-clk-ingenic-program-X1600-I2S-multiplier-and-divider-fields.patch \
    file://letux/drivers/clk/ingenic/0006-clk-ingenic-add-the-X1600-clock-controller.patch \
    file://letux/drivers/clocksource/0007-clocksource-ingenic-support-TCU-variants-and-per-CPU-interrupts.patch \
    file://letux/drivers/clocksource/0008-clocksource-ingenic-support-per-CPU-and-split-OST-blocks.patch \
    file://letux/drivers/irqchip/0009-irqchip-ingenic-support-per-CPU-controllers-and-SoC-variants.patch \
    file://letux/drivers/pinctrl/0010-pinctrl-ingenic-use-X1600-hardware-edge-interrupt-handling.patch \
    file://letux/drivers/dma/0011-dmaengine-jz4780-support-JZ4730-registers-and-PDMA-request-IDs.patch \
    file://letux/drivers/tty/serial/8250/0012-serial-8250-ingenic-recognize-X1600-UARTs.patch \
    file://letux/drivers/spi/0013-spi-ingenic-adjust-the-source-clock-before-choosing-the-divider.patch \
    file://letux/drivers/spi/0014-spi-ingenic-complete-DMA-transfers-from-the-transmit-callback.patch \
    file://letux/drivers/spi/0015-spi-ingenic-clear-the-SSICDR-gate-before-hardware-setup.patch \
    file://letux/drivers/spi/0016-spi-ingenic-match-the-X1600-SSI-controller.patch \
    file://letux/drivers/mmc/host/0017-mmc-jz4740-fall-back-to-PIO-after-a-DMA-timeout.patch \
    file://letux/drivers/mmc/host/0018-mmc-jz4740-delay-each-reset-poll-and-report-timeout.patch \
    file://letux/drivers/mmc/host/0019-mmc-jz4740-release-DMA-channels-before-freeing-the-IRQ.patch \
    file://letux/drivers/phy/ingenic/0020-phy-ingenic-add-X1600-USB-PHY-initialization.patch \
    file://letux/drivers/watchdog/0021-watchdog-jz4740-support-the-JZ4730-register-layout.patch \
    file://letux/include/dt-bindings/0022-dt-bindings-add-IRQ-GMAC-and-audio-constants-used-by-X1600.patch \
    file://letux/arch/mips/boot/dts/ingenic/0023-MIPS-DTS-Ingenic-describe-the-X1600-SoC.patch \
    file://letux/drivers/spi/0024-x1600-add-SFC-driver.patch \
    file://letux/arch/mips/0025-Fix-boot-crash-in-Falcon-mode-U-Boot-SPL-uzImage-self-decompressor-kernel-jump.patch \
    file://letux/arch/mips/0026-x1600-add-support-for-early-printk-on-UART2.patch \
    file://letux/drivers/char/hw_random/0027-x1600-add-missing-HWRNG-clock-definition-and-driver-support.patch \
    file://letux/drivers/mmc/host/0028-jz4740_mmc-fix-broken-compat-with-aic8800.patch \
    file://letux/drivers/phy/ingenic/0029-phy-ingenic-usb-use-optimal-flags-for-forced-role-switching-without-VBUS-and-ID-pins.patch \
    file://letux/arch/mips/0030-mips-x1600-log-early-CP0-Count-as-boot-time-estimate.patch \
    file://letux/crypto/0031-crypto-ingenic-add-AES-accelerator-driver.patch \
    file://letux/arch/mips/0032-mips-genex-use-literal-immediates-for-r4k-wait-skipover.patch \
    file://letux/drivers/mmc/host/0033-jz4740_mmc-reduce-poll-irq-timeout-budget.patch \
    file://letux/drivers/char/hw_random/0034-hwrng-ingenic-gate-DTRNG-clock-around-requests.patch \
    file://letux/drivers/i2c/busses/0035-i2c-jz4780-gate-clock-around-transfers.patch \
    file://letux/drivers/dma/0036-dmaengine-jz4780-gate-controller-clock-around-transf.patch \
    file://letux/crypto/0037-crypto-ingenic-chain-prepared-CTR-DMA-from-IRQ.patch \
    file://letux/crypto/0038-crypto-af_alg-add-skcipher-keystream-only-operation.patch \
    file://letux/drivers/watchdog/0039-watchdog-jz4740-use-counter-clear-strobe-on-X1600.patch \
"
