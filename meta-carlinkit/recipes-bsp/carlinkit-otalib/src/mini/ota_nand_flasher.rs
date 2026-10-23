//! NAND OTA flashing PoC used by the `ota` CLI command.
//! The caller must be running from a recovery environment, not from a filesystem
//! on a partition it is about to erase.

extern crate alloc;

use alloc::{vec, vec::Vec};
use core::{ffi::c_void, mem::size_of};

use crate::{
    mini::ota::{HEADER_SIZE, OtaExtractor, OtaImageSection, OtaParseOptions},
    nostd::SmallFd,
    println,
};

const MTD_NANDFLASH: u8 = 4;
const MTD_MLCNANDFLASH: u8 = 8;
const PROC_MTD_MAX: usize = 8192;

// Linux include/uapi/mtd/mtd-abi.h; _IOC layout used by supported Linux targets.
const fn ioc(dir: u32, nr: u32, size: u32) -> libc::Ioctl {
    ((dir << 30) | (size << 16) | (b'M' as u32) << 8 | nr) as libc::Ioctl
}
const MEMGETINFO: libc::Ioctl = ioc(2, 1, size_of::<MtdInfo>() as u32);
const MEMERASE: libc::Ioctl = ioc(1, 2, size_of::<EraseInfo>() as u32);
const MEMGETBADBLOCK: libc::Ioctl = ioc(1, 11, size_of::<i64>() as u32);

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct MtdInfo {
    kind: u8,
    flags: u32,
    size: u32,
    erasesize: u32,
    writesize: u32,
    oobsize: u32,
    padding: u64,
}

#[repr(C)]
struct EraseInfo {
    start: u32,
    length: u32,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct FlashOptions {
    pub staging: bool,
    pub keep_persist: bool,
    /// Require all good blocks after linear images to contain only 0xff.
    /// Boot0 keeps its separate ROM-slot layout.
    pub erase_tail: bool,
    pub boot0: Option<Boot0Platform>,
}

#[derive(Clone, Copy, Debug)]
pub enum Boot0Platform {
    V851s,
}

impl Boot0Platform {
    pub fn detect() -> Option<Self> {
        let fd = SmallFd::open_readonly("/proc/device-tree/compatible").ok()?;
        let mut compatible = [0u8; 512];
        let n = fd.read(&mut compatible).ok()?;
        if compatible[..n]
            .windows(b"catplay,v851s3-aic8800d80".len())
            .any(|item| item == b"catplay,v851s3-aic8800d80")
        {
            Some(Self::V851s)
        } else {
            None
        }
    }
}

#[derive(Clone, Copy)]
struct Boot0Slot {
    start_page: u32,
    length_pages: u32,
}

impl Boot0Slot {
    const fn new(start_page: u32, length_pages: u32) -> Self {
        Self {
            start_page,
            length_pages,
        }
    }
}

// ROM probes these page numbers, even when physical eraseblocks are 128 pages.
const V851S_BOOT0: [Boot0Slot; 8] = [
    Boot0Slot::new(0, 64),
    Boot0Slot::new(64, 64),
    Boot0Slot::new(128, 64),
    Boot0Slot::new(192, 64),
    Boot0Slot::new(256, 64),
    Boot0Slot::new(320, 64),
    Boot0Slot::new(384, 64),
    Boot0Slot::new(448, 64),
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FlashError {
    InvalidOta,
    MissingPartition,
    AmbiguousPartition,
    BadMtdTable,
    Geometry,
    ImageTooLarge,
    NoBoot0Slot,
    Io,
    Verify,
}

struct MtdEntry<'a> {
    number: u32,
    name: &'a str,
    size: u32,
    erasesize: u32,
}

struct MtdDevice {
    fd: SmallFd,
    info: MtdInfo,
    dt_offset: u32,
}

struct Target<'a> {
    image: OtaImageSection<'a>,
    mtd: MtdDevice,
    // Ordinary images: physical eraseblock starts, ordered as the reader sees them.
    blocks: Vec<u32>,
    // Boot0: physical starts of valid ROM candidate slots.
    slots: Vec<u32>,
}

/// File boundary. The mmap guard remains alive for the entire flash operation.
pub fn flash_mapped_file(path: &str, options: FlashOptions) -> Result<(), FlashError> {
    println!("OTA: opening {path}");
    let file = SmallFd::open_readonly(path).map_err(|_| FlashError::Io)?;
    let size = file.stat().map_err(|_| FlashError::Io)?.st_size;
    if size < HEADER_SIZE as i64 || (size as u64) > usize::MAX as u64 {
        return Err(FlashError::InvalidOta);
    }
    // SmallFd only exposes `mem()` as &mut [u8]. A private writable mapping
    // matches that API while the opened file remains read-only; we never mutate it.
    let mut map = file
        .mmap_priv(0, size as usize)
        .map_err(|_| FlashError::Io)?;
    println!("OTA: mapped {size} bytes");
    flash_ota(map.mem(), options)
}

/// Lower layer. Preflights all images and MTD geometry before the first erase.
/// No external erase/write utilities are run.
pub fn flash_ota(package: &[u8], options: FlashOptions) -> Result<(), FlashError> {
    let mut ota = OtaExtractor::new(package);
    ota.parse_with_options(OtaParseOptions {
        reject_checksum_failures: true,
    })
    .map_err(|_| FlashError::InvalidOta)?;
    println!("OTA: package checksums passed");

    let mut mtd_text = [0u8; PROC_MTD_MAX];
    let entries = MtdDevice::partitions(&mut mtd_text)?;
    println!("OTA: found {} MTD partitions", entries.len());

    let mut targets = Vec::new();
    for image in ota.sections().iter().flatten().copied() {
        if (options.staging && image.ty == "boot0")
            || (options.keep_persist && image.ty == "persist")
        {
            continue;
        }
        let matches: Vec<_> = entries
            .iter()
            .filter(|entry| entry.name == image.ty)
            .collect();
        let entry = match matches.as_slice() {
            [] => return Err(FlashError::MissingPartition),
            [entry] => *entry,
            _ => return Err(FlashError::AmbiguousPartition),
        };
        let mut target = Target {
            image,
            mtd: MtdDevice::open(entry)?,
            blocks: Vec::new(),
            slots: Vec::new(),
        };
        println!("OTA: opened {} on mtd{}", image.ty, entry.number);
        if image.ty == "boot0" {
            prepare_boot0(&mut target, options.boot0.ok_or(FlashError::Geometry)?)?;
        } else {
            prepare_linear(&mut target, options.erase_tail)?;
        }
        println!("OTA: preflight passed for {}", image.ty);
        targets.push(target);
    }

    for target in &targets {
        println!("OTA: writing {}", target.image.ty);
        if target.image.ty == "boot0" {
            write_boot0(target)?;
        } else {
            write_linear(target)?;
        }
        println!("OTA: verified {}", target.image.ty);
    }
    Ok(())
}

impl MtdDevice {
    fn partitions<'a>(buf: &'a mut [u8; PROC_MTD_MAX]) -> Result<Vec<MtdEntry<'a>>, FlashError> {
        let proc = SmallFd::open_readonly("/proc/mtd").map_err(|_| FlashError::Io)?;
        let n = proc.read(buf).map_err(|_| FlashError::Io)?;
        if n == PROC_MTD_MAX {
            return Err(FlashError::BadMtdTable);
        }
        parse_mtd(core::str::from_utf8(&buf[..n]).map_err(|_| FlashError::BadMtdTable)?)
    }

    fn open(entry: &MtdEntry<'_>) -> Result<Self, FlashError> {
        let path = Self::path(entry.number)?;
        println!("OTA: opening MTD {path}");
        let fd = SmallFd::open(&path).map_err(|_| FlashError::Io)?;
        println!("OTA: reading DT reg for mtd{}", entry.number);
        let dt_offset = Self::read_dt_reg(entry.number, entry.size)?;
        let mut info = MtdInfo::default();
        println!("OTA: MEMGETINFO for mtd{}", entry.number);
        if unsafe { libc::ioctl(fd.raw_fd(), MEMGETINFO, &mut info) } < 0 {
            println!("OTA: MEMGETINFO errno {}", unsafe {
                *libc::__errno_location()
            });
            return Err(FlashError::Io);
        }
        if !matches!(info.kind, MTD_NANDFLASH | MTD_MLCNANDFLASH)
            || info.size != entry.size
            || info.erasesize != entry.erasesize
            || info.erasesize == 0
            || info.writesize == 0
            || info.size % info.erasesize != 0
            || info.erasesize % info.writesize != 0
        {
            return Err(FlashError::Geometry);
        }
        Ok(Self {
            fd,
            info,
            dt_offset,
        })
    }

    fn is_bad(&self, offset: u32) -> Result<bool, FlashError> {
        let mut off = i64::from(offset);
        let ret = unsafe { libc::ioctl(self.fd.raw_fd(), MEMGETBADBLOCK, &mut off) };
        if ret < 0 {
            println!("OTA: MEMGETBADBLOCK offset {offset:#x} errno {}", unsafe {
                *libc::__errno_location()
            });
            Err(FlashError::Io)
        } else {
            Ok(ret != 0)
        }
    }

    fn erase(&self, start: u32) -> Result<(), FlashError> {
        let mut request = EraseInfo {
            start,
            length: self.info.erasesize,
        };
        if unsafe { libc::ioctl(self.fd.raw_fd(), MEMERASE, &mut request) } < 0 {
            println!("OTA: MEMERASE offset {start:#x} errno {}", unsafe {
                *libc::__errno_location()
            });
            return Err(FlashError::Io); // A failed erase is always fatal.
        }
        Ok(())
    }

    fn block_matches(&self, start: u32, expected: &[u8]) -> bool {
        let mut actual = vec![0u8; expected.len()];
        let read = unsafe {
            libc::pread(
                self.fd.raw_fd(),
                actual.as_mut_ptr().cast::<c_void>(),
                actual.len(),
                i64::from(start),
            )
        };
        read == expected.len() as isize && actual == expected
    }

    fn write_and_verify(&self, start: u32, data: &[u8]) -> Result<(), FlashError> {
        let page_size = self.info.writesize as usize;
        let mut page = vec![0xffu8; page_size];
        let mut verify = vec![0u8; page_size];
        for (index, chunk) in data.chunks(page_size).enumerate() {
            page.fill(0xff);
            page[..chunk.len()].copy_from_slice(chunk);
            let off = i64::from(start) + (index * page_size) as i64;
            let n = unsafe {
                libc::pwrite(
                    self.fd.raw_fd(),
                    page.as_ptr().cast::<c_void>(),
                    page_size,
                    off,
                )
            };
            if n != page_size as isize {
                println!(
                    "OTA: pwrite offset {off:#x} returned {n}, errno {}",
                    unsafe { *libc::__errno_location() }
                );
                return Err(FlashError::Io);
            }
            let n = unsafe {
                libc::pread(
                    self.fd.raw_fd(),
                    verify.as_mut_ptr().cast::<c_void>(),
                    page_size,
                    off,
                )
            };
            if n != page_size as isize || verify != page {
                println!(
                    "OTA: readback offset {off:#x} returned {n}, errno {}",
                    unsafe { *libc::__errno_location() }
                );
                return Err(FlashError::Verify);
            }
        }
        Ok(())
    }
}

fn parse_mtd(text: &str) -> Result<Vec<MtdEntry<'_>>, FlashError> {
    let mut entries = Vec::new();
    for line in text.lines().skip(1) {
        let (dev, rest) = line.split_once(':').ok_or(FlashError::BadMtdTable)?;
        let number = dev
            .strip_prefix("mtd")
            .ok_or(FlashError::BadMtdTable)?
            .parse()
            .map_err(|_| FlashError::BadMtdTable)?;
        let mut fields = rest.split_whitespace();
        let size = u32::from_str_radix(fields.next().ok_or(FlashError::BadMtdTable)?, 16)
            .map_err(|_| FlashError::BadMtdTable)?;
        let erasesize = u32::from_str_radix(fields.next().ok_or(FlashError::BadMtdTable)?, 16)
            .map_err(|_| FlashError::BadMtdTable)?;
        let name = fields
            .next()
            .ok_or(FlashError::BadMtdTable)?
            .strip_prefix('"')
            .and_then(|s| s.strip_suffix('"'))
            .ok_or(FlashError::BadMtdTable)?;
        if fields.next().is_some() {
            return Err(FlashError::BadMtdTable);
        }
        entries.push(MtdEntry {
            number,
            name,
            size,
            erasesize,
        });
    }
    Ok(entries)
}

impl MtdDevice {
    fn path(number: u32) -> Result<alloc::string::String, FlashError> {
        use core::fmt::Write;
        let mut path = alloc::string::String::from("/dev/mtd");
        write!(&mut path, "{number}").map_err(|_| FlashError::Io)?;
        Ok(path)
    }

    // PoC supports DT fixed-partitions with #address-cells = #size-cells = 1.
    // /proc/mtd gives names and sizes, while this property supplies flash offset.
    fn read_dt_reg(number: u32, expected_size: u32) -> Result<u32, FlashError> {
        use core::fmt::Write;
        let mut path = alloc::string::String::from("/sys/class/mtd/mtd");
        write!(&mut path, "{number}/of_node/reg").map_err(|_| FlashError::Io)?;
        let fd = SmallFd::open_readonly(&path).map_err(|_| FlashError::BadMtdTable)?;
        let mut reg = [0u8; 16];
        let n = fd.read(&mut reg).map_err(|_| FlashError::BadMtdTable)?;
        if n != 8 || u32::from_be_bytes(reg[4..8].try_into().unwrap()) != expected_size {
            return Err(FlashError::BadMtdTable);
        }
        Ok(u32::from_be_bytes(reg[0..4].try_into().unwrap()))
    }
}

fn prepare_linear(target: &mut Target<'_>, erase_tail: bool) -> Result<(), FlashError> {
    let erasesize = target.mtd.info.erasesize;
    let needed = target.image.data.len().div_ceil(erasesize as usize);
    target.blocks = linear_blocks(
        target.mtd.info.size,
        erasesize,
        needed,
        erase_tail,
        |offset| target.mtd.is_bad(offset),
    )?;
    Ok(())
}

fn linear_blocks(
    size: u32,
    erasesize: u32,
    needed: usize,
    erase_tail: bool,
    mut is_bad: impl FnMut(u32) -> Result<bool, FlashError>,
) -> Result<Vec<u32>, FlashError> {
    let mut blocks = Vec::new();
    for block in 0..size / erasesize {
        let offset = block * erasesize;
        if !is_bad(offset)? && (erase_tail || blocks.len() < needed) {
            blocks.push(offset);
        }
    }
    if blocks.len() < needed {
        return Err(FlashError::ImageTooLarge);
    }
    Ok(blocks)
}

fn prepare_boot0(target: &mut Target<'_>, platform: Boot0Platform) -> Result<(), FlashError> {
    let slots = match platform {
        Boot0Platform::V851s => &V851S_BOOT0[..],
    };
    let page = target.mtd.info.writesize;
    let erase = target.mtd.info.erasesize;
    // ROM page numbers are absolute; this MTD partition must begin at flash 0.
    if target.mtd.dt_offset != 0 {
        return Err(FlashError::Geometry);
    }
    if platform_is_v851s(platform) && page != 2048 {
        return Err(FlashError::Geometry);
    }
    // eGON length limit from the V851S ROM; also protects against slot overlap.
    if target.image.data.len() > 0x1e000 {
        return Err(FlashError::ImageTooLarge);
    }
    for slot in slots {
        let start = slot
            .start_page
            .checked_mul(page)
            .ok_or(FlashError::Geometry)?;
        let length = slot
            .length_pages
            .checked_mul(page)
            .ok_or(FlashError::Geometry)?;
        if start % page != 0
            || start + length > target.mtd.info.size
            || target.image.data.len() > length as usize
        {
            return Err(FlashError::Geometry);
        }
        // A copy cannot straddle an eraseblock: ROM locations stay physical.
        if start / erase != (start + length - 1) / erase {
            return Err(FlashError::Geometry);
        }
        if !target.mtd.is_bad(start / erase * erase)? {
            target.slots.push(start);
        }
    }
    if target.slots.is_empty() {
        return Err(FlashError::NoBoot0Slot);
    }
    Ok(())
}

fn platform_is_v851s(platform: Boot0Platform) -> bool {
    matches!(platform, Boot0Platform::V851s)
}

fn write_linear(target: &Target<'_>) -> Result<(), FlashError> {
    let block_size = target.mtd.info.erasesize as usize;
    let mut skipped = 0;
    let mut programmed = 0;
    let mut expected = vec![0xffu8; block_size];
    for (index, &start) in target.blocks.iter().enumerate() {
        let source_start = core::cmp::min(index * block_size, target.image.data.len());
        let source_end = core::cmp::min(source_start + block_size, target.image.data.len());
        let source = &target.image.data[source_start..source_end];
        expected.fill(0xff);
        expected[..source.len()].copy_from_slice(source);
        if target.mtd.block_matches(start, &expected) {
            skipped += 1;
            continue;
        }
        target.mtd.erase(start)?;
        target.mtd.write_and_verify(start, source)?;
        // Empty tail blocks must be verified too; no pages are programmed there.
        // Also check the erased padding after a partial final image block.
        if source.len() < block_size && !target.mtd.block_matches(start, &expected) {
            return Err(FlashError::Verify);
        }
        programmed += 1;
    }
    println!(
        "OTA: {} blocks: skipped {skipped}, programmed {programmed}",
        target.image.ty
    );
    Ok(())
}

fn write_boot0(target: &Target<'_>) -> Result<(), FlashError> {
    let erase = target.mtd.info.erasesize;
    let mut expected = vec![0xffu8; erase as usize];
    let mut skipped = 0;
    let mut programmed = 0;
    let mut slot_index = 0;
    // Slots are in increasing physical order. Two slots may share an eraseblock.
    while slot_index < target.slots.len() {
        let block = target.slots[slot_index] / erase * erase;
        let first = slot_index;
        expected.fill(0xff);
        while slot_index < target.slots.len() && target.slots[slot_index] / erase * erase == block {
            let in_block = (target.slots[slot_index] - block) as usize;
            expected[in_block..in_block + target.image.data.len()]
                .copy_from_slice(target.image.data);
            slot_index += 1;
        }
        if target.mtd.block_matches(block, &expected) {
            skipped += 1;
            continue;
        }
        target.mtd.erase(block)?;
        for &start in &target.slots[first..slot_index] {
            target.mtd.write_and_verify(start, target.image.data)?;
        }
        programmed += 1;
    }
    println!("OTA: boot0 blocks: skipped {skipped}, programmed {programmed}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn linear_writer_skips_erased_tail_and_detects_dirty_tail() {
        let path = std::env::temp_dir().join(alloc::format!(
            "c2aota-linear-tail-{}.bin",
            std::process::id()
        ));
        let image = [1, 2, 3];
        let mut contents = vec![0xff; 256];
        contents[..image.len()].copy_from_slice(&image);
        std::fs::write(&path, &contents).unwrap();
        let target = Target {
            image: OtaImageSection {
                ty: "persist",
                name: "persist",
                size: image.len() as u32,
                md5: "",
                crc: 0,
                offset: 0,
                data: &image,
                crc_fail: false,
                md5_fail: false,
            },
            mtd: MtdDevice {
                fd: SmallFd::open_readonly(path.to_str().unwrap()).unwrap(),
                info: MtdInfo {
                    size: 256,
                    erasesize: 128,
                    writesize: 16,
                    ..Default::default()
                },
                dt_offset: 0,
            },
            blocks: vec![0, 128],
            slots: Vec::new(),
        };
        let erased = write_linear(&target);
        contents[128] = 0x55; // Stale data beyond the blob must trigger an erase.
        std::fs::write(&path, &contents).unwrap();
        let dirty = write_linear(&target);
        std::fs::remove_file(path).unwrap();
        assert_eq!(erased, Ok(()));
        // A regular file rejects MEMERASE, proving the dirty tail was not skipped.
        assert_eq!(dirty, Err(FlashError::Io));
    }

    #[test]
    fn linear_tail_flag_extends_plan_past_image_skipping_bad_blocks() {
        let plan = |erase_tail| {
            linear_blocks(6 * 128, 128, 2, erase_tail, |offset| {
                Ok(offset == 128 || offset == 512)
            })
        };
        assert_eq!(plan(false).unwrap(), [0, 256]);
        assert_eq!(plan(true).unwrap(), [0, 256, 384, 640]);
    }

    #[test]
    fn linear_tail_still_requires_enough_good_blocks_for_image() {
        for erase_tail in [false, true] {
            assert_eq!(
                linear_blocks(3 * 128, 128, 3, erase_tail, |offset| Ok(offset == 128)),
                Err(FlashError::ImageTooLarge)
            );
        }
    }

    #[test]
    fn linear_tail_bad_block_query_failure_aborts_preflight() {
        assert_eq!(
            linear_blocks(3 * 128, 128, 1, true, |offset| {
                if offset == 256 {
                    Err(FlashError::Io)
                } else {
                    Ok(false)
                }
            }),
            Err(FlashError::Io)
        );
    }

    #[test]
    fn corrupt_package_bails_before_mtd_discovery() {
        let mut packer = crate::mini::ota_packer::OtaPacker::new();
        packer.add("kernel", "fitImage", b"image data");
        let mut package = vec![0u8; packer.output_size()];
        packer.pack(&mut package);
        *package.last_mut().unwrap() ^= 1;
        assert_eq!(
            flash_ota(&package, FlashOptions::default()),
            Err(FlashError::InvalidOta)
        );
    }

    #[test]
    fn v851s_rom_slots_are_fixed_even_for_larger_eraseblocks() {
        let offsets: Vec<_> = V851S_BOOT0.iter().map(|s| s.start_page * 2048).collect();
        assert_eq!(
            offsets,
            [
                0, 0x20000, 0x40000, 0x60000, 0x80000, 0xa0000, 0xc0000, 0xe0000
            ]
        );
        assert_eq!(offsets[0] / 0x40000, offsets[1] / 0x40000);
    }
}
