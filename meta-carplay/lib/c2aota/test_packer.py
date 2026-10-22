import tempfile
import unittest
from pathlib import Path

from packer import HEADER_SIZE, pack_ota, parse_partitions


class OtaPayloadLimitTests(unittest.TestCase):
    def test_mixed_legacy_and_limited_entries(self):
        self.assertEqual(
            parse_partitions("boot0 /tmp/boot 0x100 kernel /tmp/kernel rootfs /tmp/root 256"),
            [("boot0", "/tmp/boot", 256), ("kernel", "/tmp/kernel"), ("rootfs", "/tmp/root", 256)],
        )

    def test_invalid_limit_is_rejected(self):
        for raw in ("kernel /tmp/kernel 0", "kernel /tmp/kernel 0xZZ", "kernel /tmp/kernel -1"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "invalid OTA payload limit"):
                parse_partitions(raw)

    def test_limit_accepts_exact_size_and_rejects_one_extra_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "kernel"
            output = Path(directory) / "image.c2aota"
            source.write_bytes(b"payload")

            pack_ota(output, [("kernel", source, 7)])
            self.assertEqual(output.stat().st_size, HEADER_SIZE + 7)
            original = output.read_bytes()

            with self.assertRaisesRegex(ValueError, "too large: 7 > 6"):
                pack_ota(output, [("kernel", source, 6)])
            self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
