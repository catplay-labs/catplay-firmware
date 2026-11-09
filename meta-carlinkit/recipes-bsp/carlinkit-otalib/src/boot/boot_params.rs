use crate::nostd::SmallFd;

#[derive(Default)]
pub struct BootParams {
    /// Additional g_iphone and CatPlay startup delay, in seconds.
    pub boot_delay: u32,
}

#[cfg(test)]
mod tests {
    use super::BootParams;

    #[test]
    fn reads_dt_delay_and_defaults_missing_or_malformed_values() {
        let root =
            std::env::temp_dir().join(std::format!("catplay-boot-params-{}", std::process::id()));
        let node = root.join("boot-params");
        std::fs::create_dir_all(&node).unwrap();
        let root_path = root.to_str().unwrap();
        assert!(BootParams::load_from_root(root_path).is_none());

        std::fs::write(node.join("compatible"), b"catplay,boot-params\0").unwrap();
        assert_eq!(BootParams::load_from_root(root_path).unwrap().boot_delay, 0);
        for delay in [0u32, 4, 256, u32::MAX] {
            std::fs::write(node.join("boot-delay"), delay.to_be_bytes()).unwrap();
            assert_eq!(
                BootParams::load_from_root(root_path).unwrap().boot_delay,
                delay
            );
        }
        for invalid in [&[0u8, 0, 4][..], &[0, 0, 0, 4, 0][..]] {
            std::fs::write(node.join("boot-delay"), invalid).unwrap();
            assert_eq!(BootParams::load_from_root(root_path).unwrap().boot_delay, 0);
        }
        std::fs::write(node.join("compatible"), b"catplay,other\0").unwrap();
        assert!(BootParams::load_from_root(root_path).is_none());
        std::fs::remove_dir_all(root).unwrap();
    }
}

impl BootParams {
    pub fn load() -> Self {
        for root in ["/proc/device-tree", "/sys/firmware/devicetree/base"] {
            if let Some(params) = Self::load_from_root(root) {
                return params;
            }
        }
        Self::default()
    }

    fn load_from_root(root: &str) -> Option<Self> {
        let path = heapless::format!(256; "{root}/boot-params/compatible").ok()?;
        let fd = SmallFd::open_readonly(&path).ok()?;
        let mut compatible = [0; 512];
        let len = fd.read(&mut compatible).ok()?;
        if !compatible[..len]
            .split(|byte| *byte == 0)
            .any(|value| value == b"catplay,boot-params")
        {
            return None;
        }

        let boot_delay = (|| {
            let path = heapless::format!(256; "{root}/boot-params/boot-delay").ok()?;
            let fd = SmallFd::open_readonly(&path).ok()?;
            // DT cells are big-endian u32 values. Reject truncated or extra cells.
            let mut bytes = [0; 5];
            let mut len = 0;
            while len < bytes.len() {
                let n = fd.read(&mut bytes[len..]).ok()?;
                if n == 0 {
                    break;
                }
                len += n;
            }
            let cell: [u8; 4] = bytes[..len].try_into().ok()?;
            Some(u32::from_be_bytes(cell))
        })()
        .unwrap_or(0);

        Some(Self { boot_delay })
    }
}
