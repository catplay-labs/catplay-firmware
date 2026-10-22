inherit ccache

# Config merging compiles host tools before do_configure stages DEPENDS.
# Supply ccache to this early task whenever the recipe enables it.
do_kernel_configme[depends] += "${@bb.utils.contains('DEPENDS', 'ccache-native', 'ccache-native:do_populate_sysroot', '', d)}"

C2A_KERNEL_CLANG ?= "1"
C2A_KERNEL_CLANG_LLD ?= "${C2A_KERNEL_CLANG}"

# Broken relocations
C2A_KERNEL_CLANG_LLD:mipsarch = "0"

# Wrynose selects the recipe toolchain through a deferred class inherit.
# Assign TOOLCHAIN declaratively so that selection sees the requested compiler;
# changing it from anonymous Python happens too late and leaves TCOVERRIDE and
# the inherited toolchain class pointing at GCC.
TOOLCHAIN = "${@'clang' if d.getVar('C2A_KERNEL_CLANG') == '1' else 'gcc'}"
BUILD_OPTIMIZATION:toolchain-clang = ""

KERNEL_LD:toolchain-clang = "${CCACHE}${TARGET_PREFIX}${@'ld.lld' if d.getVar('C2A_KERNEL_CLANG_LLD') == '1' else 'ld.bfd'}"

python () {
    clang = (d.getVar("C2A_KERNEL_CLANG") or "").strip()
    lld = (d.getVar("C2A_KERNEL_CLANG_LLD") or "").strip()

    if clang not in ("0", "1"):
        bb.fatal("C2A_KERNEL_CLANG must be '0' or '1'")
    if lld not in ("0", "1"):
        bb.fatal("C2A_KERNEL_CLANG_LLD must be '0' or '1'")
}

DEPENDS:append:toolchain-clang = " clang-cross-${TARGET_ARCH}"
# Clang does not support GCC's -mno-thumb-interwork; drop it so ARMv5
# kernels using a Thumb-capable tune can pass the compiler checks in Kconfig.
TARGET_CC_KERNEL_ARCH:remove:toolchain-clang = "-mno-thumb-interwork"
KERNEL_CC:toolchain-clang = "${CCACHE}${TARGET_PREFIX}clang ${HOST_CC_KERNEL_ARCH} ${DEBUG_PREFIX_MAP} -fno-integrated-as -fdebug-prefix-map=${STAGING_KERNEL_DIR}=${KERNEL_SRC_PATH}"

KERNEL_AR:toolchain-clang = "${CCACHE}${TARGET_PREFIX}llvm-ar"
# LTO initcall ordering invokes NM as one executable, not a shell command.
KERNEL_NM:toolchain-clang = "${TARGET_PREFIX}llvm-nm"
KERNEL_AS:toolchain-clang = "${CCACHE}${TARGET_PREFIX}llvm-as"
KERNEL_OBJCOPY:toolchain-clang = "${CCACHE}${TARGET_PREFIX}llvm-objcopy"
KERNEL_OBJDUMP:toolchain-clang = "${CCACHE}${TARGET_PREFIX}llvm-objdump"

# kernel class ignores NM/AR/AS
EXTRA_OEMAKE:append:toolchain-clang = ' NM="${KERNEL_NM}" AR="${KERNEL_AR}" AS="${KERNEL_AS}"'
