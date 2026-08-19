use core::time::Duration;

use crate::{
    ModprobeError, Radio, SystemUtil,
    boot::udhcpd::{Udhcpd, UdhcpdMode},
    dmesg,
    modprobe_util::ModprobeUtil,
};

#[derive(Debug)]
pub enum RadioError {
    Modprobe(ModprobeError),
    UnknownRadio,
    OfflineRadio,
    UdhcpdStart(&'static str),
    UnsupportedRadio,
}

impl From<ModprobeError> for RadioError {
    fn from(value: ModprobeError) -> Self {
        RadioError::Modprobe(value)
    }
}

pub struct BootRadio(());

impl BootRadio {
    pub fn boot_radio_early() {
        dmesg!("[boot] Starting early radio load");

        // A seperate process that pre-starts parts of `boot_radio` that don't depend on /persist
        let _ = SystemUtil::run_shell("/etc/init.d/dbus-1 start &");

        match Radio::detect_or_timeout(Duration::from_secs(5)) {
            Radio::AIC8800D80 => {
                dmesg!("[boot] early preload for aic8800_fdrv starting");
                let _ = ModprobeUtil::modprobe("aic8800_fdrv");
                dmesg!("[boot] early preload for aic8800_fdrv returned");
            }
            _ => {}
        }
    }

    pub fn fork_bluez() {
        // Note: BlueZ depends on /persist so it's expected to be mounted at this stage

        dmesg!("[boot] BlueZ forking now");

        SystemUtil::wait_file("/run/dbus/system_bus_socket", 2);
        let _ = SystemUtil::run_shell("(/etc/init.d/bluetooth start) &");
    }

    pub fn boot_radio() -> Result<Radio, RadioError> {
        let mut radio = Radio::detect();
        if radio == Radio::Offline {
            dmesg!("[boot] Polling for radio chip...");
            radio = Radio::detect_or_timeout(Duration::from_secs(5));
            dmesg!("[boot] Finished polling for radio chip. Result: {radio}");
        } else {
            dmesg!("[boot] Found radio chip {}", radio);
        }

        let allow_early_bt_patching = match radio {
            Radio::Offline => return Err(RadioError::OfflineRadio),
            Radio::Unknown => return Err(RadioError::UnknownRadio),
            _ => false,
        };

        Self::fork_bluez();

        if allow_early_bt_patching {
            // This only works if hci_uart is compiled as a module
            dmesg!("[boot] Starting modprobe hci_uart");
            ModprobeUtil::modprobe("hci_uart")?;
            dmesg!("[boot] Finished modprobe hci_uart");
        }

        match radio {
            Radio::AIC8800D80 => {
                dmesg!("[boot] Starting modprobe aic8800_fdrv");
                ModprobeUtil::modprobe("aic8800_fdrv")?;
                dmesg!("[boot] Finished modprobe aic8800_fdrv");
            }
            Radio::BCM4358 => {
                ModprobeUtil::modprobe_with_params(
                    "bcmdhd_sdio",
                    "firmware_path=brcm/fw_bcm4358_ag_apsta.bin nvram_path=brcm/brcmfmac4358-sdio.txt",
                )?;
            }
            Radio::RTL8822CS => {
                ModprobeUtil::modprobe("88x2cs")?;
            }
            Radio::RTL8733BS => {
                ModprobeUtil::modprobe("8733bs")?;
            }
            // Radio::BCM4335 => {}
            Radio::Offline | Radio::Unknown => unreachable!(),
            _ => return Err(RadioError::UnsupportedRadio),
        }

        // Wait for wlan0 with a timeout; don't care about waiting for async hci0 here
        dmesg!("[boot] Polling for wlan0");
        if SystemUtil::wait_file("/sys/class/net/wlan0/address", 10).is_err() {
            dmesg!("[boot] Timed out polling for wlan0");
            return Err(RadioError::OfflineRadio);
        }
        dmesg!("[boot] Found wlan0");

        if !allow_early_bt_patching {
            dmesg!("[boot] Starting modprobe hci_uart");
            ModprobeUtil::modprobe("hci_uart")?;
            dmesg!("[boot] Finished modprobe hci_uart");
        }

        Self::setup_wlan();
        Udhcpd::start(UdhcpdMode::Wlan).map_err(RadioError::UdhcpdStart)?;

        Ok(radio)
    }

    pub fn boot_radio_p2p() -> Result<(), RadioError> {
        Self::setup_p2p();
        Udhcpd::start(UdhcpdMode::P2P).map_err(RadioError::UdhcpdStart)?;
        Ok(())
    }

    fn setup_wlan() {
        let _ = SystemUtil::write_file("/proc/sys/net/ipv6/conf/wlan0/addr_gen_mode", "1");
        let _ = SystemUtil::write_file("/proc/sys/net/ipv6/conf/wlan0/accept_dad", "0");

        let _ = SystemUtil::exec("/sbin/ip", &["addr", "add", "192.168.50.2/24", "dev", "wlan0"]);
        let _ = SystemUtil::exec("/sbin/ip", &["-6", "addr", "add", "fe80::1234:5678:9abc:def4/64", "dev", "wlan0"]);
        let _ = SystemUtil::exec("/sbin/ip", &["link", "set", "wlan0", "up"]);
    }

    fn setup_p2p() {
        // p2p0 is valid only for aic8800
        let _ = SystemUtil::write_file("/proc/sys/net/ipv6/conf/p2p0/addr_gen_mode", "1");
        let _ = SystemUtil::write_file("/proc/sys/net/ipv6/conf/p2p0/accept_dad", "0");

        let _ = SystemUtil::exec("/sbin/ip", &["addr", "add", "192.168.52.2/24", "dev", "p2p0"]);
        let _ = SystemUtil::exec("/sbin/ip", &["-6", "addr", "add", "fe80::1234:5678:9abc:def8/64", "dev", "p2p0"]);
        let _ = SystemUtil::exec("/sbin/ip", &["link", "set", "p2p0", "up"]);
    }

    pub fn fix_lo_iface() {
        let _ = SystemUtil::exec("/sbin/ip", &["addr", "add", "127.0.0.1/8", "dev", "lo"]);
        let _ = SystemUtil::exec("/sbin/ip", &["addr", "add", "::1/128", "dev", "lo"]);
        let _ = SystemUtil::exec("/sbin/ip", &["link", "set", "lo", "up"]);
    }

    pub fn patch_bluez_config() {
        let _ = SystemUtil::unlink_if_exists("/etc/bluetooth/main.conf");
        let cfg = "[General]\nFastConnectable = true\nJustWorksRepairing = always\n";
        let _ = SystemUtil::write_file("/etc/bluetooth/main.conf", cfg);
    }
}
