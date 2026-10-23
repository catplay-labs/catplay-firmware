SUMMARY = "Allwinner recovery flasher tools"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/files/common-licenses/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch deploy

PACKAGE_ARCH = "${MACHINE_ARCH}"
PR = "r4"

SRC_URI += " \
    file://flash.py \
    file://recov.py \
    file://uploader.py \
    file://cpbox_client.py \
    file://cpbox2fel.py \
    file://wizard.py \
    file://reboot2recovery.py \
"

S = "${UNPACKDIR}"

C2A_FLASHER_FILES = " \
    flash.py \
    recov.py \
    uploader.py \
    cpbox_client.py \
    cpbox2fel.py \
    wizard.py \
    reboot2recovery.py \
"

do_install() {
    install -d "${D}${datadir}/${PN}"

    for f in ${C2A_FLASHER_FILES}; do
        install -m 0755 "${S}/${f}" "${D}${datadir}/${PN}/${f}"
    done
}

FILES:${PN} += "${datadir}/${PN}"

do_deploy() {
    install -d "${DEPLOYDIR}/tools"

    for f in ${C2A_FLASHER_FILES}; do
        install -m 0755 "${S}/${f}" "${DEPLOYDIR}/tools/${f}"
    done
}

addtask do_deploy before do_build after do_install
