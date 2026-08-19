use core::time::Duration;
use heapless::{String, format};

use crate::{
    ModprobeError, SystemUtil,
    boot::udhcpd::{Udhcpd, UdhcpdMode},
    dmesg,
};

pub struct RecoveryGadget;

impl RecoveryGadget {
    const GADGET_DIR: &str = "/sys/kernel/config/usb_gadget/ncm_gadget";

    pub fn start(udc: &str) -> Result<(), &'static str> {
        dmesg!("[recovery-gadget] Starting recovery USB gadget");

        Self::prepare_modules().map_err(|_| "modprobe failed")?;
        Self::prepare_cleanup(udc);
        Self::create_ncm_gadget(udc)?;
        Self::configure_network()?;
        Self::start_udhcpd()?;

        dmesg!("[recovery-gadget] Recovery USB gadget started");
        Ok(())
    }

    pub fn start_best_effort(udc: &str) {
        if let Err(err) = Self::start(udc) {
            dmesg!("[recovery-gadget] Failed to start: {err}");
        }
    }

    fn prepare_modules() -> Result<(), ModprobeError> {
        let _ = SystemUtil::modprobe("libcomposite");
        let _ = SystemUtil::modprobe("configfs");

        Ok(())
    }

    fn prepare_cleanup(udc: &str) {
        let role_path = Self::usb_role_path(udc);

        // Stop service first, then hard-kill leftovers.
        let _ = SystemUtil::run_shell("/etc/init.d/catplay stop");
        let _ = SystemUtil::run_shell("killall -9 catplay_c2a");
        let _ = SystemUtil::run_shell("rm -f /var/run/catplay.pid");
        let _ = SystemUtil::run_shell("mv /usr/bin/catplay_c2a /usr/bin/catplay_c2a.bak");
        SystemUtil::sleep(Duration::from_millis(200));

        for _ in 0..10 {
            let _ = SystemUtil::write_file("/sys/class/iphone/remove", "default");
            if !SystemUtil::path_exists("/sys/module/g_iphone").unwrap_or(false) {
                break;
            }
            let _ = SystemUtil::run_shell("rmmod g_iphone");
            SystemUtil::sleep(Duration::from_millis(100));
        }
        SystemUtil::sleep(Duration::from_millis(200));

        if let Ok(path) = role_path {
            let _ = SystemUtil::write_file(path.as_str(), "device\n");
        }

        if SystemUtil::path_exists(Self::GADGET_DIR).unwrap_or(false) {
            let udc_path = concat!("/sys/kernel/config/usb_gadget/ncm_gadget", "/UDC");
            let umount_path = concat!("/sys/kernel/config/usb_gadget/ncm_gadget", "/configs/c.1/strings/0x409");

            let _ = SystemUtil::write_file(udc_path, "");
            let _ = SystemUtil::path_umount(umount_path);
            let _ = SystemUtil::run_shell("rm -rf /sys/kernel/config/usb_gadget/ncm_gadget");
        }
    }

    fn create_ncm_gadget(udc: &str) -> Result<(), &'static str> {
        SystemUtil::run_shell("mkdir -p /sys/kernel/config/usb_gadget/ncm_gadget")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/idVendor", "0x0525\n")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/idProduct", "0xa4a1\n")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/bcdUSB", "0x0200\n")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/bDeviceClass", "0xEF\n")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/bDeviceSubClass", "0x02\n")?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/bDeviceProtocol", "0x01\n")?;

        SystemUtil::run_shell("mkdir -p /sys/kernel/config/usb_gadget/ncm_gadget/strings/0x409")?;
        SystemUtil::write_file(
            "/sys/kernel/config/usb_gadget/ncm_gadget/strings/0x409/serialnumber",
            "1234567890\n",
        )?;
        SystemUtil::write_file(
            "/sys/kernel/config/usb_gadget/ncm_gadget/strings/0x409/manufacturer",
            "Carlinkit Recovery Mode\n",
        )?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/strings/0x409/product", "USB NCM Gadget\n")?;

        SystemUtil::run_shell("mkdir -p /sys/kernel/config/usb_gadget/ncm_gadget/configs/c.1/strings/0x409")?;
        SystemUtil::write_file(
            "/sys/kernel/config/usb_gadget/ncm_gadget/configs/c.1/strings/0x409/configuration",
            "CDC NCM\n",
        )?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/configs/c.1/MaxPower", "250\n")?;

        SystemUtil::run_shell("mkdir -p /sys/kernel/config/usb_gadget/ncm_gadget/functions/ncm.usb0")?;
        SystemUtil::write_file(
            "/sys/kernel/config/usb_gadget/ncm_gadget/functions/ncm.usb0/host_addr",
            "02:00:00:00:00:01\n",
        )?;
        SystemUtil::write_file(
            "/sys/kernel/config/usb_gadget/ncm_gadget/functions/ncm.usb0/dev_addr",
            "02:00:00:00:00:02\n",
        )?;
        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/functions/ncm.usb0/qmult", "1\n")?;
        SystemUtil::ensure_symlink(
            "/sys/kernel/config/usb_gadget/ncm_gadget/functions/ncm.usb0",
            "/sys/kernel/config/usb_gadget/ncm_gadget/configs/c.1/ncm.usb0",
        )?;

        SystemUtil::write_file("/sys/kernel/config/usb_gadget/ncm_gadget/UDC", udc)?;

        Ok(())
    }

    fn configure_network() -> Result<(), &'static str> {
        SystemUtil::run_shell("ip link set usb0 up")?;
        SystemUtil::run_shell("ip addr add 192.168.51.2/24 dev usb0")?;
        Ok(())
    }

    fn start_udhcpd() -> Result<(), &'static str> {
        Udhcpd::start(UdhcpdMode::Recovery("usb0"))
    }

    fn usb_role_path(udc: &str) -> Result<String<128>, &'static str> {
        format!(128; "/sys/class/usb_role/{}-role-switch/role", udc).map_err(|_| "udc too long")
    }
}
