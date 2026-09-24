#!/usr/bin/env python3
"""
SFTP uploader with legacy Dropbear compatibility.

Does not execute system scp/sftp binaries.
Uses Paramiko directly.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import posixpath
import select
import socket
import stat
import sys
import time
from pathlib import Path
from typing import IO, Iterable

import paramiko
from cryptography.exceptions import UnsupportedAlgorithm


LEGACY_KEX = [
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
]
LEGACY_CIPHERS = [
    "aes128-ctr",
    "aes256-ctr",
    "aes128-cbc",
    "3des-cbc",
]
LEGACY_DIGESTS = [
    "hmac-sha1",
    "hmac-sha1-96",
]


class UploadError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SFTP tool (legacy Dropbear-friendly, no-auth mode): upload, download, or exec."
    )
    p.add_argument("--host", required=True, help="SSH server hostname or IP")
    p.add_argument("path", nargs="?", help="Target path (upload/download mode)")
    p.add_argument("items", nargs="*", help="Source paths (upload/download mode)")
    p.add_argument("-u", "--user", default="root", help="SSH username")
    p.add_argument("-P", "--port", type=int, default=22, help="SSH port")
    p.add_argument("--recursive", action="store_true", help="Upload directories recursively")
    p.add_argument("--download", action="store_true", help="Download mode (reverse direction)")
    p.add_argument("--exec-cmd", help="Execute remote command and stream stdout/stderr line-by-line")
    p.add_argument(
        "--legacy-dropbear",
        action="store_true",
        default=True,
        help="Prefer legacy algorithms for Dropbear compatibility (default: enabled)",
    )
    p.add_argument(
        "--modern-only",
        action="store_true",
        help="Disable legacy algorithm preference",
    )
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    p.add_argument("--timeout", type=float, default=10.0, help="TCP connect timeout in seconds")
    return p.parse_args(argv)


def _preferred(current: Iterable[str], preferred: list[str]) -> tuple[str, ...]:
    cur = list(current)
    cur_set = set(cur)
    ordered = [x for x in preferred if x in cur_set]
    ordered.extend(x for x in cur if x not in ordered)
    return tuple(ordered)


def _configure_legacy_algorithms(transport: paramiko.Transport) -> None:
    sec = transport.get_security_options()
    try:
        sec.kex = _preferred(sec.kex, LEGACY_KEX)
    except Exception:
        pass
    try:
        sec.ciphers = _preferred(sec.ciphers, LEGACY_CIPHERS)
    except Exception:
        pass
    try:
        sec.digests = _preferred(sec.digests, LEGACY_DIGESTS)
    except Exception:
        pass


def _host_key_sha256(key: paramiko.PKey) -> str:
    digest = base64.b64encode(__import__("hashlib").sha256(key.asbytes()).digest()).decode("ascii")
    return f"SHA256:{digest.rstrip('=')}"


def _progress_line(msg: str) -> None:
    """\r overwrites cleanly in a real terminal, but anything that captures
    output non-interactively (a pipe, a log file, a tool that isn't a tty)
    doesn't process \\r as a line reset - each update ends up concatenated
    onto one giant line instead of being readable at all. Use a plain
    newline there instead; a real terminal still gets the familiar
    single-line-updating progress display."""
    if sys.stdout.isatty():
        print(f"\r{msg}", end="", flush=True)
    else:
        print(msg, flush=True)


def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    if not remote_dir or remote_dir == "/":
        return
    parts = [p for p in remote_dir.split("/") if p]
    cur = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        cur = f"{cur}/{part}" if cur else part
        try:
            st = sftp.stat(cur)
            if not stat.S_ISDIR(st.st_mode or 0):
                raise UploadError(f"Remote path exists but is not a directory: {cur}")
        except FileNotFoundError:
            sftp.mkdir(cur)
        except OSError:
            try:
                st = sftp.stat(cur)
                if not stat.S_ISDIR(st.st_mode or 0):
                    raise UploadError(f"Remote path exists but is not a directory: {cur}")
            except Exception as e:
                raise UploadError(f"Failed to create remote directory {cur}: {e}") from e


def _is_remote_dir_hint(remote_path: str) -> bool:
    return remote_path.endswith("/")


def _put_file(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    _mkdir_p(sftp, posixpath.dirname(remote))
    total = local.stat().st_size
    last_print = 0.0

    def cb(sent: int, size: int) -> None:
        nonlocal last_print
        denom = size if size > 0 else total
        now = time.monotonic()
        # paramiko calls this on every chunk (32KB by default - 500+ times
        # for a 16MB firmware image). \r overwrites cleanly in a real
        # terminal, but anything that captures/logs output non-interactively
        # (a pipe, a log file, ...) turns every single call into its own
        # line. Throttle to a few updates a second regardless of chunk
        # count; always print the final one so completion is still visible.
        if sent < denom and now - last_print < 0.5:
            return
        last_print = now
        pct = (sent / denom * 100.0) if denom else 100.0
        _progress_line(f"[upload] {local} -> {remote}  {sent}/{denom} bytes ({pct:5.1f}%)")

    sftp.put(str(local), remote, callback=cb)
    if sys.stdout.isatty():
        print()


def _iter_local_files(source: Path, recursive: bool) -> list[tuple[Path, str]]:
    if source.is_file():
        return [(source, source.name)]
    if source.is_dir():
        if not recursive:
            raise UploadError(f"{source} is a directory (use --recursive)")
        out: list[tuple[Path, str]] = []
        for p in sorted(source.rglob("*")):
            if p.is_file():
                out.append((p, p.relative_to(source).as_posix()))
        return out
    raise UploadError(f"Source does not exist or is unsupported: {source}")


def _get_file(sftp: paramiko.SFTPClient, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    last_print = 0.0

    def cb(done: int, total: int) -> None:
        nonlocal last_print
        denom = total if total > 0 else 1
        now = time.monotonic()
        # See the matching comment in _put_file(): throttle regardless of
        # chunk count, but always show the final (100%) update.
        if done < denom and now - last_print < 0.5:
            return
        last_print = now
        pct = done / denom * 100.0
        _progress_line(f"[download] {remote} -> {local}  {done}/{total} bytes ({pct:5.1f}%)")

    sftp.get(remote, str(local), callback=cb)
    if sys.stdout.isatty():
        print()


def _is_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> bool:
    st = sftp.stat(remote_path)
    return stat.S_ISDIR(st.st_mode)


def _iter_remote_files(sftp: paramiko.SFTPClient, source: str, recursive: bool) -> list[tuple[str, str]]:
    if not _is_remote_dir(sftp, source):
        return [(source, posixpath.basename(source.rstrip("/")))]
    if not recursive:
        raise UploadError(f"{source} is a remote directory (use --recursive)")

    out: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = [(source.rstrip("/"), "")]
    while stack:
        cur_remote, cur_rel = stack.pop()
        entries = sorted(sftp.listdir_attr(cur_remote), key=lambda e: e.filename)
        for entry in entries:
            child_remote = f"{cur_remote}/{entry.filename}"
            child_rel = f"{cur_rel}/{entry.filename}" if cur_rel else entry.filename
            if stat.S_ISDIR(entry.st_mode or 0):
                stack.append((child_remote, child_rel))
            elif stat.S_ISREG(entry.st_mode or 0):
                out.append((child_remote, child_rel))
    return out


def _emit_complete_lines(buf: bytes, data: bytes, stream: IO[str]) -> bytes:
    combined = buf + data
    parts = combined.split(b"\n")
    for line in parts[:-1]:
        stream.write(line.decode("utf-8", errors="replace") + "\n")
        stream.flush()
    return parts[-1]


def _stream_exec(transport: paramiko.Transport, cmd: str, *, heartbeat_interval: float = 15.0) -> int:
    chan = transport.open_session()
    chan.exec_command(cmd)

    out_buf = b""
    err_buf = b""
    start = time.monotonic()
    last_heartbeat = start
    while True:
        select.select([chan], [], [], 0.2)
        if chan.recv_ready():
            out_buf = _emit_complete_lines(out_buf, chan.recv(4096), sys.stdout)
        if chan.recv_stderr_ready():
            err_buf = _emit_complete_lines(err_buf, chan.recv_stderr(4096), sys.stderr)

        if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
            break

        # This has no overall timeout at all - unbounded by design, since a
        # real flash/command can legitimately take a long time - but with
        # no other feedback, a long-but-normal run looks identical to a
        # genuine hang. Print to stderr specifically so this never mixes
        # into stdout output that callers may be parsing (e.g.
        # exploit.py's is_recovery_stub() reading /proc/cmdline).
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_interval:
            last_heartbeat = now
            print(f"[*] still running ({now - start:.0f}s elapsed)...", file=sys.stderr, flush=True)

    if out_buf:
        sys.stdout.write(out_buf.decode("utf-8", errors="replace"))
        if not out_buf.endswith(b"\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    if err_buf:
        sys.stderr.write(err_buf.decode("utf-8", errors="replace"))
        if not err_buf.endswith(b"\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()

    return chan.recv_exit_status()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.modern_only:
        args.legacy_dropbear = False

    if args.exec_cmd is None:
        if not args.path:
            raise UploadError("path is required for upload/download mode")
        if not args.items:
            raise UploadError("at least one source item is required for upload/download mode")
        if not args.download:
            for src in args.items:
                if not Path(src).exists():
                    raise UploadError(f"Source not found: {src}")

    sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    transport = paramiko.Transport(sock)
    if args.legacy_dropbear:
        _configure_legacy_algorithms(transport)

    try:
        transport.start_client(timeout=args.timeout)
        server_key = transport.get_remote_server_key()
        if args.verbose:
            print("[+] Connected")
            server_fp = _host_key_sha256(server_key)
            print(f"[+] Server host key: {server_key.get_name()} {server_fp}")

        transport.auth_none(args.user)

        if not transport.is_authenticated():
            raise UploadError("No-auth authentication failed")

        if args.exec_cmd is not None:
            print(f"[*] Executing remote command: {args.exec_cmd}")
            rc = _stream_exec(transport, args.exec_cmd)
            print(f"[+] Remote command exit status: {rc}")
            return 0 if rc == 0 else rc

        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise UploadError("Failed to open SFTP channel")
        try:
            if args.download:
                local_base = Path(args.path)
                many_sources = len(args.items) > 1
                local_as_dir = many_sources or str(local_base).endswith("/")
                if many_sources and not local_base.exists():
                    local_base.mkdir(parents=True, exist_ok=True)

                for remote_source in args.items:
                    remote_entries = _iter_remote_files(sftp, remote_source, args.recursive)
                    source_is_dir = _is_remote_dir(sftp, remote_source)
                    for remote_file, rel in remote_entries:
                        if local_as_dir:
                            if source_is_dir:
                                local_target = local_base / Path(posixpath.basename(remote_source.rstrip("/"))) / Path(rel)
                            else:
                                local_target = local_base / Path(posixpath.basename(remote_file))
                        else:
                            if len(remote_entries) > 1:
                                raise UploadError("Single local target path used with multiple remote files")
                            local_target = local_base
                        _get_file(sftp, remote_file, local_target)
                print("[+] Download complete")
            else:
                remote_base = args.path
                many_sources = len(args.items) > 1
                remote_as_dir = many_sources or _is_remote_dir_hint(remote_base)

                for src in args.items:
                    source = Path(src)
                    entries = _iter_local_files(source, args.recursive)
                    source_is_dir = source.is_dir()

                    for local_file, rel in entries:
                        if remote_as_dir:
                            if source_is_dir:
                                remote_target = posixpath.join(remote_base.rstrip("/"), source.name, rel)
                            else:
                                remote_target = posixpath.join(remote_base.rstrip("/"), local_file.name)
                        else:
                            if len(entries) > 1:
                                raise UploadError(
                                    "Single-file remote target used with multiple local files"
                                )
                            remote_target = remote_base
                        _put_file(sftp, local_file, remote_target)
                print("[+] Upload complete")
        finally:
            sftp.close()
        return 0
    except UnsupportedAlgorithm as e:
        print(
            "[!] Error: cryptography backend rejected a required algorithm. "
            "This is commonly caused by legacy Dropbear using ssh-rsa/SHA1 "
            "while the local crypto backend blocks SHA1.",
            file=sys.stderr,
        )
        print(
            "[!] Fix options: enable an ECDSA/ED25519 host key on the server, "
            "upgrade Dropbear, or use a non-FIPS/OpenSSL configuration that permits SHA1.",
            file=sys.stderr,
        )
        print(f"[!] Details: {e}", file=sys.stderr)
        return 1
    except (UploadError, paramiko.SSHException, OSError, binascii.Error) as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1
    finally:
        transport.close()
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
