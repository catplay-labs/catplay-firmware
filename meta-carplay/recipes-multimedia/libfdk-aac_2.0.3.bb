SUMMARY = "Fraunhofer FDK AAC Codec Library"
HOMEPAGE = "https://github.com/mstorsjo/fdk-aac"
DESCRIPTION = "High-quality open-source AAC encoder library developed by Fraunhofer IIS"

LICENSE = "Fraunhofer_FDK_AAC_Codec_Library_for_Android"
LICENSE_FLAGS = "commercial"
LIC_FILES_CHKSUM = "file://NOTICE;md5=5985e1e12f4afa710d64ed7bfd291875"

SRC_URI = "git://github.com/mstorsjo/fdk-aac.git;protocol=https;branch=master"
SRCREV = "716f4394641d53f0d79c9ddac3fa93b03a49f278"

PV = "2.0.3"
PR = "r3"

inherit autotools pkgconfig

# Keep FDK AAC at -O3 even when the distro selects -Oz.
BUILD_OPTIMIZATION:forcevariable = "-O3"
SELECTED_OPTIMIZATION:forcevariable = "-O3"

EXTRA_OECONF = "--enable-static --disable-shared"

TARGET_CC_ARCH:toolchain-clang += "-flto -fno-fat-lto-objects -fvisibility=hidden -ffunction-sections -fdata-sections"
TARGET_CC_ARCH:toolchain-clang:armv7ve += "-mfloat-abi=hard"
TARGET_LDFLAGS:toolchain-clang += "-flto -fuse-ld=lld -Wl,--gc-sections"

# FILES:${PN} = "${libdir}/*.a"
# FILES:${PN}-dev = "${includedir} ${libdir}/pkgconfig"
