use core::{ffi::CStr, fmt, mem, ptr, time::Duration};

use crate::nostd::SmallFd;

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Radio {
    RTL8822BS,
    RTL8822CS,
    RTL8733BS,
    BCM4335,
    BCM4354,
    BCM4358,
    BCM43569,
    SD8997,
    SD8987,
    IW416,

    AIC8800D80, // Only Ultra

    Offline,
    Unknown,
}

impl Radio {
    pub fn detect() -> Self {
        id_radio()
    }

    pub fn detect_or_timeout(timeout: Duration) -> Self {
        let radio = Self::detect();
        if radio != Radio::Offline {
            return radio;
        }

        if let Some(radio) = wait_for_radio_uevent(timeout) {
            return radio;
        }

        poll_for_radio(timeout)
    }

    pub fn valid(&self) -> bool {
        !matches!(self, Radio::Offline | Radio::Unknown)
    }
}

fn wait_for_radio_uevent(timeout: Duration) -> Option<Radio> {
    let fd = unsafe {
        libc::socket(
            libc::AF_NETLINK,
            libc::SOCK_DGRAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            libc::NETLINK_KOBJECT_UEVENT,
        )
    };
    if fd < 0 {
        return None;
    }

    let mut addr: libc::sockaddr_nl = unsafe { mem::zeroed() };
    addr.nl_family = libc::AF_NETLINK as libc::sa_family_t;
    addr.nl_pid = 0;
    addr.nl_groups = 1;
    let ret = unsafe {
        libc::bind(
            fd,
            &addr as *const _ as *const libc::sockaddr,
            mem::size_of::<libc::sockaddr_nl>() as libc::socklen_t,
        )
    };
    if ret < 0 {
        unsafe { libc::close(fd) };
        return None;
    }

    let radio = Radio::detect();
    if radio != Radio::Offline {
        unsafe { libc::close(fd) };
        return Some(radio);
    }

    let radio = wait_for_radio_fd(fd, timeout);
    unsafe { libc::close(fd) };
    Some(radio)
}

fn wait_for_radio_fd(fd: libc::c_int, timeout: Duration) -> Radio {
    let timeout_ms = timeout.as_millis().min(u64::MAX as u128) as u64;
    let deadline_ms = monotonic_millis().saturating_add(timeout_ms);
    let mut pollfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };

    loop {
        let now_ms = monotonic_millis();
        if now_ms >= deadline_ms {
            return Radio::Offline;
        }

        let timeout_ms = (deadline_ms - now_ms).min(libc::c_int::MAX as u64) as libc::c_int;
        let ret = unsafe { libc::poll(&mut pollfd, 1, timeout_ms) };
        if ret == 0 {
            return Radio::Offline;
        }
        if ret < 0 {
            let errno = unsafe { *libc::__errno_location() };
            if errno == libc::EINTR {
                continue;
            }
            return Radio::Offline;
        }

        let mut buf = [0u8; 2048];
        loop {
            let ret = unsafe { libc::recv(fd, buf.as_mut_ptr() as *mut _, buf.len(), libc::MSG_DONTWAIT) };
            if ret < 0 {
                let errno = unsafe { *libc::__errno_location() };
                if errno == libc::EINTR {
                    continue;
                }
                break;
            }
            if ret == 0 {
                break;
            }

            let radio = Radio::detect();
            if radio != Radio::Offline {
                return radio;
            }
        }
    }
}

fn poll_for_radio(timeout: Duration) -> Radio {
    let timeout_ms = timeout.as_millis().min(u64::MAX as u128) as u64;
    let deadline_ms = monotonic_millis().saturating_add(timeout_ms);

    loop {
        let now_ms = monotonic_millis();
        if now_ms >= deadline_ms {
            return Radio::Offline;
        }

        let radio = Radio::detect();
        if radio != Radio::Offline {
            return radio;
        }

        let sleep_ms = (deadline_ms - now_ms).min(20) as libc::c_int;
        unsafe {
            libc::poll(ptr::null_mut(), 0, sleep_ms);
        }
    }
}

fn monotonic_millis() -> u64 {
    let mut ts: libc::timespec = unsafe { core::mem::zeroed() };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) } < 0 {
        return 0;
    }

    ts.tv_sec as u64 * 1000 + ts.tv_nsec as u64 / 1_000_000
}

impl fmt::Display for Radio {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Radio::RTL8822BS => "RTL8822BS",
            Radio::RTL8822CS => "RTL8822CS",
            Radio::RTL8733BS => "RTL8733BS",
            Radio::BCM4335 => "BCM4335",
            Radio::BCM4354 => "BCM4354",
            Radio::BCM4358 => "BCM4358",
            Radio::BCM43569 => "BCM43569",
            Radio::SD8997 => "SD8997",
            Radio::SD8987 => "SD8987",
            Radio::IW416 => "IW416",
            Radio::AIC8800D80 => "AIC8800D80",

            Radio::Unknown => "unknown",
            Radio::Offline => "offline",
        };
        f.write_str(s)
    }
}

pub fn id_radio() -> Radio {
    // RCA is assigned during card initialization and is not a stable identity.
    // Only function 1 is used for radio identification on supported platforms.
    id_radio_glob(c"/sys/bus/sdio/devices/mmc*:1/device")
}

struct RadioDevices(libc::glob_t);

impl Drop for RadioDevices {
    fn drop(&mut self) {
        unsafe { libc::globfree(&mut self.0) };
    }
}

fn id_radio_glob(pattern: &CStr) -> Radio {
    let mut devices = RadioDevices(unsafe { mem::zeroed() });
    if unsafe { libc::glob(pattern.as_ptr(), 0, None, &mut devices.0) } != 0 {
        return Radio::Offline;
    }

    let mut result = Radio::Offline;
    for index in 0..devices.0.gl_pathc {
        let path = unsafe { CStr::from_ptr(*devices.0.gl_pathv.add(index)) };
        let Ok(path) = path.to_str() else { continue };
        let Ok(fd) = SmallFd::open_readonly(path) else {
            continue;
        };
        let mut buf = [0u8; 32];
        let Ok(len) = fd.read(&mut buf) else { continue };
        let Ok(id) = core::str::from_utf8(&buf[..len]) else {
            continue;
        };
        result = radio_from_device_id(id.trim());
        if result != Radio::Unknown {
            return result;
        }
    }
    result
}

fn radio_from_device_id(s: &str) -> Radio {
    match s {
        "0xb822" => Radio::RTL8822BS,
        "0xc822" => Radio::RTL8822CS,
        "0xb733" => Radio::RTL8733BS,
        "0x4335" => Radio::BCM4335,
        "0x4354" => Radio::BCM4354,
        "0x4358" => Radio::BCM4358,
        "0xaa31" => Radio::BCM43569,
        "0x9141" => Radio::SD8997,
        "0x9149" => Radio::SD8987,
        "0x9159" => Radio::IW416,

        "0x0082" => Radio::AIC8800D80, // Only Ultra
        _ => Radio::Unknown,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        ffi::CString,
        fs,
        path::PathBuf,
        sync::atomic::{AtomicUsize, Ordering},
    };

    struct Devices(PathBuf);
    impl Devices {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(std::format!(
                "otalib-sdio-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn add(&self, name: &str, id: &str) {
            let path = self.0.join(name);
            fs::create_dir(&path).unwrap();
            fs::write(path.join("device"), id).unwrap();
        }
        fn detect(&self) -> Radio {
            let pattern = CString::new(std::format!("{}/mmc*:1/device", self.0.display())).unwrap();
            id_radio_glob(&pattern)
        }
    }
    impl Drop for Devices {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn discovers_radio_independently_of_host_and_rca() {
        for name in ["mmc1:390b:1", "mmc1:9c85:1", "mmc7:abcd:1"] {
            let devices = Devices::new();
            devices.add(name, "0x0082\n");
            assert_eq!(devices.detect(), Radio::AIC8800D80, "{name}");
        }
    }

    #[test]
    fn skips_secondary_functions_and_non_mmc_devices() {
        let devices = Devices::new();
        for name in [
            "mmc1:9c85:2",
            "mmc1:9c85:10",
            "mmc1:9c85:11",
            "other:9c85:1",
        ] {
            devices.add(name, "0x0082\n");
        }
        assert_eq!(devices.detect(), Radio::Offline);
        devices.add("mmc3:1234:1", "0xb733\n");
        assert_eq!(devices.detect(), Radio::RTL8733BS);
    }

    #[test]
    fn continues_past_unknown_and_missing_attributes() {
        let devices = Devices::new();
        devices.add("mmc0:1111:1", "0xffff\n");
        fs::create_dir(devices.0.join("mmc1:2222:1")).unwrap();
        assert_eq!(devices.detect(), Radio::Unknown);
        devices.add("mmc2:3333:1", "0x4335\n");
        assert_eq!(devices.detect(), Radio::BCM4335);
    }

    #[test]
    fn empty_or_missing_bus_is_offline() {
        let devices = Devices::new();
        assert_eq!(devices.detect(), Radio::Offline);
        let pattern = CString::new(std::format!(
            "{}/missing/mmc*:1/device",
            devices.0.display()
        ))
        .unwrap();
        assert_eq!(id_radio_glob(&pattern), Radio::Offline);
    }
}
