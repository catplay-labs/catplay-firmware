#!/usr/bin/env python3
"""Enter FEL, boot recovery, and flash firmware."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


FDT_MAGIC = 0xD00DFEED
FIT_APPEND_ARGS = "c2a_boot=recovery"


def _parse_fdt(blob: bytes) -> tuple[tuple[int, ...], list[tuple], dict[tuple[str, str], int], set[str]]:
    """Parse an FDT/FIT structure block while keeping property bytes intact."""
    if len(blob) < 40:
        raise ValueError("FDT is shorter than its header")
    header = struct.unpack_from(">10I", blob)
    magic, total, struct_off, strings_off, reserve_off, version, _last, _boot, strings_size, struct_size = header
    if magic != FDT_MAGIC or total > len(blob) or version < 17:
        raise ValueError("unsupported or malformed FDT (expected version 17 or newer)")
    strings = blob[strings_off:strings_off + strings_size]
    if len(strings) != strings_size or struct_off + struct_size > total:
        raise ValueError("FDT section lies outside the declared blob")

    tokens: list[tuple] = []
    properties: dict[tuple[str, str], int] = {}
    nodes: set[str] = set()
    stack: list[str] = []
    pos = struct_off
    end = struct_off + struct_size
    while pos + 4 <= end:
        tag = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if tag == 1:  # FDT_BEGIN_NODE
            nul = blob.find(b"\0", pos, end)
            if nul < 0:
                raise ValueError("unterminated FDT node name")
            name = blob[pos:nul].decode("ascii")
            pos = (nul + 4) & ~3
            stack.append(name)
            path = "/" + "/".join(part for part in stack if part)
            nodes.add(path or "/")
            tokens.append(("begin", name))
        elif tag == 2:  # FDT_END_NODE
            if not stack:
                raise ValueError("unbalanced FDT node end")
            stack.pop()
            tokens.append(("end",))
        elif tag == 3:  # FDT_PROP
            if pos + 8 > end or not stack:
                raise ValueError("malformed FDT property")
            size, nameoff = struct.unpack_from(">II", blob, pos)
            pos += 8
            if nameoff >= len(strings):
                raise ValueError("FDT property name offset is invalid")
            name_end = strings.find(b"\0", nameoff)
            if name_end < 0:
                raise ValueError("unterminated FDT property name")
            name = strings[nameoff:name_end].decode("ascii")
            value = blob[pos:pos + size]
            if len(value) != size:
                raise ValueError("truncated FDT property")
            pos = (pos + size + 3) & ~3
            path = "/" + "/".join(part for part in stack if part)
            properties[(path or "/", name)] = len(tokens)
            tokens.append(("prop", nameoff, name, value))
        elif tag == 4:  # FDT_NOP
            tokens.append(("nop",))
        elif tag == 9:  # FDT_END
            tokens.append(("finish",))
            break
        else:
            raise ValueError(f"unknown FDT structure tag {tag}")
    if not tokens or tokens[-1][0] != "finish":
        raise ValueError("FDT structure has no end marker")
    return header, tokens, properties, nodes


def _encode_fdt(original: bytes, header: tuple[int, ...], tokens: list[tuple]) -> bytes:
    """Rebuild an FDT after property changes, preserving reserve entries and strings."""
    _magic, total, struct_off, strings_off, reserve_off, version, last, boot, strings_size, struct_size = header
    strings = original[strings_off:strings_off + strings_size]
    structure = bytearray()
    for token in tokens:
        kind = token[0]
        if kind == "begin":
            name = token[1].encode("ascii") + b"\0"
            structure += struct.pack(">I", 1) + name
            structure += b"\0" * (-len(structure) % 4)
        elif kind == "end":
            structure += struct.pack(">I", 2)
        elif kind == "prop":
            _kind, nameoff, _name, value = token
            structure += struct.pack(">III", 3, len(value), nameoff) + value
            structure += b"\0" * (-len(structure) % 4)
        elif kind == "nop":
            structure += struct.pack(">I", 4)
        elif kind == "finish":
            structure += struct.pack(">I", 9)

    # Keep the existing reservation map and its alignment padding.
    reserve = original[reserve_off:struct_off]
    prefix = original[:reserve_off]
    new_struct_off = len(prefix) + len(reserve)
    new_strings_off = new_struct_off + len(structure)
    result = bytearray(prefix + reserve + structure + strings)
    struct.pack_into(">10I", result, 0, FDT_MAGIC, len(result), new_struct_off,
                     new_strings_off, reserve_off, version, last, boot,
                     len(strings), len(structure))
    return bytes(result)


def _fdt_string(value: bytes, description: str) -> str:
    if not value.endswith(b"\0") or b"\0" in value[:-1]:
        raise ValueError(f"{description} is not a single FDT string")
    return value[:-1].decode("utf-8")


def _append_args(existing: str, extra: str) -> str:
    args = existing.split()
    for item in extra.split():
        if item not in args:
            args.append(item)
    return " ".join(args)


def _patch_dtb_bootargs(dtb: bytes, extra: str) -> tuple[bytes, str]:
    header, tokens, properties, nodes = _parse_fdt(dtb)
    bootargs_index = properties.get(("/chosen", "bootargs"))
    if bootargs_index is None:
        raise ValueError("selected FIT DTB has no /chosen/bootargs property")
    token = tokens[bootargs_index]
    old = _fdt_string(token[3], "/chosen/bootargs")
    updated = _append_args(old, extra)
    tokens[bootargs_index] = ("prop", token[1], token[2], updated.encode("utf-8") + b"\0")
    return _encode_fdt(dtb, header, tokens), updated


def patch_fit_bootargs(fit: bytes, extra: str = FIT_APPEND_ARGS) -> tuple[bytes, str]:
    """Append kernel arguments to the DTB selected by a FIT's default config."""
    header, tokens, properties, _nodes = _parse_fdt(fit)
    default_index = properties.get(("/configurations", "default"))
    if default_index is None:
        raise ValueError("FIT has no default configuration")
    default_name = _fdt_string(tokens[default_index][3], "FIT default configuration")
    config_path = f"/configurations/{default_name}"
    fdt_index = properties.get((config_path, "fdt"))
    if fdt_index is None:
        raise ValueError(f"FIT configuration {default_name!r} has no FDT image")
    fdt_name = _fdt_string(tokens[fdt_index][3], "FIT configuration FDT reference")
    image_path = f"/images/{fdt_name}"
    data_index = properties.get((image_path, "data"))
    if data_index is None:
        raise ValueError(f"FIT FDT image {fdt_name!r} has no inline data")

    hash_path = image_path + "/hash-1"
    algo_index = properties.get((hash_path, "algo"))
    value_index = properties.get((hash_path, "value"))
    if algo_index is None or value_index is None:
        raise ValueError(f"FIT FDT image {fdt_name!r} has no hash-1")
    algo = _fdt_string(tokens[algo_index][3], "FIT image hash algorithm")
    if algo == "crc32":
        hasher = lambda payload: struct.pack(">I", zlib.crc32(payload) & 0xffffffff)
    elif algo in ("sha1", "sha256"):
        hasher = lambda payload: hashlib.new(algo, payload).digest()
    else:
        raise ValueError(f"unsupported FIT image hash algorithm {algo!r}")

    dtb, bootargs = _patch_dtb_bootargs(tokens[data_index][3], extra)
    tokens[data_index] = ("prop", tokens[data_index][1], tokens[data_index][2], dtb)
    tokens[value_index] = ("prop", tokens[value_index][1], tokens[value_index][2], hasher(dtb))
    if any("signature" in path for path, _name in properties):
        raise ValueError("signed FIT cannot be modified without regenerating its signature")
    return _encode_fdt(fit, header, tokens), bootargs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preset", help="Device preset passed to cpbox2fel.py")
    p.add_argument("--fw", default="../v821b-rtl8733bs.c2aflash", help="Local c2aflash firmware (default: ../v821b-rtl8733bs.c2aflash)")
    p.add_argument("--ota", nargs="?", const="../v851s3-aic8800d80.c2aota", metavar="FILE",
                   help="Flash OTA instead of --fw (default file: ../v851s3-aic8800d80.c2aota; implied by --v851)")
    p.add_argument("--already-fel", action="store_true", help="Skip entering FEL")
    p.add_argument("--already-recov", action="store_true", help="Skip entering FEL and booting recovery")
    p.add_argument("--refresh", action="store_true", help="Enter FEL via USB vendor request")
    p.add_argument("--no-flash", action="store_true", help="Only boot recovery")
    p.add_argument("--v851", action="store_true", help="Use the V851S FEL/U-Boot boot flow")
    v851_stage = p.add_mutually_exclusive_group()
    v851_stage.add_argument("--staging", action="store_true", help="Boot the staged SPL that reads the kernel FIT from NAND")
    v851_stage.add_argument("--staging-proper", action="store_true", help="Boot the FEL SPL, then U-Boot proper reading the kernel FIT from NAND")
    p.add_argument("--v851-spl", default="../v851s3-u-boot-fel.egon.bin")
    p.add_argument("--v851-kernel", help="V851 recovery FIT/uImage (default: detect in bundle)")
    p.add_argument("--v851-uboot", default="../v851s3-u-boot-proper.bin")
    p.add_argument("--v851-staging-egon", default="../v851s3-u-boot-staging.egon.bin")
    p.add_argument("--v851-staging-proper", default="../v851s3-u-boot-staging-proper.bin")
    p.add_argument("--v851-extra-bootargs", help="Append additional kernel arguments to the V851 FIT bootargs")
    args = p.parse_args(argv)
    if args.v851_kernel is None:
        bundle_dir = Path(__file__).resolve().parent.parent
        candidates = ("v851s3-aic8800d80-recov.fitImage", "v851s-recovery.fitImage", "v851s-recovery.uImage")
        args.v851_kernel = str(next(
            (bundle_dir / name for name in candidates if (bundle_dir / name).is_file()),
            bundle_dir / candidates[0],
        ))
    if (args.staging or args.staging_proper) and not args.v851:
        p.error("V851 staging options require --v851")
    if not args.v851 and not args.already_recov and not args.already_fel and not args.refresh and not args.preset:
        p.error("--preset is required unless --already-recov, --already-fel or --refresh is used")
    if args.v851 and args.preset:
        p.error("V851 does not use a device preset; power-cycle it into FEL first")
    if args.v851 and args.ota is None:
        args.ota = "../v851s3-aic8800d80.c2aota"
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
            elif not args.v851:
                rc = run_step("cpbox2fel.py", [args.preset])
            else:
                rc = 0
            if rc != 0:
                return rc

        if not args.already_recov:
            if args.v851:
                argv = ["--platform", "v851"]
                if args.staging:
                    argv.extend(["--staging", "--staging-egon", args.v851_staging_egon])
                elif args.staging_proper:
                    if args.v851_extra_bootargs:
                        raise ValueError("--v851-extra-bootargs requires a RAM-boot FIT image")
                    argv.extend([
                        "--staging-proper",
                        "--fel-spl", args.v851_spl,
                        "--u-boot", args.v851_staging_proper,
                    ])
                    rc = run_step("recov.py", argv)
                else:
                    argv.extend([
                        "--fel-spl", args.v851_spl,
                        "--u-boot", args.v851_uboot,
                    ])
                    kernel_path = Path(args.v851_kernel)
                    temporary_fit = None
                    fit_data = kernel_path.read_bytes()
                    is_fit = len(fit_data) >= 4 and struct.unpack_from(">I", fit_data)[0] == FDT_MAGIC
                    if is_fit:
                        requested_args = _append_args(FIT_APPEND_ARGS, args.v851_extra_bootargs or "")
                        patched_fit, bootargs = patch_fit_bootargs(fit_data, requested_args)
                        with tempfile.NamedTemporaryFile(
                            prefix="v851-recovery-", suffix=".fitImage", delete=False,
                        ) as output:
                            output.write(patched_fit)
                            temporary_fit = Path(output.name)
                        kernel_path = temporary_fit
                        print(f"[+] Temporary FIT bootargs: {bootargs}", flush=True)
                    elif args.v851_extra_bootargs:
                        raise ValueError("--v851-extra-bootargs requires a FIT image")
                    argv.extend(["--kernel", str(kernel_path)])
                    try:
                        rc = run_step("recov.py", argv)
                    finally:
                        if temporary_fit is not None:
                            temporary_fit.unlink(missing_ok=True)
                if args.staging:
                    if args.v851_extra_bootargs:
                        raise ValueError("--v851-extra-bootargs requires a RAM-boot FIT image")
                    rc = run_step("recov.py", argv)
            else:
                rc = run_step("recov.py", [
                "--fes", "../fes1.bin",
                "--erofs-initrd", "../v821b-rtl8733bs-recov.initrd.bin",
                "--opensbi", "../opensbi.bin",
                "--kernel", "../v821b-rtl8733bs-recov.Image.bin",
                "--extra-bootargs", "c2a_boot=recovery",
                "--simpleboot", "../simpleboot.bin",
                "--dtb", "../woo.dtb",
                ])
            if rc != 0:
                return rc
        if not args.no_flash:
            firmware = ["--ota", args.ota] if args.ota is not None else ["--fw", args.fw]
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
