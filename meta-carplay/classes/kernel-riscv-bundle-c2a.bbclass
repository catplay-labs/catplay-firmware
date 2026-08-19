
# A simplified kernel bundle for Falcon-style boot on RISC-V without Allwinner vendor mess (TOC1/Android bootimg).
# Packs kernel, OpenSBI and DTB.
# OpenSBI should be delivered as virtual/opensbi package exporting exactly one file inside.
# DTB should be delivered as a seperate(out-of-tree) virtual/dtb package with also exactly one file inside.

C2A_RISCV_BUNDLE_MAGIC = "0x56523243"
C2A_RISCV_BUNDLE_GZIP = "1"
C2A_RISCV_BUNDLE_SECTOR_SIZE = "512"
C2A_RISCV_BUNDLE_IMAGETYPE = "c2rvImage"
C2A_RISCV_BUNDLE_KERNEL_IMAGETYPE = "Image"
C2A_RISCV_BUNDLE_OPENSBI ?= "${DEPLOY_DIR_IMAGE}/sunxi-opensbi/*.bin.gz"
C2A_RISCV_BUNDLE_DTB ?= "${DEPLOY_DIR_IMAGE}/devicetree/*.dtb.gz"
C2A_RISCV_BUNDLE_KERNEL ?= "${B}/${KERNEL_OUTPUT_DIR}/${C2A_RISCV_BUNDLE_KERNEL_IMAGETYPE}"
C2A_RISCV_BUNDLE_OPENSBI_LOADADDRESS ?= "0x80fc0000"
C2A_RISCV_BUNDLE_DTB_LOADADDRESS ?= "0x801e0000"
C2A_RISCV_BUNDLE_KERNEL_LOADADDRESS ?= "0x80400000"
C2A_RISCV_BUNDLE_OPENSBI_JUMPADDRESS ?= "${C2A_RISCV_BUNDLE_OPENSBI_LOADADDRESS}"
C2A_RISCV_BUNDLE_DTB_JUMPADDRESS ?= "${C2A_RISCV_BUNDLE_DTB_LOADADDRESS}"
C2A_RISCV_BUNDLE_KERNEL_JUMPADDRESS ?= "${C2A_RISCV_BUNDLE_KERNEL_LOADADDRESS}"

python __anonymous() {
    kerneltypes = (d.getVar("KERNEL_IMAGETYPES") or "").split()
    if d.getVar("C2A_RISCV_BUNDLE_IMAGETYPE") not in kerneltypes:
        return

    typeformake = (d.getVar("KERNEL_IMAGETYPE_FOR_MAKE") or "").split()
    bundle_type = d.getVar("C2A_RISCV_BUNDLE_IMAGETYPE")
    kernel_type = d.getVar("C2A_RISCV_BUNDLE_KERNEL_IMAGETYPE")
    if bundle_type in typeformake:
        typeformake = [kernel_type if image_type == bundle_type else image_type for image_type in typeformake]
    if kernel_type not in typeformake:
        typeformake.append(kernel_type)
    d.setVar("KERNEL_IMAGETYPE_FOR_MAKE", " ".join(typeformake))

    d.appendVarFlag("do_assemble_c2rvimage", "depends", " virtual/opensbi:do_deploy virtual/dtb:do_deploy")
}

python do_assemble_c2rvimage() {
    import glob
    import gzip
    import os
    import struct
    import zlib

    def one_path(pattern, fallback=None):
        paths = sorted(glob.glob(pattern))
        if not paths and fallback:
            paths = sorted(glob.glob(fallback))
        paths = [path for path in paths if os.path.isfile(path)]
        if len(paths) != 1:
            bb.fatal("Expected exactly one file for %s, found %d: %s" %
                     (pattern, len(paths), " ".join(paths)))
        return paths[0]

    def parse_addr(varname):
        value = d.getVar(varname)
        if not value:
            bb.fatal("%s must be set" % varname)
        return int(value, 0)

    def payload(path):
        with open(path, "rb") as f:
            data = f.read()
        if path.endswith(".gz") or data.startswith(b"\x1f\x8b"):
            return data, len(gzip.decompress(data))
        return gzip.compress(data, compresslevel=9, mtime=0), len(data)

    def align(data, size):
        pad = (-len(data)) % size
        return data + (b"\0" * pad)

    sector_size = int(d.getVar("C2A_RISCV_BUNDLE_SECTOR_SIZE"), 0)
    if d.getVar("C2A_RISCV_BUNDLE_IMAGETYPE") not in (d.getVar("KERNEL_IMAGETYPES") or "").split():
        return

    entries = (
        ("C2A_RISCV_BUNDLE_OPENSBI", "C2A_RISCV_BUNDLE_OPENSBI_LOADADDRESS", "C2A_RISCV_BUNDLE_OPENSBI_JUMPADDRESS"),
        ("C2A_RISCV_BUNDLE_DTB", "C2A_RISCV_BUNDLE_DTB_LOADADDRESS", "C2A_RISCV_BUNDLE_DTB_JUMPADDRESS"),
        ("C2A_RISCV_BUNDLE_KERNEL", "C2A_RISCV_BUNDLE_KERNEL_LOADADDRESS", "C2A_RISCV_BUNDLE_KERNEL_JUMPADDRESS"),
    )

    payloads = []
    header_entries = []
    for path_var, load_addr_var, jump_addr_var in entries:
        path = one_path(d.getVar(path_var))
        data, uncompressed_size = payload(path)
        load_addr = parse_addr(load_addr_var)
        jump_addr = parse_addr(jump_addr_var)
        if load_addr > 0xffffffff or jump_addr > 0xffffffff:
            bb.fatal("%s/%s must fit in 32 bits: load=0x%x jump=0x%x" %
                     (load_addr_var, jump_addr_var, load_addr, jump_addr))
        payloads.append(data)
        header_entries.append(struct.pack(
            "<IIIIII",
            len(data),
            uncompressed_size,
            int(d.getVar("C2A_RISCV_BUNDLE_GZIP"), 0),
            load_addr,
            jump_addr,
            zlib.crc32(data) & 0xffffffff,
        ))

    entry_blob = b"".join(header_entries)
    header = struct.pack(
        "<II",
        int(d.getVar("C2A_RISCV_BUNDLE_MAGIC"), 0),
        zlib.crc32(entry_blob) & 0xffffffff,
    ) + entry_blob
    if len(header) > sector_size:
        bb.fatal("RISC-V bundle header is larger than one sector")

    outdir = os.path.join(d.getVar("B"), d.getVar("KERNEL_OUTPUT_DIR"))
    os.makedirs(outdir, exist_ok=True)
    outname = d.getVar("C2A_RISCV_BUNDLE_IMAGETYPE")
    outpath = os.path.join(outdir, outname)
    with open(outpath, "wb") as f:
        f.write(align(header, sector_size))
        for data in payloads:
            f.write(align(data, sector_size))
}

addtask assemble_c2rvimage before do_install after do_compile
