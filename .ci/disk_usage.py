#!/usr/bin/env python3
"""Record runner root disk pressure while executing a command, preserving its exit code."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time


def measure(output, arguments):
    output.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    samples = []
    def sample():
        usage = shutil.disk_usage('/')
        record = dict(timestamp=time.time(), total=usage.total, used=usage.used, free=usage.free)
        samples.append(record)
        with (output / 'disk.jsonl').open('a') as stream:
            stream.write(json.dumps(record) + '\n')
    def monitor():
        while not stopped.wait(5):
            sample()
    sample()
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    code = 130
    try:
        code = subprocess.call(arguments)
        return code
    finally:
        stopped.set()
        thread.join()
        sample()
        summary = dict(exit_code=code, initial_free_bytes=samples[0]['free'],
                       minimum_free_bytes=min(row['free'] for row in samples),
                       final_free_bytes=samples[-1]['free'], sample_interval_s=5)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print('disk usage:', json.dumps(summary), flush=True)


def report(output, container):
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'summary.json').exists() and os.environ.get('GITHUB_STEP_SUMMARY'):
        summary = json.loads((output / 'summary.json').read_text())
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('### Disk usage\n\n| Metric | Value |\n| --- | ---: |\n')
            for key in ('initial_free_bytes', 'minimum_free_bytes', 'final_free_bytes'):
                stream.write(f'| {key} | {summary[key] / 1024**3:.2f} GiB |\n')
            stream.write(f"| Build exit code | {summary['exit_code']} |\n")
    result = subprocess.run(['docker', 'inspect', container], capture_output=True, text=True)
    if result.returncode:
        (output / 'sizes.txt').write_text('Build container was not created.\n')
        return
    mounts = json.loads(result.stdout)[0]['Mounts']
    with (output / 'sizes.txt').open('w') as stream:
        for mount in mounts:
            if mount['Destination'] not in ('/cache', '/cache/build/downloads', '/cache/build/sstate-cache'):
                continue
            stream.write('\n' + mount['Destination'] + '\n')
            stream.flush()
            # These paths are supplied by Docker inspect, passed without a shell.
            depth = 3 if mount['Destination'] == '/cache' else 1
            subprocess.run(['sudo', '-n', 'du', '-x', '-B1', f'--max-depth={depth}', mount['Source']],
                           stdout=stream, stderr=subprocess.STDOUT, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'report'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--container')
    import sys
    tokens = sys.argv[1:]
    split = tokens.index('--') if '--' in tokens else len(tokens)
    args = parser.parse_args(tokens[:split])
    if args.mode == 'report':
        if not args.container:
            parser.error('report requires --container')
        report(args.output, args.container)
        return 0
    command = tokens[split + 1:]
    if not command:
        parser.error('run requires a command after --')
    return measure(args.output, command)


if __name__ == '__main__':
    raise SystemExit(main())
