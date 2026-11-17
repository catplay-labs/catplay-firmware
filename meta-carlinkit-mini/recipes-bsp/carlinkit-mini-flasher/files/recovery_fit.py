#!/usr/bin/env python3
"""Boot X1600 recovery or stage a NOR loader through the USB BootROM."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys
import time

from usb.core import USBError
from errors import X1600UsbBootError
from recov import X1600UsbBoot, wait_for_ssh_open


def wait_rom(usb, timeout=10):
    deadline = time.monotonic() + timeout
    usb.close()
    time.sleep(0.2)
    while time.monotonic() < deadline:
        try:
            usb.open()
            if usb.get_cpu_info():
                return
        except X1600UsbBootError:
            pass
        except USBError as error:
            if error.errno != 19:
                raise
        usb.close()
        time.sleep(0.2)
    raise X1600UsbBootError('TPL did not return to responsive BootROM')


def read_tpl(path):
    data = Path(path).read_bytes()
    if not 0 < len(data) <= 20 * 1024:
        raise ValueError(f'{path}: TPL exceeds the 20KiB ROM window or is empty')
    return data


def read_fit(path, limit=0x2400000):
    data = Path(path).read_bytes()
    if len(data) < 40 or len(data) > limit:
        raise ValueError(f'{path}: invalid FIT file size')
    magic, size = struct.unpack_from('>II', data)
    if magic != 0xd00dfeed or not 40 <= size <= len(data):
        raise ValueError(f'{path}: missing or truncated FIT header')
    return data


def read_proper(path):
    data = Path(path).read_bytes()
    if not 64 <= len(data) <= 0x400000 or data[:4] in (b'\xd0\x0d\xfe\xed', b'\x27\x05\x19\x56'):
        raise ValueError(f'{path}: expected raw U-Boot proper, not a FIT/uImage')
    return data


def start_tpl(usb, tpl):
    usb.open()
    usb.download_blob(0x80001800, tpl, verify=True)
    # Invalidate the SRAM I-cache before executing newly uploaded TPL.
    words = [0x3c088000, 0x35091800, 0x352a5000, 0xbd200000,
             0x25290020, 0x152afffd, 0, 0xf, 0x35191800, 0x03200008, 0]
    usb.download_blob(0x80001400, struct.pack('<11I', *words), verify=True)
    usb.program_start1(0x80001400)


def init_dram(usb, tpl):
    start_tpl(usb, tpl)
    wait_rom(usb)


def start_proper(usb, proper):
    usb.download_blob(0xa3800000, proper, verify=True)
    usb.flush_caches()
    try:
        usb.program_start1(0x83800000)
    except USBError as error:
        if error.errno != 19:
            raise


def load_recovery(tpl_path, kernel_path, proper_path, *, transport_factory=X1600UsbBoot):
    # Validate all inputs before USB access. Proper relocates the kernel,
    # FDT and EROFS out of these upload windows before jumping to Linux.
    tpl, kernel, proper = read_tpl(tpl_path), read_fit(kernel_path), read_proper(proper_path)
    usb = transport_factory()
    try:
        init_dram(usb, tpl)
        usb.download_blob(0xa0800000, kernel, verify=True)
        start_proper(usb, proper)
        print('Recovery proper handoff sent; waiting for SSH', flush=True)
    finally:
        usb.close()


def load_staging_proper(tpl_path, proper_path, *, transport_factory=X1600UsbBoot):
    tpl, proper = read_tpl(tpl_path), read_proper(proper_path)
    usb = transport_factory()
    try:
        init_dram(usb, tpl)
        start_proper(usb, proper)
        print('Staging proper started; reading kernel FIT from NOR', flush=True)
    finally:
        usb.close()


def load_staging(tpl_path, *, transport_factory=X1600UsbBoot):
    tpl = read_tpl(tpl_path)
    usb = transport_factory()
    try:
        start_tpl(usb, tpl)
        print('Staging TPL started; reading SPL uImage and kernel FIT from NOR', flush=True)
    finally:
        usb.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument('--staging', action='store_true')
    stage.add_argument('--staging-proper', action='store_true')
    parser.add_argument('--tpl', required=True, type=Path)
    parser.add_argument('--proper', type=Path)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--host', default='192.168.51.2')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    if args.staging and (args.proper or args.kernel):
        parser.error('--staging only takes --tpl')
    if not args.staging and not args.proper:
        parser.error('--proper is required')
    if args.staging_proper and args.kernel:
        parser.error('--staging-proper does not upload a kernel')
    if not (args.staging or args.staging_proper) and not args.kernel:
        parser.error('--kernel is required for recovery')
    try:
        if args.staging:
            load_staging(args.tpl)
        elif args.staging_proper:
            load_staging_proper(args.tpl, args.proper)
        else:
            load_recovery(args.tpl, args.kernel, args.proper)
        # A staged production image need not expose the recovery SSH gadget.
        if args.staging or args.staging_proper:
            return 0
        if not wait_for_ssh_open(args.host, args.timeout):
            raise X1600UsbBootError('SSH did not become available')
        print(f'SSH available at {args.host}', flush=True)
        return 0
    except (OSError, ValueError, X1600UsbBootError, USBError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
