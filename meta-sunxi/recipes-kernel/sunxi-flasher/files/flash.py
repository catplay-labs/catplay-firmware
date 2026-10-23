#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path

import uploader

def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Upload and flash c2aflash or OTA firmware via SSH")
    p.add_argument("--host", required=True, help="Target device IP/hostname")
    firmware = p.add_mutually_exclusive_group()
    firmware.add_argument("--fw", default="fw.bin", help="Local c2aflash firmware to upload")
    firmware.add_argument("--ota", nargs="?", const="../v851s3-aic8800d80.c2aota", metavar="FILE",
                          help="Flash OTA instead (default: ../v851s3-aic8800d80.c2aota)")
    p.add_argument("--online-timeout", type=_positive_int, default=60,
                   help="Timeout in seconds waiting for SSH before flashing (default: 60)")
    p.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("."),
        help="Directory where backup_<timestamp>.bin will be saved",
    )
    return p.parse_args(argv)


def wait_for_online(host: str, timeout: int) -> int:
    print(f"[*] Waiting for SSH at {host}:22 ({timeout}s timeout)")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"[!] Timeout waiting for SSH at {host}:22")
            return 1
        try:
            with socket.create_connection((host, 22), timeout=min(3, remaining)):
                return 0
        except OSError:
            time.sleep(max(0, min(1, deadline - time.monotonic())))


def run_step(argv: list[str]) -> int:
    print(f"[*] uploader {' '.join(argv)}")
    rc = uploader.main(argv)
    if rc != 0:
        print(f"[!] Step failed with exit code {rc}")
    return rc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rc = wait_for_online(args.host, args.online_timeout)
    if rc != 0:
        return rc
    pre_steps: list[list[str]] = []
    # The legacy whole-flash backup does not support the V851 NAND layout.
    if args.ota is None:
        timestamp = int(time.time())
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = args.backup_dir / f"backup_{timestamp}.bin"
        pre_steps = [
            ["--host", args.host, "--exec-cmd", "rm -rf /tmp/backup.bin"],
            ["--host", args.host, "--exec-cmd", "carlinkit_otalib backup /tmp/backup.bin"],
            ["--host", args.host, "--download", str(backup_path), "/tmp/backup.bin"],
            ["--host", args.host, "--exec-cmd", "rm -rf /tmp/backup.bin"],
        ]
    command = "ota" if args.ota is not None else "flash"
    firmware_path = args.ota if args.ota is not None else args.fw
    post_steps: list[list[str]] = [
        ["--host", args.host, "/tmp/fw.bin", firmware_path],
        ["--host", args.host, "--exec-cmd", "mount -o remount,ro /persist || exit 0"],
        ["--host", args.host, "--exec-cmd", f"carlinkit_otalib {command} /tmp/fw.bin && (sleep 2 && reboot &)"],
    ]

    for step in pre_steps:
        rc = run_step(step)
        if rc != 0:
            return rc

    if rc != 0:
        return rc

    for step in post_steps:
        rc = run_step(step)
        if rc != 0:
            return rc

    print("[+] Flash sequence finished successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
