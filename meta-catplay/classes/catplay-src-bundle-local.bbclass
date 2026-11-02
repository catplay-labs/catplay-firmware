# Bundles provided by create-src-bundle.sh in meta-catplay/recipes-apps/files.

CATPLAY_SRC_BUNDLE_NAME ?= "catplay.tar.gz"
CATPLAY_SRC_BUNDLE := "${THISDIR}/../recipes-apps/files/${CATPLAY_SRC_BUNDLE_NAME}"
CATPLAY_SRC_BUNDLE_SHA256 = ""

python () {
    import hashlib
    import os

    bundle = d.getVar("CATPLAY_SRC_BUNDLE")
    if not bundle or not os.path.exists(bundle):
        return
    bb.parse.mark_dependency(d, bundle)

    h = hashlib.sha256()
    with open(bundle, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    d.setVar("CATPLAY_SRC_BUNDLE_SHA256", h.hexdigest())
}

SRC_URI = "file://${CATPLAY_SRC_BUNDLE_NAME}"
SRC_URI[sha256sum] = "${CATPLAY_SRC_BUNDLE_SHA256}"
PR = "r3"
