use core::fmt;

use crate::mini::{
    crc::xor_crc_img,
    md5::{md5_calc, parse_md5_hex},
};

pub const HEADER_SIZE: usize = 131_072;
pub const BLOCK_SIZE: usize = 131_072;
pub const MAX_IMAGES: usize = 8;

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct OtaImageSection<'a> {
    pub ty: &'a str,
    pub name: &'a str,
    pub size: u32,
    pub md5: &'a str,
    pub crc: u32,
    pub offset: usize,
    pub data: &'a [u8],

    pub crc_fail: bool,
    pub md5_fail: bool,
}

impl<'a> fmt::Debug for OtaImageSection<'a> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OtaImageSection")
            .field("ty", &self.ty)
            .field("name", &self.name)
            .field("size", &self.size)
            .field("md5", &self.md5)
            .field("crc", &format_args!("{:#010x}", self.crc))
            .field("offset", &self.offset)
            .field("crc_fail", &self.crc_fail)
            .field("md5_fail", &self.md5_fail)
            .field("data", &format_args!("{} bytes", self.data.len()))
            .finish()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct OtaImageGlobals<'a> {
    pub bs_size: usize,
    pub packet_md5: Option<&'a str>,
    pub packet_md5_fail: bool,
}

impl<'a> OtaImageSection<'a> {
    pub fn data(&self, buf: &'a [u8]) -> &'a [u8] {
        &buf[self.offset..self.offset + self.size as usize]
    }
}

fn parse_ota<'a>(
    buf: &'a [u8],
    images: &mut [Option<OtaImageSection<'a>>],
    globals: &mut OtaImageGlobals<'a>,
) -> Result<usize, &'static str> {
    if buf.len() < HEADER_SIZE {
        return Err("OTA header is truncated");
    }
    let header = &buf[..HEADER_SIZE];
    let header_end = header.iter().position(|&b| b == 0).unwrap_or(HEADER_SIZE);
    let text = str::from_utf8(&header[..header_end]).map_err(|_| "OTA header is not UTF-8")?;

    let mut count = 0;
    let mut offset = HEADER_SIZE;

    *globals = parse_globals(text)?;

    for part in text.split("\n\n") {
        if part
            .lines()
            .any(|line| line.trim_start().starts_with("img_"))
        {
            if count >= images.len() {
                return Err("too many OTA sections");
            }
            let mut name = "";
            let mut ty = "";
            let mut size = None;
            let mut md5 = "";
            let mut crc = 0u32;

            for line in part.lines() {
                let line = line.trim();
                if let Some(pos) = line.find('=') {
                    let (k, v) = line.split_at(pos);
                    let val = v[1..].trim();
                    match k.trim() {
                        "img_name" => name = val,
                        "img_type" => ty = val,
                        "img_size" => {
                            size = Some(val.parse::<u32>().map_err(|_| "invalid OTA section size")?)
                        }
                        "img_md5" => md5 = val,
                        "img_crc" => {
                            crc = val.parse::<u32>().map_err(|_| "invalid OTA section CRC")?
                        }
                        _ => {}
                    }
                }
            }

            let size = size.ok_or("missing OTA section size")?;
            if name.is_empty() || ty.is_empty() || size == 0 || parse_md5_hex(md5).is_none() {
                return Err("incomplete OTA section");
            }
            if images[..count].iter().flatten().any(|image| image.ty == ty) {
                return Err("duplicate OTA image type");
            }
            let end = offset
                .checked_add(size as usize)
                .ok_or("OTA section offset overflow")?;
            if end > buf.len() {
                return Err("OTA section exceeds packet");
            }

            let data = &buf[offset..end];
            let mut section = OtaImageSection {
                ty,
                name,
                size,
                md5,
                crc,
                offset,
                data,

                crc_fail: false,
                md5_fail: false,
            };

            if crc != 0 && xor_crc_img(data, globals.bs_size) != crc {
                section.crc_fail = true;
                // debug!("OTA Section: CRC fail");
            } else {
                // debug!("OTA Section: CRC OK");
            }

            if !md5.is_empty() {
                let md5_hex = parse_md5_hex(md5);
                if let Some(md5_hex) = md5_hex {
                    if md5_hex != md5_calc(data) {
                        // debug!("OTA Section: MD5 fail");
                        section.md5_fail = true;
                    } else {
                        // debug!("OTA Section: MD5 OK");
                    }
                } else {
                    section.md5_fail = true;
                    // debug!("OTA Section: MD5 fail (unparsable)");
                }
            }

            images[count].replace(section);

            count += 1;
            offset = end;
        }
    }

    if count == 0 || offset != buf.len() {
        return Err("OTA payload does not match sections");
    }

    let packet_md5 = globals.packet_md5.ok_or("missing OTA packet MD5")?;
    let parsed_global_md5 = parse_md5_hex(packet_md5);
    let global_md5 = md5_calc(&buf[HEADER_SIZE..offset]);

    if parsed_global_md5 != Some(global_md5) {
        globals.packet_md5_fail = true;
    }

    Ok(count)
}

fn parse_globals<'a>(text: &'a str) -> Result<OtaImageGlobals<'a>, &'static str> {
    let mut bs_size = BLOCK_SIZE;
    let mut packet_md5 = None;
    for part in text.split("\n\n") {
        if part
            .lines()
            .any(|line| line.trim_start().starts_with("img_"))
        {
            continue;
        }
        for line in part.lines().map(|l| l.trim()) {
            if let Some((k, v)) = line.split_once('=') {
                let v = v.trim();
                match k.trim() {
                    "bs_size" => bs_size = v.parse().map_err(|_| "invalid OTA block size")?,
                    "ota_img_packet_md5" => packet_md5 = Some(v),
                    _ => {}
                }
            }
        }
    }
    if bs_size == 0 || bs_size > HEADER_SIZE {
        return Err("invalid OTA block size");
    }

    Ok(OtaImageGlobals {
        bs_size,
        packet_md5,
        packet_md5_fail: false,
    })
}

pub struct OtaExtractor<'a> {
    buf: &'a [u8],
    sections: [Option<OtaImageSection<'a>>; MAX_IMAGES],
    globals: OtaImageGlobals<'a>,
    parsed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OtaParseOptions {
    pub reject_checksum_failures: bool,
}

impl Default for OtaParseOptions {
    fn default() -> Self {
        Self {
            reject_checksum_failures: true,
        }
    }
}

impl<'a> OtaExtractor<'a> {
    pub fn new(buf: &'a [u8]) -> Self {
        Self {
            buf,
            sections: [None; MAX_IMAGES],
            globals: OtaImageGlobals::default(),
            parsed: false,
        }
    }

    pub fn parse(&mut self) -> Result<(), &'static str> {
        self.parse_with_options(OtaParseOptions::default())
    }

    pub fn parse_with_options(&mut self, options: OtaParseOptions) -> Result<(), &'static str> {
        self.sections = [None; MAX_IMAGES];
        self.parsed = false;
        let n = parse_ota(self.buf, &mut self.sections, &mut self.globals)?;

        if options.reject_checksum_failures {
            for i in 0..n {
                let Some(image) = self.sections[i] else {
                    continue;
                };

                if image.crc_fail {
                    return Err("failed section crc check");
                }

                if image.md5_fail {
                    return Err("failed section md5 check");
                }
            }

            if self.globals.packet_md5_fail {
                return Err("failed packet md5 check");
            }
        }

        self.parsed = true;
        Ok(())
    }

    pub fn globals(&self) -> &OtaImageGlobals<'a> {
        &self.globals
    }

    pub fn sections(&self) -> &[Option<OtaImageSection<'a>>; MAX_IMAGES] {
        &self.sections
    }
}

#[cfg(test)]
mod tests {
    use std::fs::{self};

    use super::*;
    use crate::mini::ota_packer::OtaPacker;
    extern crate std;

    #[test]
    fn checksum_policy_rejects_each_corruption() {
        let mut packer = OtaPacker::new();
        packer.add("kernel", "fitImage", b"image data");
        let mut valid = vec![0u8; packer.output_size()];
        packer.pack(&mut valid);

        for (field, check) in [("img_crc=", 0), ("img_md5=", 1), ("ota_img_packet_md5=", 2)] {
            let mut corrupt = valid.clone();
            let prefix = field.as_bytes();
            let start = corrupt
                .windows(prefix.len())
                .position(|v| v == prefix)
                .unwrap()
                + prefix.len();
            corrupt[start] = if corrupt[start] == b'0' { b'1' } else { b'0' };

            let mut inspect = OtaExtractor::new(&corrupt);
            inspect
                .parse_with_options(OtaParseOptions {
                    reject_checksum_failures: false,
                })
                .unwrap();
            assert_eq!(inspect.sections()[0].unwrap().crc_fail, check == 0);
            assert_eq!(inspect.sections()[0].unwrap().md5_fail, check == 1);
            assert_eq!(inspect.globals().packet_md5_fail, check == 2);

            let mut verified = OtaExtractor::new(&corrupt);
            assert!(
                verified
                    .parse_with_options(OtaParseOptions {
                        reject_checksum_failures: true
                    })
                    .is_err()
            );
        }
    }

    #[test]
    fn parser_rejects_duplicate_truncated_and_zero_block_size_packets() {
        let mut packer = OtaPacker::new();
        packer.add("kernel", "fitImage", b"first");
        packer.add("kernel", "fitImage2", b"second");
        let mut duplicate = vec![0u8; packer.output_size()];
        packer.pack(&mut duplicate);
        assert!(OtaExtractor::new(&duplicate).parse().is_err());

        let mut packer = OtaPacker::new();
        packer.add("kernel", "fitImage", b"first");
        let mut valid = vec![0u8; packer.output_size()];
        packer.pack(&mut valid);
        assert!(OtaExtractor::new(&valid).parse().is_ok());

        let mut truncated = valid.clone();
        truncated.pop();
        assert!(OtaExtractor::new(&truncated).parse().is_err());

        let needle = b"bs_size=131072";
        let start = valid
            .windows(needle.len())
            .position(|v| v == needle)
            .unwrap();
        valid[start + 8..start + needle.len()].copy_from_slice(b"000000");
        assert!(OtaExtractor::new(&valid).parse().is_err());
    }

    #[test]
    fn test_parse() {
        let file = "/tmp/ota.bin";

        let data = fs::read(file).unwrap();

        let mut parser = OtaExtractor::new(&data);
        parser.parse().unwrap();

        parser.sections[0].as_mut().unwrap().data = &[];
        parser.sections[1].as_mut().unwrap().data = &[];

        assert_eq!(
            parser.sections().iter().flatten().collect::<Vec<_>>(),
            [
                Some(OtaImageSection {
                    ty: "kernel",
                    name: "xImage",
                    size: 3088448,
                    md5: "7e5f7b665e18ddfe948dbfba69f6a398",
                    crc: 99439955,
                    offset: 131072,
                    crc_fail: false,
                    md5_fail: false,

                    data: &[]
                }),
                Some(OtaImageSection {
                    ty: "rootfs",
                    name: "rootfs.squashfs",
                    size: 12054528,
                    md5: "efda6cd62dd10202545e36f07d4a85b3",
                    crc: 3804379493,
                    offset: 3219520,
                    crc_fail: false,
                    md5_fail: false,

                    data: &[]
                }),
                None
            ]
            .iter()
            .flatten()
            .collect::<Vec<_>>()
        );

        assert_eq!(
            parser.globals(),
            &OtaImageGlobals {
                bs_size: 131072,
                packet_md5: Some("fe5800799de0acba6d778ffd895b4e81"),
                packet_md5_fail: false
            }
        );
    }
}
