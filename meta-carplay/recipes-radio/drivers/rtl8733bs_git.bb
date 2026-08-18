SUMMARY = "Realtek RTL8733BS SDIO Wi-Fi driver"

SRCREV = "6b522b308fb7482cb43be290d639cfdf2bfd1838"
PV = "1.0+git${SRCPV}"
PR = "r2"

FILESEXTRAPATHS:prepend := "${THISDIR}/rtl8733bs:"

SRC_URI = "git://github.com/smp79/rtl8733BS_WiFi_linux_v5.15.17-113.git;branch=ce;protocol=https \
           file://0001-linux-7.0-compatibility.patch \
"

S = "${WORKDIR}/git"

REALTEK_MODULE ?= "8733bs"
REALTEK_CFLAGS = "-DCONFIG_LITTLE_ENDIAN \
                  -DCONFIG_IOCTL_CFG80211 \
                  -DRTW_USE_CFG80211_STA_EVENT \
                  -DCONFIG_RTW_IOCTL_SET_COUNTRY \
                  -DCONFIG_CONCURRENT_MODE \
"
REALTEK_TARGET ?= "RTL8733BS"

require realtek.inc

# Keep the vendor diagnostics enabled while bringing up the mainline port.
# EXTRA_OEMAKE += "CONFIG_RTW_DEBUG=y CONFIG_RTW_LOG_LEVEL=5"
