#![no_std]
#![no_main]
use carlinkit_otalib::{
    Flash, Hwid, Radio, SystemUtil,
    boot::{
        BootUltra,
        boot_platform::{Boot, BootPlatform},
        recovery_gadget::RecoveryGadget,
    },
    dmesg,
    mini::{ota::OtaExtractor, ota_packer::OtaPacker},
    nostd::{SmallFd, argv_to_heapless, sanitize_filename},
    println, sign_wic, stdout,
    telnet::TelnetServer,
    web::WebServer,
};
use heapless::{CString, String, format};
use libc::S_IRWXU;

#[panic_handler]
#[cfg(panic = "abort")]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    stdout!("Panic: {}\n", _info.message());
    unsafe { libc::_exit(1) }
}

const FORCE_TELNET_SPAWN: bool = false;

const HELP: &str = r"Available commands:
backup <outfile> - backup flash content
sign <image.bin> <hwid> - sign 16MB bin file using provided HWID
flash <image.bin> - flash 16MB bin file and autosign using local HWID
flash_nosign <image.bin> - flash 16MB bin file directly to flash
flash_fit <fitImage> - flash fitImage firmware file
var hwid/layout/radio - dump HWID, flash layout type or radio ID for bash scripting. exits with non-zero code if unreadable

Carlinkit Mini Ultra commands:
mini_pack <outfile> <xImage> <rootfs.squashfs> - create OTA binary
mini_unpack <infile> <outfolder> - split OTA file into sections 
web [port] - run property HTTP server in background (GUI: /, API: GET /props, GET/PUT /prop/<name>)
";

#[unsafe(no_mangle)]
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn main(_argc: i32, _argv: *const *const u8) -> i32 {
    if FORCE_TELNET_SPAWN {
        let telnet = TelnetServer::new(4444);
        match telnet {
            Ok(v) => {
                v.run_forked().expect("failed to fork telnet");
                // println!("Telnet has forked!");
            }
            Err(err) => {
                // println!("Failed to bind telnet??? {err:?}");
            }
        };
    }

    if SystemUtil::getpid() == 1 {
        dmesg!("[boot] Taking over PID1 role");
        BootUltra::boot_head();

        let pid = unsafe { libc::fork() };
        if pid == 0 {
            dmesg!("[boot] Starting boot flow child");
            BootUltra::boot_early();
            BootUltra::boot_late();
            dmesg!("[boot] Boot flow child finished");
            unsafe { libc::_exit(0) };
        } else if pid < 0 {
            dmesg!("[boot] Failed to fork boot flow child, running inline");
            BootUltra::boot_early();
            BootUltra::boot_late();
        } else {
            dmesg!("[boot] Boot flow child pid={pid}");
        }

        BootUltra::boot_tail();

        unreachable!();
    }

    let args = unsafe { argv_to_heapless::<16, 1024>(_argc, _argv) };
    let args = &args[1..];

    let flash = Flash::new();
    let radio = Radio::detect();

    let hwid = Hwid::detect().ok();
    let hwid_str = match hwid {
        Some(hwid) => format!(1024; "{}", hwid),
        None => format!(1024; "<unknown>"),
    }
    .unwrap();

    if args.is_empty() {
        println!("Carlinkit OTA-lib\n\n");
        println!("HWID: {}\nFlash layout: {:?}\nRadio: {:?}\n", hwid_str, Flash::layout(), radio);
        println!("{}", HELP);
        return 0;
    }

    match args[0].as_str() {
        "telnet" => {
            let telnet = TelnetServer::new(4444);
            match telnet {
                Ok(v) => {
                    v.run_forked().expect("failed to fork telnet");
                    println!("Telnet has forked!");
                }
                Err(err) => {
                    println!("Failed to bind telnet??? {err:?}");
                }
            };
        }
        "web" => {
            let port = if args.len() >= 2 {
                args[1].parse::<u16>().unwrap_or(8080)
            } else {
                8080
            };

            let web = WebServer::new(port);
            match web {
                Ok(v) => {
                    v.run_forked().expect("failed to fork web server");
                    println!("Web server has forked on port {port}");
                }
                Err(err) => {
                    println!("Failed to bind web server (errno={}): {:?}", err.0, err);
                }
            };
        }
        "backup" => {
            let Err(_) = SmallFd::open(&args[1]) else {
                panic!("cannot backup: file already exists");
            };

            let new_fd = SmallFd::create(&args[1]).unwrap();
            new_fd.truncate(flash.size()).unwrap();

            let mut file_mapped = new_fd.mmap(0, flash.size()).unwrap();
            flash.backup(file_mapped.mem()).unwrap();
            file_mapped.msync().unwrap();

            println!("backup completed!");
        }
        "sign" => {
            let Ok(file) = SmallFd::open(&args[1]) else {
                panic!("failed to open input file for signing");
            };

            if file.stat().unwrap().st_size != flash.size() as _ {
                panic!("invalid sized file for flashing/signing");
            }

            let hwid: Hwid = args[2].as_str().try_into().unwrap();
            let mut mmap = file.mmap(0, flash.size()).unwrap();

            sign_wic(mmap.mem(), hwid).unwrap();
            mmap.msync().unwrap();
            println!("file signed!");
        }
        "flash" => {
            let Ok(file) = SmallFd::open_readonly(&args[1]) else {
                panic!("failed to open input file for flashing");
            };

            if file.stat().unwrap().st_size != flash.size() as _ {
                panic!("invalid sized file for flashing/signing");
            }

            let mut file_mapped = file.mmap_priv(0, flash.size()).unwrap();
            println!("Starting flash...be patient...");
            flash.flash_autosign(file_mapped.mem()).unwrap();
            println!("flash completed!");
        }
        "flash_nosign" => {
            let Ok(file) = SmallFd::open_readonly(&args[1]) else {
                panic!("failed to open input file for flashing");
            };

            if file.stat().unwrap().st_size != flash.size() as _ {
                panic!("invalid sized file for flashing/signing");
            }
            let mut file_mapped = file.mmap_priv(0, flash.size()).unwrap();
            println!("Starting flash...be patient...");
            flash.flash_nosign(file_mapped.mem()).unwrap();
            println!("flash nosign completed!");
        }
        "flash_fit" => {
            let file = SmallFd::open_readonly(&args[1]).unwrap();
            let mut file_mapped = file.mmap_priv(0, file.stat().unwrap().st_size as _).unwrap();
            flash.flash_fitimage(file_mapped.mem()).unwrap();
            println!("flash fit completed!");
        }
        "var" => {
            if args.len() < 2 {
                println!("{}", HELP);
                return 1;
            }

            match args[1].as_str() {
                "hwid" => {
                    let Some(hwid) = hwid else {
                        stdout!("unknown");
                        return 1;
                    };

                    println!("{}", hwid);
                }
                "layout" => {
                    let ret = match flash.layout {
                        carlinkit_otalib::FlashLayout::Modern => "modern",
                        carlinkit_otalib::FlashLayout::Legacy => "legacy",
                        carlinkit_otalib::FlashLayout::Unknown => "unknown",
                        carlinkit_otalib::FlashLayout::LegacyUltraNor => "legacy_ultra_nor",
                        carlinkit_otalib::FlashLayout::LegacyUltraNand => "legacy_ultra_nand",
                    };
                    println!("{}", ret);
                }
                "radio" => {
                    println!("{}", radio);
                }
                _ => {
                    println!("{}", HELP);
                }
            }
        }

        #[cfg(feature = "ota_packer")]
        "mini_pack" => {
            let Ok(outfile) = SmallFd::create(&args[1]) else {
                panic!("failed to open output file");
            };

            let Ok(ximage) = SmallFd::open_readonly(&args[2]) else {
                panic!("failed to open input file (xImage)");
            };
            let ximage_size = ximage.stat().unwrap().st_size;
            let mut ximage_buf = ximage.mmap_readonly(0, ximage_size as _).unwrap();

            let Ok(rootfs) = SmallFd::open_readonly(&args[3]) else {
                panic!("failed to open input file (xImage)");
            };

            let rootfs_size = rootfs.stat().unwrap().st_size;
            let mut rootfs_buf = rootfs.mmap_readonly(0, rootfs_size as _).unwrap();

            let mut ota_packer = OtaPacker::new();
            ota_packer.add("kernel", "xImage", ximage_buf.mem());
            ota_packer.add("rootfs", "rootfs.squashfs", rootfs_buf.mem());

            let size = ota_packer.output_size();
            outfile.truncate(size).unwrap();

            let mut outfile_buf = outfile.mmap(0, size).unwrap();
            ota_packer.pack(outfile_buf.mem());

            println!("OTA successfully packed!");
        }
        #[cfg(feature = "ota_packer")]
        "mini_unpack" => {
            let Ok(infile) = SmallFd::create(&args[1]) else {
                panic!("failed to open output file");
            };

            let outfolder = &args[2];

            let mut outfolder_path = CString::<1024>::new();
            outfolder_path.extend_from_bytes(outfolder.as_bytes()).unwrap();

            let ret = unsafe { libc::mkdir(outfolder_path.as_ptr(), S_IRWXU) };
            if ret < 0 {
                println!("[Warning] failed mkdir for output folder {outfolder}");
            }

            let infile_size = infile.stat().unwrap().st_size;
            let mut infile_buf = infile.mmap_readonly(0, infile_size as _).unwrap();

            let mut ota_extractor = OtaExtractor::new(infile_buf.mem());
            ota_extractor.parse().unwrap();

            for section in ota_extractor.sections() {
                let Some(section) = section else {
                    continue;
                };

                let name: String<1024> = sanitize_filename(section.name);
                let path = format!(1024; "{outfolder}/{name}").unwrap();
                println!("Found OTA section: {section:?} / output file {path}");

                let Ok(file) = SmallFd::create(&path) else {
                    panic!("failed to open output file: {path}")
                };

                let section_len = section.data.len();
                file.truncate(section_len).unwrap();
                let mut file_buf = file.mmap(0, section_len).unwrap();
                file_buf.mem().copy_from_slice(section.data);

                println!("Extracted OTA section {} to {path}", section.name);
            }
        }
        "sb" => {
            if !Flash::is_ultra() {
                panic!("Not Ingenic device");
            }

            println!("Performing softbrick");
            flash.softbrick().unwrap();
            println!("Done");
        }
        "usboot" => {
            if !Flash::is_ultra() {
                panic!("Not Ingenic device");
            }

            {
                // Legacy firmware
                println!("Trying legacy reset...");
                let _ = SystemUtil::run_shell("devmem 0x100000cc 32 0x42575302; echo wdt > /proc/jz/reset/reset");
            }

            {
                println!("Trying modern reset...");
                unsafe {
                    libc::syscall(
                        libc::SYS_reboot,
                        libc::LINUX_REBOOT_MAGIC1,
                        libc::LINUX_REBOOT_MAGIC2,
                        libc::LINUX_REBOOT_CMD_RESTART2,
                        c"usb".as_ptr(),
                    );
                }
            }

            println!("Failed...should not reach here");
        }
        "gadget" => {
            RecoveryGadget::start_best_effort(BootPlatform::detect().params().main_udc);
            println!("... recovery gadget was started if possible");
        }
        "ultraboot" => {
            BootUltra::boot_early();
            BootUltra::boot_late();
        }
        _ => {
            println!("{}", HELP);
            return 1;
        }
    }

    0
}
