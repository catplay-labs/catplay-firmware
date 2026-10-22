C2A_DD_BS ?= "4096"
C2A_FLASH_SIZE_BYTES ?= "${@16 * 1024 * 1024}"

IMAGE_TYPES += " c2aflash"

python c2aflash_create_image() {
    import os
    import shlex

    out = os.path.join(d.getVar("IMGDEPLOYDIR"), f"{d.getVar('IMAGE_NAME')}.c2aflash")
    flash_size = int(d.getVar("C2A_FLASH_SIZE_BYTES"), 0)
    fill_byte = int(d.getVar("C2A_FLASH_FILL_BYTE") or "0", 0)
    if not 0 <= fill_byte <= 255:
        bb.fatal(f"C2A_FLASH_FILL_BYTE must be between 0 and 255, got {fill_byte}")
    parts_raw = d.getVar("C2A_FLASH_PARTITIONS") or ""

    bb.note(f"Using C2A_FLASH_PARTITIONS: {parts_raw}")

    if not parts_raw.strip():
        bb.note("C2A_FLASH_PARTITIONS is empty, skipping c2aflash image generation")
        return

    parts = shlex.split(parts_raw)
    if len(parts) % 3 != 0:
        bb.fatal(
            f"C2A_FLASH_PARTITIONS must be groups of 3 "
            f"(file offset size), got {len(parts)} elements"
        )

    bb.note(f"Generating C2A flash image: {out}")
    bb.note(f"Flash size: {flash_size}")

    if os.path.exists(out):
        os.unlink(out)

    with open(out, "wb") as img:
        img.truncate(flash_size)
        if fill_byte:
            fill = bytes([fill_byte]) * (1024 * 1024)
            img.seek(0)
            remaining = flash_size
            while remaining:
                chunk = fill[:min(len(fill), remaining)]
                img.write(chunk)
                remaining -= len(chunk)

        for part_file, off_raw, size_raw in zip(parts[0::3], parts[1::3], parts[2::3]):
            off = int(off_raw, 0)
            size = int(size_raw, 0)

            if not os.path.exists(part_file):
                os.unlink(out)
                bb.fatal(f"Missing partition file: {part_file}")

            real = os.stat(part_file).st_size
            if real > size:
                os.unlink(out)
                bb.fatal(f"{part_file} too large: {real} > {size}")

            bb.note(f"Writing {part_file} @ {off} ({real} bytes)")

            with open(part_file, "rb") as src:
                img.seek(off)
                img.write(src.read())

    bb.note("C2A flash image ready")
}

IMAGE_CMD:c2aflash = ":"
do_image_c2aflash[prefuncs] += "c2aflash_create_image"
do_image_c2aflash[vardeps] += "C2A_FLASH_PARTITIONS C2A_FLASH_SIZE_BYTES C2A_FLASH_FILL_BYTE"
