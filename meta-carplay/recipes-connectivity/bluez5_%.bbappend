FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"
PACKAGECONFIG = "tools readline"
PACKAGES:remove = "${PN}-testtools ${PN}-obex"
#SRC_URI:append = " file://0001-src-log-h-disable-logging-macros.patch"
#SRC_URI:append = " file://0002-bluez5-stop-using-debug-section-in-bluetoothd.patch"
#SRC_URI:append = " file://0003-no-op-shared-log-macros.patch"
#SRC_URI:append = " file://0004-no-op-shared-att-verbose.patch"
SRC_URI:append = " file://0005-storage-use-atomic-no-op-writes.patch"
SRC_URI:append = " file://0006-tools-hex2hcd-include-libgen-for-basename.patch"
SRC_URI:append = " file://bluetoothd-logged"

#DEPENDS:remove = "glib-2.0"

do_install:append() {
    rm -rf ${D}${libdir}/bluez/test
    rmdir --ignore-fail-on-non-empty ${D}${libdir}/bluez || true
    install -m 0755 ${WORKDIR}/bluetoothd-logged ${D}${libexecdir}/bluetooth/bluetoothd-logged
    sed -i -e "s#^DAEMON=.*#DAEMON=${libexecdir}/bluetooth/bluetoothd-logged#" ${D}${sysconfdir}/init.d/bluetooth
}
