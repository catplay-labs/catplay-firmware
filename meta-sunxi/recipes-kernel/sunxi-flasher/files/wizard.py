#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyusb>=1.2",
#     "pylibfdt",
#     "paramiko>=3.0",
#     "cryptography>=41",
# ]
# ///
# The steps run as subprocesses of this interpreter, so the dependencies of all
# of them are listed here.
"""Enter FEL, boot recovery, and flash firmware."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    bundle_dir = Path(__file__).resolve().parent.parent
    default_machine = next(
        (machine for machine in ("v821b-rtl8733bs", "v553-aic8800d80", "v851s3-aic8800d80")
         if any((bundle_dir / (machine + suffix)).is_file()
                for suffix in ("-recov.fitImage", "-u-boot-fel.egon.bin"))),
        "v851s3-aic8800d80",
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preset", help="Device preset passed to cpbox2fel.py")
    p.add_argument("--machine", default=default_machine,
                   help="Machine used for default artifact paths (default: %(default)s)")
    p.add_argument("--platform", choices=("v821", "v851"),
                   help="Boot platform (default: v821 for v821* machines, otherwise v851)")
    firmware = p.add_mutually_exclusive_group()
    firmware.add_argument("--fw", help="Local c2aflash firmware (default: <machine>.c2aflash in bundle)")
    firmware.add_argument("--ota", nargs="?", const="", metavar="FILE",
                   help="Flash OTA instead of --fw (default: <machine>.c2aota in bundle; used by default)")
    p.add_argument("--already-fel", action="store_true", help="Skip entering FEL")
    p.add_argument("--already-recov", action="store_true", help="Skip entering FEL and booting recovery")
    p.add_argument("--refresh", action="store_true", help="Enter FEL via USB vendor request")
    p.add_argument("--no-flash", action="store_true", help="Only boot recovery")
    stage = p.add_mutually_exclusive_group()
    stage.add_argument("--staging", action="store_true", help="Boot the staged SPL that reads the kernel FIT from flash")
    stage.add_argument("--staging-proper", action="store_true", help="Boot the FEL SPL, then U-Boot proper reading the kernel FIT from flash")
    p.add_argument("--spl", help="FEL SPL (default: <machine>-u-boot-fel.egon.bin in bundle)")
    p.add_argument("--kernel", help="Recovery FIT (default: <machine>-recov.fitImage in bundle)")
    p.add_argument("--uboot", help="U-Boot proper (default: <machine>-u-boot-proper.bin in bundle)")
    p.add_argument("--staging-egon", help="Staging SPL (default: <machine>-u-boot-staging.egon.bin in bundle)")
    p.add_argument("--staging-uboot", help="Staging U-Boot (default: <machine>-u-boot-staging-proper.bin in bundle)")
    p.add_argument("--extra-bootargs", default="", help="Append kernel arguments to the recovery FIT")
    args = p.parse_args(argv)
    if args.platform is None:
        args.platform = "v821" if args.machine.startswith("v821") else "v851"
    args.flash_format = "fw" if args.fw is not None else "ota"
    for field, suffix in (
        ("spl", "-u-boot-fel.egon.bin"),
        ("kernel", "-recov.fitImage"),
        ("uboot", "-u-boot-proper.bin"),
        ("staging_egon", "-u-boot-staging.egon.bin"),
        ("staging_uboot", "-u-boot-staging-proper.bin"),
        ("fw", ".c2aflash"),
        ("ota", ".c2aota"),
    ):
        if getattr(args, field) is None or (field == "ota" and args.ota == ""):
            setattr(args, field, str(bundle_dir / (args.machine + suffix)))
    if args.platform == "v851" and args.preset:
        p.error("V851 does not use a device preset; power-cycle it into FEL first")
    return args


def run_step(script: str, argv: list[str]) -> int:
    command = [sys.executable, str(Path(__file__).resolve().parent / script), *argv]
    display_command = [Path(sys.executable).name, os.path.relpath(command[1]), *argv]
    print(f"[*] {shlex.join(display_command)}", flush=True)
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
                rc = run_step("reboot2recovery.py", ["--mode", "vendor_request"])
            elif args.preset:
                rc = run_step("cpbox2fel.py", [args.preset])
            else:
                rc = 0
            if rc != 0:
                return rc

        if not args.already_recov:
            argv = ["--platform", args.platform]
            if args.extra_bootargs:
                argv.append(f"--extra-bootargs={args.extra_bootargs}")
            if args.staging:
                argv.extend(["--staging", "--staging-egon", args.staging_egon])
            elif args.staging_proper:
                argv.extend([
                    "--staging-proper",
                    "--fel-spl", args.spl,
                    "--u-boot", args.staging_uboot,
                ])
            else:
                argv.extend(["--fel-spl", args.spl, "--u-boot", args.uboot, "--kernel", args.kernel])
            rc = run_step("recov.py", argv)
            if rc != 0:
                return rc
        if not args.no_flash:
            firmware = ["--" + args.flash_format, getattr(args, args.flash_format)]
            return run_step("flash.py", ["--host", "192.168.51.2", *firmware])
        return 0
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
