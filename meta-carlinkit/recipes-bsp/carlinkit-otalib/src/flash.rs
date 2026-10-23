use crate::{Hwid, nostd::SmallFd, sign_wic};

#[derive(Debug, PartialEq, Eq)]
pub enum FlashLayout {
    /// Modern(custom) firmware - /dev/mtdblock0 reserved as 16MB block + other partitions
    Modern,
    /// Legacy firmware - /dev/mtdblock0 + /dev/mtdblock1 + /dev/mtdblock2 = 16MB sum
    Legacy,
    // Carlinkit Mini Ultra NOR - 16MB SPI NOR - /dev/mtdblock_bbt_ro0 .. ro3 = 16MB sum
    // 0 = 128KB uboot, 1 = 3MB kernel, 2 = 9.5MB rootfs, 3 = 3.375MB userdata
    LegacyUltraNor,
    // Carlinkit Mini Ultra NAND = 128MB SPI NAND - /dev/mtdblock_bbt_ro0 .. ro6 = 128MB sum
    LegacyUltraNand,
    /// Unknown flash layout detected
    Unknown,
}

const FLASH_SIZE: usize = 16 * 1024 * 1024;
const FLASH_BLOCK_SIZE: usize = 64 * 1024;
const FLASH_PARTITIONS: [&str; 4] = [
    "/dev/mtdblock0\0",
    "/dev/mtdblock1\0",
    "/dev/mtdblock2\0",
    "/dev/mtdblock3\0", // only ultra
];
const FLASH_PARTITION_STATS: [&str; 4] = [
    "/sys/class/block/mtdblock0/size\0",
    "/sys/class/block/mtdblock1/size\0",
    "/sys/class/block/mtdblock2/size\0",
    "/sys/class/block/mtdblock3/size\0", // only ultra
];

const ULTRA_TEST_PARTITION: &str = "/dev/mtdblock_bbt_ro0";
const ULTRA_NAND_FLASH_SIZE: usize = 128 * 1024 * 1024;

pub struct Flash {
    pub layout: FlashLayout,
}

impl Default for Flash {
    fn default() -> Self {
        Self::new()
    }
}

impl Flash {
    pub fn new() -> Self {
        let layout = Self::layout();
        Self { layout }
    }

    pub fn valid(&self) -> bool {
        self.layout != FlashLayout::Unknown
    }

    pub fn size(&self) -> usize {
        if Self::is_ultra() && Self::layout() == FlashLayout::LegacyUltraNand {
            return ULTRA_NAND_FLASH_SIZE;
        }

        FLASH_SIZE
    }

    pub fn stat_partition(i: usize) -> Result<usize, &'static str> {
        let fd = SmallFd::open_readonly(FLASH_PARTITION_STATS[i]);
        let Ok(fd) = fd else {
            return Err("partition is offline");
        };

        // /sys/class/block/mtdblockX/size * 512
        let mut size_str = [0u8; 64];
        let n = fd.read(&mut size_str)?;
        let size = unsafe { str::from_utf8_unchecked(&size_str[..n]) };
        Ok(size.trim().parse::<usize>().unwrap() * 512)
    }

    pub fn is_ultra() -> bool {
        !crate::boot::boot_platform::BootPlatform::is_v851()
            && SmallFd::open_readonly(ULTRA_TEST_PARTITION).is_ok()
    }

    pub fn layout() -> FlashLayout {
        let mut sizes = [0usize; 4];

        for (i, _) in FLASH_PARTITIONS.iter().enumerate() {
            if let Ok(size) = Self::stat_partition(i) {
                sizes[i] = size;
            }
        }

        let sum: usize = sizes.iter().sum();
        if Self::is_ultra() && sum == ULTRA_NAND_FLASH_SIZE {
            return FlashLayout::LegacyUltraNand;
        }

        if sizes[0] == FLASH_SIZE {
            return FlashLayout::Modern;
        }

        if sum == FLASH_SIZE && sizes[0] > 0 && sizes[1] > 0 && sizes[2] > 0 {
            if sizes[3] > 0 && Self::is_ultra() {
                return FlashLayout::LegacyUltraNor;
            }

            return FlashLayout::Legacy;
        }

        FlashLayout::Unknown
    }

    pub fn write(&self, offset: usize, data: &[u8]) -> Result<(), &'static str> {
        if offset + data.len() > FLASH_SIZE {
            return Err("write exceeds flash size");
        }

        match Self::layout() {
            FlashLayout::Modern => {
                let fd = SmallFd::open(FLASH_PARTITIONS[0])?;
                let mut mmap = fd.mmap(0, FLASH_SIZE)?;
                mmap.write_if_different(offset, data, FLASH_BLOCK_SIZE)?;
                Self::sync_flash();
                Ok(())
            }
            FlashLayout::Legacy | FlashLayout::LegacyUltraNor => {
                let mut sizes = [0usize; 4];

                for (i, _) in FLASH_PARTITIONS.iter().enumerate() {
                    if let Ok(size) = Self::stat_partition(i) {
                        sizes[i] = size;
                    } else {
                        return Err("legacy partition is offline");
                    }
                }

                let mut written = 0;
                let mut off = offset;

                let total: usize = sizes.iter().sum();
                if offset + data.len() > total {
                    return Err("write exceeds flash size #2");
                }

                for i in 0..sizes.len() {
                    if off >= sizes[i] {
                        off -= sizes[i];
                        continue;
                    }

                    let avail = sizes[i] - off;
                    let to_write = core::cmp::min(avail, data.len() - written);

                    let fd = SmallFd::open(FLASH_PARTITIONS[i])?;
                    let mut mmap = fd.mmap(0, sizes[i])?;
                    mmap.write_if_different(off, &data[written..written + to_write], FLASH_BLOCK_SIZE)?;
                    mmap.msync()?;

                    written += to_write;
                    off = 0;

                    if written >= data.len() {
                        break;
                    }
                }

                Self::sync_flash();
                Ok(())
            }

            // TODO NAND: write using nandwrite and /dev/mtdX
            FlashLayout::LegacyUltraNand => Err("flashing ultra NAND not supported yet"),
            FlashLayout::Unknown => Err("unknown flash layout"),
        }
    }

    pub fn read(&self, offset: usize, data: &mut [u8]) -> Result<(), &'static str> {
        if offset + data.len() > FLASH_SIZE {
            return Err("read exceeds flash size");
        }

        match Self::layout() {
            FlashLayout::Modern => {
                let fd = SmallFd::open(FLASH_PARTITIONS[0])?;
                let mut mmap = fd.mmap(0, FLASH_SIZE)?;
                mmap.read(offset, data)?;
                Ok(())
            }
            FlashLayout::Legacy | FlashLayout::LegacyUltraNor => {
                let mut sizes = [0usize; 4];

                for (i, _) in FLASH_PARTITIONS.iter().enumerate() {
                    if let Ok(size) = Self::stat_partition(i) {
                        sizes[i] = size;
                    } else {
                        return Err("legacy partition is offline");
                    }
                }

                let mut read_total = 0;
                let mut off = offset;

                let total: usize = sizes.iter().sum();
                if offset + data.len() > total {
                    return Err("read exceeds flash size #2");
                }

                for i in 0..sizes.len() {
                    if off >= sizes[i] {
                        off -= sizes[i];
                        continue;
                    }

                    let avail = sizes[i] - off;
                    let to_read = core::cmp::min(avail, data.len() - read_total);

                    let fd = SmallFd::open(FLASH_PARTITIONS[i])?;
                    let mut mmap = fd.mmap(0, sizes[i])?;
                    mmap.read(off, &mut data[read_total..read_total + to_read])?;

                    read_total += to_read;
                    off = 0;

                    if read_total >= data.len() {
                        break;
                    }
                }

                Ok(())
            }
            // TODO NAND: read using mtdblock_bbt_roX ?
            FlashLayout::LegacyUltraNand => Err("flashing ultra NAND not supported yet"),
            FlashLayout::Unknown => Err("unknown flash layout"),
        }
    }
}

impl Flash {
    fn sync_flash() {
        unsafe {
            libc::syscall(libc::SYS_sync);
        }
    }

    pub fn backup(&self, buf: &mut [u8]) -> Result<(), &'static str> {
        if buf.len() != FLASH_SIZE {
            return Err("invalid buffer size for backup");
        }

        self.read(0, buf)
    }

    pub fn flash_nosign(&self, buf: &[u8]) -> Result<(), &'static str> {
        if buf.len() != FLASH_SIZE {
            return Err("invalid file size for flashing");
        }

        self.write(0, buf)
    }

    pub fn flash_sign(&self, buf: &mut [u8], hwid: Hwid) -> Result<(), &'static str> {
        if buf.len() != FLASH_SIZE {
            return Err("invalid file size for flashing");
        }

        sign_wic(buf, hwid)?;
        self.write(0, buf)
    }

    pub fn flash_autosign(&self, buf: &mut [u8]) -> Result<(), &'static str> {
        if Self::is_ultra() {
            return self.flash_nosign(buf);
        }

        let hwid = Hwid::detect()?;
        self.flash_sign(buf, hwid)
    }

    pub fn flash_fitimage(&self, _buf: &[u8]) -> Result<(), &'static str> {
        if Self::layout() != FlashLayout::Modern {
            return Err("flash layout too old to use fitImage flash");
        }

        todo!("TODO: flash to fitImage partition")
    }

    /// Wipe 128KB at offset 0 to softbrick the device (forced USB boot for easier development)
    pub fn softbrick(&self) -> Result<(), &'static str> {
        let buf = [0u8; 131072];
        self.write(0, &buf)
    }
}
