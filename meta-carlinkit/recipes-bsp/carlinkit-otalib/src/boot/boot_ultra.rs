use core::time::Duration;

use crate::{
    Flash, SystemUtil,
    boot::{
        boot_persist::{mount_persist, mount_persist_overlays},
        boot_platform::{BootPlatform, BootTarget},
        boot_radio::BootRadio,
        catplay::{CatPlay, CatplayConfig},
        hostapd::{Hostapd, HostapdConfigParams, P2P_ULTRA, match_hostapd_template},
        recovery_gadget::RecoveryGadget,
        sysctl::apply_sysctl,
    },
    dmesg,
    imx::boot::ImxBootUtil,
    modprobe_util::ModprobeUtil,
    nostd::SmallFd,
    telnet::TelnetServer,
};
use heapless::format;

pub struct BootUltra(());

impl BootUltra {
    pub fn boot_early() {
        let boot = match BootPlatform::detect() {
            v @ BootPlatform::Imx => {
                dmesg!("[boot] Platform: IMX6");
                ImxBootUtil::setup_usb();
                ImxBootUtil::setup_charger();
                ImxBootUtil::setup_rgb();
                v.params()
            }
            v @ BootPlatform::Ingenic => {
                dmesg!("[boot] Platform: Ingenic/Ultra");
                let _ = SystemUtil::write_file("/sys/class/leds/blue/brightness", "1");
                if let Some(_fork_guard) = SystemUtil::fork_guard() {
                    dmesg!("[boot] Modprobing jz4740_mmc");
                    let _ = ModprobeUtil::modprobe("jz4740_mmc");
                    dmesg!("[boot] Finished modprobing jz4740_mmc");
                }
                v.params()
            }
            v @ BootPlatform::V821 => {
                dmesg!("[boot] Platform: V821 beta");
                if let Some(_fork_guard) = SystemUtil::fork_guard() {
                    dmesg!("[boot] Modprobing sunxi-mmc");
                    let _ = ModprobeUtil::modprobe("sunxi-mmc");
                    dmesg!("[boot] Finished modprobing sunxi-mmc");
                }

                v.params()
            }
        };

        let target = boot.target;

        let ssid = "C2A_AP";
        let ssid_p2p = "C2A_P2P";
        let pass = "lovec@ts";
        let channel = if Flash::is_ultra() { "48" } else { "36" };
        let name = "CatDongle";
        let wlan0 = "wlan0";
        let p2p0 = "p2p0";
        let hci0 = "hci0";

        Self::log_release();
        dmesg!("[boot] Boot mode: {target:?}");

        // Boot flow
        // +---------------------+
        // | Start boot sequence |
        // +----------+----------+
        //            |
        //            v
        // +-------------------------------------+
        // | 1) fix_lo_iface()                   |
        // |    - ensure/create loopback 'lo'    |
        // +----------+--------------------------+
        //            |
        //            v
        // +---------------------+
        // | 2) Apply sysctls    |
        // +----------+----------+
        //            |
        //            v
        // +-------------------------------+
        // | 3) Branch by BootTarget       |
        // +---------------+---------------+
        //                 |
        //      +----------+----------+
        //      |                     |
        //      v                     v
        // +-----------+       +--------------------+
        // | CatPlay   |       | Recovery           |
        // | - modprobe|       | - skip g_iphone    |
        // |   g_iphone|       | - skip CatPlay     |
        // | - boot    |       | - start USB gadget |
        // |   CatPlay |       +---------+----------+
        // +-----+-----+                 |
        //       |                       |
        //       +-----------+-----------+
        //                   |
        //                   v
        // +--------------------------------------+
        // | 4) Start early radio init (bg fork)  |
        // +------------------+-------------------+
        //                    |
        //                    v
        // +--------------------------------------+
        // | 5) Mount /persist + persist overlays |
        // +------------------+-------------------+
        //                    |
        //                    v
        // +--------------------------------------+
        // | 6) Full radio init + start hostapd   |
        // +------------------+-------------------+
        //                    |
        //                    v
        //        +-----------+------------+
        //        | Radio started OK?      |
        //        +-----------+------------+
        //                    |
        //          +---------+---------+
        //          |                   |
        //          v                   v
        // +--------------------+   +------------------------------+
        // | Continue normal    |   | Start USB recovery gadget    |
        // | operation          |   | (if not already started)     |
        // +--------------------+   +------------------------------+

        BootRadio::fix_lo_iface();

        if let Err(err) = apply_sysctl(boot.sysctls) {
            dmesg!("[boot] Failed to fully apply sysctls: {err}");
        }

        if let Some(_fork_guard) = SystemUtil::fork_guard() {
            if let Ok(t) = TelnetServer::new(4444) {
                let _ = t.run_forked();
            }
            return;
        }

        if let Some(_fork_guard) = SystemUtil::fork_guard() {
            SystemUtil::set_sched_idle();
            dmesg!("[boot] Start readahead");

            for p in boot.readahead.iter() {
                let path = p.as_str();
                dmesg!("[boot] Start readahead of {path}");
                SystemUtil::readahead(path);
                dmesg!("[boot] Finish readahead of {path}");
            }
            dmesg!("[boot] Finish readahead");
            return;
        }

        if let Some(_fork_guard) = SystemUtil::fork_guard() {
            BootRadio::patch_bluez_config();
            BootRadio::boot_radio_early();
            return;
        }

        let mut recovery_gadget_started = false;

        if target == BootTarget::CatPlay {
            dmesg!("[boot] Loading g_iphone");
            let _ = SystemUtil::modprobe_with_params(
                "g_iphone",
                &format!(256; "udc_name={} device_name=default", boot.main_udc).expect("g_iphone args too long"),
            );
            dmesg!("[boot] Loading g_iphone done");
        } else if target == BootTarget::Recovery {
            dmesg!("[boot] Starting USB recovery gadget");
            RecoveryGadget::start_best_effort(boot.main_udc);
            recovery_gadget_started = true;
        }

        if target != BootTarget::Recovery
            && let Err(err) = mount_persist(boot.persist_mtd, boot.persist_fs)
        {
            dmesg!("[boot] Failed to mount /persist, falling back to tmpfs overlay: {err}");
        }

        mount_persist_overlays();

        if target == BootTarget::CatPlay {
            let cfg = CatplayConfig {
                // Would be better to check radio chip against p2p whitelist first
                // but for now CatPlay forks too early to do that
                ssid: if boot.p2p_catplay { ssid_p2p } else { ssid },
                wpa_passphrase: pass,
                wlan_dev: if boot.p2p_catplay { p2p0 } else { wlan0 },
                hci_dev: hci0,
                name,
                udc: boot.main_udc,
                udc_extra: boot.extra_udc,
                mfi_bus: boot.mfi_bus,
                mfi_dev_addr: boot.mfi_dev_addr,
                disable_gadget: false,
                channel: Some(channel),
            };
            if let Err(err) = CatPlay::start(&cfg) {
                dmesg!("Failed to boot catplay, continuing anyway: {err}");
            }
        }

        let mut radio_started = true;
        let mut p2p_started = false;

        let mut radio = None;
        match BootRadio::boot_radio() {
            Ok(v) => {
                radio.replace(v);
            }
            Err(err) => {
                dmesg!("[boot] Failed to setup radio: {err:?}");
                radio_started = false;
            }
        }

        if boot.p2p_allow
            && let Some(radio) = radio
            && boot.p2p_whitelist.contains(&radio)
        {
            match BootRadio::boot_radio_p2p() {
                Ok(_) => {
                    match Hostapd::start_p2p(
                        P2P_ULTRA,
                        &HostapdConfigParams {
                            ssid: ssid_p2p,
                            wpa_passphrase: pass,
                            channel_override: Some(channel),
                        },
                    ) {
                        Ok(_) => {
                            dmesg!("[boot] Started p2p radio");
                            p2p_started = true;
                        }
                        Err(err) => {
                            dmesg!("[boot] Failed to start wpa supplicant: {err}");
                            radio_started = false;
                        }
                    }
                }
                Err(err) => {
                    dmesg!("[boot] Failed to setup p2p radio: {err:?}");
                    radio_started = false;
                }
            }
        }

        if radio_started
            && let Some(radio) = radio
            && !(p2p_started && boot.p2p_without_hostapd)
            && let Err(err) = Hostapd::start(
                match_hostapd_template(radio),
                &HostapdConfigParams {
                    ssid,
                    wpa_passphrase: pass,
                    channel_override: Some(channel),
                },
            )
        {
            dmesg!("[boot] Failed to start hostapd: {err}");
            radio_started = false;
        }

        match radio_started {
            true => {
                dmesg!("[boot] Successful radio start");
            }
            false => {
                dmesg!("[boot] Failed radio start; starting USB recovery gadget");
                if !recovery_gadget_started {
                    RecoveryGadget::start_best_effort(boot.main_udc);
                }
            }
        }

        if radio_started
            && !recovery_gadget_started
            && let Some(_fork_guard) = SystemUtil::fork_guard()
        {
            SystemUtil::sleep(Duration::from_millis(10000));
            let test = SystemUtil::run_shell(if p2p_started {
                "ps | grep wpa_supplicant | grep -v grep"
            } else {
                "ps | grep hostapd | grep -v grep"
            });
            if test.is_err() {
                dmesg!("[boot] Hostapd has crashed post-start! Starting recovery...");
                RecoveryGadget::start_best_effort(boot.main_udc);
            } else {
                dmesg!("[boot] Hostapd seems stable, no need to start recovery")
            }
        }
    }

    /// Print /etc/c2a-release (written into the rootfs at build time: firmware tree, kernel
    /// subset and CatPlay commit) so every dmesg capture says what it was running.
    fn log_release() {
        let Ok(fd) = SmallFd::open_readonly("/etc/c2a-release") else {
            dmesg!("[boot] Release: /etc/c2a-release missing");
            return;
        };
        let mut buf = [0u8; 192];
        let n = fd.read(&mut buf).unwrap_or(0);
        match core::str::from_utf8(&buf[..n]) {
            Ok(text) => dmesg!("[boot] Release: {}", text.trim_end()),
            Err(_) => dmesg!("[boot] Release: /etc/c2a-release unreadable"),
        }
    }

    pub fn boot_late() {
        // Some timing or UPX issue used to make Dropbear crash on first start on MIPS so keeping this to be safe
        if let Some(_fork_guard) = SystemUtil::fork_guard() {
            for _i in 0..10 {
                let _ = SystemUtil::run_shell("/etc/init.d/dropbear start");
                SystemUtil::sleep(Duration::from_millis(50));
            }
        }
    }
}
