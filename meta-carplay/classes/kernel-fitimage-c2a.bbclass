# Kernel-side producer for the standalone linux-yocto-fitimage recipe.
#
# Since Yocto 6.0, FIT assembly no longer runs inside the kernel recipe.
# This wrapper keeps the C2A-specific kernel preparation while the standalone
# recipe consumes the deployed linux.bin and linux_comp artifacts.
inherit kernel-fit-extra-artifacts

uboot_prep_kimage() {
    # zImage decompresses itself after the FIT loader passes it the selected
    # DTB in r2. Do not make SPL decompress the kernel a second time.

    bbnote "Preparing the C2A zImage kernel payload for fitImage"

    IMG_SRC="${B}/arch/arm/boot/zImage"

	output_dir=$1
	# Keep compatibility with kernel-uboot callers which omit the argument.
	if [ -z "$output_dir" ]; then
		output_dir='.'
	fi
	linux_bin=$output_dir/linux.bin

    rm -f "${linux_bin}"
    cp "${IMG_SRC}" "${linux_bin}"
    linux_comp="none"

	printf "$linux_comp" > "$output_dir/linux_comp"
}

C2A_PATCHED_PREP_KIMAGE = "1"
