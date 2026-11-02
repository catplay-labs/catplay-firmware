#!/usr/bin/env python3
"""Copy glob-selected BitBake artifacts while preserving relative paths."""

from __future__ import annotations

import argparse
import glob
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pattern", required=True, action="append")
    return parser.parse_args(argv)


def validate_pattern(pattern: str) -> None:
    path = Path(pattern)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"artifact pattern must stay relative to the build directory: {pattern!r}"
        )


def copy_artifacts(source: Path, output: Path, patterns: Sequence[str]) -> int:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise ValueError(f"artifact source directory does not exist: {source}")
    if not output.is_dir():
        raise ValueError(f"artifact output directory does not exist: {output}")

    matches: dict[Path, Path] = {}
    for pattern in patterns:
        validate_pattern(pattern)
        for raw_match in glob.iglob(str(source / pattern), recursive=True):
            match = Path(raw_match)
            relative = match.relative_to(source)
            matches[relative] = match

    if not matches:
        joined = ", ".join(repr(pattern) for pattern in patterns)
        raise ValueError(f"artifact patterns matched no paths: {joined}")

    copied_files = 0
    for relative, match in sorted(matches.items(), key=lambda item: item[0].parts):
        destination = output / relative
        if match.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink() or destination.exists():
                destination.unlink()
            destination.symlink_to(match.readlink())
        elif match.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif match.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(match, destination)
            copied_files += 1

    print(
        f"exported {copied_files} artifact file(s) from {source} to {output}",
        flush=True,
    )
    return copied_files


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        copy_artifacts(args.source, args.output, args.pattern)
    except (OSError, ValueError) as error:
        print(f"copy-artifacts.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
