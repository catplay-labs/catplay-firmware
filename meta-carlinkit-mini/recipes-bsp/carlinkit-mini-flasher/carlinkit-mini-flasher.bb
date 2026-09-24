SUMMARY = "Carlinkit Mini recovery flasher tools"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/files/common-licenses/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch deploy

SRC_URI += " \
    file://errors.py \
    file://exploit.py \
    file://flash.py \
    file://reboot2recovery.py \
    file://recov.py \
    file://trampoline.py \
    file://uimage.py \
    file://uploader.py \
"

S = "${UNPACKDIR}"

C2A_FLASHER_FILES = " \
    errors.py \
    exploit.py \
    flash.py \
    reboot2recovery.py \
    recov.py \
    trampoline.py \
    uimage.py \
    uploader.py \
"

do_install() {
    install -d "${D}${datadir}/${PN}"

    for f in ${C2A_FLASHER_FILES}; do
        install -m 0755 "${UNPACKDIR}/${f}" "${D}${datadir}/${PN}/${f}"
    done
}

FILES:${PN} += "${datadir}/${PN}"

do_deploy() {
    install -d "${DEPLOYDIR}/tools"

    for f in ${C2A_FLASHER_FILES}; do
        install -m 0755 "${UNPACKDIR}/${f}" "${DEPLOYDIR}/tools/${f}"
    done
}

addtask do_deploy before do_build after do_install
