"""Low-level Ingenic X1600 USB BootROM transport."""

from __future__ import annotations

import logging
import time
import socket

import usb.core
import usb.util

from errors import X1600UsbBootError

USB_VID = 0xA108
USB_PID = 0xEAEF
USB_TIMEOUT_MS = 5000
USB_PACKETS_PER_TRANSFER = 32
USB_PROGRESS_INTERVAL = 1024 * 1024

LOGGER = logging.getLogger(__name__)

VR_GET_CPU_INFO = 0x00
VR_SET_DATA_ADDRESS = 0x01
VR_SET_DATA_LENGTH = 0x02
VR_FLUSH_CACHES = 0x03
VR_PROGRAM_START1 = 0x04
VR_PROGRAM_START2 = 0x05
BM_REQTYPE_IN = 0xC0
BM_REQTYPE_OUT = 0x40


class X1600UsbBoot:
    """Low-level transport for one X1600 device in BootROM mode."""

    def __init__(self, vid: int = USB_VID, pid: int = USB_PID) -> None:
        self.vid = vid
        self.pid = pid
        self.dev: usb.core.Device | None = None
        self.ep_out = None
        self.ep_in = None
        self.intf_number: int | None = None

    @classmethod
    def device_present(cls, vid: int = USB_VID, pid: int = USB_PID) -> bool:
        return usb.core.find(idVendor=vid, idProduct=pid) is not None

    def open(self) -> None:
        device = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if device is None:
            raise X1600UsbBootError(
                f"USB boot device {self.vid:04x}:{self.pid:04x} not found"
            )

        self.dev = device
        try:
            configuration = device.get_active_configuration()
        except usb.core.USBError:
            device.set_configuration()
            configuration = device.get_active_configuration()

        interface = configuration[(0, 0)]
        self.intf_number = interface.bInterfaceNumber
        try:
            if device.is_kernel_driver_active(self.intf_number):
                device.detach_kernel_driver(self.intf_number)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(device, self.intf_number)
        self.ep_out = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_direction(endpoint.bEndpointAddress)
                == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(endpoint.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        self.ep_in = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_direction(endpoint.bEndpointAddress)
                == usb.util.ENDPOINT_IN
                and usb.util.endpoint_type(endpoint.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        if self.ep_out is None or self.ep_in is None:
            self.close()
            raise X1600UsbBootError("failed to find bulk IN/OUT endpoints")

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            if self.intf_number is not None:
                usb.util.release_interface(self.dev, self.intf_number)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None
        self.ep_out = None
        self.ep_in = None
        self.intf_number = None

    @staticmethod
    def _split_u32_be_halves(value: int) -> tuple[int, int]:
        return ((value >> 16) & 0xFFFF, value & 0xFFFF)

    def _ctrl_out(self, request: int, value: int = 0, index: int = 0) -> None:
        if self.dev is None:
            raise X1600UsbBootError("USB boot device is not open")
        self.dev.ctrl_transfer(
            BM_REQTYPE_OUT,
            request,
            wValue=value & 0xFFFF,
            wIndex=index & 0xFFFF,
            data_or_wLength=None,
            timeout=USB_TIMEOUT_MS,
        )

    def _ctrl_in(self, request: int, length: int) -> bytes:
        if self.dev is None:
            raise X1600UsbBootError("USB boot device is not open")
        return bytes(
            self.dev.ctrl_transfer(
                BM_REQTYPE_IN,
                request,
                wValue=0,
                wIndex=0,
                data_or_wLength=length,
                timeout=USB_TIMEOUT_MS,
            )
        )

    def get_cpu_info(self) -> bytes | None:
        try:
            return self._ctrl_in(VR_GET_CPU_INFO, 8)
        except usb.core.USBError:
            return None

    def _set_u32(self, request: int, value: int) -> None:
        high, low = self._split_u32_be_halves(value)
        self._ctrl_out(request, high, low)

    def download_blob(
        self,
        address: int,
        blob: bytes,
        verify: bool = False,
        timeout_ms: int | None = None,
    ) -> None:
        self._set_u32(VR_SET_DATA_ADDRESS, address)
        self._set_u32(VR_SET_DATA_LENGTH, len(blob))
        self._bulk_write(blob, timeout_ms)
        if verify:
            self._set_u32(VR_SET_DATA_ADDRESS, address)
            self._set_u32(VR_SET_DATA_LENGTH, len(blob))
            if self._bulk_read(len(blob), timeout_ms) != blob:
                raise X1600UsbBootError(
                    f"verification failed for address 0x{address:08x}"
                )

    def read_blob(self, address: int, length: int) -> bytes:
        """Read a RAM region through the BootROM bulk-IN path."""
        self._set_u32(VR_SET_DATA_ADDRESS, address)
        self._set_u32(VR_SET_DATA_LENGTH, length)
        return self._bulk_read(length)

    def _bulk_write(self, data: bytes, timeout_ms: int | None) -> None:
        if self.ep_out is None:
            raise X1600UsbBootError("bulk OUT endpoint is not open")
        timeout_ms = USB_TIMEOUT_MS if timeout_ms is None else timeout_ms
        packet_size = self.ep_out.wMaxPacketSize or 512
        transfer_size = packet_size * USB_PACKETS_PER_TRANSFER
        sent = 0
        next_progress = USB_PROGRESS_INTERVAL
        view = memoryview(data)
        while sent < len(data):
            try:
                written = self.ep_out.write(
                    view[sent : sent + transfer_size], timeout=timeout_ms
                )
            except usb.core.USBError as exc:
                # A timed-out OUT URB can have reached the device without its
                # completion reaching usbfs. Retrying at the same offset would
                # then duplicate data in BootROM's sequential receive stream.
                raise X1600UsbBootError(
                    f"bulk write failed at offset 0x{sent:x} of "
                    f"0x{len(data):x}: {exc}"
                ) from exc
            if written <= 0:
                raise X1600UsbBootError(f"bulk write failed at offset 0x{sent:x}")
            sent += written
            if sent >= next_progress or sent == len(data):
                LOGGER.info(
                    "USB upload progress: 0x%x/0x%x (%.1f%%)",
                    sent,
                    len(data),
                    sent * 100 / len(data),
                )
                next_progress = ((sent // USB_PROGRESS_INTERVAL) + 1) * (
                    USB_PROGRESS_INTERVAL
                )

    def _bulk_read(self, length: int, timeout_ms: int | None = None) -> bytes:
        if self.ep_in is None:
            raise X1600UsbBootError("bulk IN endpoint is not open")
        timeout_ms = USB_TIMEOUT_MS if timeout_ms is None else timeout_ms
        packet_size = self.ep_in.wMaxPacketSize or 512
        received = bytearray()
        while len(received) < length:
            try:
                chunk = self.ep_in.read(
                    min(packet_size, length - len(received)),
                    timeout=timeout_ms,
                )
            except usb.core.USBError as exc:
                raise X1600UsbBootError(
                    f"bulk read failed at offset 0x{len(received):x} of "
                    f"0x{length:x}: {exc}"
                ) from exc
            if len(chunk) <= 0:
                raise X1600UsbBootError(
                    f"bulk read failed at offset 0x{len(received):x}"
                )
            received.extend(chunk)
        return bytes(received)

    def program_start1(self, entry: int) -> None:
        self._set_u32(VR_PROGRAM_START1, entry)

    def program_start2(self, entry: int) -> None:
        self._set_u32(VR_PROGRAM_START2, entry)

    def flush_caches(self) -> None:
        self._ctrl_out(VR_FLUSH_CACHES)

    def wait_for_reenumeration(
        self,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.2,
    ) -> None:
        if self.dev is None:
            raise X1600UsbBootError("USB boot device is not open")

        deadline = time.monotonic() + timeout_s
        self.close()
        # X1600 can return from the first-stage program without changing its
        # USB address.  Merely reopening the existing configuration therefore
        # says nothing about BootROM readiness.  Give the jump a chance to
        # start, then require a successful vendor probe before bulk uploads.
        time.sleep(min(poll_interval_s, max(0.0, deadline - time.monotonic())))

        while time.monotonic() < deadline:
            try:
                self.open()
            except X1600UsbBootError:
                time.sleep(poll_interval_s)
                continue
            if self.get_cpu_info():
                return
            self.close()
            time.sleep(poll_interval_s)
        raise X1600UsbBootError(
            "device did not become responsive in BootROM within the SPL return timeout"
        )


def wait_for_ssh_open(host: str, timeout_s: float, port: int = 22) -> bool:
    deadline = time.monotonic() + max(timeout_s, 0.0)
    while time.monotonic() <= deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False
