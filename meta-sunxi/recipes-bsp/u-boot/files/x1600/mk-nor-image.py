#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Frame a raw X1600 TPL for SFC NOR BootROM, then append the SPL uImage."""
import argparse
from pathlib import Path
import struct
import zlib

HEADER_SIZE = 0x800
SPL_OFFSET = 0x5000
BOOT_SIZE = 0x20000
SPL_ADDRESS = 0x81800000
PARAMS_OFFSET = 0x100
CPM_SRBC = 0x00c4
CPM_SRBC_OTG_RESET = 1 << 12

# Active NOR configuration from Ingenic's spl_params_fixer_x1600.c and
# CatPlay's AX1800 OTG reset patch. MSC0/MSC1 are not selected in that build.
CPM_CPCCR = 0x0000
CPM_CPAPCR = 0x0010
CPM_DDRCDR = 0x002c
CPM_SFCCDR = 0x0074
CPM_CPCSR = 0x00d4
BOOTROM_PLL_FREQUENCY = 624000000
BOOTROM_APLL = (52 << 20) | (1 << 14) | (2 << 11) | (1 << 8) | (1 << 7) | (1 << 6) | 1
BOOTROM_DIVIDERS = (7 << 16) | (3 << 12) | (3 << 8) | (1 << 4)
BOOTROM_CPCCR = (2 << 30) | (1 << 28) | (1 << 26) | (1 << 24) | BOOTROM_DIVIDERS
BOOTROM_DIVIDER_CHANGE = (0x55 << 24) | (7 << 20) | BOOTROM_DIVIDERS
PLL_READY = (1 << 3) | (1 << 2)
CLOCK_MUX_READY = 0xf << 28
CLOCK_DIVIDER_BUSY = 7
CDR_BUSY = 1 << 28
BOOTROM_DDR = (1 << 30) | (1 << 29) | 7
BOOTROM_SFC = (1 << 29) | 15
BOOTROM_NAND_TIMING = bytes((3, 14, 6, 20, 6, 12, 15, 0, 0, 0, 0, 0, 0, 30, 0, 0))
BOOTROM_DESCRIPTORS = (
    (CPM_SRBC, 0, CPM_SRBC_OTG_RESET, 0, 0),
    (CPM_CPAPCR, CPM_CPAPCR, BOOTROM_APLL, PLL_READY, 0),
    (CPM_CPCCR, CPM_CPCSR, BOOTROM_DIVIDER_CHANGE, 0, CLOCK_DIVIDER_BUSY),
    (CPM_CPCCR, CPM_CPCSR, BOOTROM_CPCCR, CLOCK_MUX_READY, 0),
    (CPM_DDRCDR, CPM_DDRCDR, BOOTROM_DDR, 0, CDR_BUSY),
    (CPM_SFCCDR, CPM_SFCCDR, BOOTROM_SFC, 0, CDR_BUSY),
    (CPM_SRBC, 0, 0, 0, 0),
    (0xffff, 0xffff, 0, 0, 0),
)


def crc7(data):
    """CRC-7/MMC, as used by Ingenic's spi_checksum and the X1600 BootROM."""
    crc = 0
    for value in data:
        for _ in range(8):
            crc <<= 1
            if (value ^ crc) & 0x80:
                crc ^= 0x09
            value <<= 1
        crc &= 0x7f
    return crc


def make_image(tpl, spl):
    if not tpl or len(tpl) > SPL_OFFSET - HEADER_SIZE:
        raise ValueError('TPL must contain 1..18432 bytes, leaving room for the NOR header')
    if not 64 <= len(spl) <= BOOT_SIZE - SPL_OFFSET:
        raise ValueError('SPL uImage must fit between offsets 0x5000 and 0x20000')
    magic, hcrc, _, size, load, entry, dcrc, os, arch, kind, comp, _ = struct.unpack(
        '>7I4B32s', spl[:64])
    if (magic != 0x27051956 or size != len(spl) - 64 or
            (load, entry) != (SPL_ADDRESS, SPL_ADDRESS) or
            (os, arch, kind, comp) != (17, 5, 5, 0)):
        raise ValueError('SPL must be an uncompressed MIPS U-Boot firmware uImage at 0x81800000')
    if zlib.crc32(spl[:4] + bytes(4) + spl[8:64]) != hcrc:
        raise ValueError('SPL uImage header CRC32 mismatch')
    if zlib.crc32(spl[64:]) != dcrc:
        raise ValueError('SPL uImage payload CRC32 mismatch')

    # ROM reads words; include zero alignment bytes in both length and CRC7.
    payload = tpl + bytes(-len(tpl) % 4)
    header = bytearray(HEADER_SIZE)
    struct.pack_into('<3I', header, 0, 0x03040506, 0x55aa5502, 0xffff00aa)
    header[9] = crc7(payload)
    struct.pack_into('<H', header, 12, HEADER_SIZE + len(payload))
    # Match the full vendor ROM setup; TPL then selects production clocks.
    header[PARAMS_OFFSET:PARAMS_OFFSET + 4] = b'INGE'
    struct.pack_into('<I', header, PARAMS_OFFSET + 4,
                     (HEADER_SIZE + len(payload) + 511) & ~511)
    struct.pack_into('<II', header, PARAMS_OFFSET + 8,
                     BOOTROM_PLL_FREQUENCY, BOOTROM_CPCCR)
    header[PARAMS_OFFSET + 0x10:PARAMS_OFFSET + 0x20] = BOOTROM_NAND_TIMING
    for index, descriptor in enumerate(BOOTROM_DESCRIPTORS):
        struct.pack_into('<HHIII', header, PARAMS_OFFSET + 0x20 + index * 16,
                         *descriptor)
    header[14] = crc7(header[0x100:0x200])
    header[15] = crc7(header[:15])
    image = header + payload
    return image + b'\xff' * (SPL_OFFSET - len(image)) + spl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tpl', required=True, type=Path)
    parser.add_argument('--spl', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        image = make_image(args.tpl.read_bytes(), args.spl.read_bytes())
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.output.write_bytes(image)


if __name__ == '__main__':
    main()
