inherit c2a-artifact-names

IMAGE_NAME_SUFFIX = ".rootfs"
EXTRA_IMAGECMD:jffs2 = "--pad ${JFFS2_ENDIANNESS} --eraseblock=${JFFS2_ERASEBLOCK} --compression-mode=priority -x zlib -x lzo"

C2A_ROOTFS_FSTYPE ?= "jffs2"

# This is only available from the "inside" via ${IMGDEPLOYDIR} because at this stage
# it's too early for it to exist in ${DEPLOY_DIR_IMAGE}
C2A_WIC_ROOTFS_PATH = "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.${C2A_ROOTFS_FSTYPE}"

python () {
    types = filter(None, [
        d.getVar("C2A_EXTRA_FSTYPES"),
        d.getVar("C2A_ROOTFS_FSTYPE"),
        
        "wic" if d.getVar("C2A_SUPPORTS_WIC") == "1" else None,
        "c2aflash" if d.getVar("C2A_FLASH_PARTITIONS") != None else None,
        "c2aota" if (d.getVar("C2A_OTA_PARTITIONS") or "").strip() else None
    ])
    d.setVar("IMAGE_FSTYPES", " ".join(types))
}

C2A_WICVARS ?= ""
WICVARS:append := " ${C2A_WICVARS} "

inherit image-c2aflash
inherit image-c2aota
inherit c2a-x-persist

C2A_WIC_SKIP_BOOTLOADER_BUILD ?= "0"
C2A_C2AFLASH_NEEDS_INITRAMFS ?= "1"

do_image_wic[depends] += "${PN}:do_image_${C2A_ROOTFS_FSTYPE}"
do_image_wic[depends] += "wic-tools:do_populate_sysroot"
do_image_wic[depends] += "${@bb.utils.contains('C2A_WIC_SKIP_BOOTLOADER_BUILD', '1', '', 'virtual/bootloader:do_deploy', d)}"
do_image_wic[depends] += "${C2A_FITIMAGE_DEPENDENCY}"

do_image_c2aflash[depends] += "${PN}:do_image_${C2A_ROOTFS_FSTYPE}"
do_image_c2aflash[depends] += "${@bb.utils.contains('C2A_WIC_SKIP_BOOTLOADER_BUILD', '1', '', 'virtual/bootloader:do_deploy', d)}"
do_image_c2aflash[depends] += "virtual/kernel:do_deploy"
do_image_c2aflash[depends] += "${@bb.utils.contains('C2A_C2AFLASH_NEEDS_INITRAMFS', '1', '${C2A_INITRAMFS_IMAGE}:do_image_complete', '', d)}"

do_image_c2aota[depends] += "${PN}:do_image_${C2A_ROOTFS_FSTYPE}"
do_image_c2aota[depends] += "${@bb.utils.contains('C2A_WIC_SKIP_BOOTLOADER_BUILD', '1', '', 'virtual/bootloader:do_deploy', d)}"
do_image_c2aota[depends] += "virtual/kernel:do_deploy"
do_image_c2aota[depends] += "${@bb.utils.contains('C2A_C2AFLASH_NEEDS_INITRAMFS', '1', '${C2A_INITRAMFS_IMAGE}:do_image_complete', '', d)}"

# Reduce glitching between kernel release and kernel modules on rootfs...
KERNELRELEASE_STAMP = "${@oe.utils.read_file(d.expand('${STAGING_KERNEL_BUILDDIR}/include/config/kernel.release')).strip() if os.path.exists(d.expand('${STAGING_KERNEL_BUILDDIR}/include/config/kernel.release')) else ''}"
do_rootfs[depends] += "virtual/kernel:do_packagedata"
do_rootfs[depends] += "virtual/kernel:do_package_write_ipk"
do_rootfs[vardeps] += "KERNELRELEASE_STAMP"

do_rootfs[recrdeptask] += "do_packagedata do_package_write_ipk"
