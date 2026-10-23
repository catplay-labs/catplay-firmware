use crate::{
    Flash, Radio, dmesg,
    modprobe_util::{ModprobeError, ModprobeUtil},
    nostd::SmallFd,
};
use heapless::{String, Vec};

const READAHEAD_PATH_LEN: usize = 512;
const READAHEAD_PATHS_MAX: usize = 24;

pub enum BootPlatform {
    Imx,
    Ingenic,
    V821,
    V851,
}

#[derive(PartialEq, Eq, Debug)]
pub enum BootTarget {
    Recovery,
    CatPlay,
}

pub struct Boot<'a> {
    pub target: BootTarget,
    pub main_udc: &'a str,
    // Optional g_iphone override; None preserves its legacy UDC mapping.
    pub main_rs_name: Option<&'a str>,
    pub main_role_switch: &'a str,
    pub extra_udc: Option<&'a str>,
    pub readahead: Vec<String<READAHEAD_PATH_LEN>, READAHEAD_PATHS_MAX>,
    pub mfi_bus: &'a str,
    pub mfi_dev_addr: &'a str,
    pub sysctls: &'a [(&'a str, &'a str)],
    pub platform: BootPlatform,

    pub p2p_allow: bool,
    pub p2p_whitelist: &'a [Radio],
    pub p2p_without_hostapd: bool,
    pub p2p_catplay: bool,
}

fn read_cmdline_arg<const N: usize>(arg: &str) -> Option<String<N>> {
    let cmdline_fd = match SmallFd::open_readonly("/proc/cmdline") {
        Err(v) => {
            dmesg!("[boot] Failed to open /proc/cmdline: {v}");
            return None;
        }
        Ok(v) => v,
    };
    let mut cmdline_buf = [0u8; 1024];

    match cmdline_fd.read(&mut cmdline_buf) {
        Ok(read_len) => {
            let cmdline = core::str::from_utf8(&cmdline_buf[..read_len]).unwrap_or("");
            let value = cmdline.split_ascii_whitespace().find_map(|entry| {
                let (key, value) = entry.split_once('=')?;
                (key == arg).then_some(value)
            })?;
            let mut out = String::new();
            out.push_str(value).ok()?;
            Some(out)
        }
        Err(err) => {
            dmesg!("[boot] Failed to read /proc/cmdline: {err}");
            None
        }
    }
}

fn read_boot_target_from_cmdline() -> BootTarget {
    match read_cmdline_arg::<64>("c2a_boot").as_deref() {
        Some("recovery") => BootTarget::Recovery,
        _ => BootTarget::CatPlay,
    }
}

fn push_readahead_path(out: &mut Vec<String<READAHEAD_PATH_LEN>, READAHEAD_PATHS_MAX>, path: &str) -> Result<(), &'static str> {
    let mut owned = String::new();
    owned.push_str(path).map_err(|_| "path too long")?;
    out.push(owned).map_err(|_| "too many paths")
}

impl BootPlatform {
    pub fn boot_params_imx() -> Boot<'static> {
        Boot {
            target: read_boot_target_from_cmdline(),
            main_udc: "ci_hdrc.1",
            main_rs_name: None,
            main_role_switch: "ci_hdrc.1",
            extra_udc: Some("ci_hdrc.0"),
            readahead: Vec::new(),
            mfi_bus: "1",
            mfi_dev_addr: "0x11",
            sysctls: &[
                ("net.ipv4.tcp_rmem", "4096 262144 16777216"),
                ("net.ipv4.tcp_wmem", "4096 262144 16777216"),
                ("net.core.rmem_max", "16777216"),
                ("net.core.rmem_default", "2097152"),
                ("net.core.wmem_max", "16777216"),
                ("net.core.wmem_default", "2097152"),
                ("net.ipv4.tcp_moderate_rcvbuf", "1"),
                ("net.ipv4.tcp_low_latency", "1"),

                ("vm.dirty_expire_centisecs", "100"),
                ("vm.dirty_writeback_centisecs", "100")

            ],
            platform: BootPlatform::Imx,

            p2p_allow: false,
            p2p_whitelist: &[],
            p2p_catplay: false,
            p2p_without_hostapd: false,
        }
    }

    pub fn boot_params_ultra() -> Boot<'static> {
        let mut readahead = Vec::new();
        for m in ["aic8800_bsp", "aic8800_fdrv"] {
            if let Err(err) = ModprobeUtil::for_each_dependency_module_path(m, |path| {
                push_readahead_path(&mut readahead, path).map_err(|_| ModprobeError::PushStrFailed)
            }) {
                dmesg!("[boot] Failed to add {m} module paths to readahead: {err:?}");
            }
        }

        for path in [
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_table_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_adid_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_8800d80_u02_ext0.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fmacfw_8800d80_h_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fmacfw_8800d80_u02.bin",
            "/usr/bin/catplay_c2a",
            "/bin/busybox",
            "/usr/libexec/bluetooth/bluetoothd",
            // "/usr/sbin/wpa_supplicant",
            // "/usr/sbin/wpa_cli",
            "/usr/sbin/hostapd",
        ] {
            if let Err(err) = push_readahead_path(&mut readahead, path) {
                dmesg!("[boot] Failed to add {path} to readahead: {err}");
            }
        }

        Boot {
            target: read_boot_target_from_cmdline(),
            main_udc: "13500000.usb",
            main_rs_name: None,
            main_role_switch: "13500000.usb",
            extra_udc: None,
            readahead,
            mfi_bus: "0",
            mfi_dev_addr: "0x10",
            sysctls: &[
                ("net.core.netdev_max_backlog", "512"),
                ("net.ipv4.tcp_moderate_rcvbuf", "1"),
                ("net.ipv4.tcp_low_latency", "1"),
                ("net.ipv4.tcp_max_orphans", "256"),
                ("net.ipv4.tcp_tw_reuse", "1"),
                ("vm.watermark_boost_factor", "0"),
                ("vm.watermark_scale_factor", "10"),
                ("vm.min_free_kbytes", "2048"),
                ("vm.swappiness", "10"),
                ("vm.vfs_cache_pressure", "500"),
                ("vm.page-cluster", "0"),

                ("vm.dirty_expire_centisecs", "100"),
                ("vm.dirty_writeback_centisecs", "100")
            ],
            platform: BootPlatform::Ingenic,

            // Disable AIC8800 P2P for now. Why?
            // Because testing shows iOS is slightly(~1-2s) slower to discover it
            // and the stability level is exactly the same as with a normal hotspot. 
            p2p_allow: false,
            p2p_whitelist: &[Radio::AIC8800D80],
            p2p_catplay: false,
            p2p_without_hostapd: true,
        }
    }

    pub fn boot_params_v821() -> Boot<'static> {
        let mut readahead = Vec::new();
        for m in ["aic8800_bsp", "aic8800_fdrv"] {
            if let Err(err) = ModprobeUtil::for_each_dependency_module_path(m, |path| {
                push_readahead_path(&mut readahead, path).map_err(|_| ModprobeError::PushStrFailed)
            }) {
                dmesg!("[boot] Failed to add {m} module paths to readahead: {err:?}");
            }
        }

        for path in [
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_table_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_adid_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_8800d80_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fw_patch_8800d80_u02_ext0.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fmacfw_8800d80_h_u02.bin",
            "/lib/firmware/aic8800_fw/SDIO/aic8800D80/fmacfw_8800d80_u02.bin",
            "/usr/bin/catplay_c2a",
            "/bin/busybox",
            "/usr/libexec/bluetooth/bluetoothd",
            // "/usr/sbin/wpa_supplicant",
            // "/usr/sbin/wpa_cli",
            "/usr/sbin/hostapd",
        ] {
            if let Err(err) = push_readahead_path(&mut readahead, path) {
                dmesg!("[boot] Failed to add {path} to readahead: {err}");
            }
        }

        Boot {
            target: read_boot_target_from_cmdline(),
            main_udc: "musb-hdrc.1.auto",
            main_rs_name: None,
            main_role_switch: "44100000.usb",
            extra_udc: None,
            readahead,
            mfi_bus: "1",
            mfi_dev_addr: "0x11",
            sysctls: &[
                ("net.core.netdev_max_backlog", "512"),
                ("net.ipv4.tcp_moderate_rcvbuf", "1"),
                ("net.ipv4.tcp_low_latency", "1"),
                ("net.ipv4.tcp_max_orphans", "256"),
                ("net.ipv4.tcp_tw_reuse", "1"),
                ("vm.watermark_boost_factor", "0"),
                ("vm.watermark_scale_factor", "10"),
                ("vm.min_free_kbytes", "2048"),
                ("vm.swappiness", "10"),
                ("vm.vfs_cache_pressure", "500"),
                ("vm.page-cluster", "0"),

                ("vm.dirty_expire_centisecs", "100"),
                ("vm.dirty_writeback_centisecs", "100")
            ],
            platform: BootPlatform::V821,

            p2p_allow: false,
            p2p_whitelist: &[Radio::AIC8800D80],
            p2p_catplay: false,
            p2p_without_hostapd: true,
        }
    }

    pub fn boot_params_v851() -> Boot<'static> {
        Boot {
            target: read_boot_target_from_cmdline(),
            main_udc: "musb-hdrc.2.auto",
            main_rs_name: Some("4100000.usb"),
            main_role_switch: "4100000.usb",
            extra_udc: None,
            readahead: Vec::new(),
            mfi_bus: "3",
            mfi_dev_addr: "0x10",
            sysctls: &[
                ("net.core.netdev_max_backlog", "512"),
                ("net.ipv4.tcp_moderate_rcvbuf", "1"),
                ("net.ipv4.tcp_low_latency", "1"),
                ("net.ipv4.tcp_max_orphans", "256"),
                ("net.ipv4.tcp_tw_reuse", "1"),
                ("vm.watermark_boost_factor", "0"),
                ("vm.watermark_scale_factor", "10"),
                ("vm.min_free_kbytes", "2048"),
                ("vm.swappiness", "10"),
                ("vm.vfs_cache_pressure", "500"),
                ("vm.page-cluster", "0"),
                ("vm.dirty_expire_centisecs", "100"),
                ("vm.dirty_writeback_centisecs", "100"),
            ],
            platform: BootPlatform::V851,
            p2p_allow: false,
            p2p_whitelist: &[Radio::AIC8800D80],
            p2p_without_hostapd: true,
            p2p_catplay: false,
        }
    }

    pub fn is_v851() -> bool {
        for path in [
            "/proc/device-tree/compatible",
            "/sys/firmware/devicetree/base/compatible",
        ] {
            let Ok(fd) = SmallFd::open_readonly(path) else {
                continue;
            };
            let mut bytes = [0u8; 512];
            let Ok(len) = fd.read(&mut bytes) else {
                continue;
            };
            if bytes[..len].split(|byte| *byte == 0).any(|compat| {
                compat == b"allwinner,sun8i-v853"
                    || compat == b"catplay,v851s3-aic8800d80"
                    || compat == b"catplay,v851s3-aic8800d80-recov"
            }) {
                return true;
            }
        }
        false
    }

    pub fn params(&self) -> Boot<'static> {
        match self {
            BootPlatform::Imx => Self::boot_params_imx(),
            BootPlatform::Ingenic => Self::boot_params_ultra(),
            BootPlatform::V821 => Self::boot_params_v821(),
            BootPlatform::V851 => Self::boot_params_v851(),
        }
    }

    pub fn detect() -> Self {
        #[cfg(target_arch = "riscv32")]
        return BootPlatform::V821;

        if Self::is_v851() {
            return BootPlatform::V851;
        }

        match Flash::is_ultra() {
            true => BootPlatform::Ingenic,
            false => BootPlatform::Imx,
        }
    }
}
