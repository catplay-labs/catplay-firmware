#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyusb>=1.2",
#     "paramiko>=3.0",
#     "cryptography>=41",
# ]
# ///
# The steps run as subprocesses of this interpreter, so the dependencies of all
# of them are listed here.
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
BUNDLE_PREFIX = "../x1600-aic8800d80-recov"



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, help="Stock device IP preset")
    parser.add_argument(
        "--fw",
        default="../x1600-aic8800d80.c2aflash",
        help="Firmware image (default: ../x1600-aic8800d80.c2aflash)",
    )
    parser.add_argument("--already-fel", action="store_true", help="Skip entering USB boot mode")
    parser.add_argument(
        "--already-recov", action="store_true", help="Skip entering USB boot mode and booting recovery"
    )
    parser.add_argument("--refresh", action="store_true", help="Enter USB boot mode via USB vendor request")
    parser.add_argument("--no-flash", action="store_true", help="Skip firmware flash")
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--staging", action="store_true", help="Boot TPL reading SPL and kernel from NOR, without flashing")
    stage.add_argument("--staging-proper", action="store_true", help="Boot USB proper reading the kernel from NOR, without flashing")
    parser.add_argument("--tpl", default=BUNDLE_PREFIX + "-u-boot-tpl.bin")
    parser.add_argument("--fit", default=BUNDLE_PREFIX + ".fitImage")
    parser.add_argument("--uboot", default=BUNDLE_PREFIX + "-u-boot-proper.bin")
    parser.add_argument("--staging-tpl", default=BUNDLE_PREFIX + "-u-boot-flash-tpl.bin")
    parser.add_argument("--staging-uboot", default=BUNDLE_PREFIX + "-u-boot-staging-proper.bin")
    args = parser.parse_args(argv)
    if args.already_recov and (args.staging or args.staging_proper):
        parser.error("--already-recov cannot be combined with --staging or --staging-proper")
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
            if args.staging:
                loader_args = ["--staging", "--tpl", args.staging_tpl]
            elif args.staging_proper:
                loader_args = ["--staging-proper", "--tpl", args.tpl, "--proper", args.staging_uboot]
            else:
                loader_args = ["--tpl", args.tpl, "--kernel", args.fit, "--proper", args.uboot]
            rc = run_step("recovery_fit.py", loader_args, usb_access=True)
            if rc != 0:
                return rc

        if not args.no_flash and not (args.staging or args.staging_proper):
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
