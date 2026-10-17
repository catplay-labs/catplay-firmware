#!/usr/bin/env python3
"""
V821 FEL boot helper.

Python dependencies: pyusb, pylibfdt (imported as libfdt).

Flow:
  1) upload and start FES to initialize DRAM
  2) optionally patch the DTB in memory with bootargs and linux,initrd-start/end
  3) upload OpenSBI, Linux Image, optional EROFS initrd, DTB and simpleboot
  4) start simpleboot through FEL
  5) monitor USB for gadget, Apple reset, or FEL return, and verify the host
     actually created a network interface for the NCM gadget
"""

# TODO: this is V821 only; ARM variants will need a specific cache flush procedure
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
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
            if require_v821 and "soc=00001882" not in version:
                fel.close()
                raise FelBootError(
                    f"Unexpected SoC; required V821 ID 0x1882, got: {version}"
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


def host_netif_names() -> set:
    try:
        return set(os.listdir("/sys/class/net"))
    except OSError:
        return set()


def wait_for_new_netif(before: set, timeout_s: float = 10.0) -> Optional[str]:
    """Name of a host netif that appeared after `before`, within timeout_s."""
    deadline = time.monotonic() + timeout_s
    while True:
        new = host_netif_names() - before
        if new:
            return sorted(new)[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.2, remaining))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Boot V821 through Allwinner FEL: FES DRAM init -> simpleboot -> OpenSBI -> Linux"
    )
    p.add_argument("--fes", type=Path, required=True)
    p.add_argument("--fes-addr", type=parse_int, default=DEFAULT_FES_ADDR)
    p.add_argument("--fes-entry", type=parse_int, default=DEFAULT_FES_ENTRY)
    p.add_argument("--fes-wait-seconds", type=float, default=15)
    p.add_argument("--opensbi", type=Path, required=True)
    p.add_argument("--simpleboot", type=Path, required=True)
    p.add_argument("--kernel", type=Path, required=True)
    p.add_argument("--kernel-addr", type=parse_int, default=DEFAULT_KERNEL_ADDR)
    p.add_argument("--dtb", type=Path, required=True)
    p.add_argument("--erofs-initrd", type=Path)
    p.add_argument("--erofs-initrd-addr", type=parse_int)
    p.add_argument("--erofs-window-start", type=parse_int, default=DEFAULT_EROFS_WINDOW_START)
    p.add_argument("--erofs-window-end", type=parse_int, default=DEFAULT_EROFS_WINDOW_END)
    p.add_argument("--erofs-bootargs", default="root=/dev/initrd rootfstype=erofs ro")
    p.add_argument("--extra-bootargs")
    p.add_argument("--monitor-seconds", type=int)
    p.add_argument("--skip-load-fes", action="store_true")
    return p.parse_args()


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


def monitor_usb(args: argparse.Namespace, started: float, netif_before: set) -> int:
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
            iface = wait_for_new_netif(netif_before)
            if iface is None:
                print(
                    f"[!] Gadget {gadget_pid} enumerated, but no new host network "
                    f"interface appeared within 10 s. This is a host-side driver "
                    "gap, not a device fault: the host kernel needs the NCM stack "
                    "(usbnet, cdc_ether, cdc_ncm).",
                    file=sys.stderr,
                )
                print(
                    "    Check:  lsmod | grep -Ei 'usbnet|cdc_ncm'",
                    file=sys.stderr,
                )
                print(
                    "    Fix:    modprobe usbnet cdc_ether cdc_ncm, then re-run: "
                    "python wizard.py --already-recov",
                    file=sys.stderr,
                )
                return 5
            print(f"[+] USB gadget is working (host interface {iface}).")
            return 0

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if any(dev_id.startswith("05ac:") for dev_id in ids):
            print(f"[!] Watchdog/SBI reset to Apple after {elapsed_ms} ms.", file=sys.stderr)
            return 4

        time.sleep(0.05)

    print(f"[!] No USB gadget or FEL return within {monitor_seconds} s.", file=sys.stderr)
    return 1


def main() -> int:
    args = parse_args()
    fel: Optional[SunxiFel] = None

    try:
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
        netif_before = host_netif_names()
        fel.execute(layout.simpleboot_entry)
        fel.close()
        started = time.monotonic()
        return monitor_usb(args, started, netif_before)

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
