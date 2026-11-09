#!/usr/bin/env -S pipx run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyusb>=1.2",
# ]
# ///
"""Prepare a cached CPBox OTA, upload it, and wait for BootROM FEL."""

from __future__ import annotations

import argparse
import dataclasses
import socket
import sys
import tempfile
import time
from pathlib import Path

from cpbox_client import CPBoxDeviceClient, CPBoxOtaClient, OtaRepacker


@dataclasses.dataclass(frozen=True)
class Preset:
    name: str
    ly: str
    ip: str


PRESETS = {
    "carlinkit": Preset(name="carlinkit", ly="ly5190", ip="192.168.50.100"),
    "wooboobox": Preset(name="wooboobox", ly="ly5166", ip="192.168.1.101"),
    "ekiy": Preset(name="ekiy", ly="ly5101", ip="192.168.1.101"),
}
CACHE_DIR = Path(__file__).resolve().parent / "cpbox-cache"
OTA_HOST = "cpbox-abroad.oss-us-west-1.aliyuncs.com"


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", choices=PRESETS)
    parser.add_argument("--force-ota-refresh", action="store_true",
                        help="download a fresh OTA even when cached")
    parser.add_argument("--timeout", type=_positive_int, default=60,
                        help="request timeout in seconds (default: 60)")
    parser.add_argument("--wait-seconds", type=_positive_int, default=600,
                        help="update completion timeout (default: 600)")
    return parser.parse_args(argv)


def _wait_for_port(host: str, port: int, *, timeout: float | None = None) -> None:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError(f"timed out waiting for {host}:{port} after {timeout:g}s")
        try:
            with socket.create_connection((host, port), timeout=min(3, remaining)
                                          if remaining is not None else 3):
                return
        except OSError:
            remaining = None if deadline is None else deadline - time.monotonic()
            time.sleep(1 if remaining is None else max(0, min(1, remaining)))


def _prepare_ota(preset: Preset, *, force_refresh: bool, timeout: int) -> CPBoxOtaClient.Download:
    client = CPBoxOtaClient(timeout=timeout)
    cache = CACHE_DIR / preset.ly
    image = cache / "update.img"
    method = client.decrypt_method_for(preset.ly)
    if not force_refresh and image.is_file() and image.stat().st_size > 0:
        print(f"cache: {image}")
        return CPBoxOtaClient.Download(image, method)
    print("wait: Internet connection to OTA server (60s timeout)")
    _wait_for_port(OTA_HOST, 443, timeout=60)
    download = client.download_ota(preset.ly, cache)
    print(f"downloaded: {download.update_path}")
    return download


def _wait_for_usb(find_device, *, timeout: float = 60) -> None:
    print(f"wait: USB 1f3a:efe8 ({timeout:g}s timeout)")
    deadline = time.monotonic() + timeout
    while True:
        if find_device(idVendor=0x1F3A, idProduct=0xEFE8) is not None:
            print("success: USB FEL device 1f3a:efe8 detected")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for USB FEL device 1f3a:efe8")
        time.sleep(min(0.5, remaining))


def _push(preset: Preset, image: Path, *, timeout: int, wait_seconds: int) -> int:
    client = CPBoxDeviceClient(f"http://{preset.ip}", timeout=timeout)
    device = client.query_device()
    print(
        f"device: {device.name!r}, {device.ly}, appver {device.appver}, "
        f"SID {device.sid}, update={device.update!r}"
    )
    if device.ly != preset.ly:
        raise CPBoxDeviceClient.Error(
            f"device is {device.ly}, but preset targets {preset.ly}; refusing upload"
        )
    if device.update != 0:
        raise CPBoxDeviceClient.Error(
            f"device updater is not idle (update={device.update!r}); refusing upload"
        )

    print(f"upload: {image}")
    response = client.upload(image)
    print(f"accepted: {response}")
    print(f"wait: updater -> state 3 ({wait_seconds}s timeout)")
    final_device = client.wait_for_update_complete(wait_seconds)
    if final_device.ly != preset.ly or final_device.sid != device.sid:
        raise CPBoxDeviceClient.Error("device identity changed during update")
    print(f"update complete: state 3 on {final_device.ly}")
    print("reboot: requesting device restart")
    reboot_response = client.reboot()
    print(f"success: reboot accepted: {reboot_response}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        # Check USB support before starting an update that reboots the device.
        import usb.core
        usb.core.find(idVendor=0x1F3A, idProduct=0xEFE8)
        preset = PRESETS[args.preset]
        print(f"preset: {preset.name}, {preset.ly}, {preset.ip}")
        download = _prepare_ota(preset, force_refresh=args.force_ota_refresh,
                                timeout=args.timeout)
        with tempfile.TemporaryDirectory(prefix="cpbox2fel-") as directory:
            workspace = Path(directory)
            image = workspace / "patched.img"
            result = OtaRepacker(preset.ly, download.decrypt_method).repack(
                download.update_path, image, temp_parent=workspace,
            )
            print(f"built temporary OTA: {result.output_size} bytes")
            print(f"wait: {preset.ip}:80 — connect to the device Wi-Fi (Ctrl+C to cancel)")
            _wait_for_port(preset.ip, 80)
            _push(preset, image, timeout=args.timeout, wait_seconds=args.wait_seconds)
            _wait_for_usb(usb.core.find, timeout=60)
        return 0
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except ImportError as error:
        print(f"error: USB detection requires PyUSB: {error}", file=sys.stderr)
        return 1
    except (CPBoxOtaClient.Error, CPBoxDeviceClient.Error, OtaRepacker.Error,
            OSError, ValueError, usb.core.USBError, usb.core.NoBackendError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
