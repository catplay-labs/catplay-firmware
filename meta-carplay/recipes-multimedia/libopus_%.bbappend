## Opus' configure probe force-adds -mfpu=neon and therefore concludes that
## Clang can build NEON intrinsics even when the selected CPU is ARM926EJ-S.
## This target cannot execute NEON, and Opus 1.6.1's unit objects also fail to
## compile with that false-positive configuration. Keep ARM/EDSP assembly but
## disable the inapplicable intrinsics path for ARMv5.
EXTRA_OECONF:append:armv5 = " --disable-intrinsics"

# Keep Opus at -O3 even when the distro selects -Oz.
BUILD_OPTIMIZATION:forcevariable = "-O3"
SELECTED_OPTIMIZATION:forcevariable = "-O3"

EXTRA_OECONF:append:armv7a = " --disable-shared --enable-static --disable-examples"
EXTRA_OECONF:remove:armv7a = "--enable-shared"
DEPENDS:armv7a += " ne10"

do_install:append:armv7a() {
    sed -i 's/^Libs:.*/& -lNE10/' ${D}${libdir}/pkgconfig/libopusenc.pc
}
