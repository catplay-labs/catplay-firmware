#!/usr/bin/env python3
"""Locate per-build S3 snapshots and merge them after the firmware matrix."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import tempfile

from ci_common import Store, backup_database, snapshot
from hashserv_snapshot import S3SnapshotStore, database_schema, merge_database


def machine_list():
    machines = json.loads(os.environ['FIRMWARE_MACHINES'])
    if (not isinstance(machines, list) or not machines or len(machines) > 250
            or any(not isinstance(machine, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', machine)
                   for machine in machines) or len(set(machines)) != len(machines)):
        raise ValueError('Expected 1..250 unique machine names')
    return machines


def snapshot_urls(store, machine, attempt=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', machine):
        raise ValueError('Invalid machine name')
    key = store.key('ci-hashserv', os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_RUN_ID'],
                    str(attempt if attempt is not None else os.environ['GITHUB_RUN_ATTEMPT']),
                    machine + '.sqlite3.gz')
    return (f's3://{store.bucket}/{store.database}/current.json', f's3://{store.bucket}/{key}')


def merge(store):
    machines = machine_list()
    attempt = int(os.environ['GITHUB_RUN_ATTEMPT'])
    transfers = S3SnapshotStore(client=store.client)
    with tempfile.TemporaryDirectory(prefix='hashserv-merge-') as temporary:
        directory = Path(temporary)
        base = directory / 'merged.sqlite3'
        current, _ = snapshot_urls(store, machines[0])
        has_base = transfers.download(current, base, missing_ok=True)
        if has_base:
            with closing(sqlite3.connect(base)) as db:
                database_schema(db)
        merged = 0
        for machine in machines:
            incoming = directory / 'incoming.sqlite3'
            # "Re-run failed jobs" preserves successful jobs from older attempts.
            # A retried build may reuse local task stamps, so its snapshot need
            # not contain every mapping from an unpublished previous attempt.
            # Union all attempts, preferring the newest mapping on conflicts.
            found = False
            for previous_attempt in range(attempt, 0, -1):
                _, url = snapshot_urls(store, machine, previous_attempt)
                if not transfers.download(url, incoming, missing_ok=True):
                    continue
                found = True
                with closing(sqlite3.connect(incoming)) as db:
                    database_schema(db)
                if has_base:
                    merge_database(base, incoming)
                else:
                    backup_database(incoming, base)
                    has_base = True
                merged += 1
                incoming.unlink()
                print(f'hashserv: merged {machine} (attempt {previous_attempt})', flush=True)
            if not found:
                print(f'WARNING: no hashserv snapshot for {machine}; job may have failed before startup',
                      file=sys.stderr, flush=True)
        if merged:
            # Workflow concurrency serializes runs. Builders upload only their
            # private objects: this is the only writer of the shared pointer.
            snapshot(store, base, directory)
            print(f'hashserv: published merged database from {merged} snapshot(s)', flush=True)
        else:
            print('hashserv: no build snapshots to merge; current snapshot unchanged', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('paths', 'merge', 'seed'))
    parser.add_argument('--machine')
    parser.add_argument('--database', type=Path)
    args = parser.parse_args(argv)
    store = Store()
    if args.role == 'paths':
        if not args.machine:
            parser.error('paths requires --machine')
        restore_url, upload_url = snapshot_urls(store, args.machine)
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
            output.write(f'restore={restore_url}\nupload={upload_url}\n')
    elif args.role == 'seed':
        if not args.database:
            parser.error('seed requires --database')
        if store.get(store.database + '/current.json') is not None:
            raise ValueError('Refusing to replace an existing hashserv snapshot')
        with closing(sqlite3.connect(args.database)) as db:
            database_schema(db)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot(store, args.database, temporary)
    else:
        merge(store)
    return 0


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print(f'ci-hashserv: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
