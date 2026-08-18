#!/usr/bin/env python3
"""
Minimal USB Boot loader for Ingenic X1600.

Requires:
    pip install pyusb

On Linux, typically:
    sudo apt install libusb-1.0-0

Flow:
  1) upload SPL to first-stage area
  2) start SPL via VR_PROGRAM_START1
  3) wait until SPL returns to BootROM
  4) parse kernel.bin as uImage (64B header, big-endian)
  5) upload kernel payload to the address from uImage
  6) upload initramfs.bin to the manually provided address
  7) upload MIPS firmware ABI block (argc/argv/envp + cmdline)
  8) upload MIPS trampoline that sets a0-a3 and jumps to kernel
  9) flush cache
 10) start trampoline via VR_PROGRAM_START2

Notes:
- SPL must be first-stage and must return to BootROM after RAM init.
- kernel.bin must be a legacy uImage, not FIT and not multi-uImage.
- initramfs.bin is loaded to a manually selected address.
- The initramfs address is an example; adjust it as needed.
- The script does not set DTB or ATAGs; it passes MIPS firmware args (a0-a3),
  where a0/a1/a2/a3 default to argc/argv/envp/promvec.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import usb.core
import usb.util
from errors import X1600UsbBootError
from trampoline import MipsFwArgsLayout, Trampoline
from uimage import UImage


USB_VID = 0xA108
USB_PID = 0xEAEF

VR_GET_CPU_INFO = 0x00
VR_SET_DATA_ADDRESS = 0x01
VR_SET_DATA_LENGTH = 0x02
VR_FLUSH_CACHES = 0x03
VR_PROGRAM_START1 = 0x04
VR_PROGRAM_START2 = 0x05

BM_REQTYPE_IN = 0xC0
BM_REQTYPE_OUT = 0x40

DEFAULT_SPL_LOAD_ADDR = 0x80001800
DEFAULT_SPL_ENTRY_ADDR = 0x80001800
DEFAULT_INITRAMFS_LOAD_ADDR = 0x82100000
DEFAULT_BOOTARGS_ADDR = 0x83FF0000
DEFAULT_TRAMPOLINE_ADDR = 0x83FF1000
# Where kernel self-decompresess itself (for reference only)
KERNEL_SELF_DECOMPRESSES_TO = 0x80010000
# Where kernel uImage points (for reference only)
KERNEL_ZBOOT_LOAD_ADDRESS = 0x82F00000
# 64 MiB RAM window
RAM_BASE = 0x80000000
RAM_END_EXCL = 0x84000000

FIRST_STAGE_MAX = 20 * 1024
USB_TIMEOUT_MS = 5000

DEFAULT_BOOTARGS = "init=/usr/bin/carlinkit_otalib root=/dev/initrd mem=64M@0x0 console=ttyS2,3000000n8 rootfstype=erofs rw lpj=549888 driver_async_probe=dwc2,jz4740-mmc c2a_boot=recovery"

@dataclass
class StageLayout:
    spl_load_addr: int
    spl_entry_addr: int
    initramfs_load_addr: int


class X1600UsbBoot:
    def __init__(self, vid: int = USB_VID, pid: int = USB_PID) -> None:
        self.vid = vid
        self.pid = pid
        self.dev: Optional[usb.core.Device] = None
        self.ep_out = None
        self.ep_in = None
        self.intf_number: Optional[int] = None

    def open(self) -> None:
        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise X1600UsbBootError(
                f"USB boot device {self.vid:04x}:{self.pid:04x} not found"
            )

        self.dev = dev

        cfg = dev.get_active_configuration() if self._has_active_configuration(dev) else None
        if cfg is None:
            dev.set_configuration()
            cfg = dev.get_active_configuration()

        intf = cfg[(0, 0)]
        self.intf_number = intf.bInterfaceNumber

        try:
            if dev.is_kernel_driver_active(self.intf_number):
                dev.detach_kernel_driver(self.intf_number)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(dev, self.intf_number)

        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )

        if self.ep_out is None or self.ep_in is None:
            raise X1600UsbBootError("Failed to find bulk IN/OUT endpoints")

    @staticmethod
    def _has_active_configuration(dev: usb.core.Device) -> bool:
        try:
            dev.get_active_configuration()
            return True
        except usb.core.USBError:
            return False

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            if self.intf_number is not None:
                usb.util.release_interface(self.dev, self.intf_number)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None
        self.ep_out = None
        self.ep_in = None
        self.intf_number = None

    def ctrl_out(self, request: int, value: int = 0, index: int = 0) -> None:
        assert self.dev is not None
        self.dev.ctrl_transfer(
            BM_REQTYPE_OUT,
            request,
            wValue=value & 0xFFFF,
            wIndex=index & 0xFFFF,
            data_or_wLength=None,
            timeout=USB_TIMEOUT_MS,
        )

    def ctrl_in(self, request: int, length: int, value: int = 0, index: int = 0) -> bytes:
        assert self.dev is not None
        data = self.dev.ctrl_transfer(
            BM_REQTYPE_IN,
            request,
            wValue=value & 0xFFFF,
            wIndex=index & 0xFFFF,
            data_or_wLength=length,
            timeout=USB_TIMEOUT_MS,
        )
        return bytes(data)

    @staticmethod
    def _split_u32_be_halves(x: int) -> tuple[int, int]:
        return ((x >> 16) & 0xFFFF, x & 0xFFFF)

    def get_cpu_info(self) -> Optional[bytes]:
        try:
            return self.ctrl_in(VR_GET_CPU_INFO, 8)
        except usb.core.USBError as e:
            print(f"[!] VR_GET_CPU_INFO failed: {e}", file=sys.stderr)
            return None

    def set_data_address(self, addr: int) -> None:
        wValue, wIndex = self._split_u32_be_halves(addr)
        self.ctrl_out(VR_SET_DATA_ADDRESS, wValue, wIndex)

    def set_data_length(self, length: int) -> None:
        wValue, wIndex = self._split_u32_be_halves(length)
        self.ctrl_out(VR_SET_DATA_LENGTH, wValue, wIndex)

    def flush_caches(self) -> None:
        self.ctrl_out(VR_FLUSH_CACHES, 0, 0)

    def program_start1(self, entry: int) -> None:
        wValue, wIndex = self._split_u32_be_halves(entry)
        self.ctrl_out(VR_PROGRAM_START1, wValue, wIndex)

    def program_start2(self, entry: int) -> None:
        wValue, wIndex = self._split_u32_be_halves(entry)
        self.ctrl_out(VR_PROGRAM_START2, wValue, wIndex)

    def bulk_write(self, data: bytes, timeout_ms: Optional[int] = None) -> None:
        assert self.ep_out is not None
        if timeout_ms is None:
            timeout_ms = USB_TIMEOUT_MS

        packet_size = self.ep_out.wMaxPacketSize or 512
        total = len(data)
        sent_total = 0
        view = memoryview(data)

        while sent_total < total:
            chunk_end = min(sent_total + packet_size, total)
            chunk = view[sent_total:chunk_end]
            written = self.ep_out.write(chunk, timeout=timeout_ms)

            if written <= 0:
                raise X1600UsbBootError(f"Bulk write failed at offset 0x{sent_total:x}")

            start = sent_total
            sent_total += written
            # print(
            #    f"[USB] ACK 0x{start:08x} -> 0x{sent_total:08x} "
            #    f"(total {sent_total}/{total})"
            #)
    def bulk_read(self, length: int) -> bytes:
            assert self.ep_in is not None
            data = self.ep_in.read(length, timeout=USB_TIMEOUT_MS)
            return bytes(data)

    def download_blob(self, addr: int, blob: bytes, verify: bool = False, timeout_ms: Optional[int] = None) -> None:
            self.set_data_address(addr)
            self.set_data_length(len(blob))
            self.bulk_write(blob, timeout_ms=timeout_ms)

            if verify:
                self.set_data_address(addr)
                self.set_data_length(len(blob))
                back = self.bulk_read(len(blob))
                if back != blob:
                    raise X1600UsbBootError(
                        f"Verification failed for 0x{addr:08x}: readback differs from written data"
                    )
    def wait_for_reenumeration(
        self,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.2,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        self.close()

        while time.monotonic() < deadline:
            try:
                self.open()
                return
            except X1600UsbBootError:
                time.sleep(poll_interval_s)

        raise X1600UsbBootError(
            "Device did not return to BootROM within expected time after SPL start"
        )


def read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise X1600UsbBootError(f"Failed to read {path}: {e}") from e


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="X1600 USB boot loader: SPL -> uImage kernel + initramfs -> jump to kernel"
    )
    p.add_argument("spl", type=Path, help="Path to spl.bin")
    p.add_argument("kernel", type=Path, help="Path to kernel.bin (legacy uImage)")
    p.add_argument("initramfs", type=Path, help="Path to initramfs.bin")

    p.add_argument("--spl-load-addr", type=lambda x: int(x, 0), default=DEFAULT_SPL_LOAD_ADDR)
    p.add_argument("--spl-entry-addr", type=lambda x: int(x, 0), default=DEFAULT_SPL_ENTRY_ADDR)
    p.add_argument("--initramfs-load-addr", type=lambda x: int(x, 0), default=DEFAULT_INITRAMFS_LOAD_ADDR)
    p.add_argument(
        "--extra-bootargs",
        type=str,
        default=DEFAULT_BOOTARGS,
        help=(
            "Kernel bootargs"
        ),
    )
    p.add_argument(
        "--bootargs-addr",
        type=lambda x: int(x, 0),
        default=DEFAULT_BOOTARGS_ADDR,
        help="RAM address where the MIPS ABI block is uploaded: argv/envp + cmdline",
    )
    p.add_argument(
        "--trampoline-addr",
        type=lambda x: int(x, 0),
        default=DEFAULT_TRAMPOLINE_ADDR,
        help="RAM address for the MIPS trampoline that sets a0-a3 and jumps to kernel",
    )
    p.add_argument(
        "--linux-a0",
        type=lambda x: int(x, 0),
        default=None,
        help="a0 register value (default: argc from MIPS ABI block)",
    )
    p.add_argument(
        "--linux-a1",
        type=lambda x: int(x, 0),
        default=None,
        help="a1 register value (default: argv pointer from MIPS ABI block)",
    )
    p.add_argument(
        "--linux-a2",
        type=lambda x: int(x, 0),
        default=None,
        help="a2 register value (default: envp pointer from MIPS ABI block)",
    )
    p.add_argument(
        "--linux-a3",
        type=lambda x: int(x, 0),
        default=None,
        help="a3 register value (default: 0, promvec)",
    )
    p.add_argument(
        "--bulk-timeout",
        type=int,
        default=60000,
        help="Bulk write timeout in ms (e.g. 20000 for large images)",
    )
    p.add_argument(
        "--verify",
        default = True,
        action="store_true",
        help="After each transfer, read data back and compare",
    )
    p.add_argument(
        "--verify-uimage-data-crc",
        default=True,
        action="store_true",
        help="Also verify uImage data CRC",
    )
    p.add_argument(
        "--spl-return-timeout",
        type=float,
        default=10.0,
        help="How many seconds to wait for SPL to return to BootROM",
    )
    p.add_argument(
        "--verify-gadget-ip",
        type=str,
        default="192.168.51.2",
        help="Gadget IP to verify SSH availability after kernel launch",
    )
    p.add_argument(
        "--verify-gadget-timeout",
        type=float,
        default=20.0,
        help="How many seconds to wait for SSH port on gadget IP",
    )
    return p.parse_args()


def format_ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return max(a0, b0) < min(a1, b1)


def initramfs_bootargs(args: str, initramfs_start: int, initramfs_size: int) -> str:
    initramfs_end = initramfs_start + initramfs_size
    return (
        f"{args} "
        f"rd_start=0x{initramfs_start:08x} "
        f"rd_size=0x{initramfs_size:08x}"
    )


def wait_for_ssh_open(host: str, timeout_s: float, port: int = 22) -> bool:
    deadline = time.monotonic() + max(timeout_s, 0.0)
    while time.monotonic() <= deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    args = parse_args()

    layout = StageLayout(
        spl_load_addr=args.spl_load_addr,
        spl_entry_addr=args.spl_entry_addr,
        initramfs_load_addr=args.initramfs_load_addr,
    )

    spl = read_file(args.spl)
    kernel_raw = read_file(args.kernel)
    initramfs = read_file(args.initramfs)

    if len(spl) > FIRST_STAGE_MAX:
        raise X1600UsbBootError(
            f"SPL is {len(spl)} B, but first stage should not exceed 20 KB per the manual"
        )

    uimg = UImage(
        kernel_raw,
        verify_header_crc=True,
        verify_data_crc=args.verify_uimage_data_crc,
    )

    kernel = uimg.data
    kernel_load_addr = uimg.load_addr
    kernel_entry_addr = uimg.entry_addr
    extra_bootargs = (
        args.extra_bootargs
        if args.initramfs is None
        else initramfs_bootargs(args.extra_bootargs, layout.initramfs_load_addr, len(initramfs))
    )
    fw_args = MipsFwArgsLayout.build(args.bootargs_addr, extra_bootargs)
    linux_a0 = args.linux_a0 if args.linux_a0 is not None else fw_args.argc
    linux_a1 = args.linux_a1 if args.linux_a1 is not None else fw_args.argv_addr
    linux_a2 = args.linux_a2 if args.linux_a2 is not None else fw_args.envp_addr
    linux_a3 = args.linux_a3 if args.linux_a3 is not None else fw_args.promvec
    trampoline = Trampoline.build_linux_mips(
        kernel_entry=kernel_entry_addr,
        a0_val=linux_a0,
        a1_val=linux_a1,
        a2_val=linux_a2,
        a3_val=linux_a3,
    )

    regions = [
        ("kernel payload", kernel_load_addr, kernel_load_addr + len(kernel)),
        ("initramfs", layout.initramfs_load_addr, layout.initramfs_load_addr + len(initramfs)),
        ("bootargs", args.bootargs_addr, args.bootargs_addr + len(fw_args.blob)),
        ("trampoline", args.trampoline_addr, args.trampoline_addr + len(trampoline)),
    ]
    for name, start, end in regions:
        if start < RAM_BASE or end > RAM_END_EXCL:
            raise X1600UsbBootError(
                f"Area {name} exceeds RAM memory range: [0x{start:08x}, 0x{end:08x}), "
                f"RAM=[0x{RAM_BASE:08x}, 0x{RAM_END_EXCL:08x})"
            )
        if start >= end:
            raise X1600UsbBootError(
                f"Invalid {name} region: start=0x{start:08x}, end=0x{end:08x}"
            )
    for i, (name_a, start_a, end_a) in enumerate(regions):
        for name_b, start_b, end_b in regions[i + 1 :]:
            if overlaps(start_a, end_a, start_b, end_b):
                raise X1600UsbBootError(
                    f"Memory regions overlap: {name_a} [0x{start_a:08x}, 0x{end_a:08x}) "
                    f"and {name_b} [0x{start_b:08x}, 0x{end_b:08x})"
                )

    print(f"[+] Parsed {uimg}")
    print(
        f"[+] kernel payload: file={args.kernel}, payload_size={len(kernel)} B, "
        f"load=0x{kernel_load_addr:08x}, entry=0x{kernel_entry_addr:08x}"
    )
    print(
        f"[+] initramfs: file={args.initramfs}, size={len(initramfs)} B, "
        f"load=0x{layout.initramfs_load_addr:08x}"
    )
    print(
        f"[+] bootargs/cmdline: '{fw_args.cmdline}', size={len(fw_args.blob)} B, "
        f"addr=0x{args.bootargs_addr:08x}, argc={fw_args.argc}, "
        f"argv=0x{fw_args.argv_addr:08x}, envp=0x{fw_args.envp_addr:08x}"
    )
    print(
        f"[+] trampoline: size={len(trampoline)} B, addr=0x{args.trampoline_addr:08x}, "
        f"linux_a0=0x{linux_a0:08x}, linux_a1=0x{linux_a1:08x}, "
        f"linux_a2=0x{linux_a2:08x}, linux_a3=0x{linux_a3:08x}"
    )

    boot = X1600UsbBoot()

    try:
        print("[*] Looking for X1600 USB boot device...")
        boot.open()
        print("[+] Device found")

        cpu_info = boot.get_cpu_info()
        if cpu_info is not None:
            print(f"[+] CPU info '{format_ascii(cpu_info)}'")

        print(
            f"[*] Upload SPL: {args.spl} -> 0x{layout.spl_load_addr:08x} "
            f"({len(spl)} B), entry=0x{layout.spl_entry_addr:08x}"
        )
        boot.download_blob(layout.spl_load_addr, spl, verify=args.verify)

        print("[*] Start SPL (VR_PROGRAM_START1)")
        boot.program_start1(layout.spl_entry_addr)

        print("[*] Waiting for SPL to return to BootROM...")
        boot.wait_for_reenumeration(timeout_s=args.spl_return_timeout)
        print("[+] Device returned to BootROM")

        cpu_info = boot.get_cpu_info()
        if cpu_info is not None:
            print(f"[+] CPU info after SPL: '{format_ascii(cpu_info)}'")

        print(
            f"[*] Upload kernel payload -> 0x{kernel_load_addr:08x} "
            f"({len(kernel)} B)"
        )
        boot.download_blob(kernel_load_addr, kernel, verify=args.verify, timeout_ms=args.bulk_timeout)

        print(
            f"[*] Upload initramfs -> 0x{layout.initramfs_load_addr:08x} "
            f"({len(initramfs)} B)"
        )
        boot.download_blob(layout.initramfs_load_addr, initramfs, verify=args.verify)

        print(
            f"[*] Upload bootargs -> 0x{args.bootargs_addr:08x} "
            f"({len(fw_args.blob)} B)"
        )
        boot.download_blob(args.bootargs_addr, fw_args.blob, verify=args.verify)

        print(
            f"[*] Upload trampoline -> 0x{args.trampoline_addr:08x} "
            f"({len(trampoline)} B)"
        )
        boot.download_blob(args.trampoline_addr, trampoline, verify=args.verify)

        print("[*] Flush caches (VR_FLUSH_CACHES)")
        boot.flush_caches()

        print(
            f"[*] Start trampoline (VR_PROGRAM_START2) entry=0x{args.trampoline_addr:08x} "
            f"-> kernel=0x{kernel_entry_addr:08x}"
        )
        boot.program_start2(args.trampoline_addr)

        print("[+] Done, trampoline started and kernel launched")
        print(
            f"[*] Verifying SSH on {args.verify_gadget_ip}:22 "
            f"(timeout {args.verify_gadget_timeout:.1f}s)..."
        )
        if not wait_for_ssh_open(
            args.verify_gadget_ip,
            args.verify_gadget_timeout,
            port=22,
        ):
            print(
                f"[!] Timeout waiting for SSH on {args.verify_gadget_ip}:22",
                file=sys.stderr,
            )
            return 3
        print(f"[+] SSH is open on {args.verify_gadget_ip}:22")
        return 0

    except X1600UsbBootError as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1
    except usb.core.USBError as e:
        print(f"[!] USB error: {e}", file=sys.stderr)
        return 2
    finally:
        boot.close()


if __name__ == "__main__":
    raise SystemExit(main())
