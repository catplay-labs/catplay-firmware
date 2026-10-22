"""Native Python writer for the format in carlinkit_otalib::mini::ota_packer."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import tempfile
from pathlib import Path
from typing import TypeAlias

HEADER_SIZE = 131_072
BLOCK_SIZE = 131_072
MAX_IMAGES = 8
POLY = 0x04C11DB7
OtaSection: TypeAlias = tuple[str, str | os.PathLike[str]] | tuple[str, str | os.PathLike[str], int]


def parse_partitions(raw: str) -> list[OtaSection]:
    """Parse (img_type file [max_payload_bytes]) entries from BitBake metadata."""
    parts = shlex.split(raw)
    sections: list[OtaSection] = []
    index = 0
    while index < len(parts):
        if index + 1 >= len(parts):
            raise ValueError("C2A_OTA_PARTITIONS must contain (img_type file [max_payload_bytes]) entries")
        image_type, source = parts[index:index + 2]
        index += 2
        if index < len(parts) and parts[index] and parts[index][0] in "+-0123456789":
            try:
                limit = int(parts[index], 0)
            except ValueError as error:
                raise ValueError(f"invalid OTA payload limit for {image_type}: {parts[index]!r}") from error
            if limit <= 0:
                raise ValueError(f"invalid OTA payload limit for {image_type}: {limit}")
            sections.append((image_type, source, limit))
            index += 1
        else:
            sections.append((image_type, source))
    if not sections:
        raise ValueError("C2A_OTA_PARTITIONS must contain (img_type file [max_payload_bytes]) entries")
    return sections


def _crc_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (POLY if crc & 0x8000_0000 else 0)) & 0xFFFF_FFFF
        table.append(crc)
    return tuple(table)


CRC_TABLE = _crc_table()


def cksum_crc32(data: bytes) -> int:
    """Match mini/crc.rs:cksum_crc32, including the little-endian length suffix."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]) & 0xFFFF_FFFF
    length = len(data)
    while length:
        crc = ((crc << 8) ^ CRC_TABLE[((crc >> 24) ^ (length & 0xFF)) & 0xFF]) & 0xFFFF_FFFF
        length >>= 8
    return crc


def _metadata_lines(raw: str) -> list[str]:
    reserved = {"ota_version", "check_img", "bs_size", "ota_img_packet_md5"}
    lines = []
    for item in shlex.split(raw):
        key, sep, value = item.partition("=")
        if not sep or not key or not value or key in reserved or key.startswith("img_"):
            raise ValueError(f"invalid OTA metadata: {item!r}")
        if any(c in item for c in "\r\n\0"):
            raise ValueError(f"invalid OTA metadata: {item!r}")
        item.encode("ascii")
        lines.append(item)
    if len({line.partition("=")[0] for line in lines}) != len(lines):
        raise ValueError("duplicate OTA metadata key")
    return lines


def _check_atom(value: str, description: str) -> None:
    if not value or any(c in value for c in "\r\n\0="):
        raise ValueError(f"invalid {description}: {value!r}")
    value.encode("utf-8")


def pack_ota(
    output: str | os.PathLike[str],
    sections: list[OtaSection],
    metadata: str = "",
    bs_size: int = BLOCK_SIZE,
) -> Path:
    """Pack (img_type, file[, max_payload_bytes]) entries."""
    if not 0 < len(sections) <= MAX_IMAGES:
        raise ValueError(f"OTA needs 1..{MAX_IMAGES} images")
    if not 0 < bs_size <= HEADER_SIZE:
        raise ValueError("invalid bs_size")
    metadata_lines = _metadata_lines(metadata)

    image_records = []
    packet_md5 = hashlib.md5()
    seen = set()
    for section in sections:
        if len(section) not in (2, 3):
            raise ValueError(f"invalid OTA section: {section!r}")
        image_type, source = section[:2]
        limit = section[2] if len(section) == 3 else None
        if limit is not None and limit <= 0:
            raise ValueError(f"invalid OTA payload limit for {image_type}: {limit}")
        source = Path(source)
        image_name = source.name
        _check_atom(image_type, "img_type")
        _check_atom(image_name, "img_name")
        if image_type in seen:
            raise ValueError(f"duplicate img_type: {image_type}")
        seen.add(image_type)
        if limit is not None:
            actual = source.stat().st_size
            if actual > limit:
                raise ValueError(f"{source} too large: {actual} > {limit}")

        image_md5 = hashlib.md5()
        image_crc = 0
        size = 0
        with source.open("rb") as stream:
            while chunk := stream.read(bs_size):
                size += len(chunk)
                image_md5.update(chunk)
                packet_md5.update(chunk)
                image_crc ^= cksum_crc32(chunk)
        if not 0 < size <= 0xFFFF_FFFF:
            raise ValueError(f"invalid image size for {source}: {size}")
        if limit is not None and size > limit:
            raise ValueError(f"{source} too large: {size} > {limit}")
        image_records.append((image_type, image_name, source, size, image_md5.hexdigest(), image_crc))

    header = "ota_version=0\n\n"
    for image_type, image_name, _, size, md5, crc in image_records:
        header += (
            f"img_type={image_type}\n"
            f"img_name={image_name}\n"
            f"img_size={size}\n"
            f"img_md5={md5}\n"
            f"img_crc={crc}\n\n"
        )
    header += f"check_img=1\nbs_size={bs_size}\n"
    for line in metadata_lines:
        header += f"{line}\n"
    header += f"ota_img_packet_md5={packet_md5.hexdigest()}\n"
    header_bytes = header.encode("utf-8")
    if len(header_bytes) > HEADER_SIZE:
        raise ValueError(f"OTA header too large: {len(header_bytes)} > {HEADER_SIZE}")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            temp.write(header_bytes)
            temp.write(bytes(HEADER_SIZE - len(header_bytes)))
            for _, _, source, expected_size, expected_md5, _ in image_records:
                copied = 0
                copied_md5 = hashlib.md5()
                with source.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        temp.write(chunk)
                        copied += len(chunk)
                        copied_md5.update(chunk)
                if copied != expected_size or copied_md5.hexdigest() != expected_md5:
                    raise ValueError(f"image changed while packing: {source}")
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    os.replace(temp_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--section", nargs=2, metavar=("IMG_TYPE", "FILE"), action="append", required=True)
    parser.add_argument("--metadata", default="")
    parser.add_argument("--bs-size", type=int, default=BLOCK_SIZE)
    args = parser.parse_args()
    print(pack_ota(args.output, args.section, args.metadata, args.bs_size))


if __name__ == "__main__":
    main()
