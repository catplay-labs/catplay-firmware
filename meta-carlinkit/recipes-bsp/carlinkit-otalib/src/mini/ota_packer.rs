use heapless::{String, format};
use md5hash::MD5Hasher;

use crate::mini::{
    crc::xor_crc_img,
    md5::md5_calc,
    ota::{BLOCK_SIZE, HEADER_SIZE, MAX_IMAGES},
};

const HEADER_SIZE_MAX: usize = 8192;

struct OtaPackerSection<'a> {
    data: &'a [u8],
    img_type: &'a str,
    img_name: &'a str,

    img_md5: [u8; 16],
    img_crc: u32,
}

#[derive(Default)]
pub struct OtaPacker<'a> {
    n: usize,
    images: [Option<OtaPackerSection<'a>>; MAX_IMAGES],
}

impl<'a> OtaPacker<'a> {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn output_size(&self) -> usize {
        let mut size = HEADER_SIZE;

        for image in &self.images {
            size += image.as_ref().map(|i| i.data.len()).unwrap_or(0);
        }

        size
    }

    pub fn add(&mut self, img_type: &'a str, img_name: &'a str, data: &'a [u8]) {
        let img = OtaPackerSection {
            data,
            img_type,
            img_name,
            img_md5: md5_calc(data),
            img_crc: xor_crc_img(data, BLOCK_SIZE),
        };
        self.images[self.n].replace(img);
        self.n += 1;
    }

    pub fn build_header(&self) -> String<HEADER_SIZE_MAX> {
        let mut header: String<HEADER_SIZE_MAX> = String::new();
        header.push_str("ota_version=0\n\n").unwrap();

        let mut hasher = MD5Hasher::new();

        for img in &self.images {
            let Some(img) = img else { continue };

            header.push_str("img_type=").unwrap();
            header.push_str(img.img_type).unwrap();

            header.push_str("\nimg_name=").unwrap();
            header.push_str(img.img_name).unwrap();

            header.push_str("\nimg_size=").unwrap();
            header
                .push_str(&format!(32; "{}", img.data.len()).unwrap())
                .unwrap();

            header.push_str("\nimg_md5=").unwrap();
            for i in img.img_md5 {
                header.push_str(&format!(2; "{:02x}", i).unwrap()).unwrap();
            }
            header.push_str("\nimg_crc=").unwrap();
            header
                .push_str(&format!(32; "{}", img.img_crc).unwrap())
                .unwrap();
            header.push_str("\n\n").unwrap();

            hasher.digest(&img.data);
        }

        header.push_str("check_img=1\n").unwrap();
        header.push_str("bs_size=").unwrap();
        header
            .push_str(&format!(32; "{}", BLOCK_SIZE).unwrap())
            .unwrap();

        let ota_img_packet_md5: [u8; 16] = hasher.finish().into();
        header.push_str("\nota_img_packet_md5=").unwrap();
        for i in ota_img_packet_md5 {
            header.push_str(&format!(2; "{:02x}", i).unwrap()).unwrap();
        }
        header.push_str("\n").unwrap();
        header
    }

    pub fn pack(&self, out: &mut [u8]) {
        let header = self.build_header();
        out[..header.len()].copy_from_slice(header.as_bytes());

        let mut offset = HEADER_SIZE;
        for img in &self.images {
            let Some(img) = img else { continue };

            out[offset..offset + img.data.len()].copy_from_slice(img.data);
            offset += img.data.len();
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::mini::{
        ota::{OtaExtractor, OtaImageGlobals, OtaImageSection},
        ota_packer::OtaPacker,
    };

    #[test]
    fn test_pack_unpack() {
        let mut packer = OtaPacker::new();
        packer.add("kernel", "xImage", b"kernel_data");
        packer.add("rootfs", "rootfs.squashfs", b"rootfs_data");

        let mut buf = vec![0u8; packer.output_size()];
        packer.pack(&mut buf);

        let header = packer.build_header();
        println!("Header: {header:?}");

        let mut parser = OtaExtractor::new(&buf);
        parser.parse().unwrap();

        assert_eq!(
            parser.sections().iter().flatten().collect::<Vec<_>>(),
            [
                Some(OtaImageSection {
                    ty: "kernel",
                    name: "xImage",
                    size: 11,
                    md5: "c6dc2c4d1c885494678b955b3a7f85af",
                    crc: 3879308967,
                    offset: 131072,
                    crc_fail: false,
                    md5_fail: false,
                    data: b"kernel_data"
                }),
                Some(OtaImageSection {
                    ty: "rootfs",
                    name: "rootfs.squashfs",
                    size: 11,
                    md5: "d26bae11c792c92e1fc15546498a16de",
                    crc: 3497329009,
                    offset: 131083,
                    crc_fail: false,
                    md5_fail: false,
                    data: b"rootfs_data"
                }),
            ]
            .iter()
            .flatten()
            .collect::<Vec<_>>()
        );

        assert_eq!(
            parser.globals(),
            &OtaImageGlobals {
                bs_size: 131072,
                packet_md5: Some("6ca0659796a7f68d823bea038418f329"),
                packet_md5_fail: false
            }
        );
    }
}
