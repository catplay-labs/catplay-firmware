#!/usr/bin/env python3
"""Serialize container work without passing a lock to the BitBake daemon.

Exit 75: active worker. Exit 76: interrupted/unclaimed operation, requiring a
container restart before reuse. The marker is a recovery journal, not a PID lock.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
from pathlib import Path
import signal
import sys

BUSY = 75
INTERRUPTED = 76


def operate(cache: Path, action: str, token: str | None, arguments: list[str]) -> int:
    with (cache / ".catplay-build.lock").open("a+") as lock:
        # Python descriptors are non-inheritable; subprocesses close them too.
        os.set_inheritable(lock.fileno(), False)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another CatPlay worker is using this cache", file=sys.stderr)
            return BUSY
        marker = cache / ".catplay-operation"
        if action == "probe":
            return INTERRUPTED if marker.exists() else 0
        if action == "claim":
            if marker.exists():
                return INTERRUPTED
            marker.write_text(token + "\n")
            return 0
        if not marker.exists() or marker.read_text() != token + "\n":
            print("Build reservation was superseded; refusing to start", file=sys.stderr)
            return INTERRUPTED

        # Import and run in this process, which owns the lock. If it is killed,
        # the kernel unlocks it but the marker forces recovery of orphaned tasks.
        path = Path(__file__).with_name("main.py")
        spec = importlib.util.spec_from_file_location("catplay_main", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        def interrupted(signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        result = module.main(arguments)
        # main() returns 130 on interruption. A signal might have left the BB
        # daemon building, so preserve the journal and restart before reuse.
        if result != 130:
            marker.unlink()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("/cache"))
    parser.add_argument("action", choices=("probe", "claim", "run"))
    parser.add_argument("--token")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    # Keep worker flags ahead of action; everything after action is main args.
    args = parser.parse_args()
    if args.action != "probe" and not args.token:
        parser.error("claim/run require --token before action")
    return operate(args.cache, args.action, args.token, args.arguments)


if __name__ == "__main__":
    raise SystemExit(main())
