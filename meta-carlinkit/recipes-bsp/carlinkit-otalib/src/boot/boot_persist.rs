use crate::{SystemUtil, dmesg, nostd::SmallFd};
use heapless::CString;

// Linux include/uapi/mtd/ubi-user.h: UBI_IOCATT = _IOW('o', 64, ubi_attach_req).
#[repr(C)]
#[derive(Default)]
struct UbiAttachReq {
    ubi_num: i32,
    mtd_num: i32,
    vid_hdr_offset: i32,
    max_beb_per1024: i16,
    disable_fm: i8,
    need_resv_pool: i8,
    padding: [i8; 8],
}

fn persist_mtd_number(table: &str) -> Result<i32, &'static str> {
    let mut found = None;
    for line in table.lines() {
        let Some((entry, name)) = line.split_once('"') else { continue };
        if name.trim_end().strip_suffix('"') != Some("persist") {
            continue;
        }
        let number = entry.split_once(':')
            .and_then(|(device, _)| device.strip_prefix("mtd"))
            .and_then(|number| number.parse::<i32>().ok())
            .filter(|number| *number >= 0)
            .ok_or("invalid persist MTD number")?;
        if found.replace(number).is_some() {
            return Err("multiple persist MTD partitions");
        }
    }
    found.ok_or("persist MTD partition not found")
}

fn find_persist_mtd() -> Result<i32, &'static str> {
    let fd = SmallFd::open_readonly("/proc/mtd")?;
    let mut buf = [0u8; 4096];
    let len = fd.read(&mut buf)?;
    if len == buf.len() {
        return Err("MTD partition table too long");
    }
    persist_mtd_number(core::str::from_utf8(&buf[..len]).map_err(|_| "invalid MTD partition table")?)
}

fn ubi_mount_source(mtd_num: i32) -> Result<(CString<64>, Option<i32>), &'static str> {
    // Reuse an attachment if the caller retries mounting the same partition.
    let mut ubi_num = None;
    for number in 0..32 {
        let path = heapless::format!(64; "/sys/class/ubi/ubi{number}/mtd_num")
            .map_err(|_| "UBI sysfs path too long")?;
        if let Ok(fd) = SmallFd::open_readonly(path.as_str()) {
            let mut buf = [0u8; 16];
            if let Ok(len) = fd.read(&mut buf)
                && core::str::from_utf8(&buf[..len]).ok()
                    .and_then(|text| text.trim().parse::<i32>().ok()) == Some(mtd_num)
            {
                ubi_num = Some(number);
                break;
            }
        }
    }
    let number = match ubi_num {
        Some(number) => number,
        None => {
            let _ = SystemUtil::modprobe("ubi");
            let ctrl = SmallFd::open("/dev/ubi_ctrl")?;
            let mut request = UbiAttachReq { ubi_num: -1, mtd_num, ..Default::default() };
            let attach = ((1u32 << 30) | ((core::mem::size_of::<UbiAttachReq>() as u32) << 16)
                | (b'o' as u32) << 8 | 64) as libc::Ioctl;
            if unsafe { libc::ioctl(ctrl.raw_fd(), attach, &mut request) } < 0 {
                dmesg!("[boot] UBI attach failed for mtd{mtd_num}, errno {}", unsafe { *libc::__errno_location() });
                return Err("UBI attach failed");
            }
            request.ubi_num
        }
    };
    // Yocto's ubinize configuration puts the persist filesystem in volume 0.
    let path = heapless::format!(64; "/dev/ubi{number}_0").map_err(|_| "UBI volume path too long")?;
    let mut source = CString::new();
    source.extend_from_bytes(path.as_bytes()).map_err(|_| "UBI volume path too long")?;
    Ok((source, if ubi_num.is_none() { Some(number) } else { None }))
}

pub fn mount_persist_with_fallback() -> Result<(), &'static str> {
    if let Err(err) = mount_persist() {
        let fd = SmallFd::open_readonly("/proc/cmdline")?;
        let mut buf = [0u8; 4096];
        let len = fd.read(&mut buf)?;
        let cmdline = core::str::from_utf8(&buf[..len]).map_err(|_| "invalid kernel cmdline")?;
        if !cmdline.split_ascii_whitespace().any(|arg| arg == "c2a_no_ovl") {
            return Err(err);
        }

        dmesg!("[boot] Failed to mount /persist: {err}; mounting temporary tmpfs (c2a_no_ovl)");
        mount_persist_source(c"tmpfs", c"tmpfs")?;
    }
    Ok(())
}

pub fn mount_persist() -> Result<(), &'static str> {
    let _ = SystemUtil::mkdir_if_missing("/persist");
    let mtd_num = find_persist_mtd()?;
    if let Err(err) = mount_persist_ubifs(mtd_num) {
        dmesg!("[boot] UBIFS persist probe failed: {err}; trying JFFS2 on mtd{mtd_num}");
        let _ = SystemUtil::modprobe("mtdblock");
        let _ = SystemUtil::modprobe("jffs2");
        let path = heapless::format!(64; "/dev/mtdblock{mtd_num}")
            .map_err(|_| "mount source path too long")?;
        let mut source = CString::<64>::new();
        source.extend_from_bytes(path.as_bytes()).map_err(|_| "mount source path too long")?;
        mount_persist_source(&source, c"jffs2")?;
    }
    Ok(())
}

fn mount_persist_ubifs(mtd_num: i32) -> Result<(), &'static str> {
    let _ = SystemUtil::modprobe("ubifs");
    let (source, attached) = ubi_mount_source(mtd_num)?;
    let result = mount_persist_source(&source, c"ubifs");
    if result.is_err()
        && let Some(mut number) = attached
    {
        // Release only the attachment created by this probe before trying JFFS2.
        // Linux UBI_IOCDET = _IOW('o', 65, __s32).
        let ctrl = SmallFd::open("/dev/ubi_ctrl")?;
        let detach = ((1u32 << 30) | ((core::mem::size_of::<i32>() as u32) << 16)
            | (b'o' as u32) << 8 | 65) as libc::Ioctl;
        if unsafe { libc::ioctl(ctrl.raw_fd(), detach, &mut number) } < 0 {
            dmesg!("[boot] Failed to detach ubi{number} after unsuccessful persist probe");
            return Err("UBI detach failed");
        }
    }
    result
}

fn mount_persist_source(source: &core::ffi::CStr, fs_type: &core::ffi::CStr) -> Result<(), &'static str> {
    let _ = SystemUtil::mkdir_if_missing("/persist");
    dmesg!("[boot] Starting persist mount ({fs_type:?})");
    let ret = unsafe { libc::mount(source.as_ptr(), c"/persist".as_ptr(), fs_type.as_ptr(), 0, core::ptr::null()) };
    dmesg!("[boot] Finished persist mount ({fs_type:?})");
    if ret == 0 { Ok(()) } else { Err("mount persist failed") }
}

pub fn mount_persist_overlays() {
    let _ = SystemUtil::mkdir_if_missing("/persist");
    let _ = SystemUtil::mkdir_if_missing("/persist/c2a_bluetooth");
    let _ = SystemUtil::mkdir_if_missing("/persist/c2a_config");
    let _ = SystemUtil::mkdir_if_missing("/var/lib/bluetooth");
    let _ = SystemUtil::mount_bind("/persist/c2a_bluetooth", "/var/lib/bluetooth");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ubi_attach_matches_kernel_abi() {
        assert_eq!(core::mem::size_of::<UbiAttachReq>(), 24);
        assert_eq!(core::mem::offset_of!(UbiAttachReq, padding), 16);
    }

    #[test]
    fn persist_lookup_uses_exact_name_and_actual_index() {
        let table = "dev: size erasesize name\nmtd0: 01000000 00010000 \"flash.bin\"\nmtd3: 000e0000 00010000 \"persist-backup\"\nmtd12: 00100000 00010000 \"persist\"\n";
        assert_eq!(persist_mtd_number(table), Ok(12));
        assert_eq!(persist_mtd_number(&table.replace("mtd12:", "mtd6:")), Ok(6));
    }

    #[test]
    fn persist_lookup_rejects_missing_ambiguous_or_invalid_partition() {
        for table in [
            "dev: size erasesize name\n",
            "mtd3: 1000 1000 \"userdata\"\n",
            "mtd3: 1000 1000 \"persist-backup\"\n",
            "mtd3: 1000 1000 \"persist\"\nmtd4: 1000 1000 \"persist\"\n",
            "mtd-1: 1000 1000 \"persist\"\n",
            "mtd2147483648: 1000 1000 \"persist\"\n",
        ] {
            assert!(persist_mtd_number(table).is_err(), "{table}");
        }
    }
}
