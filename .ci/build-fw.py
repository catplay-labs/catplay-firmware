#!/usr/bin/env python3
"""Build CatPlay firmware in an isolated Docker container."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path

DEFAULT_TARGET = "c2a-system-bundle"
DEFAULT_ARTIFACTS_PATTERN = "tmp-c2a/**"
MAX_PACK_JOBS = 4
CONTAINER_CONTRACT = "3"
UPLOAD_VARIABLES = (
    "S3_UPLOAD_ENDPOINT", "S3_UPLOAD_BUCKET", "S3_UPLOAD_ACCESS_KEY_ID",
    "S3_UPLOAD_SECRET_ACCESS_KEY", "S3_UPLOAD_REGION", "S3_UPLOAD_PREFIX",
    "S3_UPLOAD_STORAGE_CLASS",
)
SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    machines = parser.add_mutually_exclusive_group()
    machines.add_argument("--machine", dest="machine_values", action="append", nargs="?", const="")
    machines.add_argument("--machines", dest="machines_values", nargs="*",
                          help="machines to build; omit or leave empty to only pack local artifacts")
    parser.add_argument(
        "--unglitch-recipe", "--unglitch-recipes",
        dest="unglitch_recipes", action="append", default=[],
    )
    parser.add_argument("--target", action="append", dest="target_values")
    parser.add_argument("--targets", nargs="+", dest="targets_values")
    parser.add_argument("--firmware-dir", type=Path)
    parser.add_argument(
        "--config-dir", type=Path, default=Path(__file__).resolve().parent / "config"
    )
    parser.add_argument("--catplay-dir", type=Path)
    parser.add_argument(
        "--alias", help="stable name for Docker resources (required when building)"
    )
    parser.add_argument(
        "--cached-image",
        nargs="?",
        const="",
        metavar="IMAGE",
        help="skip docker build, optionally selecting an image name",
    )
    parser.add_argument("--reset-tmp", action="store_true")
    parser.add_argument(
        "--fresh-c2a-bundle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ensure that cross-machine C2A bundles won't ship files that were removed from the bundle",
    )
    parser.add_argument("--state-s3", nargs="?", const="", metavar="BASE_URL",
                        help="HTTP(S)/s3:// mirror URL, or omit URL to read privately using S3_UPLOAD_*")
    parser.add_argument("--s3-upload", action="store_true")
    parser.add_argument("--hashserve", metavar="HOST:PORT", help="shared hash equivalence server")
    parser.add_argument(
        "--keep-container",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep container and BitBake RAM cache; otherwise stop after build (default: true)",
    )
    parser.add_argument(
        "--artifacts-pattern",
        action="append",
        help=(
            "comma-separated glob(s) relative to /cache/build; may be repeated "
            f"(default: {DEFAULT_ARTIFACTS_PATTERN})"
        ),
    )
    parser.add_argument(
        "--pack-artifacts",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="pack exported tmp-c2a subfolders as maximum-compression LZMA .7z files and gzip .c2aota files at level 9 into artifacts-output/c2a",
    )
    parser.add_argument(
        "--artifacts-output",
        type=Path,
        help="host directory receiving matched build artifacts",
    )
    return parser.parse_args(argv)


def checked_dir(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"{description} does not exist: {resolved}")
    return resolved


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    quiet: bool = False,
) -> None:
    #print("+ " + subprocess.list2cmdline(list(command)), flush=True)
    subprocess.run(
        command,
        cwd=cwd,
        check=check,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )


def resource_name(alias: str, suffix: str) -> str:
    clean = SAFE_NAME.sub("-", alias).strip("-._").lower()
    if not clean:
        raise ValueError("--alias must contain a letter or digit")
    return f"catplay-bitbake-{clean}-{suffix}"


def capture(command: Sequence[str], *, missing_ok: bool = False):
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        if missing_ok and ("No such object" in completed.stderr or
                           "No such container" in completed.stderr):
            return None
        raise ValueError(f"{subprocess.list2cmdline(command)}: {completed.stderr.strip()}")
    return completed.stdout.strip()


@contextmanager
def cache_lock(volume: str):
    daemon = capture(["docker", "info", "--format", "{{.ID}}"])
    key = hashlib.sha256(f"{daemon}:{volume}".encode()).hexdigest()
    directory = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "catplay-bitbake/locks"
    directory.mkdir(parents=True, exist_ok=True)
    # Never unlink lock files: all invocations must lock the same inode.
    with (directory / (key + ".lock")).open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"cache {volume} is busy in another build-fw.py invocation") from error
        yield


def inspect_container(container: str):
    output = capture(["docker", "container", "inspect", container], missing_ok=True)
    return json.loads(output)[0] if output is not None else None


def compatibility_errors(info, *, image_id, mounts):
    errors = []
    if info["Image"] != image_id:
        errors.append("image ID changed")
    actual_mounts = {
        m["Destination"]: (m["Type"], m.get("Name") if m["Type"] == "volume" else m["Source"], m["RW"])
        for m in info["Mounts"] if m["Destination"] != "/tmp"
    }
    if actual_mounts != mounts:
        errors.append("mounts changed")
    environment = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    if any(environment.get(key) != value for key, value in {
        "LOCAL_UID": str(os.getuid()), "LOCAL_GID": str(os.getgid()), "HOME": "/cache/home",
    }.items()):
        errors.append("UID/GID or HOME changed")
    if (info["Config"].get("Labels") or {}).get("catplay.contract") != CONTAINER_CONTRACT:
        errors.append("container contract changed")
    tmpfs = info["HostConfig"].get("Tmpfs", {})
    if "/tmp" not in tmpfs:
        errors.append("missing ephemeral readiness directory")
    elif "exec" not in tmpfs["/tmp"].split(",") or "noexec" in tmpfs["/tmp"].split(","):
        errors.append("/tmp tmpfs must allow execution for OE-core sanity checks")
    if not info["HostConfig"].get("Init"):
        errors.append("missing container init process")
    return errors


def worker_command(container: str, action: str, token: str | None = None):
    command = ["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}",
               "--env", "HOME=/cache/home", "--workdir", "/workdir"]
    # These are invocation settings, never persistent container credentials.
    for variable in UPLOAD_VARIABLES:
        command.extend(["--env", variable if variable in os.environ else variable + "="])
    if "NO_COLOR" not in os.environ:
        command.extend(["--env", "TERM=xterm-256color"])
        if action == "run":
            command.append("--tty")
    command.extend([container, "python3", "/orchestrator/container-worker.py"])
    if token is not None:
        command.extend(["--token", token])
    return [*command, action]


def probe_worker(container: str) -> int:
    # Probe the old container independently of its orchestrator mount and UID.
    # Opening an existing lock read-only avoids creating root-owned cache files.
    script = """
import fcntl, pathlib, sys
p = pathlib.Path('/cache/.catplay-build.lock')
if p.exists():
    lock = p.open('r')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(75)
sys.exit(76 if pathlib.Path('/cache/.catplay-operation').exists() else 0)
"""
    completed = subprocess.run(
        ["docker", "exec", "--user", "0", container, "python3", "-c", script],
        check=False,
    )
    if completed.returncode not in (0, 75, 76):
        raise ValueError(f"cannot check worker in {container} (exit {completed.returncode})")
    if completed.returncode == 75:
        raise ValueError(f"container {container} has an active build; refusing to modify it")
    return completed.returncode


def wait_ready(container: str):
    run(["docker", "exec", container, "sh", "-ec",
         "i=0; while [ ! -e /tmp/catplay-container-ready ]; do "
         "i=$((i+1)); [ \"$i\" -lt 300 ] || { echo 'container readiness timeout' >&2; exit 1; }; "
         "sleep 0.1; done"])


def artifact_patterns(values: Sequence[str] | None) -> list[str]:
    """Normalize repeated, comma-separated artifact glob arguments."""
    patterns = []
    for value in values or [DEFAULT_ARTIFACTS_PATTERN]:
        for pattern in value.split(","):
            pattern = pattern.strip()
            if not pattern:
                raise ValueError("--artifacts-pattern contains an empty glob")
            patterns.append(pattern)
    return patterns


def export_artifacts(
    *,
    image: str,
    volume: str,
    downloads_volume: str,
    sstate_volume: str,
    orchestrator_dir: Path,
    output: Path,
    patterns: Sequence[str],
) -> None:
    """Copy selected paths from the named build volume onto the host."""
    output = output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise ValueError(f"artifacts output is not a directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

    command = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--volume",
        f"{volume}:/cache:ro",
        "--mount",
        f"type=volume,src={downloads_volume},dst=/cache/build/downloads,readonly,volume-nocopy",
        "--mount",
        f"type=volume,src={sstate_volume},dst=/cache/build/sstate-cache,readonly,volume-nocopy",
        "--volume",
        f"{orchestrator_dir}:/orchestrator:ro",
        "--volume",
        f"{output}:/artifacts",
        image,
        "python3",
        "/orchestrator/copy-artifacts.py",
        "--source",
        "/cache/build",
        "--output",
        "/artifacts",
    ]
    for pattern in patterns:
        command.extend(["--pattern", pattern])
    run(command)


def find_archiver() -> str:
    archiver = shutil.which("7zz") or shutil.which("7z")
    if archiver is None:
        raise ValueError("--pack-artifacts requires 7zz or 7z installed on the host")
    return archiver


def exported_bundle_folders(output: Path, patterns: Sequence[str]) -> list[Path]:
    """Use export-relative globs to select local tmp-c2a's direct children."""
    folders = set()
    for pattern in patterns:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError(f"artifact pattern must stay relative: {pattern!r}")
        for match in glob.iglob(str(output / pattern), recursive=True):
            relative = Path(match).relative_to(output)
            if "tmp-c2a" not in relative.parts:
                continue
            index = relative.parts.index("tmp-c2a")
            root = output.joinpath(*relative.parts[:index + 1])
            if len(relative.parts) == index + 1:
                candidates = root.iterdir()
            else:
                candidates = [root / relative.parts[index + 1]]
            for candidate in candidates:
                if candidate.is_dir():
                    # Never archive a link to a live tree outside the export.
                    if not candidate.resolve().is_relative_to(output):
                        raise ValueError(f"bundle folder escapes local artifact export: {candidate}")
                    folders.add(candidate)
    return sorted(folders)


def pack_exported_artifacts(output: Path, patterns: Sequence[str]) -> None:
    output = output.expanduser().resolve()
    folders = exported_bundle_folders(output, patterns)
    if not folders:
        raise ValueError("--pack-artifacts: no exported tmp-c2a subfolders match the artifact patterns")
    names = set()
    ota_files = {}
    for folder in folders:
        if folder.name in names:
            raise ValueError(f"duplicate bundle folder name for packing: {folder.name}")
        names.add(folder.name)
        for source in sorted(folder.rglob("*.c2aota")):
            if not source.is_file():
                continue
            if not source.resolve().is_relative_to(output):
                raise ValueError(f"OTA file escapes local artifact export: {source}")
            if source.name in ota_files:
                raise ValueError(f"duplicate .c2aota filename in exported bundles: {source.name}")
            ota_files[source.name] = source

    archiver = find_archiver()
    destination = output / "c2a"
    destination.mkdir(parents=True, exist_ok=True)

    def pack(folder: Path) -> None:
        # Build from scratch: updating an old archive would retain removed files.
        with tempfile.TemporaryDirectory(prefix=".pack-", dir=output) as temporary:
            archive = Path(temporary) / (folder.name + ".7z")
            run([archiver, "a", "-t7z", "-m0=LZMA", "-mx=9", "-mmt=on",
                 "-snl", str(archive), "--", "./" + folder.name], cwd=folder.parent)
            archive.replace(destination / archive.name)

    with ThreadPoolExecutor(max_workers=MAX_PACK_JOBS) as pool:
        # Consume all results so compressor failures fail the build command.
        for result in pool.map(pack, folders):
            pass
    for name, source in ota_files.items():
        with tempfile.TemporaryDirectory(prefix=".ota-", dir=output) as temporary:
            compressed = Path(temporary) / (name + ".gz")
            with source.open("rb") as original, gzip.open(compressed, "wb", compresslevel=9) as target:
                shutil.copyfileobj(original, target)
            compressed.replace(destination / compressed.name)
            # Remove the raw copy produced by older pack-artifacts versions,
            # only after its replacement has been published successfully.
            (destination / name).unlink(missing_ok=True)
    print(f"packed {len(folders)} bundle(s) and gzip-compressed {len(ota_files)} OTA file(s) into {destination}", flush=True)


def execute(args: argparse.Namespace) -> int:
    if args.pack_artifacts:
        if args.artifacts_output is None:
            raise ValueError("--pack-artifacts requires --artifacts-output")
        find_archiver()
    machines = [machine for machine in (args.machine_values or args.machines_values or []) if machine]
    if not machines:
        if not args.pack_artifacts:
            raise ValueError("specify --machine/--machines to build, or --pack-artifacts to pack local artifacts")
        pack_exported_artifacts(args.artifacts_output, artifact_patterns(args.artifacts_pattern))
        return 0
    if args.firmware_dir is None or not args.alias:
        raise ValueError("building requires --firmware-dir and --alias")
    args.machine_values = machines
    args.machines_values = None
    with cache_lock(resource_name(args.alias, "cache")):
        return execute_locked(args)


def execute_locked(args: argparse.Namespace) -> int:
    firmware_dir = checked_dir(args.firmware_dir, "firmware directory")
    config_dir = checked_dir(args.config_dir, "config directory")
    orchestrator_dir = Path(__file__).resolve().parent

    for relative in ("bitbake", "openembedded-core/oe-init-build-env"):
        if not (firmware_dir / relative).exists():
            raise ValueError(f"firmware directory is missing {relative}")
    for filename in ("local.conf", "bblayers.conf"):
        if not (config_dir / filename).is_file():
            raise ValueError(f"config directory is missing {filename}")

    image = args.cached_image or resource_name(args.alias, "image")
    volume = resource_name(args.alias, "cache")
    downloads_volume = resource_name(args.alias, "downloads")
    sstate_volume = resource_name(args.alias, "sstate-cache")
    container = resource_name(args.alias, "run")

    # Check orphaned work before source bundling or any cache/config mutation.
    interrupted = False
    previous = inspect_container(container)
    if previous is not None and (previous["State"].get("Paused") or
                                 previous["State"].get("Restarting")):
        raise ValueError(f"container {container} is paused or restarting")
    if previous is not None and previous["State"]["Running"]:
        processes = capture(["docker", "top", container, "-eo", "pid,args"])
        if any("/orchestrator/main.py" in line for line in processes.splitlines()):
            raise ValueError(f"container {container} has an active legacy build")
        if probe_worker(container) == 76:
            interrupted = True
            print(f"stopping interrupted operation in {container}", flush=True)
            run(["docker", "stop", container])

    if args.catplay_dir:
        catplay_dir = checked_dir(args.catplay_dir, "catplay directory")
        bundle_script = firmware_dir / "meta-catplay/scripts/create-src-bundle.sh"
        if not bundle_script.is_file():
            raise ValueError(f"bundle script does not exist: {bundle_script}")
        run(["bash", str(bundle_script), str(catplay_dir)])

    if args.cached_image is None:
        run(
            [
                "docker", "build", "--tag", image,
                "--file", str(orchestrator_dir / "Dockerfile"),
                str(firmware_dir),
            ]
        )

    for name in (volume, downloads_volume, sstate_volume):
        run(["docker", "volume", "create", name])

    main_args = [
        "python3", "/orchestrator/main.py",
        "--firmware-dir", "/workdir",
        "--build-dir", "/cache/build",
        "--config-dir", "/config",
    ]
    for machine in args.machine_values or args.machines_values:
        main_args.extend(["--machine", machine])
    for recipe in args.unglitch_recipes:
        main_args.extend(["--unglitch-recipes", recipe])
    for target in args.target_values or args.targets_values or [DEFAULT_TARGET]:
        main_args.extend(["--target", target])
    if args.reset_tmp:
        main_args.append("--reset-tmp")
    main_args.append("--fresh-c2a-bundle" if args.fresh_c2a_bundle else "--no-fresh-c2a-bundle")
    if args.state_s3 is not None:
        main_args.append("--state-s3")
        if args.state_s3:
            main_args.append(args.state_s3)
    if args.s3_upload:
        main_args.append("--s3-upload")
    if args.hashserve:
        main_args.extend(["--hashserve", args.hashserve])

    docker_run = [
        "docker", "run", "--init", "--name", container,
        "--entrypoint", "/orchestrator/container-entrypoint.sh",
        "--volume", f"{firmware_dir}:/workdir",
        "--volume", f"{volume}:/cache",
        "--mount", f"type=volume,src={downloads_volume},dst=/cache/build/downloads,volume-nocopy",
        "--mount", f"type=volume,src={sstate_volume},dst=/cache/build/sstate-cache,volume-nocopy",
        "--volume", f"{orchestrator_dir}:/orchestrator:ro",
        "--volume", f"{config_dir}:/config:ro",
        "--env", "HOME=/cache/home",
        "--env", f"LOCAL_UID={os.getuid()}",
        "--env", f"LOCAL_GID={os.getgid()}",
    ]
    docker_run.extend(["--label", f"catplay.contract={CONTAINER_CONTRACT}",
                       "--tmpfs", "/tmp:exec,mode=1777"])
    image_id = capture(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    mounts = {
        "/workdir": ("bind", str(firmware_dir), True),
        "/cache": ("volume", volume, True),
        "/cache/build/downloads": ("volume", downloads_volume, True),
        "/cache/build/sstate-cache": ("volume", sstate_volume, True),
        "/orchestrator": ("bind", str(orchestrator_dir), False),
        "/config": ("bind", str(config_dir), False),
    }
    info = inspect_container(container)
    if info is not None:
        if info["State"].get("Paused") or info["State"].get("Restarting"):
            raise ValueError(f"container {container} is paused or restarting")
        errors = compatibility_errors(info, image_id=image_id, mounts=mounts)
        if interrupted:
            errors.append("interrupted operation")
        if info["State"]["Running"]:
            # Legacy runners have no worker lock. Do not replace an active one.
            processes = capture(["docker", "top", container, "-eo", "pid,args"])
            if any("/orchestrator/main.py" in line for line in processes.splitlines()):
                raise ValueError(f"container {container} has an active legacy build")
            if not errors:
                wait_ready(container)
            if probe_worker(container) == 76:
                errors.append("interrupted operation; discard orphaned BitBake work")
        if errors:
            print(f"recreating {container}: {', '.join(errors)}", flush=True)
            if info["State"]["Running"]:
                run(["docker", "stop", container])
            run(["docker", "rm", container])
            info = None
    created = info is None
    if created:
        # Pin the ID we inspected, even if another process retags the image.
        run([*docker_run, "--detach", image_id, "sleep", "infinity"])
    elif not info["State"]["Running"]:
        print(f"starting {container} (BitBake RAM cache is cold)", flush=True)
        run(["docker", "start", container])
    else:
        print(f"reusing {container} (preserving BitBake RAM cache)", flush=True)

    claimed = False
    try:
        wait_ready(container)
        if created:
            # Creation follows removal (or absence) of the old container, so no
            # old worker can still own this reservation.
            run(["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}",
                 container, "rm", "-f", "/cache/.catplay-operation"])
        # A stopped container may contain an interrupted operation journal.
        if probe_worker(container) == 76:
            run(["docker", "stop", container])
            run(["docker", "rm", container])
            run([*docker_run, "--detach", image_id, "sleep", "infinity"])
            wait_ready(container)
            # No process from the old container survives. Clear its journal.
            run(["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}",
                 container, "rm", "-f", "/cache/.catplay-operation"])
        token = uuid.uuid4().hex
        run(worker_command(container, "claim", token))
        claimed = True
        # main.py executes inside the worker process, not as a separate child.
        run([*worker_command(container, "run", token), *main_args[2:]])
        if args.artifacts_output is not None:
            export_artifacts(
                image=image_id,
                volume=volume,
                downloads_volume=downloads_volume,
                sstate_volume=sstate_volume,
                orchestrator_dir=orchestrator_dir,
                output=args.artifacts_output,
                patterns=artifact_patterns(args.artifacts_pattern),
            )
            if args.pack_artifacts:
                pack_exported_artifacts(args.artifacts_output, artifact_patterns(args.artifacts_pattern))
    finally:
        if not args.keep_container:
            failed = sys.exc_info()[0] is not None
            try:
                # After claiming, any surviving worker is ours: stop also
                # cancels our interrupted build. Before claiming, never stop
                # a worker that may belong to a previous invocation.
                if not claimed:
                    probe_worker(container)
                run(["docker", "stop", container])
            except (ValueError, subprocess.CalledProcessError):
                if not failed:
                    raise
                print(f"could not stop {container}; original build error preserved", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return execute(parse_args(argv))
    except ValueError as error:
        print(f"build-fw.py: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError as error:
        print(f"build-fw.py: executable not found: {error.filename}", file=sys.stderr)
        return 127
    except subprocess.CalledProcessError as error:
        return error.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
