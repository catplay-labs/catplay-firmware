#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "paramiko>=3.0",
#     "cryptography>=41",
# ]
# ///
from __future__ import annotations

import argparse
import time
from pathlib import Path

import uploader

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run exploit sequence via uploader.py")
    p.add_argument("--host", required=True, help="Target device IP/hostname")
    p.add_argument("--fw", default="fw.bin", help="Local firmware file to upload")
    p.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("."),
        help="Directory where backup_<timestamp>.bin will be saved",
    )
    return p.parse_args()


def run_step(argv: list[str]) -> int:
    print(f"[*] uploader {' '.join(argv)}")
    rc = uploader.main(argv)
    if rc != 0:
        print(f"[!] Step failed with exit code {rc}")
    return rc


def main() -> int:
    args = parse_args()
    timestamp = int(time.time())
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = args.backup_dir / f"backup_{timestamp}.bin"

    pre_steps: list[list[str]] = [
        ["--host", args.host, "--exec-cmd", "rm -rf /tmp/backup.bin"],
        ["--host", args.host, "--exec-cmd", "carlinkit_otalib backup /tmp/backup.bin"],
        ["--host", args.host, "--download", str(backup_path), "/tmp/backup.bin"],
        ["--host", args.host, "--exec-cmd", "rm -rf /tmp/backup.bin"],
    ]
    post_steps: list[list[str]] = [
        ["--host", args.host, "/tmp/fw.bin", args.fw],
        ["--host", args.host, "--exec-cmd", "mount -o remount,ro /persist || exit 0"],
        ["--host", args.host, "--exec-cmd", "carlinkit_otalib flash /tmp/fw.bin && (sleep 2 && reboot &)"],
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
