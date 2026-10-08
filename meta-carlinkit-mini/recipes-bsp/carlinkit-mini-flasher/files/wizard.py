#!/usr/bin/env python3
"""Enter X1600 USB boot mode, boot recovery, and flash CLK Mini Ultra NOR."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


PRESETS = {
    "wooboobox": "192.168.1.101",
    "carlinkit": "192.168.50.100",
}
RECOVERY_FILES = (
    "../ax1800_spi_nor_burner_u-boot-spl.bin",
    "../clk-mini-ultra-nor-recov.uzImage.bin",
    "../clk-mini-ultra-nor-recov.initrd.bin",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, help="Stock device IP preset")
    parser.add_argument(
        "--fw",
        default="../clk-mini-ultra-nor.c2aflash",
        help="Firmware image (default: ../clk-mini-ultra-nor.c2aflash)",
    )
    parser.add_argument("--already-fel", action="store_true", help="Skip entering USB boot mode")
    parser.add_argument(
        "--already-recov", action="store_true", help="Skip entering USB boot mode and booting recovery"
    )
    parser.add_argument("--refresh", action="store_true", help="Enter USB boot mode via USB vendor request")
    parser.add_argument("--no-flash", action="store_true", help="Skip firmware flash")
    args = parser.parse_args(argv)
    if not (args.preset or args.refresh or args.already_fel or args.already_recov):
        parser.error("--preset is required unless --refresh, --already-fel or --already-recov is used")
    return args


def run_step(script: str, argv: list[str], *, usb_access: bool = False) -> int:
    command = [sys.executable, str(Path(__file__).resolve().parent / script), *argv]
    if usb_access and os.name == "posix" and os.geteuid() != 0:
        command.insert(0, "sudo")
    print(f"[*] {shlex.join(command)}", flush=True)
    rc = subprocess.run(command).returncode
    if rc != 0:
        print(f"[!] Step failed with exit code {rc}", file=sys.stderr)
    return rc if rc >= 0 else 128 - rc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(r"""
               /\
              /  \
             /____\
            /(o_o )\
           /  /|\   \
          /  / | \   \
             / |\
            /  | \__
           /   |    \__
          /    |       /\_/\
               |      ( o.o )
               |       > ^ <
              / \     /   \
             /   \   /_____\

    THE WIZARD WILL NOW INSTALL YOUR SOFTWARE
    """)
    try:
        if not args.already_recov and not args.already_fel:
            if args.refresh:
                reboot_args = ["--mode", "vendor_request"]
            else:
                reboot_args = ["--mode", "ultra_exploit", "--early-host", PRESETS[args.preset]]
            rc = run_step("reboot2recovery.py", reboot_args, usb_access=True)
            if rc != 0:
                return rc

        if not args.already_recov:
            rc = run_step("recov.py", list(RECOVERY_FILES), usb_access=True)
            if rc != 0:
                return rc

        if not args.no_flash:
            return run_step("flash.py", ["--host", "192.168.51.2", "--fw", args.fw])
        return 0
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
