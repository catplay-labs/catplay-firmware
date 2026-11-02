#!/usr/bin/env python3
"""Minimal BitBake build driver that handles BitBake tasks within the sandbox"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit


DEFAULT_TARGET = "c2a-system-bundle"
RESETTABLE_TMP_DIRS = ("tmp", "tmp-c2a")
S3_CACHE_DIRS = ("sstate-cache", "downloads")
RCLONE_TRANSFER_PATTERN = re.compile(r"\bxfr#(\d+)/")


@dataclass(frozen=True)
class S3UploadConfig:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str
    prefix: str
    storage_class: str = ""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run serial BitBake builds for one or more MACHINE values.",
    )
    parser.add_argument(
        "--firmware-dir",
        required=True,
        type=Path,
        help="catplay-firmware checkout visible inside the build environment",
    )
    parser.add_argument(
        "--build-dir",
        required=True,
        type=Path,
        help="OpenEmbedded build directory (TOPDIR)",
    )
    parser.add_argument(
        "--config-dir",
        required=True,
        type=Path,
        help="directory containing local.conf and bblayers.conf",
    )

    machines = parser.add_mutually_exclusive_group(required=True)
    machines.add_argument(
        "--machine",
        dest="machine_values",
        action="append",
        metavar="MACHINE",
        help="machine to build; may be repeated",
    )
    machines.add_argument(
        "--machines",
        dest="machines_values",
        nargs="+",
        metavar="MACHINE",
        help="ordered list of machines to build",
    )

    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        metavar="TARGET",
        help=f"BitBake target; may be repeated (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--unglitch-recipes",
        action="extend",
        nargs="+",
        default=[],
        metavar="RECIPE",
        help="recipes to clean serially before targets; may be repeated",
    )
    parser.add_argument(
        "--fresh-c2a-bundle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ensure that cross-machine C2A bundles won't ship files that were removed from the bundle",
    )
    parser.add_argument(
        "--reset-tmp",
        action="store_true",
        help="clear tmp and tmp-c2a before running BitBake",
    )
    parser.add_argument(
        "--state-s3",
        nargs="?",
        const="",
        metavar="BASE_URL",
        help=(
            "mirror base URL (HTTP(S) or private s3://bucket/prefix); "
            "without a URL, use S3_UPLOAD_* for authenticated reads"
        ),
    )
    parser.add_argument(
        "--s3-upload",
        action="store_true",
        help=(
            "after a successful build, upload changed sstate-cache and "
            "downloads objects with rclone; upload failures are warnings"
        ),
    )
    parser.add_argument("--hashserve", metavar="HOST:PORT",
                        help="shared hash equivalence server; upload errors become fatal")
    return parser.parse_args(argv)


def normalize_state_s3(value: str) -> str:
    if any(character.isspace() for character in value):
        raise ValueError("--state-s3 must not contain whitespace")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "s3"}:
        raise ValueError("--state-s3 must use an http://, https:// or s3:// URL")
    if not parsed.hostname:
        raise ValueError("--state-s3 must contain a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("--state-s3 must not contain credentials")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError(f"--state-s3 has an invalid port: {error}") from error
    if parsed.query or parsed.fragment:
        raise ValueError("--state-s3 must not contain a query or fragment")
    if parsed.scheme == "s3" and parsed.port is not None:
        raise ValueError("s3:// mirror must not contain a port; use S3_UPLOAD_ENDPOINT")
    if any(character in value for character in ('"', "\\", ";", "$")):
        raise ValueError("--state-s3 contains a character unsafe for BitBake config")

    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


RUNTIME_CONFIG = "catplay-runtime.conf"


def write_runtime_config(build_dir: Path, contents: str) -> Path:
    """Preserve stat identity on a no-op; publish changes with a new inode."""
    destination = build_dir / "conf" / RUNTIME_CONFIG
    if destination.exists() and destination.read_text() == contents:
        return destination
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".catplay-runtime-",
            dir=destination.parent, delete=False,
        ) as config_file:
            temporary = Path(config_file.name)
            config_file.write(contents)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def private_s3_settings(base_url: str) -> tuple[str, S3UploadConfig]:
    config = load_s3_upload_config(required=True)
    endpoint = normalize_state_s3(config.endpoint)
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.path:
        raise ValueError("S3_UPLOAD_ENDPOINT must be an HTTP(S) service endpoint without a path")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", config.bucket):
        raise ValueError("invalid S3_UPLOAD_BUCKET")
    if not re.fullmatch(r"[A-Za-z0-9-]+", config.region):
        raise ValueError("invalid S3_UPLOAD_REGION")
    if config.prefix and (not re.fullmatch(r"[A-Za-z0-9._/-]+", config.prefix) or
                          ".." in config.prefix.split("/")):
        raise ValueError("invalid S3_UPLOAD_PREFIX")
    base_url = normalize_state_s3(base_url or "s3://" + "/".join(
        part for part in (config.bucket, config.prefix) if part))
    if urlsplit(base_url).netloc != config.bucket:
        raise ValueError("private --state-s3 bucket must match S3_UPLOAD_BUCKET")
    # Secrets remain in BB_ORIGENV. A non-secret fingerprint forces a config
    # refresh when credentials rotate, even in an already-running BB server.
    return base_url, config


def hashserve_config(endpoint: str | None) -> str:
    if not endpoint:
        return ""
    host, separator, port = endpoint.rpartition(":")
    if not separator or not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("--hashserve requires HOST:PORT")
    return ('BB_SIGNATURE_HANDLER = "OEEquivHash"\n'
            f'BB_HASHSERVE = "{endpoint}"\n'
            'BB_HASHSERVE_UPSTREAM = ""\n')


def create_state_s3_config(build_dir: Path, base_url: str, hashserve: str | None = None) -> Path:
    private = not base_url or urlsplit(base_url).scheme == "s3"
    private_config = ""
    if private:
        base_url, config = private_s3_settings(base_url)
        try:
            import boto3
        except ImportError:
            raise ValueError("private S3 reads require boto3 in BitBake's Python; rebuild the builder image without --cached-image") from None
        revision = hashlib.sha256(
            (config.access_key_id + "\0" + config.secret_access_key).encode()
        ).hexdigest()
        private_config = (
            'INHERIT += "catplay-s3"\n'
            f'CATPLAY_S3_ENDPOINT = "{config.endpoint.rstrip("/")}"\n'
            f'CATPLAY_S3_REGION = "{config.region}"\n'
            f'CATPLAY_S3_BUCKET = "{config.bucket}"\n'
            f'CATPLAY_S3_AUTH_REVISION = "{revision}"\n'
        )
    sstate_url = f"{base_url}/sstate-cache"
    downloads_url = f"{base_url}/downloads"
    contents = (
        '# Generated by CatPlay main.py; stable mirror configuration.\n'
        'INHERIT += "own-mirrors"\n'
        'BB_GENERATE_MIRROR_TARBALLS = "1"\n'
        f'SOURCE_MIRROR_URL = "{downloads_url}"\n'
        'SSTATE_MIRRORS = "file://.* '
        f'{sstate_url}/PATH;downloadfilename=PATH"\n'
        + private_config
    )

    return write_runtime_config(build_dir, contents + hashserve_config(hashserve))


def load_s3_upload_config(*, required: bool = False) -> S3UploadConfig | None:
    required_variables = (
        "S3_UPLOAD_ENDPOINT",
        "S3_UPLOAD_BUCKET",
        "S3_UPLOAD_ACCESS_KEY_ID",
        "S3_UPLOAD_SECRET_ACCESS_KEY",
    )
    missing = [name for name in required_variables if not os.environ.get(name)]
    if missing:
        if required:
            raise ValueError("private S3 reads require: " + ", ".join(missing))
        print(
            "WARNING: s3-upload: missing environment variables: "
            + ", ".join(missing),
            file=sys.stderr,
            flush=True,
        )
        return None

    return S3UploadConfig(
        endpoint=os.environ["S3_UPLOAD_ENDPOINT"].rstrip("/"),
        bucket=os.environ["S3_UPLOAD_BUCKET"].strip("/"),
        access_key_id=os.environ["S3_UPLOAD_ACCESS_KEY_ID"],
        secret_access_key=os.environ["S3_UPLOAD_SECRET_ACCESS_KEY"],
        region=os.environ.get("S3_UPLOAD_REGION", "garage"),
        prefix=os.environ.get("S3_UPLOAD_PREFIX", "").strip("/"),
        storage_class=os.environ.get("S3_UPLOAD_STORAGE_CLASS", "").strip(),
    )


def s3_destination(config: S3UploadConfig, cache_dirname: str) -> str:
    path_components = [config.bucket]
    if config.prefix:
        path_components.append(config.prefix)
    path_components.append(cache_dirname)
    return ":s3:" + "/".join(path_components)


def has_upload_files(source_dir: Path) -> bool:
    """Find one eligible file without listing S3 or scanning the whole cache."""
    def walk_error(error: OSError) -> None:
        raise error

    for directory, subdirectories, filenames in os.walk(source_dir, onerror=walk_error):
        if source_dir.name == "downloads" and Path(directory) == source_dir:
            subdirectories[:] = [name for name in subdirectories if name != "git2"]
        for name in filenames:
            path = Path(directory) / name
            # Match rclone's default: ignore symlinks and special files.
            if not path.is_symlink() and path.is_file():
                return True
    return False


def run_rclone_copy(
    rclone_path: str,
    source_dir: Path,
    destination: str,
    config: S3UploadConfig,
) -> tuple[bool, int | None]:
    try:
        if not has_upload_files(source_dir):
            print(f"s3-upload: {source_dir} has no uploadable files; skipping", flush=True)
            return True, 0
    except OSError as error:
        print(f"WARNING: s3-upload: cannot inspect {source_dir}: {error}",
              file=sys.stderr, flush=True)
        return False, None

    command = [
        rclone_path,
        "copy",
        str(source_dir),
        destination,
        "--size-only",
        "--check-first",
        # Sstate fans out into thousands of hash directories. List S3
        # recursively in pages instead of issuing a LIST for each directory.
        "--fast-list",
        # Overlap request latency across the many small cache objects on a
        # gigabit uplink. Bound request rate below OVH's 300 PUT/s soft limit;
        # this counts all API calls, not just writes (including MPU parts).
        "--transfers",
        "32",
        "--checkers",
        "16",
        "--tpslimit",
        "250",
        "--tpslimit-burst",
        "32",
        "--s3-provider",
        "Other",
        "--s3-endpoint",
        config.endpoint,
        "--s3-region",
        config.region,
        "--s3-env-auth",
        "--s3-force-path-style",
        "--stats",
        "30s",
        "--stats-one-line-date",
        "--stats-log-level",
        "NOTICE",
        "--log-level",
        "NOTICE",
    ]
    if config.storage_class:
        command.extend(["--s3-storage-class", config.storage_class])
    if source_dir.name == "downloads":
        # downloads/git2 is BitBake's local bare-repository working cache.
        # Remote source mirrors provide gitshallow_/git2_ tarballs instead;
        # BitBake never restores the raw downloads/git2 tree from them.
        command.extend(["--exclude", "/git2/**"])

    child_env = os.environ.copy()
    child_env["AWS_ACCESS_KEY_ID"] = config.access_key_id
    child_env["AWS_SECRET_ACCESS_KEY"] = config.secret_access_key
    child_env.pop("AWS_SESSION_TOKEN", None)

    print(
        "s3-upload: " + shlex.join(command),
        flush=True,
    )

    transferred_objects: int | None = None
    try:
        process = subprocess.Popen(
            command,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        print(
            f"WARNING: s3-upload: could not start rclone: {error}",
            file=sys.stderr,
            flush=True,
        )
        return False, None

    assert process.stdout is not None
    for line in process.stdout:
        stripped_line = line.rstrip("\n")
        print(f"s3-upload: {stripped_line}", flush=True)
        match = RCLONE_TRANSFER_PATTERN.search(stripped_line)
        if match:
            transferred_objects = int(match.group(1))

    return_code = process.wait()
    if return_code != 0:
        print(
            f"WARNING: s3-upload: rclone failed for {source_dir.name} "
            f"(exit={return_code})",
            file=sys.stderr,
            flush=True,
        )
        return False, transferred_objects

    return True, transferred_objects


def upload_s3_cache(build_dir: Path, *, required: bool = False) -> None:
    started_at = time.monotonic()
    rclone_path = shutil.which("rclone")
    if rclone_path is None:
        if required:
            raise ValueError("s3-upload: rclone is required with shared hashserv")
        print(
            "WARNING: s3-upload: rclone executable not found; upload skipped",
            file=sys.stderr,
            flush=True,
        )
        return

    config = load_s3_upload_config(required=required)
    if config is None:
        return

    results: list[tuple[bool, int | None]] = []
    for cache_dirname in S3_CACHE_DIRS:
        source_dir = build_dir / cache_dirname
        if not source_dir.is_dir():
            print(
                f"s3-upload: {source_dir} does not exist; skipping",
                flush=True,
            )
            results.append((True, 0))
            continue

        results.append(
            run_rclone_copy(
                rclone_path,
                source_dir,
                s3_destination(config, cache_dirname),
                config,
            )
        )

    elapsed_seconds = time.monotonic() - started_at
    succeeded = all(success for success, _ in results)
    known_counts = [count for _, count in results if count is not None]
    all_counts_known = len(known_counts) == len(results)

    if not succeeded:
        if required:
            raise ValueError("s3-upload failed with shared hashserv")
        print(
            f"WARNING: s3-upload: finished with errors after "
            f"{elapsed_seconds:.1f}s; successful build result is preserved",
            file=sys.stderr,
            flush=True,
        )
        return

    if all_counts_known:
        print(
            f"s3-upload: completed in {elapsed_seconds:.1f}s; "
            f"synchronized objects: {sum(known_counts)}",
            flush=True,
        )
    else:
        print(
            f"s3-upload: completed in {elapsed_seconds:.1f}s; "
            "rclone did not expose a parseable object count",
            flush=True,
        )


def copy_if_changed(source, destination):
    source, destination = Path(source), Path(destination)
    if not destination.is_file() or source.read_bytes() != destination.read_bytes():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
                temporary = Path(file.name)
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return str(destination)


def resolve_directories(
    firmware_arg: Path,
    build_arg: Path,
    config_arg: Path,
) -> tuple[Path, Path]:
    firmware_dir = firmware_arg.expanduser().resolve()
    if not firmware_dir.is_dir():
        raise ValueError(f"firmware directory does not exist: {firmware_dir}")

    oe_init = firmware_dir / "openembedded-core" / "oe-init-build-env"
    if not oe_init.is_file():
        raise ValueError(f"missing OpenEmbedded bootstrap: {oe_init}")

    bitbake_dir = firmware_dir / "bitbake"
    if not bitbake_dir.is_dir():
        raise ValueError(f"missing BitBake checkout: {bitbake_dir}")

    build_dir = build_arg.expanduser()
    build_dir.mkdir(parents=True, exist_ok=True)
    build_dir = build_dir.resolve()
    if not build_dir.is_dir():
        raise ValueError(f"build path is not a directory: {build_dir}")

    config_dir = config_arg.expanduser().resolve()
    if not config_dir.is_dir():
        raise ValueError(f"config directory does not exist: {config_dir}")

    required_conf = (
        config_dir / "bblayers.conf",
        config_dir / "local.conf",
    )
    missing_conf = [str(path) for path in required_conf if not path.is_file()]
    if missing_conf:
        formatted = "\n  - ".join(missing_conf)
        raise ValueError(f"missing build configuration:\n  - {formatted}")

    build_conf_dir = build_dir / "conf"
    build_conf_dir.mkdir(parents=True, exist_ok=True)
    if (config_dir / RUNTIME_CONFIG).exists():
        raise ValueError(f"reserved runner config name: {RUNTIME_CONFIG}")
    for source in config_dir.iterdir():
        destination = build_conf_dir / source.name
        if source.is_dir():
            shutil.copytree(
                source, destination, dirs_exist_ok=True, copy_function=copy_if_changed
            )
        elif source.is_file():
            copy_if_changed(source, destination)

    return firmware_dir, build_dir


def reset_tmp_directories(
    build_dir: Path,
    directories: Sequence[str] = RESETTABLE_TMP_DIRS,
    *,
    remove_root: bool = False,
) -> None:
    resolved_build_dir = build_dir.resolve(strict=True)
    operation = "fresh-c2a-bundle" if remove_root else "reset-tmp"

    for dirname in directories:
        tmp_dir = resolved_build_dir / dirname
        if tmp_dir.is_symlink():
            raise ValueError(f"refusing to clear symlinked tmp directory: {tmp_dir}")
        if not tmp_dir.exists():
            print(f"{operation}: {tmp_dir} does not exist; skipping", flush=True)
            continue
        if not tmp_dir.is_dir():
            raise ValueError(f"tmp path is not a directory: {tmp_dir}")
        if tmp_dir.parent.resolve(strict=True) != resolved_build_dir:
            raise ValueError(f"tmp directory escaped build directory: {tmp_dir}")

        if remove_root:
            print(f"fresh-c2a-bundle: removing {tmp_dir}", flush=True)
            shutil.rmtree(tmp_dir)
            continue
        print(f"reset-tmp: clearing contents of {tmp_dir}", flush=True)
        for entry in tmp_dir.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()


def run_bitbake(
    firmware_dir: Path,
    build_dir: Path,
    machine: str,
    bitbake_args: Sequence[str],
    state_s3_config: Path | None,
) -> int:
    shell_script = r"""
set -e
firmware_dir=$1
build_dir=$2
machine=$3
shift 3

cd "$firmware_dir/openembedded-core"
. ./oe-init-build-env "$build_dir" >/dev/null

export MACHINE="$machine"
export BB_ENV_PASSTHROUGH_ADDITIONS="${BB_ENV_PASSTHROUGH_ADDITIONS:-} MACHINE"

exec bitbake "$@"
"""
    command_args = ["--idle-timeout", "-1"]
    if state_s3_config is not None:
        command_args.extend(["-R", str(state_s3_config)])
    command_args.extend(bitbake_args)

    display_command = shlex.join(["bitbake", *command_args])
    print(f"[{machine}] {display_command}", flush=True)

    completed = subprocess.run(
        [
            "bash",
            "-c",
            shell_script,
            "catplay-main",
            str(firmware_dir),
            str(build_dir),
            machine,
            *command_args,
        ],
        check=False,
    )
    return completed.returncode


def wait_hashserve(endpoint: str | None, firmware_dir: Path, *, timeout: float = 180) -> None:
    """Fail before BitBake if the explicitly selected server is not usable."""
    if not endpoint:
        return
    hashserve_config(endpoint)
    print(f"hashserv: waiting for {endpoint}", flush=True)
    # Use BitBake's actual client, not a TCP-only check. A child process puts
    # a hard deadline around connection/retry behavior inside the RPC client.
    script = (
        "import sys;sys.path.insert(0,sys.argv[2]);import hashserv;"
        "c=hashserv.create_client(sys.argv[1]);"
        "c.get_unihash('catplay-ci-health','0'*64);c.close()"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            subprocess.run(
                [sys.executable, "-c", script, endpoint, str(firmware_dir / "bitbake/lib")],
                check=True, capture_output=True, timeout=min(10, max(.01, remaining)),
            )
            print(f"hashserv: {endpoint} ready (protocol check passed)", flush=True)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise ValueError(f"required hashserv {endpoint} is not ready; refusing to start BitBake")


def execute(args: argparse.Namespace) -> int:
    firmware_dir, build_dir = resolve_directories(
        args.firmware_dir,
        args.build_dir,
        args.config_dir,
    )
    machines = args.machine_values or args.machines_values
    targets = args.targets or [DEFAULT_TARGET]
    state_s3 = normalize_state_s3(args.state_s3) if args.state_s3 else args.state_s3
    state_s3_config = (
        create_state_s3_config(build_dir, state_s3, args.hashserve) if state_s3 is not None else
        write_runtime_config(build_dir, "# CatPlay runtime configuration.\n" + hashserve_config(args.hashserve))
    )

    wait_hashserve(args.hashserve, firmware_dir)

    if args.reset_tmp:
        reset_tmp_directories(build_dir)
    if args.fresh_c2a_bundle:
        reset_tmp_directories(build_dir, ("tmp-c2a",), remove_root=True)

    print(f"firmware-dir: {firmware_dir}", flush=True)
    print(f"build-dir:    {build_dir}", flush=True)
    print(f"machines:     {' '.join(machines)}", flush=True)
    print(f"targets:      {' '.join(targets)}", flush=True)
    if state_s3 is not None:
        print(f"state-s3:     {state_s3 or 'private mirror from S3_UPLOAD_*'}", flush=True)
    if args.unglitch_recipes:
        print(
            f"unglitch:     {' '.join(args.unglitch_recipes)}",
            flush=True,
        )

    for machine in machines:
        for recipe in args.unglitch_recipes:
            wait_hashserve(args.hashserve, firmware_dir)
            status = run_bitbake(
                firmware_dir,
                build_dir,
                machine,
                ["-f", "-c", "cleansstate", recipe],
                state_s3_config,
            )
            if status != 0:
                return status

        for target in targets:
            wait_hashserve(args.hashserve, firmware_dir)
            status = run_bitbake(
                firmware_dir,
                build_dir,
                machine,
                [target],
                state_s3_config,
            )
            if status != 0:
                return status

    if args.s3_upload:
        if args.hashserve:
            upload_s3_cache(build_dir, required=True)
        else:
            upload_s3_cache(build_dir)

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return execute(parse_args(argv))
    except ValueError as error:
        print(f"main.py: {error}", file=sys.stderr, flush=True)
        return 2
    except OSError as error:
        print(f"main.py: operating system error: {error}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("main.py: interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
