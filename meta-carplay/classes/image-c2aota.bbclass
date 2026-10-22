C2A_OTA_PARTITIONS ?= ""
C2A_OTA_METADATA ?= ""
C2A_OTA_BS_SIZE ?= "131072"
C2A_OTA_PACKER_PATH = "${@bb.utils.which(d.getVar('BBPATH'), 'lib/c2aota/packer.py') or ''}"

IMAGE_TYPES += " c2aota"

python c2aota_create_image() {
    import os
    import runpy

    parts_raw = d.getVar("C2A_OTA_PARTITIONS") or ""
    packer_path = d.getVar("C2A_OTA_PACKER_PATH")
    if not packer_path:
        bb.fatal("cannot find native c2aota packer in BBPATH")
    packer = runpy.run_path(packer_path)

    out = os.path.join(d.getVar("IMGDEPLOYDIR"), f"{d.getVar('IMAGE_NAME')}.c2aota")
    try:
        sections = packer["parse_partitions"](parts_raw)
        packer["pack_ota"](out, sections, d.getVar("C2A_OTA_METADATA") or "", int(d.getVar("C2A_OTA_BS_SIZE"), 0))
    except (OSError, ValueError) as error:
        bb.fatal(f"failed to create {out}: {error}")
    bb.note(f"Created {out} from {len(sections)} OTA sections")
}

IMAGE_CMD:c2aota = ":"
do_image_c2aota[prefuncs] += "c2aota_create_image"
do_image_c2aota[vardeps] += "C2A_OTA_PARTITIONS C2A_OTA_METADATA C2A_OTA_BS_SIZE"
do_image_c2aota[file-checksums] += "${C2A_OTA_PACKER_PATH}:True"
