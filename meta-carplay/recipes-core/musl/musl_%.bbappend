# Backport musl 1.2.6 for riscv32 support.
BASEVER = "1.2.6"
SRCREV = "9fa28ece75d8a2191de7c5bb53bed224c5947417"
LIC_FILES_CHKSUM = "file://COPYRIGHT;md5=0c2904cdc34777fb4067732bae145506"

FILESEXTRAPATHS:prepend := "${THISDIR}/${BPN}:"

# git.etalabs.net has an expired TLS certificate; fetch the same upstream tree
# from the canonical musl host instead.
SRC_URI:remove = "git://git.etalabs.net/git/musl;branch=master;protocol=https"
SRC_URI:prepend = "git://git.musl-libc.org/musl;branch=master;protocol=git "

# Keep using the local 0001/0002 patch copies from FILESEXTRAPATHS; both apply
# cleanly on 1.2.6. 0003 is included upstream.
SRC_URI:remove = "file://0003-elf.h-add-typedefs-for-Elf64_Relr-and-Elf32_Relr.patch"

# musl 1.2.6 supports riscv32; drop the 1.2.4 compatibility block.
COMPATIBLE_HOST:riscv32 = ""

# The clang in this layer stack is built without a riscv32 backend, so let musl
# bootstrap with GCC on riscv32 even when the distro defaults to clang.
#TOOLCHAIN:riscv32 = "gcc"
