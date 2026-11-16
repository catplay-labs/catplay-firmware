INSANE_SKIP:${PN} += "buildpaths"

# Opt-in FIT extensions selected by machine-boot configuration includes.
C2A_FITIMAGE_CLASSES ?= ""
inherit_defer ${C2A_FITIMAGE_CLASSES}
