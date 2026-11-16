#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyusb>=1.2",
#     "pylibfdt",
# ]
# ///
"""
Allwinner FEL boot helper.

Python dependencies: pyusb, pylibfdt (imported as libfdt).

Flow (V851 and V821):
  1) upload a U-Boot SPL that initializes DRAM and returns to FEL
  2) upload the recovery FIT and U-Boot proper at the platform addresses
  3) execute U-Boot proper, which boots the FIT
  4) monitor USB for the recovery gadget
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import libfdt
import usb.core
import usb.util

AW_USB_VID = 0x1F3A
AW_USB_PID = 0xEFE8
AW_USB_READ = 0x11
AW_USB_WRITE = 0x12
AW_FEL_VERSION = 0x001
AW_FEL_1_WRITE = 0x101
AW_FEL_1_EXEC = 0x102
AW_FEL_1_READ = 0x103
USB_TIMEOUT_MS = 10000
AW_USB_MAX_BULK_SEND = 512 * 1024
AW_USB_MAX_BULK_RECV = 512 * 1024

V851_FEL_SPL_ADDR = 0x00028000
V851_FEL_EGON_MAX_SIZE = 0x18000
V851_KERNEL_ADDR = 0x41000000
V851_UBOOT_ADDR = 0x46000000

V821_FEL_SPL_ADDR = 0x02008000
V821_FEL_EGON_MAX_SIZE = 0x8000
V821_KERNEL_ADDR = 0x81000000
V821_UBOOT_ADDR = 0x82000000
V821_STAGING_SPL_ADDR = 0x02000000
V821_STAGING_EGON_MAX_SIZE = 0xB000
V821_RTC_BOOT_GUARD_ADDR = 0x4A000208

APPLE_GADGET_ID = (0x05AC, 0x12A8)
FEL_USB_ID = (AW_USB_VID, AW_USB_PID)


@dataclass(frozen=True)
class UBootFelLayout:
    label: str
    expected_soc: str
    spl: int
    spl_limit: int
    branch: bytes
    fit: int
    proper: int
    staging: int
    staging_limit: int
    boot_guard: int | None = None

UBOOT_FEL_LAYOUTS = {
    "v851": UBootFelLayout("V851", "00001886", V851_FEL_SPL_ADDR, V851_FEL_EGON_MAX_SIZE,
                            b"\x16\x00\x00\xea", V851_KERNEL_ADDR, V851_UBOOT_ADDR,
                            V851_FEL_SPL_ADDR, V851_FEL_EGON_MAX_SIZE),
    "v821": UBootFelLayout("V821", "00001882", V821_FEL_SPL_ADDR, V821_FEL_EGON_MAX_SIZE,
                            b"\x6f\x00\x00\x06", V821_KERNEL_ADDR, V821_UBOOT_ADDR,
                            V821_STAGING_SPL_ADDR, V821_STAGING_EGON_MAX_SIZE,
                            boot_guard=V821_RTC_BOOT_GUARD_ADDR),
}


class FelBootError(RuntimeError):
    pass


class SunxiFel:
    def __init__(
        self,
        vid: int = AW_USB_VID,
        pid: int = AW_USB_PID,
        timeout_ms: int = USB_TIMEOUT_MS,
    ) -> None:
        self.vid = vid
        self.pid = pid
        self.timeout_ms = timeout_ms
        self.dev: Optional[usb.core.Device] = None
        self.ep_in = None
        self.ep_out = None
        self.interface = 0
        self.scratchpad: Optional[int] = None

    def open(self) -> None:
        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise FelBootError(f"Allwinner FEL device {self.vid:04x}:{self.pid:04x} not found")

        self.dev = dev
        try:
            cfg = dev.get_active_configuration()
        except usb.core.USBError:
            dev.set_configuration()
            cfg = dev.get_active_configuration()

        intf = cfg[(0, 0)]
        self.interface = intf.bInterfaceNumber

        try:
            if dev.is_kernel_driver_active(self.interface):
                dev.detach_kernel_driver(self.interface)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(dev, self.interface)

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
            self.close()
            raise FelBootError("Failed to find FEL bulk IN/OUT endpoints")

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            usb.util.release_interface(self.dev, self.interface)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None
        self.ep_in = None
        self.ep_out = None

    def __enter__(self) -> "SunxiFel":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def bulk_send(self, data: bytes, max_chunk: int = AW_USB_MAX_BULK_SEND) -> None:
        assert self.ep_out is not None
        view = memoryview(data)
        offset = 0
        while offset < len(data):
            chunk = view[offset:offset + max_chunk]
            written = self.ep_out.write(chunk, timeout=self.timeout_ms)
            if written <= 0:
                raise FelBootError(f"USB bulk write failed at offset 0x{offset:x}")
            offset += written

    def bulk_recv(self, length: int) -> bytes:
        assert self.ep_in is not None
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            data = bytes(self.ep_in.read(min(remaining, AW_USB_MAX_BULK_RECV), timeout=self.timeout_ms))
            if not data:
                raise FelBootError("USB bulk read returned no data")
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)

    def send_usb_request(self, request: int, length: int) -> None:
        packet = struct.pack(
            "<8sIIHI10s",
            b"AWUC\0\0\0\0",
            length,
            0x0C000000,
            request,
            length,
            b"\0" * 10,
        )
        self.bulk_send(packet)

    def read_usb_response(self) -> None:
        response = self.bulk_recv(13)
        if response[:4] != b"AWUS":
            raise FelBootError(f"Unexpected FEL USB response: {response!r}")

    def usb_write(self, data: bytes) -> None:
        self.send_usb_request(AW_USB_WRITE, len(data))
        self.bulk_send(data)
        self.read_usb_response()

    def usb_read(self, length: int) -> bytes:
        self.send_usb_request(AW_USB_READ, length)
        data = self.bulk_recv(length)
        self.read_usb_response()
        return data

    def send_fel_request(self, request: int, addr: int, length: int) -> None:
        self.usb_write(struct.pack("<IIII", request, addr, length, 0))

    def read_fel_status(self) -> None:
        self.usb_read(8)

    def version(self) -> str:
        self.send_fel_request(AW_FEL_VERSION, 0, 0)
        raw = self.usb_read(32)
        self.read_fel_status()
        sig, soc_raw, unknown_0a, protocol, unknown_12, unknown_13, scratchpad, pad0, pad1 = struct.unpack(
            "<8sIIHBBIII", raw
        )
        soc_id = (soc_raw >> 8) & 0xFFFF
        self.scratchpad = scratchpad
        signature = sig.rstrip(b"\0").decode("ascii", errors="replace")
        return (
            f"AWUSBFEX soc={soc_id:08x}({soc_id:04X}) "
            f"{signature} ver={unknown_0a} protocol={protocol} "
            f"scratchpad=0x{scratchpad:08x} pad=0x{pad0:08x},0x{pad1:08x}"
        )

    def write(self, addr: int, data: bytes) -> None:
        if not data:
            return
        self.send_fel_request(AW_FEL_1_WRITE, addr, len(data))
        self.usb_write(data)
        self.read_fel_status()

    def read(self, addr: int, length: int) -> bytes:
        if length == 0:
            return b""
        self.send_fel_request(AW_FEL_1_READ, addr, length)
        data = self.usb_read(length)
        self.read_fel_status()
        return data

    def execute(self, addr: int) -> None:
        self.send_fel_request(AW_FEL_1_EXEC, addr, 0)
        self.read_fel_status()


def _append_args(existing: str, extra: str) -> str:
    args = existing.split()
    for item in extra.split():
        if item not in args:
            args.append(item)
    return " ".join(args)


def _patch_dtb_bootargs(dtb: bytes, extra: str) -> tuple[bytes, str]:
    tree = libfdt.Fdt(dtb)
    chosen = tree.path_offset("/chosen")
    old = tree.getprop(chosen, "bootargs").as_str()
    updated = _append_args(old, extra)
    value = updated.encode("utf-8") + b"\0"
    tree.resize(tree.totalsize() + len(value) + 32)
    tree.setprop(tree.path_offset("/chosen"), "bootargs", value)
    tree.pack()
    return bytes(tree.as_bytearray()), updated


def patch_fit_bootargs(fit: bytes, extra: str = "") -> tuple[bytes, str]:
    """Append kernel arguments to the DTB selected by a FIT's default config."""
    try:
        tree = libfdt.Fdt(fit)
        # A changed DTB/hash would invalidate any FIT signature.
        def reject_signatures(node: int) -> None:
            child = tree.first_subnode(node, quiet=(libfdt.NOTFOUND,))
            while child >= 0:
                if "signature" in tree.get_name(child):
                    raise ValueError("signed FIT cannot be modified without regenerating its signature")
                reject_signatures(child)
                child = tree.next_subnode(child, quiet=(libfdt.NOTFOUND,))

        reject_signatures(tree.path_offset("/"))
        configs = tree.path_offset("/configurations")
        default = tree.getprop(configs, "default").as_str()
        config = tree.subnode_offset(configs, default)
        fdt_name = tree.getprop(config, "fdt").as_str()
        image_path = f"/images/{fdt_name}"
        image = tree.path_offset(image_path)
        dtb, bootargs = _patch_dtb_bootargs(bytes(tree.getprop(image, "data")), extra)
        hash_path = image_path + "/hash-1"
        algo = tree.getprop(tree.path_offset(hash_path), "algo").as_str()
        if algo == "crc32":
            digest = struct.pack(">I", zlib.crc32(dtb) & 0xffffffff)
        elif algo in ("sha1", "sha256"):
            digest = hashlib.new(algo, dtb).digest()
        else:
            raise ValueError(f"unsupported FIT image hash algorithm {algo!r}")
        tree.resize(tree.totalsize() + len(dtb) + len(digest) + 64)
        tree.setprop(tree.path_offset(image_path), "data", dtb)
        tree.setprop(tree.path_offset(hash_path), "value", digest)
        tree.pack()
        return bytes(tree.as_bytearray()), bootargs
    except libfdt.FdtException as error:
        raise ValueError(f"invalid FIT or DTB: {error}") from error


def require_file(path: Path, name: str) -> None:
    if not path.is_file():
        raise FelBootError(f"Missing {name}: {path}")


def wait_fel(
    timeout_s: float = 30.0,
    expected_soc: str | None = None,
    stable_required: int = 1,
    usb_timeout_ms: int = USB_TIMEOUT_MS,
) -> tuple[SunxiFel, str]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    stable = 0
    last_version = ""
    while time.monotonic() < deadline:
        fel = SunxiFel(timeout_ms=usb_timeout_ms)
        try:
            fel.open()
            version = fel.version()
            if expected_soc and f"soc={expected_soc}" not in version:
                fel.close()
                raise FelBootError(
                    f"Unexpected SoC; required {expected_soc}, got: {version}"
                )
            stable += 1
            last_version = version
            if stable >= stable_required:
                fel.timeout_ms = USB_TIMEOUT_MS
                return fel, last_version
            fel.close()
        except (FelBootError, usb.core.USBError) as e:
            fel.close()
            last_error = str(e)
            stable = 0
        time.sleep(0.2)
    raise FelBootError(f"FEL did not appear within {timeout_s:.1f} s: {last_error}")


def usb_ids() -> set[tuple[int, int]]:
    return {(dev.idVendor, dev.idProduct) for dev in usb.core.find(find_all=True)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Boot V821 or V851 through Allwinner FEL"
    )
    p.add_argument("--platform", choices=("v821", "v851"), default="v821")
    p.add_argument("--kernel", type=Path)
    p.add_argument("--extra-bootargs", default="", help="Append kernel arguments to the RAM recovery FIT")
    p.add_argument("--fel-spl", type=Path, help="FEL eGON that initializes DRAM and returns to BootROM")
    p.add_argument("--u-boot", type=Path, help="U-Boot proper image for the selected platform")
    p.add_argument("--staging-egon", type=Path, help="Staging SPL eGON reading the flash FIT")
    p.add_argument("--staging", action="store_true", help="Staged SPL reads the kernel FIT from flash")
    p.add_argument("--staging-proper", action="store_true", help="U-Boot proper reads the kernel FIT from flash")
    p.add_argument("--monitor-seconds", type=int)
    args = p.parse_args()
    if args.staging and args.staging_proper:
        p.error("--staging and --staging-proper are mutually exclusive")
    required = ("staging_egon",) if args.staging else ("fel_spl", "u_boot")
    for name in required:
        if getattr(args, name) is None:
            p.error(f"--{name.replace('_', '-')} is required for {args.platform.upper()}")
    if not args.staging and not args.staging_proper and args.kernel is None:
        p.error("--kernel is required for RAM recovery")
    if (args.staging or args.staging_proper) and args.kernel is not None:
        p.error("--kernel cannot be combined with flash staging")
    if (args.staging or args.staging_proper) and args.extra_bootargs:
        p.error("--extra-bootargs requires a RAM-boot FIT image")
    return args


def monitor_usb(args: argparse.Namespace, started: float) -> int:
    monitor_seconds = args.monitor_seconds or 90

    last = "initial"
    deadline = time.monotonic() + monitor_seconds
    # Both boot paths just executed their payload through a live FEL device.
    fel_present = True

    while time.monotonic() < deadline:
        ids = usb_ids()
        current = ",".join(
            f"{vid:04x}:{pid:04x}" for vid, pid in sorted(ids)
            if (vid, pid) == FEL_USB_ID or vid in (0x0525, 0x18D1, 0x1D6B)
            or (vid, pid) == APPLE_GADGET_ID
        ) or "none"
        if fel_present and FEL_USB_ID not in ids:
            print("[*] Observed FEL USB disconnect", flush=True)
        elif not fel_present and FEL_USB_ID in ids:
            print("[*] Observed FEL USB reappearance after disconnect", flush=True)
        fel_present = FEL_USB_ID in ids
        if current != last:
            print(f"{time.strftime('%H:%M:%S')} USB: {current}")
            last = current

        if any(vid == 0x0525 for vid, _pid in ids):
            print("[+] USB gadget is working.")
            return 0

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if APPLE_GADGET_ID in ids:
            if args.staging or args.staging_proper:
                print("[+] Non-recovery Apple iPhone gadget is working.")
                return 0
            print(f"[!] Watchdog/SBI reset to Apple after {elapsed_ms} ms.", file=sys.stderr)
            return 4

        time.sleep(0.05)

    print(f"[!] No booted USB gadget within {monitor_seconds} s.", file=sys.stderr)
    return 1


def fel_write_with_retry(
    fel: SunxiFel, addr: int, path: Path, *, platform: str = "v851", retries: int = 8,
    verify: bool = False,
) -> SunxiFel:
    data = path.read_bytes()
    for attempt in range(1, retries + 1):
        try:
            print(f"[*] FEL write {path.name} -> 0x{addr:08x} ({len(data)} B), attempt {attempt}/{retries}")
            fel.write(addr, data)
            if verify:
                if fel.read(addr, len(data)) != data:
                    raise FelBootError(f"Readback mismatch for {path.name} at 0x{addr:08x}")
                print(f"[+] Readback verified: {path.name} ({len(data)} B)")
            return fel
        except (FelBootError, usb.core.USBError) as error:
            fel.close()
            if attempt == retries:
                raise FelBootError(f"FEL write of {path.name} failed after {retries} attempts: {error}") from error
            print(f"[!] FEL I/O error while sending {path.name}; waiting for FEL and retrying: {error}")
            fel, version = wait_fel(timeout_s=20.0, expected_soc=UBOOT_FEL_LAYOUTS[platform].expected_soc)
            print(version)
            time.sleep(0.25)
    raise AssertionError("unreachable")


def clear_boot_guard(fel: SunxiFel, layout: UBootFelLayout) -> None:
    if layout.boot_guard is None:
        return
    # This explicit RAM boot supersedes a failed NOR boot's RTC guard.
    try:
        fel.write(layout.boot_guard, bytes(4))
        if fel.read(layout.boot_guard, 4) != bytes(4):
            raise FelBootError("RTC boot guard clear readback failed")
    except (FelBootError, usb.core.USBError):
        fel.close()
        raise
    print("[+] Cleared RTC boot guard for the requested RAM boot")


def boot_uboot(args: argparse.Namespace) -> int:
    layout = UBOOT_FEL_LAYOUTS[args.platform]
    if args.staging:
        assert args.staging_egon is not None
        require_file(args.staging_egon, f"{layout.label} flash staging eGON")
        image = args.staging_egon.read_bytes()
        if (len(image) < 96 or image[:4] != layout.branch or
                image[4:12] != b"eGON.BT0" or len(image) > layout.staging_limit or
                len(image) % 512 or struct.unpack_from("<I", image, 16)[0] != len(image)):
            raise FelBootError(f"{layout.label} staging SPL is not a valid-sized eGON")
        print(f"[*] Waiting for {layout.label} FEL...")
        fel, version = wait_fel(timeout_s=30.0, expected_soc=layout.expected_soc)
        print(version)
        fel = fel_write_with_retry(fel, layout.staging, args.staging_egon, platform=args.platform)
        clear_boot_guard(fel, layout)
        print(f"[*] FEL exec staged flash SPL in place @ 0x{layout.staging:08x}")
        try:
            fel.execute(layout.staging)
        except (FelBootError, usb.core.USBError):
            pass
        finally:
            fel.close()
        return monitor_usb(args, time.monotonic())
    else:
        assert args.u_boot is not None
        require_file(args.u_boot, f"{layout.label} U-Boot proper")
        if not args.staging_proper:
            assert args.kernel is not None
            require_file(args.kernel, f"{layout.label} recovery FIT")
    assert args.fel_spl is not None
    require_file(args.fel_spl, f"{layout.label} SPL")
    spl = args.fel_spl.read_bytes()
    if (len(spl) < 96 or spl[:4] != layout.branch or
            spl[4:12] != b"eGON.BT0" or len(spl) > layout.spl_limit or
            len(spl) % 512 or struct.unpack_from("<I", spl, 16)[0] != len(spl)):
        raise FelBootError(f"{layout.label} FEL SPL is not a valid-sized eGON")

    if args.kernel is not None and args.kernel.stat().st_size > layout.proper - layout.fit:
        raise FelBootError("Recovery FIT overlaps the U-Boot load address")
    if args.u_boot.stat().st_size > (0x84000000 if args.platform == "v821" else 0x48000000) - layout.proper:
        raise FelBootError("U-Boot proper exceeds the platform DRAM window")
    print(f"[*] Waiting for {layout.label} FEL...")
    fel, version = wait_fel(timeout_s=30.0, expected_soc=layout.expected_soc)
    print(version)
    fel = fel_write_with_retry(fel, layout.spl, args.fel_spl, platform=args.platform)
    spl_entry = layout.spl
    print(f"[*] FEL exec {layout.label} SPL in place @ 0x{spl_entry:08x} (DRAM init, return to FEL)")
    try:
        fel.execute(spl_entry)
    except (FelBootError, usb.core.USBError):
        # SPL may briefly disconnect the FEL endpoint while returning to BootROM.
        pass
    finally:
        fel.close()

    time.sleep(0.25)
    print(f"[*] Waiting for FEL after {layout.label} DRAM initialization...")
    fel, version = wait_fel(timeout_s=20.0, expected_soc=layout.expected_soc, stable_required=2)
    print(f"{version}\n[+] {layout.label} DRAM initialized; FEL returned.")

    assert args.u_boot is not None
    if args.kernel is not None:
        fel = fel_write_with_retry(fel, layout.fit, args.kernel, platform=args.platform)
    fel = fel_write_with_retry(fel, layout.proper, args.u_boot, platform=args.platform, verify=True)
    print(f"[*] FEL exec U-Boot proper directly @ 0x{layout.proper:08x}")
    try:
        fel.execute(layout.proper)
    except (FelBootError, usb.core.USBError):
        # Jumping to U-Boot intentionally drops the FEL USB connection.
        pass
    finally:
        fel.close()
    started = time.monotonic()
    return monitor_usb(args, started)


def main() -> int:
    args = parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix=f"{args.platform}-recovery-") as directory:
            if args.kernel is not None and args.extra_bootargs:
                require_file(args.kernel, "recovery FIT")
                data = args.kernel.read_bytes()
                is_fit = len(data) >= 4 and struct.unpack_from(">I", data)[0] == (libfdt.FDT_MAGIC & 0xFFFFFFFF)
                if is_fit:
                    patched, bootargs = patch_fit_bootargs(data, args.extra_bootargs)
                    args.kernel = Path(directory) / args.kernel.name
                    args.kernel.write_bytes(patched)
                    print(f"[+] Temporary FIT bootargs: {bootargs}", flush=True)
                elif args.extra_bootargs:
                    raise FelBootError("--extra-bootargs requires a FIT image")
            return boot_uboot(args)

    except usb.core.USBError as e:
        print(f"[!] USB error: {e}", file=sys.stderr)
        return 3
    except (FelBootError, OSError, ValueError) as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
