#!/usr/bin/env python3
"""Clear stale BitBake stamps whose promised deploy artifact went missing.

Two failure modes have been observed on this project, both caused by the
same root problem: a BitBake stamp says a task ("do_populate_lic" or
"do_package_write_ipk") already ran successfully, but the file it was
supposed to produce under build/tmp/deploy/ is gone - most likely because
tmp/deploy/ was partially cleaned by hand or by an interrupted build without
also invalidating the stamps that point at it. BitBake then trusts the
stamp, skips the task, and fails much later when something else tries to
use the missing file:

  1. do_rootfs: "Fatal QA errors were found, failing task" with
     "[license-file-missing]" for one or more recipes.
  2. do_rootfs: FileNotFoundError copying a .ipk from tmp/deploy/ipk/ into
     the per-image package feed.

Both are fixed the same way: delete the stamp (and, for do_package_write_ipk,
its "_setscene" sibling plus the matching sstate tarball) so BitBake is
forced to run the task again instead of trusting a promise it can't keep.

This is intentionally coarse - it clears *all* stamps for the given task(s)
rather than trying to work out which single recipe/sub-package is affected
(that would need parsing PKGDATA to map recipe names to the runtime package
names actually written to tmp/deploy/ipk/, which "do_package_write_ipk"
stamps don't record). Re-running these tasks is cheap (pure packaging from
already-built output), so clearing all of them is simpler and safe.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Task name -> also clear its "_setscene" sibling and matching sstate tarball.
TASKS = {
    "do_populate_lic": False,
    "do_package_write_ipk": True,
}

LICENSE_QA_RE = re.compile(r"was not in the licenses collected for recipe \S+ \[license-file-missing\]")
MISSING_IPK_RE = re.compile(r"FileNotFoundError:.*deploy/ipk/.*\.ipk")


def detect_reasons(log_text: str) -> set[str]:
    reasons = set()
    if LICENSE_QA_RE.search(log_text):
        reasons.add("do_populate_lic")
    if MISSING_IPK_RE.search(log_text):
        reasons.add("do_package_write_ipk")
    return reasons


def _stamp_dirs(build_dir: Path) -> list[Path]:
    return [d for d in (build_dir / "tmp" / "stamps").glob("*") if d.is_dir()]


def clear_task(build_dir: Path, task: str, *, also_setscene: bool, dry_run: bool) -> int:
    stamps_root = build_dir / "tmp" / "stamps"
    sstate_root = build_dir / "tmp" / "sstate-cache"
    removed = 0

    patterns = [f"*.{task}.*"]
    if also_setscene:
        patterns.append(f"*.{task}_setscene.*")

    for pattern in patterns:
        for stamp in stamps_root.rglob(pattern):
            if not stamp.is_file():
                continue
            if dry_run:
                print(f"[dry-run] would remove stamp: {stamp}")
            else:
                stamp.unlink()
            removed += 1

    if also_setscene and sstate_root.is_dir():
        for tarball in sstate_root.rglob(f"*_{task}.tar.zst*"):
            if dry_run:
                print(f"[dry-run] would remove sstate object: {tarball}")
            else:
                tarball.unlink()
            removed += 1

    return removed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--build-dir", required=True, type=Path, help="Yocto BUILD_DIR (contains tmp/)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--from-log",
        type=Path,
        help="Path to a bitbake log/output file to scan for the known failure patterns",
    )
    group.add_argument(
        "--task",
        action="append",
        choices=sorted(TASKS),
        dest="tasks",
        help="Clear stamps for this task unconditionally (may be repeated)",
    )
    p.add_argument("--dry-run", action="store_true", help="List what would be removed, but don't remove it")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (args.build_dir / "tmp").is_dir():
        print(f"[!] Not a Yocto build dir (no tmp/): {args.build_dir}", file=sys.stderr)
        return 2

    if args.from_log:
        try:
            log_text = args.from_log.read_text(errors="replace")
        except OSError as e:
            print(f"[!] Failed to read log {args.from_log}: {e}", file=sys.stderr)
            return 2
        tasks = detect_reasons(log_text)
        if not tasks:
            print("[*] No known stale-deploy-artifact failure pattern found in log; nothing to fix")
            return 1
    else:
        tasks = set(args.tasks)

    total = 0
    for task in sorted(tasks):
        n = clear_task(args.build_dir, task, also_setscene=TASKS[task], dry_run=args.dry_run)
        verb = "Would clear" if args.dry_run else "Cleared"
        print(f"[+] {verb} {n} stale {task} stamp(s)/sstate object(s)")
        total += n

    if total == 0:
        print("[*] Nothing to clean up")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
