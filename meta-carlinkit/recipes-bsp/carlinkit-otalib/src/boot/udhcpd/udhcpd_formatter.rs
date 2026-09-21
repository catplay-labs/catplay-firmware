use core::fmt::Write;
use heapless::String;

/// Where udhcpd keeps its lease table between runs, and how often it flushes it.
pub enum UdhcpdLeaseStore {
    /// Leases live in tmpfs and are gone after a reboot.
    Volatile,
    /// Leases survive reboots. CarPlay requires this for the phone-facing AP: the spec
    /// ("Networking and Service Discovery") has the accessory keep DHCP lease information
    /// across reboots so a returning iPhone gets its old address ACK-ed (INIT-REBOOT)
    /// instead of being NAK-ed into a fresh DISCOVER. udhcpd only writes the file on a
    /// timer (default 7200s) or a signal, and the dongle is simply unpowered when the car
    /// is turned off, so the timer has to be short enough to catch a normal drive.
    Persistent { flush_interval_secs: u32 },
}

pub struct UdhcpdConfigParams<'a> {
    pub interface: &'a str,
    pub start: &'a str,
    pub end: &'a str,
    pub subnet: &'a str,
    pub instance: &'a str,
    pub lease_store: UdhcpdLeaseStore,
}

/// Directory for persistent lease files; created by `mount_persist_overlays`.
pub const UDHCPD_PERSIST_DIR: &str = "/persist/c2a_dhcp";

pub fn format_udhcpd_config(params: &UdhcpdConfigParams<'_>) -> Result<String<256>, &'static str> {
    const ERR_TOO_LONG: &str = "udhcpd config too long";
    let mut rendered = String::new();
    macro_rules! pushf {
        ($($arg:tt)*) => {
            rendered
                .write_fmt(format_args!($($arg)*))
                .map_err(|_| ERR_TOO_LONG)?
        };
    }

    pushf!("start\t\t{}\n", params.start);
    pushf!("end\t\t{}\n", params.end);
    pushf!("interface\t{}\n", params.interface);
    match params.lease_store {
        UdhcpdLeaseStore::Volatile => {
            pushf!("lease_file\t/var/lib/udhcpd.{}.leases\n", params.instance);
        }
        UdhcpdLeaseStore::Persistent { flush_interval_secs } => {
            pushf!("lease_file\t{}/udhcpd.{}.leases\n", UDHCPD_PERSIST_DIR, params.instance);
            pushf!("auto_time\t{}\n", flush_interval_secs);
        }
    }
    pushf!("option\tsubnet\t{}\n", params.subnet);
    pushf!("option\tlease\t864000 # 10 days\n");

    Ok(rendered)
}

#[test]
fn format_udhcpd() {
    let config = format_udhcpd_config(&UdhcpdConfigParams {
        interface: "wlan0",
        start: "192.168.50.100",
        end: "192.168.50.200",
        subnet: "255.255.255.0",
        instance: "wifi",
        lease_store: UdhcpdLeaseStore::Volatile,
    })
    .unwrap();

    assert_eq!(
        config,
        "start\t\t192.168.50.100\n\
end\t\t192.168.50.200\n\
interface\twlan0\n\
lease_file\t/var/lib/udhcpd.wifi.leases\n\
option\tsubnet\t255.255.255.0\n\
option\tlease\t864000 # 10 days\n"
    );
}

#[test]
fn format_udhcpd_persistent() {
    let config = format_udhcpd_config(&UdhcpdConfigParams {
        interface: "wlan0",
        start: "192.168.50.100",
        end: "192.168.50.200",
        subnet: "255.255.255.0",
        instance: "wifi",
        lease_store: UdhcpdLeaseStore::Persistent { flush_interval_secs: 60 },
    })
    .unwrap();

    assert_eq!(
        config,
        "start\t\t192.168.50.100\n\
end\t\t192.168.50.200\n\
interface\twlan0\n\
lease_file\t/persist/c2a_dhcp/udhcpd.wifi.leases\n\
auto_time\t60\n\
option\tsubnet\t255.255.255.0\n\
option\tlease\t864000 # 10 days\n"
    );
}
