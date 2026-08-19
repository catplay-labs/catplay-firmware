C2A_FIRMWARE_CONFLICTS ?= ""

PACKAGESPLITFUNCS:append = " fix_modules"

python fix_modules() {
    import re

    blacklist_patterns = d.getVar("C2A_FIRMWARE_CONFLICTS").split()
    current = d.getVar("RDEPENDS:kernel-modules")
    if current == None:
        bb.warn("This kernel build has no kernel modules?")
        return
    current = current.split()

    bb.warn(f"kernel-modules RDEPENDS before filtering: {current}")

    blacklist_regex = [re.compile(pat) for pat in blacklist_patterns]

    filtered = [
        pkg for pkg in current
        if not any(r.match(pkg) for r in blacklist_regex)
    ]

    bb.warn(f"kernel-modules RDEPENDS after filtering: {filtered}")
    d.setVar("RDEPENDS:kernel-modules", " ".join(filtered))
}
