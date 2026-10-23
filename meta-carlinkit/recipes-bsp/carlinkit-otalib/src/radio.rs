use core::{fmt, mem, ptr, time::Duration};

use crate::nostd::SmallFd;

const SYSFS_PATH: &str = "/sys/bus/sdio/devices/mmc0:0001:1/device\0";
const SYSFS_PATH_V851: &str = "/sys/bus/sdio/devices/mmc0:6721:1/device\0";
const SYSFS_PATH_ULTRA: &str = "/sys/bus/sdio/devices/mmc1:390b:1/device\0";

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
    let mut buf = [0u8; 32];
    let fd = SmallFd::open_readonly(SYSFS_PATH)
        .or_else(|_| SmallFd::open_readonly(SYSFS_PATH_V851))
        .or_else(|_| SmallFd::open_readonly(SYSFS_PATH_ULTRA));
    let Ok(fd) = fd else {
        return Radio::Offline;
    };

    let len = fd.read(&mut buf);
    let Ok(len) = len else {
        return Radio::Offline;
    };

    let slice = &buf[..len];
    let s = unsafe { core::str::from_utf8_unchecked(slice) }.trim();

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
