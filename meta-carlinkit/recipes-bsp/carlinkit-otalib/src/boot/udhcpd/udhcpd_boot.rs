use heapless::format;

use crate::{
    SystemUtil,
    boot::udhcpd::{UdhcpdConfigParams, UdhcpdLeaseStore, format_udhcpd_config},
    nostd::SmallFd,
};

pub enum UdhcpdMode<'a> {
    Wlan,
    Recovery(&'a str),
    P2P,
}

pub struct Udhcpd(());

impl Udhcpd {
    pub fn start(mode: UdhcpdMode<'_>) -> Result<(), &'static str> {
        let params = match mode {
            UdhcpdMode::Wlan => UdhcpdConfigParams {
                interface: "wlan0",
                start: "192.168.50.100",
                end: "192.168.50.200",
                subnet: "255.255.255.0",
                instance: "wifi",
                // The CarPlay AP: keep leases across reboots so the returning iPhone is ACK-ed
                // straight away. Flushed every minute; the file is a few dozen bytes.
                lease_store: UdhcpdLeaseStore::Persistent { flush_interval_secs: 60 },
            },
            UdhcpdMode::Recovery(iface) => UdhcpdConfigParams {
                interface: iface,
                start: "192.168.51.100",
                end: "192.168.51.200",
                subnet: "255.255.255.0",
                instance: "recov",
                lease_store: UdhcpdLeaseStore::Volatile,
            },
            UdhcpdMode::P2P => UdhcpdConfigParams {
                interface: "p2p0",
                start: "192.168.52.100",
                end: "192.168.52.200",
                subnet: "255.255.255.0",
                instance: "p2p",
                lease_store: UdhcpdLeaseStore::Volatile,
            },
        };
        let cfg = format_udhcpd_config(&params).map_err(|_| "failed to format udhcpd config")?;

        let path = format!(256; "/tmp/udhcpd.{}.conf", params.instance).map_err(|_| "failed to create udhcpd path")?;
        let _ = SystemUtil::unlink_if_exists(&path);
        let file = SmallFd::create(&path)?;
        file.write(cfg.as_bytes())?;
        drop(file);

        let _ = SystemUtil::exec("/usr/sbin/udhcpd", &[&path]);
        Ok(())
    }
}
