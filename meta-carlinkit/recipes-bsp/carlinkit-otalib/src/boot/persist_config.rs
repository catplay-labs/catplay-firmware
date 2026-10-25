use core::fmt::Write;

use heapless::String;

use crate::{dmesg, nostd::SmallFd};

const CONFIG_PATH: &str = "/persist/config.txt";

#[derive(Default)]
pub struct PersistConfig {
    pub wifi_name: Option<String<64>>,
    pub wifi_password: Option<String<64>>,
    pub wifi_channel: Option<String<8>>,
    pub bluetooth_name: Option<String<64>>,

    /// Can be set to `DEBUG`, however this is no-op on production builds
    /// which strip all `debug!` statements due to heavy binary bloat.
    pub rust_log_level: Option<String<32>>,
    /// Dumps iAP2 and RTSP sessions to /tmp/
    /// bypassing default CatPlay verbosity levels.
    pub catplay_tracing: bool,

    /// Delay g_iphone start by N seconds.
    /// This allows time to connect to the dongle and start live tailing the logs
    /// for incompatible cars that attempt to very quickly cut the power upon detecting incompatibility.
    pub g_iphone_start_delay: Option<u32>,
    /// **DO NOT** enable this option permanently unless you are diagnosing
    /// compatibility with cars that cut the power upon detecting incompatibility.
    pub log_persist: bool,
}

impl PersistConfig {
    pub fn load() -> Self {
        let mut config = Self::default();
        let Ok(fd) = SmallFd::open_readonly(CONFIG_PATH) else {
            return config;
        };

        let mut buf = [0u8; 4096];
        let mut len = 0;
        while len < buf.len() {
            match fd.read(&mut buf[len..]) {
                Ok(0) | Err(_) => break,
                Ok(n) => len += n,
            }
        }
        if len == buf.len() {
            dmesg!("[boot] /persist/config.txt is too large; ignoring trailing content");
        }

        for line in buf[..len].split(|byte| *byte == b'\n') {
            let Ok(line) = core::str::from_utf8(line) else {
                continue;
            };
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            let key = key.trim();
            let value = value.trim();
            if value.is_empty() {
                continue;
            }

            match key {
                "wifi_name" => set_string(&mut config.wifi_name, value),
                "wifi_password" => set_string(&mut config.wifi_password, value),
                "wifi_channel" => {
                    if value
                        .parse::<u16>()
                        .is_ok_and(|channel| (1..=196).contains(&channel))
                    {
                        set_string(&mut config.wifi_channel, value);
                    }
                }
                "bluetooth_name" => set_string(&mut config.bluetooth_name, value),
                "g_iphone_start_delay" => {
                    if let Ok(delay) = value.parse::<u32>() {
                        config.g_iphone_start_delay = Some(delay);
                    }
                }
                "log_persist" => {
                    if let Some(enabled) = parse_bool(value) {
                        config.log_persist = enabled;
                    }
                }
                "rust_log_level" => set_string(&mut config.rust_log_level, value),
                "catplay_tracing" => config.catplay_tracing = true,
                _ => {}
            }
        }

        config
    }

    pub fn wifi_ssid(&self, suffix: &str) -> String<80> {
        let mut ssid = String::new();
        let _ = write!(
            ssid,
            "{}{}",
            self.wifi_name.as_deref().unwrap_or("C2A"),
            suffix
        );
        ssid
    }

    pub fn wifi_password(&self) -> &str {
        self.wifi_password.as_deref().unwrap_or("lovec@ts")
    }

    pub fn wifi_channel<'a>(&'a self, fallback: &'a str) -> &'a str {
        self.wifi_channel.as_deref().unwrap_or(fallback)
    }

    pub fn bluetooth_name(&self) -> &str {
        self.bluetooth_name.as_deref().unwrap_or("CatDongle")
    }
}

fn set_string<const N: usize>(slot: &mut Option<String<N>>, value: &str) {
    let mut result = String::new();
    if result.push_str(value).is_ok() {
        *slot = Some(result);
    }
}

fn parse_bool(value: &str) -> Option<bool> {
    match value {
        "true" | "1" | "yes" | "on" => Some(true),
        "false" | "0" | "no" | "off" => Some(false),
        _ => None,
    }
}
