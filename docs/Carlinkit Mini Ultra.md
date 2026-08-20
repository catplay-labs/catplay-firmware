# Carlinkit Mini Ultra

Carlinkit Mini Ultra is the newest generation wireless CarPlay dongle based on AIC8800 and Ingenic X1600EN chips.  

Carlinkit didn't contribute any software or hardware to this product; they only added their brand and a unique enclosure for the chip.

The device is called Wooboobox M11 and made by Shenzhen Zhiduojing Technology Co., Ltd.

There are two known revisions:
- AX1800: first version that had 128MB NAND storage and A/B OTA system(kernel+rootfs in pairs). To my knowledge it's impossible to get it anymore. 
- AX1800M: a newer revision that's "back" to traditional Carlinkit-style 16MB NOR flash. There is no A/B OTA anymore. 
The OTA process can only update main CarPlay dongle user-space binary.

The company also refers to it in official documents as V851S7 in addition to AX1800. It's unknown why as there is no trace of V581S chip in any of the revisions.

The software is based on Ingenic 4.4.94 kernel and SDK, however there are some important customizations that cannot be directly traced to Ingenic SDK nor the mainline kernel nor anywhere on GitHub.

Some of these unique customizations(A/B OTA, AIC8800 patches) are identical with software found in Creality printers, but their source isn't directly known.

There is no secure boot.

Boot process: Ingenic-custom flash header and parameters -> boot U-Boot SPL -> boot self-decompressing Linux kernel -> decompress and jump to kernel.

The decompressor uses a heavily customized head.S, a variant that doesn't appear anywhere online, without these changes that clear the CPU cache, jump to kernel will crash the device.

# Purchasing

You can buy these (almost) straight from the manufacturer much cheaper, just in the OEM casing which looks a bit different.  
Unfortunately, at this time I don't have a trusted Aliexpress link to share because my verified one ran out of stock,  
and there is a "lookalike" on the market which looks the same, but is not based on Ingenic chip, so it's useless if you accidently buy it.

# Flashing CatPlay

## Requirements
- you have the newer revision (AX1800M)
- PC is connected to SSID that starts with VehiConn_*
- Python 3 is installed (the `python3` command works)
- Python package dependencies: Run `python3 -m pip install paramiko pyusb` before executing the exploit (if you keep `sudo python3` in exploit.sh, ensure `pyusb` is installed for root too)
- Device IP configuration: Check the IP address used in `exploit.sh` — may vary depending on device batch or configuration (e.g. 192.168.50.100)

Note: there are reports that Carlinkit changed their firmware vendor _again_ and newer devices may not use VehiConn firmware.  
For now these devices are not rootable.  
If that happens to you try to buy a device from an older batch.  

1. Connect from Linux PC to the hotspot, password is 88888888 or 12345678
2. Check device IP, usually it's visible as gateway/dns server
2. Unpack `clk-mini-ultra-nor.zip` software bundle
3. Run tools/exploit.sh <ip> and wait for the process to finish

```sh
➜  tools bash exploit.sh 192.168.1.101
[...]
[+] USB recovery device detected: a108:eaef
[+] Parsed uImage(name='Linux-6.12.85', size=3063744, load=0x82f00000, entry=0x82f00000, os=5, arch=5, type=2, comp=0)
[+] kernel payload: file=../clk-mini-ultra-nor-recov.uzImage.bin, payload_size=3063744 B, load=0x82f00000, entry=0x82f00000
[+] initramfs: file=../clk-mini-ultra-nor-recov.initrd.bin, size=5357568 B, load=0x82100000
[+] bootargs/cmdline: 'init=/usr/bin/carlinkit_otalib root=/dev/initrd mem=64M@0x0 console=ttyS2,3000000n8 rootfstype=erofs rw clk_ignore_unused lpj=549888 driver_async_probe=dwc2,jz4740-mmc c2a_boot=recovery rd_start=0x82100000 rd_size=0x0051c000', size=247 B, addr=0x83ff0000, argc=2, argv=0x83ff0000, envp=0x83ff000c
[+] trampoline: size=48 B, addr=0x83ff1000, linux_a0=0x00000002, linux_a1=0x83ff0000, linux_a2=0x83ff000c, linux_a3=0x00000000
[*] Looking for X1600 USB boot device...
[+] Device found
[+] CPU info 'X1600'
[*] Upload SPL: ../ax1800_spi_nor_burner_u-boot-spl.bin -> 0x80001800 (11784 B), entry=0x80001800
[*] Start SPL (VR_PROGRAM_START1)
[*] Waiting for SPL to return to BootROM...
[+] Device returned to BootROM
[+] CPU info after SPL: 'X1600'
[*] Upload kernel payload -> 0x82f00000 (3063744 B)
[*] Upload initramfs -> 0x82100000 (5357568 B)
[*] Upload bootargs -> 0x83ff0000 (247 B)
[*] Upload trampoline -> 0x83ff1000 (48 B)
[*] Flush caches (VR_FLUSH_CACHES)
[*] Start trampoline (VR_PROGRAM_START2) entry=0x83ff1000 -> kernel=0x82f00000
[+] Done, trampoline started and kernel launched
[*] Verifying SSH on 192.168.51.2:22 (timeout 20.0s)...
[+] SSH is open on 192.168.51.2:22
[*] uploader --host 192.168.51.2 --exec-cmd rm -rf /tmp/backup.bin
[*] Executing remote command: rm -rf /tmp/backup.bin
[+] Remote command exit status: 0
[*] uploader --host 192.168.51.2 --exec-cmd carlinkit_otalib backup /tmp/backup.bin
[*] Executing remote command: carlinkit_otalib backup /tmp/backup.bin
backup completed!
[+] Remote command exit status: 0
[*] uploader --host 192.168.51.2 --download backup_1782063822.bin /tmp/backup.bin
[download] /tmp/backup.bin -> backup_1782063822.bin  16777216/16777216 bytes (100.0%)
[+] Download complete
[*] uploader --host 192.168.51.2 --exec-cmd rm -rf /tmp/backup.bin
[*] Executing remote command: rm -rf /tmp/backup.bin
[+] Remote command exit status: 0
[*] uploader --host 192.168.51.2 /tmp/fw.bin ../clk-mini-ultra-nor.c2aflash
[upload] ../clk-mini-ultra-nor.c2aflash -> /tmp/fw.bin  16777216/16777216 bytes (100.0%)
[+] Upload complete
[*] uploader --host 192.168.51.2 --exec-cmd mount -o remount,ro /persist || exit 0
[*] Executing remote command: mount -o remount,ro /persist || exit 0
mount: can't find /persist in /proc/mounts
[+] Remote command exit status: 0
[*] uploader --host 192.168.51.2 --exec-cmd carlinkit_otalib flash /tmp/fw.bin && (sleep 2 && reboot &)
[*] Executing remote command: carlinkit_otalib flash /tmp/fw.bin && (sleep 2 && reboot &)
Starting flash...be patient...

```
The device is ready to use.  
You can take it straight to the car, or connect to the Wi-Fi Hotspot and use SSH on 192.168.50.2 to look around.

SSID: C2A_AP / C2A_P2P  
Bluetooth name: CatDongle  
Pass: lovec@ts  

Note: these are currently hardcoded, there is a work in progress on a configuration system.  
Note: if device booted in recovery mode, it will be accessible over USB gadget at 192.168.51.2.  
Note: to refresh CatPlay firmware swap "ultra_exploit" to "vendor_request" in exploit.sh.  
You will to pair your phone again after flashing.  
  
Note: exploit.sh and Python scripts it calls don't depend on anything Linux-specific.  
Feel free to test on Windows and OSX and send me feedback, I don't have either of them to test.

# Capturing logs

If your car is not yet compatible with CatPlay, please follow these steps to make a useful report:
- install SSH and SFTP client on your phone(you can use `Termius`) - the IP is root@192.168.50.2
- attempt to use the dongle as normal
- download `/var/log/catplay.log`
- look into `dmesg`; consider dumping it to a downloadable file like `dmesg > /var/log/kernel.log`
  
Please include `kernel.log` and `catplay.log` along with your car details.  
  
Note: in future dumping logs will be possible via web UI but that's a work in progress,  
Note: first release of CatPlay runs a quite verbose logging by default, logging all RTSP traffic; this might be changed in a future release, with seperate steps to increase logging verbosity for diagnostics. 
