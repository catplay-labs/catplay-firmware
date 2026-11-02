"""Ephemeral host WireGuard; STUN and S3 provide discovery, never a relay."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

COOKIE = 0x2112A442
SUBNET = ipaddress.ip_network('10.193.78.0/24')
PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
PRIVATE_ENDPOINT_TIMEOUT = 12


def wireguard_debug():
    return os.environ.get('C2A_WIREGUARD_DEBUG', '').lower() in ('1', 'true', 'yes')


def mapped_address(packet, transaction):
    if len(packet) < 20:
        raise ValueError('Truncated STUN header')
    kind, length, cookie = struct.unpack('!HHI', packet[:8])
    if kind != 0x101 or cookie != COOKIE or packet[8:20] != transaction:
        raise ValueError('Unexpected STUN response')
    if len(packet) < 20 + length:
        raise ValueError('Truncated STUN response')
    offset = 20
    while offset + 4 <= 20 + length:
        attribute, size = struct.unpack('!HH', packet[offset:offset + 4])
        value = packet[offset + 4:offset + 4 + size]
        if len(value) != size:
            raise ValueError('Truncated STUN attribute')
        if attribute in (0x20, 1) and size == 8 and value[1] == 1:
            port, address = struct.unpack('!HI', value[2:])
            if attribute == 0x20:
                port ^= COOKIE >> 16
                address ^= COOKIE
            return str(ipaddress.IPv4Address(address)), port
        offset += 4 + ((size + 3) & ~3)
    raise ValueError('No IPv4 STUN mapping')


def discover(server):
    host, port = server.rsplit(':', 1)
    endpoint = (socket.gethostbyname(host), int(port))
    # Allocate before WireGuard binds this exact port. Keep the gap below the
    # NAT timeout; a raw keepalive takes over immediately after configuration.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('', 0))
        sock.settimeout(3)
        for _ in range(5):
            transaction = secrets.token_bytes(12)
            sock.sendto(struct.pack('!HHI', 1, 0, COOKIE) + transaction, endpoint)
            try:
                packet, sender = sock.recvfrom(4096)
                if sender != endpoint:
                    continue
                return sock.getsockname()[1], mapped_address(packet, transaction), endpoint
            except (TimeoutError, ValueError):
                continue
    raise RuntimeError('STUN discovery failed')


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def private_endpoint(destination, port):
    """Advertise the VM's uplink address, never Docker or the WG tunnel."""
    routes = json.loads(command('ip', '-j', '-4', 'route', 'get', destination))
    for route in routes:
        source = route.get('prefsrc') or route.get('src')
        if source and is_private_endpoint_address(source):
            return [source, port]
    return None


def is_private_endpoint_address(address):
    ip = ipaddress.IPv4Address(address)
    return ip not in SUBNET and any(ip in subnet for subnet in PRIVATE_NETWORKS)


def check_peer(peer, address):
    if peer['address'] != address:
        raise ValueError('Unexpected peer tunnel address')
    if len(base64.b64decode(peer['public_key'], validate=True)) != 32:
        raise ValueError('Invalid WireGuard public key')
    ipaddress.IPv4Address(peer['endpoint'][0])
    if not 1 <= int(peer['endpoint'][1]) <= 65535:
        raise ValueError('Invalid peer port')
    if peer.get('private_endpoint') is not None:
        host, port = peer['private_endpoint']
        if not is_private_endpoint_address(host) or not 1 <= int(port) <= 65535:
            raise ValueError('Invalid private peer endpoint')
    if peer.get('candidate') not in (None, 'private', 'public'):
        raise ValueError('Invalid peer candidate')


class Network:
    def __init__(self, identity, address):
        self.identity, self.address = identity, address
        self.interface = 'cph' + secrets.token_hex(4)
        self.stopped = threading.Event()
        self.peers = {}
        self.thread = None
        self.created = False
        self.health_error = None

    def __enter__(self):
        try:
            # A conflicting subnet must not silently steal existing routes.
            for route in json.loads(command('ip', '-j', 'route', 'show')):
                if route.get('dst', 'default') != 'default':
                    if ipaddress.ip_network(route['dst'], strict=False).overlaps(SUBNET):
                        raise RuntimeError(f'Runner route conflicts with {SUBNET}')
            self.port, endpoint, self.stun = discover(os.environ['STUN_SERVER'])
            with tempfile.TemporaryDirectory(prefix='catplay-wg-') as directory:
                key = Path(directory) / 'private'
                key.touch(mode=0o600)
                private = command('wg', 'genkey')
                key.write_text(private + '\n')
                public = command('wg', 'pubkey', input=private + '\n')
                command('sudo', '-n', 'ip', 'link', 'add', self.interface, 'type', 'wireguard')
                self.created = True
                command('sudo', '-n', 'wg', 'set', self.interface, 'listen-port', str(self.port),
                        'private-key', str(key))
            command('sudo', '-n', 'ip', 'address', 'add', self.address + '/24', 'dev', self.interface)
            command('sudo', '-n', 'ip', 'link', 'set', self.interface, 'mtu', '1380', 'up')
            self.metadata = {'public_key': public, 'endpoint': endpoint,
                             'address': self.address, 'identity': self.identity}
            local = private_endpoint(self.stun[0], self.port)
            if local:
                self.metadata['private_endpoint'] = local
            self.thread = threading.Thread(target=self.keep_mapping, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def keep_mapping(self):
        # Raw packets can share the port already owned by the kernel WG socket.
        # Keep discovery's mapping alive even before the first peer registers.
        while not self.stopped.is_set():
            try:
                command('sudo', '-n', 'nping', '--udp', '--no-capture', '--count', '1',
                        '--source-port', str(self.port), '--dest-port', str(self.stun[1]),
                        self.stun[0], timeout=8)
            except Exception as error:
                # Disabled: helper failures currently produce false positives,
                # aborting healthy builds. ProtocolHealth checks actual hashserv
                # connectivity; nping failure alone must not mark WG unhealthy.
                # self.health_error = type(error).__name__
                details = ''.join(traceback.format_exception(error))
                stderr = getattr(error, 'stderr', None)
                if stderr:
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode('utf-8', 'replace')
                    details += '\nCommand stderr:\n' + stderr
                # Exception text can include nping's command and public IPs.
                details = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<ip>', details)
                print('hashserv: DEBUG: NAT keepalive helper stopped (non-fatal):\n' + details,
                      file=sys.stderr, flush=True)
                return
            self.stopped.wait(10)

    def add_peer(self, peer, address, *, try_private=False, follow_candidate=False):
        check_peer(peer, address)
        # Only the builder advances candidates; the server follows its published
        # choice. Both actively send on the same path, without independent timers.
        candidates = []
        if try_private and peer.get('private_endpoint'):
            candidates.append(('private', tuple(peer['private_endpoint'])))
        public = ('public', tuple(peer['endpoint']))
        if not candidates or candidates[0][1] != public[1]:
            candidates.append(public)
        if follow_candidate:
            kind = peer.get('candidate') or ('private' if peer.get('private_endpoint') else 'public')
            if kind == 'private' and peer.get('private_endpoint'):
                candidates = [('private', tuple(peer['private_endpoint']))]
            else:
                candidates = [public]
        signature = (peer['public_key'], tuple(candidates))
        state = self.peers.get(address)
        now = time.monotonic()
        if state and state['signature'] == signature:
            if (state['index'] + 1 >= len(candidates)
                    or now - state['started'] < PRIVATE_ENDPOINT_TIMEOUT
                    or self.handshake(peer['public_key'])):
                return candidates[state['index']][0]
            index = state['index'] + 1
        else:
            index = 0
            if state and state['signature'][0] != peer['public_key']:
                command('sudo', '-n', 'wg', 'set', self.interface,
                        'peer', state['signature'][0], 'remove')
        kind, (host, port) = candidates[index]
        endpoint = f'{host}:{port}'
        command('sudo', '-n', 'wg', 'set', self.interface, 'peer', peer['public_key'],
                'allowed-ips', address + '/32', 'endpoint', endpoint,
                'persistent-keepalive', '25')
        self.peers[address] = {'signature': signature, 'index': index, 'started': now}
        if wireguard_debug():
            print(f'hashserv: {address}: trying {kind} WireGuard endpoint {endpoint}', flush=True)
        return kind

    def check(self):
        if self.health_error:
            raise RuntimeError('WireGuard NAT keepalive failed: ' + self.health_error)

    def handshake_age(self, key):
        self.check()
        for line in command('sudo', '-n', 'wg', 'show', self.interface, 'latest-handshakes').splitlines():
            public, timestamp = line.split()
            if public == key and int(timestamp) > 0:
                return max(0, time.time() - int(timestamp))
        return None

    def handshake(self, key):
        age = self.handshake_age(key)
        return age is not None and age < 180

    def diagnostics(self, key):
        # Never use `wg show ... dump`: its first line contains a private key.
        result = {'handshake_age_s': self.handshake_age(key)}
        for field in ('endpoints', 'transfer'):
            output = command('sudo', '-n', 'wg', 'show', self.interface, field)
            for line in output.splitlines():
                public, *values = line.split()
                if public == key:
                    if field == 'endpoints':
                        result['endpoint'] = values[0]
                    else:
                        result.update(rx_bytes=int(values[0]), tx_bytes=int(values[1]))
        return result

    def __exit__(self, *_):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=10)
        if self.created:
            subprocess.run(['sudo', '-n', 'ip', 'link', 'del', self.interface], check=False)
