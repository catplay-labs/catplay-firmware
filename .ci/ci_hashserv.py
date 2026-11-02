#!/usr/bin/env python3
"""Run an ephemeral Docker hashserv or connect a CI builder to it."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

from ci_common import Store, restore, snapshot
from ci_network import Network, command, wireguard_debug

HERE = Path(__file__).resolve().parent
SERVER = '10.193.78.1'
PORT = 8686
BOOT_TIMEOUT = 180
HEARTBEAT_TIMEOUT = 90
PROTOCOL_FAILURE_GRACE = 90


def machine_list():
    machines = json.loads(os.environ['FIRMWARE_MACHINES'])
    if not machines or len(set(machines)) != len(machines) or len(machines) > 250:
        raise ValueError('Expected 1..250 unique machines')
    return machines


def build_jobs_finished(jobs, machines):
    states = {job['name']: job['status'] for job in jobs}
    return all(states.get('Build ' + machine) == 'completed' for machine in machines)


def github_jobs():
    base = os.environ.get('GITHUB_API_URL', 'https://api.github.com')
    repo, run, attempt = (os.environ[name] for name in (
        'GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    jobs, page = [], 1
    while True:
        url = f'{base}/repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page={page}'
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json',
        })
        with urllib.request.urlopen(request, timeout=15) as response:
            batch = json.load(response)['jobs']
        jobs.extend(batch)
        if len(batch) < 100:
            return jobs
        page += 1


def probe(image, endpoint, *, container=None, smoke=None, identity='health', expect=(),
          script_path='/ci/hashserv_probe.py', user=None, workdir=None, quiet=False):
    script = ['python3', script_path, endpoint]
    if smoke:
        script += ['--smoke', smoke, '--identity', identity]
        for peer in expect:
            script += ['--expect-identity', peer]
    if container:
        args = ['docker', 'exec']
        if user:
            args += ['--user', user]
        if workdir:
            args += ['--workdir', workdir]
        args += [container, *script]
    else:
        # Default Docker bridge: verify the actual path used by firmware builds.
        args = ['docker', 'run', '--rm', '--network', 'bridge',
                '--volume', f'{HERE}:/ci:ro', '--entrypoint', 'python3', image,
                *script[1:]]
    subprocess.run(args, check=True, timeout=20, capture_output=quiet, text=True)


class ProtocolHealth:
    """Test the actual builder route; handshake age alone is not liveness."""
    def __init__(self, image, machine):
        self.image = image
        self.container = f'catplay-bitbake-ci-{machine}-run'
        self.failed_since = None
        self.failures = 0

    def check(self, network, key, process=None):
        if process is not None and process.poll() is not None:
            return
        started = time.monotonic()
        try:
            probe(self.image, f'{SERVER}:{PORT}', container=self.container,
                  script_path='/orchestrator/hashserv_probe.py',
                  user=f'{os.getuid()}:{os.getgid()}', workdir='/tmp', quiet=True)
        except subprocess.SubprocessError as error:
            if process is not None:
                # --no-keep-container stops Docker just before the orchestrator
                # exits. A concurrent docker exec can fail during that window.
                try:
                    process.wait(timeout=2)
                    return
                except subprocess.TimeoutExpired:
                    pass
            if self.failed_since is None:
                self.failed_since = started
            self.failures += 1
            elapsed = time.monotonic() - self.failed_since
            diagnostics = {}
            if wireguard_debug():
                try:
                    diagnostics = network.diagnostics(key)
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as diagnostic_error:
                    diagnostics = {'diagnostic_error': type(diagnostic_error).__name__}
            stderr = getattr(error, 'stderr', None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', 'replace')
            print('hashserv: protocol probe failed: ' + json.dumps({
                'attempt': self.failures, 'failed_for_s': round(elapsed, 1),
                'error': type(error).__name__, 'probe_stderr': stderr,
                **diagnostics}), flush=True)
            if self.failures >= 3 and elapsed >= PROTOCOL_FAILURE_GRACE:
                raise RuntimeError('Hashserv protocol unavailable from builder for '
                                   f'{elapsed:.0f}s after {self.failures} failed probes') from None
        else:
            if self.failures:
                print('hashserv: protocol connection recovered; build continues', flush=True)
            self.failed_since, self.failures = None, 0


def server_state(store):
    status = store.read('server')
    if status and status.get('state') == 'failed':
        raise RuntimeError('CI hashserv failed; see the Hash equivalence server job')
    if status and time.time() - status['heartbeat'] > HEARTBEAT_TIMEOUT:
        raise RuntimeError('CI hashserv heartbeat expired')
    return status


@contextmanager
def builder_forwarding(interface):
    rules = [
        ['-i', 'docker0', '-o', interface, '-d', SERVER + '/32', '-p', 'tcp',
         '--dport', str(PORT), '-j', 'ACCEPT'],
        ['-i', interface, '-o', 'docker0', '-s', SERVER + '/32', '-p', 'tcp',
         '--sport', str(PORT), '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'],
    ]
    installed = []
    try:
        for rule in rules:
            command('sudo', '-n', 'iptables', '-w', '-I', 'DOCKER-USER', '1', *rule)
            installed.append(rule)
        yield
    finally:
        for rule in reversed(installed):
            subprocess.run(['sudo', '-n', 'iptables', '-w', '-D', 'DOCKER-USER', *rule], check=False)


def start_server(store, image, directory):
    database = directory / 'hashserv.sqlite3'
    restored = restore(store, database)
    print('hashserv: restored S3 snapshot' if restored else 'hashserv: starting new database', flush=True)
    container = 'catplay-hashserv-' + uuid.uuid4().hex[:12]
    # Override the image's /cache VOLUME with tmpfs: no anonymous persistent volume.
    command('docker', 'run', '--detach', '--rm', '--init', '--name', container,
            '--network', 'host', '--user', f'{os.getuid()}:{os.getgid()}',
            '--workdir', '/state',
            '--tmpfs', '/cache', '--tmpfs', '/tmp:exec,mode=1777',
            '--volume', f'{directory}:/state', '--volume', f'{HERE}:/ci:ro',
            '--entrypoint', '/usr/lib/bitbake/bin/bitbake-hashserv', image,
            '--bind', f'{SERVER}:{PORT}', '--database', '/state/hashserv.sqlite3',
            '--anon-perms', '@read,@report')
    return container, database


def serve(store, image):
    machines = machine_list()
    with tempfile.TemporaryDirectory(prefix='catplay-hashserv-') as temporary:
        directory = Path(temporary)
        with Network('server', SERVER) as network:
            container = None
            database = directory / 'hashserv.sqlite3'
            ready = False
            try:
                container, database = start_server(store, image, directory)
                deadline = time.monotonic() + 60
                while True:
                    try:
                        probe(image, f'{SERVER}:{PORT}', container=container)
                        break
                    except subprocess.SubprocessError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('Docker hashserv failed readiness') from None
                        time.sleep(1)
                initial = snapshot(store, database, directory, publish=False)
                store.write('initial-snapshot', initial)
                ready = True
                next_backup, next_jobs, next_heartbeat = time.monotonic() + 300, 0, 0
                api_errors = 0
                missing_since = None
                # Leave finalization time before the job's 355 minute hard limit.
                end = time.monotonic() + 340 * 60
                while time.monotonic() < end:
                    network.check()
                    if command('docker', 'inspect', '--format', '{{.State.Running}}', container) != 'true':
                        raise RuntimeError('Docker hashserv exited unexpectedly')
                    for index, machine in enumerate(machines):
                        peer = store.read('peer-' + machine)
                        if peer:
                            network.add_peer(peer, f'10.193.78.{index + 2}', follow_candidate=True)
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        store.write('server', {**network.metadata, 'state': 'ready', 'heartbeat': time.time()})
                        next_heartbeat = now + 10
                    if now >= next_backup:
                        snapshot(store, database, directory)
                        next_backup = time.monotonic() + 300
                    if now >= next_jobs:
                        try:
                            jobs = github_jobs()
                            if build_jobs_finished(jobs, machines):
                                break
                            names = {job['name'] for job in jobs}
                            if any('Build ' + machine not in names for machine in machines):
                                missing_since = missing_since or time.monotonic()
                                if time.monotonic() - missing_since > BOOT_TIMEOUT:
                                    raise RuntimeError('Missing matrix jobs; use Re-run all jobs, not a partial rerun')
                            else:
                                missing_since = None
                            api_errors = 0
                        except (OSError, ValueError):
                            api_errors += 1
                            if api_errors >= 4:
                                raise RuntimeError('Cannot monitor builder jobs through GitHub API') from None
                        next_jobs = time.monotonic() + 15
                    time.sleep(2)
                else:
                    raise RuntimeError('Hashserv session exceeded its time budget')
            finally:
                # SIGINT/SIGTERM initiate cleanup; a second signal is left to
                # GitHub's hard kill. Snapshot failure must fail this job.
                try:
                    if container:
                        subprocess.run(['docker', 'logs', '--tail', '30', container], check=False)
                        # --rm may already have removed a crashed server. The
                        # bind-mounted database must still be snapshotted.
                        subprocess.run(['docker', 'stop', '--time', '20', container], check=False)
                finally:
                    if ready:
                        snapshot(store, database, directory)
                        print('hashserv: final snapshot published', flush=True)


def run_builder(store, image, machine, arguments, smoke):
    machines = machine_list()
    address = f'10.193.78.{machines.index(machine) + 2}'
    deadline = time.monotonic() + BOOT_TIMEOUT
    print('hashserv: preparing WireGuard and STUN discovery', flush=True)
    with Network(machine, address) as network, builder_forwarding(network.interface):
        store.write('peer-' + machine, network.metadata)
        print('hashserv: peer published; waiting for server and tunnel', flush=True)
        next_progress = 0
        while True:
            status = server_state(store)
            stage = 'server readiness in S3'
            if status and status.get('state') == 'ready':
                candidate = network.add_peer(status, SERVER, try_private=True)
                if candidate and network.metadata.get('candidate') != candidate:
                    network.metadata['candidate'] = candidate
                    store.write('peer-' + machine, network.metadata)
                stage = 'WireGuard handshake'
                if network.handshake(status['public_key']):
                    stage = 'Docker hashserv protocol probe'
                    try:
                        probe(image, f'{SERVER}:{PORT}')
                        break
                    except subprocess.SubprocessError:
                        pass
            if time.monotonic() > deadline:
                raise RuntimeError('WireGuard/hashserv bootstrap timed out before build: ' + stage)
            if time.monotonic() >= next_progress:
                print('hashserv: waiting for ' + stage, flush=True)
                if stage == 'WireGuard handshake' and wireguard_debug():
                    print('hashserv: tunnel diagnostics: ' + json.dumps(
                        network.diagnostics(status['public_key'])), flush=True)
                next_progress = time.monotonic() + 15
            time.sleep(2)
        print('hashserv: WireGuard handshake and Docker protocol probe passed', flush=True)
        if smoke:
            smoke_id = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
            probe(image, f'{SERVER}:{PORT}', smoke=smoke_id, identity=machine)
            store.write('smoke-' + machine, {'reported': True})
            while not all(store.read('smoke-' + peer) for peer in machines):
                server_state(store)
                network.check()
                if time.monotonic() > deadline:
                    raise RuntimeError('Other smoke clients did not report their mappings')
                time.sleep(2)
            probe(image, f'{SERVER}:{PORT}', smoke=smoke_id, identity=machine, expect=machines)
            return 0
        if not arguments:
            raise ValueError('Missing builder command')
        process = subprocess.Popen([*arguments, '--hashserve', f'{SERVER}:{PORT}'],
                                   start_new_session=True)
        health = ProtocolHealth(image, machine)
        try:
            while process.poll() is None:
                # Give the orchestrator time to create the builder container.
                # Reuse it for probes rather than starting temporary containers.
                try:
                    return process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
                if process.poll() is not None:
                    return process.returncode
                try:
                    network.check()
                    status = server_state(store)
                    if not status or status.get('state') != 'ready':
                        raise RuntimeError('CI hashserv is unavailable during build')
                    health.check(network, status['public_key'], process)
                except Exception:
                    # A liveness check must not override the result of a build
                    # which completed while the check was in flight.
                    if process.poll() is not None:
                        return process.returncode
                    raise
            return process.returncode
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    # Ensure a killed orchestrator cannot leave BitBake running.
                    subprocess.run(['docker', 'stop', f'catplay-bitbake-ci-{machine}-run'], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('server', 'builder', 'seed'))
    parser.add_argument('--image', default='catplay-bitbake:ci')
    parser.add_argument('--machine')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--smoke', action='store_true')
    # Command is separated with -- so its options never become helper options.
    tokens = sys.argv[1:]
    split = tokens.index('--') if '--' in tokens else len(tokens)
    args = parser.parse_args(tokens[:split])
    arguments = tokens[split + 1:]
    store = Store()
    if args.role == 'seed':
        if not args.database:
            parser.error('seed requires --database')
        if store.get(store.database + '/current.json') is not None:
            raise ValueError('Refusing to replace an existing hashserv snapshot')
        with tempfile.TemporaryDirectory() as temporary:
            snapshot(store, args.database, temporary)
        return 0
    if args.role == 'builder':
        if not args.machine:
            parser.error('builder requires --machine')
        return run_builder(store, args.image, args.machine, arguments, args.smoke)
    try:
        serve(store, args.image)
    except BaseException:
        store.write('server', {'state': 'failed', 'heartbeat': time.time()})
        raise
    else:
        store.delete_session()
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
        # Avoid dumping SDK request objects, credentials or generated keys.
        print(f'ci-hashserv: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
