"""Tests for the host-side NCM interface check in recov.monitor_usb().

recov.py imports pyusb and libfdt at module level, so run with an
interpreter that has them installed:

    python3 -m unittest discover -s <this dir> -v
"""

import argparse
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "files"))

try:
    import recov
except ImportError:
    recov = None

GADGET_IDS = ["1d6b:0002", "1f3a:efe8", "0525:a4a1", "1d6b:0003"]


def _args() -> argparse.Namespace:
    # monitor_seconds=0 falls back to the 90 s default inside monitor_usb()
    return argparse.Namespace(monitor_seconds=0)


@unittest.skipIf(recov is None, "recov.py not importable (needs pyusb + pylibfdt)")
class MonitorUsbHostCheckTest(unittest.TestCase):
    def test_gadget_without_new_host_interface_returns_5(self):
        with patch.object(recov, "lsusb_ids", return_value=GADGET_IDS), patch.object(
            recov, "wait_for_new_netif", return_value=None
        ):
            code = recov.monitor_usb(_args(), 0.0, set())
        self.assertEqual(code, 5)

    def test_gadget_with_new_host_interface_returns_0(self):
        with patch.object(recov, "lsusb_ids", return_value=GADGET_IDS), patch.object(
            recov, "wait_for_new_netif", return_value="usb0"
        ):
            code = recov.monitor_usb(_args(), 0.0, set())
        self.assertEqual(code, 0)

    def test_apple_reset_still_returns_4(self):
        ids = ["1d6b:0002", "05ac:12a8", "1d6b:0003"]
        with patch.object(recov, "lsusb_ids", side_effect=[[], ids]):
            code = recov.monitor_usb(_args(), 0.0, set())
        self.assertEqual(code, 4)

    def test_no_gadget_within_timeout_returns_1(self):
        with patch.object(recov, "lsusb_ids", return_value=["1d6b:0002"]), patch.object(
            recov.time, "monotonic", side_effect=[0.0, 91.0]
        ):
            code = recov.monitor_usb(_args(), 0.0, set())
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
