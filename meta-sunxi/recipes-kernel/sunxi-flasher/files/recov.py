#!/usr/bin/env python3
"""
Allwinner FEL boot helper.

Python dependencies: pyusb, pylibfdt (imported as libfdt).

Flow:
  1) initialize DRAM (V821 FES or V851 U-Boot SPL)
  2) optionally patch the DTB in memory with bootargs and linux,initrd-start/end
  3) upload OpenSBI, Linux Image, optional EROFS initrd, DTB and simpleboot
  4) start simpleboot through FEL
  5) monitor USB for gadget, Apple reset, or FEL return
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import libfdt
except ImportError:  # V851 does not patch a DTB and needs no libfdt dependency.
    libfdt = None
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

RAM_BASE = 0x80000000
RAM_END_EXCL = 0x84000000

DEFAULT_KERNEL_ADDR = 0x80000000
DEFAULT_OPENSBI_ADDR = 0x80FC0000
DEFAULT_DTB_ADDR = 0x80F00000

#DEFAULT_KERNEL_ADDR = 0x80400000
#DEFAULT_OPENSBI_ADDR = 0x80FC0000
#DEFAULT_DTB_ADDR = 0x801E0000

DEFAULT_FES_ADDR = 0x02008000
DEFAULT_FES_ENTRY = 0x02008000

V851_FEL_SPL_ADDR = 0x00028000
V851_FEL_EGON_MAX_SIZE = 0x18000
V851_KERNEL_ADDR = 0x41000000
V851_UBOOT_ADDR = 0x46000000

DEFAULT_SIMPLEBOOT_ADDR = 0x02000000
DEFAULT_SIMPLEBOOT_ENTRY = 0x020003C8

DEFAULT_EROFS_WINDOW_START = 0x81000000
DEFAULT_EROFS_WINDOW_END = 0x83000000

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
            data = bytes(self.ep_in.read(remaining, timeout=self.timeout_ms))
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


@dataclass
class BootLayout:
    opensbi_addr: int
    kernel_addr: int
    dtb_addr: int
    simpleboot_addr: int
    simpleboot_entry: int
    erofs_addr: Optional[int]
    erofs_end: Optional[int]


def parse_int(value: str) -> int:
    return int(value, 0)


def env_path(name: str, default: Path | str | None = None) -> Optional[Path]:
    value = os.environ.get(name)
    if value is None or value == "":
        if default is None:
            return None
        value = str(default)
    return Path(value).expanduser()


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return parse_int(value) if value else default


def env_optional_int(name: str) -> Optional[int]:
    value = os.environ.get(name)
    return parse_int(value) if value else None


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "check": check,
        "text": True,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    return subprocess.run(cmd, **kwargs)


def require_file(path: Path, name: str) -> None:
    if not path.is_file():
        raise FelBootError(f"Missing {name}: {path}")


def read_bootargs(tree: libfdt.Fdt) -> str:
    try:
        return tree.getprop(tree.path_offset("/chosen"), "bootargs").as_str().strip()
    except (OSError, libfdt.FdtException, ValueError) as error:
        raise FelBootError(f"Failed to read /chosen bootargs: {error}") from error


def set_dtb_property(tree: libfdt.Fdt, node: str, prop: str, value: bytes) -> None:
    try:
        # Allow space for a new property, its name, value, and alignment padding.
        tree.resize(tree.totalsize() + len(prop.encode("utf-8")) + len(value) + 32)
        tree.setprop(tree.path_offset(node), prop, value)
    except (OSError, libfdt.FdtException, ValueError) as error:
        raise FelBootError(f"Failed to write {node}/{prop}: {error}") from error


def set_dtb_string(tree: libfdt.Fdt, node: str, prop: str, value: str) -> None:
    set_dtb_property(tree, node, prop, value.encode("utf-8") + b"\0")


def set_dtb_addr_cells(tree: libfdt.Fdt, node: str, prop: str, addr: int) -> None:
    # Preserve the two-cell encoding used by the V821 boot flow.
    if not 0 <= addr <= 0xFFFFFFFF:
        raise FelBootError(f"DTB address does not fit in the low 32-bit cell: {addr:#x}")
    set_dtb_property(tree, node, prop, struct.pack(">II", 0, addr))


def wait_fel(
    timeout_s: float = 30.0,
    require_v821: bool = False,
    require_v851: bool = False,
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
            expected_soc = "00001882" if require_v821 else "00001886" if require_v851 else None
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


def append_arg(bootargs: str, value: str) -> str:
    if not value:
        return bootargs
    if not bootargs:
        return value
    return f"{bootargs} {value}"


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def lsusb_ids() -> list[str]:
    try:
        out = run(["lsusb"], capture=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    ids: list[str] = []
    for line in out.splitlines():
        for token in line.split():
            if ":" in token and len(token) == 9:
                ids.append(token.lower())
    return ids


def first_matching_pid(ids: list[str], prefix: str) -> Optional[str]:
    for dev_id in ids:
        if dev_id.startswith(prefix):
            return dev_id.split(":", 1)[1]
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Boot V821 or V851 through Allwinner FEL"
    )
    p.add_argument("--platform", choices=("v821", "v851"), default="v821")
    p.add_argument("--fes", type=Path)
    p.add_argument("--fes-addr", type=parse_int, default=DEFAULT_FES_ADDR)
    p.add_argument("--fes-entry", type=parse_int, default=DEFAULT_FES_ENTRY)
    p.add_argument("--fes-wait-seconds", type=float, default=15)
    p.add_argument("--opensbi", type=Path)
    p.add_argument("--simpleboot", type=Path)
    p.add_argument("--kernel", type=Path)
    p.add_argument("--kernel-addr", type=parse_int, default=DEFAULT_KERNEL_ADDR)
    p.add_argument("--dtb", type=Path)
    p.add_argument("--fel-spl", type=Path, help="V851 FEL eGON that initializes DRAM")
    p.add_argument("--u-boot", type=Path, help="V851 U-Boot proper image")
    p.add_argument("--staging-egon", type=Path, help="V851 SPL eGON forced to read the NAND kernel")
    p.add_argument("--staging", action="store_true", help="V851 staged SPL reads the kernel from NAND")
    p.add_argument("--staging-proper", action="store_true", help="V851 U-Boot proper reads the kernel from NAND")
    p.add_argument("--erofs-initrd", type=Path)
    p.add_argument("--erofs-initrd-addr", type=parse_int)
    p.add_argument("--erofs-window-start", type=parse_int, default=DEFAULT_EROFS_WINDOW_START)
    p.add_argument("--erofs-window-end", type=parse_int, default=DEFAULT_EROFS_WINDOW_END)
    p.add_argument("--erofs-bootargs", default="root=/dev/initrd rootfstype=erofs ro")
    p.add_argument("--extra-bootargs")
    p.add_argument("--monitor-seconds", type=int)
    p.add_argument("--skip-load-fes", action="store_true")
    args = p.parse_args()
    if args.platform == "v851":
        if args.staging and args.staging_proper:
            p.error("--staging and --staging-proper are mutually exclusive")
        required = ("staging_egon",) if args.staging else ("fel_spl", "u_boot")
        for name in required:
            if getattr(args, name) is None:
                p.error(f"--{name.replace('_', '-')} is required for V851")
        if not args.staging and not args.staging_proper and args.kernel is None:
            p.error("--kernel is required for V851 RAM boot")
        if (args.staging or args.staging_proper) and args.kernel is not None:
            p.error("--kernel cannot be combined with V851 staging")
    else:
        for name in ("fes", "opensbi", "simpleboot", "kernel", "dtb"):
            if getattr(args, name) is None:
                p.error(f"--{name.replace('_', '-')} is required for V821")
        if (args.staging or args.staging_proper or args.fel_spl or args.u_boot or
                args.staging_egon):
            p.error("V851 options require --platform v851")
    return args


def prepare_dtb(args: argparse.Namespace) -> tuple[bytes, BootLayout]:
    try:
        boot_dtb = args.dtb.read_bytes()
    except OSError as error:
        raise FelBootError(f"Failed to read DTB {args.dtb}: {error}") from error
    erofs_addr: Optional[int] = args.erofs_initrd_addr
    erofs_end: Optional[int] = None

    needs_dtb_patch = any(
        [
            args.extra_bootargs,
            args.erofs_initrd,
        ]
    )

    if libfdt is None and needs_dtb_patch:
        return prepare_dtb_with_cli(args, boot_dtb)

    if needs_dtb_patch:
        try:
            tree = libfdt.Fdt(boot_dtb)
        except libfdt.FdtException as error:
            raise FelBootError(f"Invalid DTB {args.dtb}: {error}") from error
        bootargs = read_bootargs(tree)
        bootargs = append_arg(bootargs, args.extra_bootargs)

        if args.erofs_initrd:
            size = args.erofs_initrd.stat().st_size
            if size % 4096:
                raise FelBootError(
                    f"EROFS initrd size must be 4096-byte aligned, got {size} B: {args.erofs_initrd}"
                )
            if erofs_addr is None:
                erofs_addr = ((args.erofs_window_end - size) // 4096) * 4096
                if erofs_addr < args.erofs_window_start:
                    raise FelBootError(
                        f"EROFS initrd ({size} B) does not fit in default window "
                        f"0x{args.erofs_window_start:08x}..0x{args.erofs_window_end:08x}"
                    )
            if erofs_addr % 4096:
                raise FelBootError(f"EROFS initrd address is not page aligned: 0x{erofs_addr:08x}")
            erofs_end = erofs_addr + size
            if args.erofs_initrd_addr is None:
                if erofs_addr < args.erofs_window_start or erofs_end > args.erofs_window_end:
                    raise FelBootError(
                        f"EROFS initrd outside FEL window: 0x{erofs_addr:08x}..0x{erofs_end:08x}, "
                        f"window 0x{args.erofs_window_start:08x}..0x{args.erofs_window_end:08x}"
                    )
            if erofs_addr < RAM_BASE or erofs_end > RAM_END_EXCL:
                raise FelBootError(
                    f"EROFS initrd outside RAM: 0x{erofs_addr:08x}..0x{erofs_end:08x}"
                )
            bootargs = append_arg(bootargs, args.erofs_bootargs)
            set_dtb_addr_cells(tree, "/chosen", "linux,initrd-start", erofs_addr)
            set_dtb_addr_cells(tree, "/chosen", "linux,initrd-end", erofs_end)
            print(
                f"[+] EROFS initrd: {args.erofs_initrd.name} "
                f"@ 0x{erofs_addr:08x}..0x{erofs_end:08x} ({size} B)"
            )

        set_dtb_string(tree, "/chosen", "bootargs", bootargs)
        try:
            tree.pack()
        except libfdt.FdtException as error:
            raise FelBootError(f"Failed to pack DTB: {error}") from error
        boot_dtb = bytes(tree.as_bytearray())
        print(f"[+] Bootargs: {bootargs}")

    return boot_dtb, BootLayout(
        opensbi_addr=DEFAULT_OPENSBI_ADDR,
        kernel_addr=args.kernel_addr,
        dtb_addr=DEFAULT_DTB_ADDR,
        simpleboot_addr=DEFAULT_SIMPLEBOOT_ADDR,
        simpleboot_entry=DEFAULT_SIMPLEBOOT_ENTRY,
        erofs_addr=erofs_addr,
        erofs_end=erofs_end,
    )


def prepare_dtb_with_cli(
    args: argparse.Namespace, boot_dtb: bytes,
) -> tuple[bytes, BootLayout]:
    """Patch a DTB with the target's native device-tree CLI tools.
    """
    fdtget = shutil.which("fdtget")
    fdtput = shutil.which("fdtput")
    if not fdtget or not fdtput:
        raise FelBootError(
            "V821 DTB patching needs pylibfdt or both fdtget and fdtput"
        )

    erofs_addr: Optional[int] = args.erofs_initrd_addr
    erofs_end: Optional[int] = None
    with tempfile.NamedTemporaryFile(prefix="v821-dtb-", suffix=".dtb") as temp:
        temp.write(boot_dtb)
        temp.flush()
        dtb_path = temp.name

        try:
            result = subprocess.run(
                [fdtget, "-t", "s", dtb_path, "/chosen", "bootargs"],
                check=True, capture_output=True, text=True,
            )
            bootargs = result.stdout.rstrip("\n")
        except subprocess.CalledProcessError as error:
            raise FelBootError(f"Failed to read /chosen/bootargs: {error}") from error

        bootargs = append_arg(bootargs, args.extra_bootargs)
        if args.erofs_initrd:
            size = args.erofs_initrd.stat().st_size
            if size % 4096:
                raise FelBootError(
                    f"EROFS initrd size must be 4096-byte aligned, got {size} B: {args.erofs_initrd}"
                )
            if erofs_addr is None:
                erofs_addr = ((args.erofs_window_end - size) // 4096) * 4096
                if erofs_addr < args.erofs_window_start:
                    raise FelBootError("EROFS initrd does not fit in the default FEL window")
            if erofs_addr % 4096:
                raise FelBootError(f"EROFS initrd address is not page aligned: 0x{erofs_addr:08x}")
            erofs_end = erofs_addr + size
            if erofs_addr < RAM_BASE or erofs_end > RAM_END_EXCL:
                raise FelBootError(
                    f"EROFS initrd outside RAM: 0x{erofs_addr:08x}..0x{erofs_end:08x}"
                )
            bootargs = append_arg(bootargs, args.erofs_bootargs)

        def put_string(prop: str, value: str) -> None:
            subprocess.run(
                [fdtput, "-t", "s", dtb_path, "/chosen", prop, value],
                check=True, capture_output=True, text=True,
            )

        def put_addr(prop: str, value: int) -> None:
            subprocess.run(
                [fdtput, "-t", "x", dtb_path, "/chosen", prop,
                 f"0x{value >> 32:08x}", f"0x{value & 0xffffffff:08x}"],
                check=True, capture_output=True, text=True,
            )

        try:
            put_string("bootargs", bootargs)
            if erofs_addr is not None and erofs_end is not None:
                put_addr("linux,initrd-start", erofs_addr)
                put_addr("linux,initrd-end", erofs_end)
        except subprocess.CalledProcessError as error:
            raise FelBootError(f"Failed to patch DTB with fdtput: {error}") from error
        boot_dtb = Path(dtb_path).read_bytes()

    if args.erofs_initrd and erofs_addr is not None and erofs_end is not None:
        print(
            f"[+] EROFS initrd: {args.erofs_initrd.name} "
            f"@ 0x{erofs_addr:08x}..0x{erofs_end:08x} "
            f"({args.erofs_initrd.stat().st_size} B)"
        )
    print(f"[+] Bootargs: {bootargs}")
    return boot_dtb, BootLayout(
        opensbi_addr=DEFAULT_OPENSBI_ADDR,
        kernel_addr=args.kernel_addr,
        dtb_addr=DEFAULT_DTB_ADDR,
        simpleboot_addr=DEFAULT_SIMPLEBOOT_ADDR,
        simpleboot_entry=DEFAULT_SIMPLEBOOT_ENTRY,
        erofs_addr=erofs_addr,
        erofs_end=erofs_end,
    )


def validate_regions(args: argparse.Namespace, boot_dtb: bytes, layout: BootLayout) -> None:
    regions = [
        ("OpenSBI", layout.opensbi_addr, layout.opensbi_addr + args.opensbi.stat().st_size),
        ("kernel", layout.kernel_addr, layout.kernel_addr + args.kernel.stat().st_size),
        ("DTB", layout.dtb_addr, layout.dtb_addr + len(boot_dtb)),
    ]
    if args.erofs_initrd and layout.erofs_addr is not None and layout.erofs_end is not None:
        regions.append(("EROFS initrd", layout.erofs_addr, layout.erofs_end))

    for name, start, end in regions:
        if start < RAM_BASE or end > RAM_END_EXCL:
            raise FelBootError(
                f"{name} outside 64 MiB RAM: 0x{start:08x}..0x{end:08x}, "
                f"RAM 0x{RAM_BASE:08x}..0x{RAM_END_EXCL:08x}"
            )
        if start >= end:
            raise FelBootError(f"Invalid {name} region: 0x{start:08x}..0x{end:08x}")
    for index, (name_a, start_a, end_a) in enumerate(regions):
        for name_b, start_b, end_b in regions[index + 1:]:
            if overlaps(start_a, end_a, start_b, end_b):
                raise FelBootError(
                    f"Memory regions overlap: {name_a} 0x{start_a:08x}..0x{end_a:08x} "
                    f"and {name_b} 0x{start_b:08x}..0x{end_b:08x}"
                )


def fel_write(fel: SunxiFel, addr: int, path: Path) -> None:
    print(f"[*] FEL write {path.name} -> 0x{addr:08x} ({path.stat().st_size} B)")
    fel.write(addr, path.read_bytes())


def fel_write_verified(fel: SunxiFel, addr: int, path: Path) -> None:
    data = path.read_bytes()
    print(f"[*] FEL write {path.name} -> 0x{addr:08x} ({len(data)} B)")
    fel.write(addr, data)
    print(f"[*] FEL verify {path.name} @ 0x{addr:08x}")
    readback = fel.read(addr, len(data))
    if readback != data:
        for offset, (actual, expected) in enumerate(zip(readback, data)):
            if actual != expected:
                raise FelBootError(
                    f"FES verify failed at +0x{offset:x}: "
                    f"read 0x{actual:02x}, expected 0x{expected:02x}"
                )
        raise FelBootError(
            f"FES verify failed: read {len(readback)} B, expected {len(data)} B"
        )


def load_fes(args: argparse.Namespace) -> SunxiFel:
    require_file(args.fes, "FES")

    print("[*] Waiting for FEL...")
    fel, version = wait_fel(timeout_s=30.0, require_v821=True)
    print(version)

    fel_write_verified(fel, args.fes_addr, args.fes)
    print(f"[*] FEL exe FES @ 0x{args.fes_entry:08x}")
    fel.execute(args.fes_entry)
    fel.close()
    time.sleep(0.25)

    print(f"[*] Waiting for FEL to return after FES ({args.fes_wait_seconds:.1f} s)...")
    fel, version = wait_fel(
        timeout_s=args.fes_wait_seconds,
        stable_required=2,
        usb_timeout_ms=1000,
    )
    print(f"{version}\n[+] FES completed successfully; FEL is stable again.")
    return fel


def monitor_usb(args: argparse.Namespace, started: float) -> int:
    monitor_seconds = args.monitor_seconds or 90

    last = "initial"
    deadline = time.monotonic() + monitor_seconds

    while time.monotonic() < deadline:
        ids = lsusb_ids()
        current = ",".join(
            dev_id for dev_id in ids
            if dev_id.startswith(("1f3a:efe8", "0525:", "05ac:", "18d1:", "1d6b:"))
        ) or "none"
        if current != last:
            print(f"{time.strftime('%H:%M:%S')} USB: {current}")
            last = current

        gadget_pid = first_matching_pid(ids, "0525:")
        if gadget_pid:
            print("[+] USB gadget is working.")
            return 0

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if any(dev_id.startswith("05ac:") for dev_id in ids):
            print(f"[!] Watchdog/SBI reset to Apple after {elapsed_ms} ms.", file=sys.stderr)
            return 4

        time.sleep(0.05)

    print(f"[!] No USB gadget or FEL return within {monitor_seconds} s.", file=sys.stderr)
    return 1


def v851_write_with_retry(
    fel: SunxiFel, addr: int, path: Path, *, retries: int = 8,
) -> SunxiFel:
    data = path.read_bytes()
    for attempt in range(1, retries + 1):
        try:
            print(f"[*] FEL write {path.name} -> 0x{addr:08x} ({len(data)} B), attempt {attempt}/{retries}")
            fel.write(addr, data)
            return fel
        except (FelBootError, usb.core.USBError) as error:
            fel.close()
            if attempt == retries:
                raise FelBootError(f"FEL write of {path.name} failed after {retries} attempts: {error}") from error
            print(f"[!] FEL I/O error while sending {path.name}; waiting for FEL and retrying: {error}")
            fel, version = wait_fel(timeout_s=20.0, require_v851=True)
            print(version)
            time.sleep(0.25)
    raise AssertionError("unreachable")


def boot_v851(args: argparse.Namespace) -> int:
    if args.staging:
        assert args.staging_egon is not None
        require_file(args.staging_egon, "V851 NAND staging eGON")
        image = args.staging_egon.read_bytes()
        if (len(image) < 96 or image[:4] != b"\x16\x00\x00\xea" or
                image[4:12] != b"eGON.BT0" or len(image) > V851_FEL_EGON_MAX_SIZE or
                len(image) % 512 or struct.unpack_from("<I", image, 16)[0] != len(image)):
            raise FelBootError("V851 staging SPL is not a valid-sized eGON")
        print("[*] Waiting for V851 FEL...")
        fel, version = wait_fel(timeout_s=30.0, require_v851=True)
        print(version)
        fel = v851_write_with_retry(fel, V851_FEL_SPL_ADDR, args.staging_egon)
        print(f"[*] FEL exec staged NAND SPL in place @ 0x{V851_FEL_SPL_ADDR:08x}")
        try:
            fel.execute(V851_FEL_SPL_ADDR)
        except (FelBootError, usb.core.USBError):
            pass
        finally:
            fel.close()
        return monitor_usb(args, time.monotonic())
    else:
        assert args.u_boot is not None
        require_file(args.u_boot, "V851 U-Boot proper")
        if not args.staging_proper:
            assert args.kernel is not None
            require_file(args.kernel, "V851 uImage")
    assert args.fel_spl is not None
    require_file(args.fel_spl, "V851 SPL")
    spl = args.fel_spl.read_bytes()
    if (len(spl) < 96 or spl[:4] != b"\x16\x00\x00\xea" or
            spl[4:12] != b"eGON.BT0" or len(spl) > V851_FEL_EGON_MAX_SIZE or
            len(spl) % 512 or struct.unpack_from("<I", spl, 16)[0] != len(spl)):
        raise FelBootError("V851 FEL SPL is not a valid-sized eGON")

    print("[*] Waiting for V851 FEL...")
    fel, version = wait_fel(timeout_s=30.0, require_v851=True)
    print(version)
    fel = v851_write_with_retry(fel, V851_FEL_SPL_ADDR, args.fel_spl)
    spl_entry = V851_FEL_SPL_ADDR
    print(f"[*] FEL exec V851 SPL in place @ 0x{spl_entry:08x} (DRAM init, return to FEL)")
    try:
        fel.execute(spl_entry)
    except (FelBootError, usb.core.USBError):
        # SPL may briefly disconnect the FEL endpoint while returning to BootROM.
        pass
    finally:
        fel.close()

    print("[*] Waiting for FEL after V851 DRAM initialization...")
    fel, version = wait_fel(timeout_s=20.0, require_v851=True, stable_required=2)
    print(f"{version}\n[+] V851 DRAM initialized; FEL returned.")

    assert args.u_boot is not None
    if args.kernel is not None:
        fel = v851_write_with_retry(fel, V851_KERNEL_ADDR, args.kernel)
    fel = v851_write_with_retry(fel, V851_UBOOT_ADDR, args.u_boot)
    print(f"[*] FEL exec U-Boot proper directly @ 0x{V851_UBOOT_ADDR:08x}")
    try:
        fel.execute(V851_UBOOT_ADDR)
    except (FelBootError, usb.core.USBError):
        # Jumping to U-Boot intentionally drops the FEL USB connection.
        pass
    finally:
        fel.close()
    started = time.monotonic()
    return monitor_usb(args, started)


def main() -> int:
    args = parse_args()
    fel: Optional[SunxiFel] = None

    try:
        if args.platform == "v851":
            return boot_v851(args)

        for name, path in [
            ("OpenSBI", args.opensbi),
            ("kernel", args.kernel),
            ("DTB", args.dtb),
            ("simpleboot", args.simpleboot),
        ]:
            require_file(path, name)
        if args.erofs_initrd:
            require_file(args.erofs_initrd, "EROFS initrd")

        if not args.skip_load_fes:
            fel = load_fes(args)
        else:
            fel, version = wait_fel(timeout_s=30.0, require_v821=True)
            print(version)

        boot_dtb, layout = prepare_dtb(args)
        validate_regions(args, boot_dtb, layout)

        fel_write(fel, layout.opensbi_addr, args.opensbi)
        fel_write(fel, layout.kernel_addr, args.kernel)
        if args.erofs_initrd and layout.erofs_addr is not None:
            fel_write(fel, layout.erofs_addr, args.erofs_initrd)
        print(f"[*] FEL write DTB -> 0x{layout.dtb_addr:08x} ({len(boot_dtb)} B)")
        fel.write(layout.dtb_addr, boot_dtb)
        fel_write(fel, layout.simpleboot_addr, args.simpleboot)

        print(
            f"[*] Starting simpleboot -> Nboot Falcon -> OpenSBI -> "
            f"{args.kernel.name} @ 0x{layout.kernel_addr:08x} -> Linux."
        )
        fel.execute(layout.simpleboot_entry)
        fel.close()
        started = time.monotonic()
        return monitor_usb(args, started)

    except FelBootError as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"[!] Command failed ({e.returncode}): {' '.join(e.cmd)}", file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr, end="")
        return e.returncode or 1
    except usb.core.USBError as e:
        print(f"[!] USB error: {e}", file=sys.stderr)
        return 3
    finally:
        if fel is not None:
            fel.close()


if __name__ == "__main__":
    raise SystemExit(main())
