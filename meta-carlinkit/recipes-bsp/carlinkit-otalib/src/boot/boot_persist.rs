use crate::{SystemUtil, boot::udhcpd::UDHCPD_PERSIST_DIR, dmesg};
use heapless::CString;

pub fn mount_persist(mtdblock_dev: &str, fs_type: &str) -> Result<(), &'static str> {
    let _ = SystemUtil::mkdir_if_missing("/persist");

    let mut source = CString::<64>::new();
    if source.extend_from_bytes(mtdblock_dev.as_bytes()).is_err() {
        return Err("mount source path too long");
    }

    let mut fstype = CString::<16>::new();
    if fstype.extend_from_bytes(fs_type.as_bytes()).is_err() {
        return Err("mount fstype too long");
    }

    dmesg!("[boot] Starting persist mount");
    if fs_type == "jffs2" {
        let _ = SystemUtil::modprobe("jffs2");
    }
    let ret = unsafe { libc::mount(source.as_ptr(), c"/persist".as_ptr(), fstype.as_ptr(), 0, core::ptr::null()) };

    dmesg!("[boot] Finished persist mount");
    if ret == 0 { Ok(()) } else { Err("mount persist failed") }
}

pub fn mount_persist_overlays() {
    let _ = SystemUtil::mkdir_if_missing("/persist");
    let _ = SystemUtil::mkdir_if_missing("/persist/c2a_bluetooth");
    let _ = SystemUtil::mkdir_if_missing("/persist/c2a_config");
    let _ = SystemUtil::mkdir_if_missing(UDHCPD_PERSIST_DIR);
    let _ = SystemUtil::mkdir_if_missing("/var/lib/bluetooth");
    let _ = SystemUtil::mount_bind("/persist/c2a_bluetooth", "/var/lib/bluetooth");
}
