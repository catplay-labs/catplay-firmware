# SPDX-License-Identifier: MIT
"""Lightweight fork-safe S3 proxy clients; credentials travel only over private UDS."""

import fcntl
import hashlib
import os
from pathlib import Path
import random
import socket
import stat
import subprocess
import sys
import time

from . import proxy

_children = {}
_revision = hashlib.sha256(Path(proxy.__file__).read_bytes()).hexdigest()
BUSY_RETRY_TIMEOUT = proxy.IO_TIMEOUT


def runtime_directory(d):
    # Fixed short path: per-recipe TMPDIR values and Unix path limits must not
    # split one TOPDIR into separate servers. A code revision gets a fresh server.
    base = proxy.private_directory(Path('/tmp') / ('catplay-s3-' + str(os.geteuid())))
    scope = os.path.realpath(d.getVar('TOPDIR') or os.getcwd())
    name = hashlib.sha256((scope + _revision).encode()).hexdigest()[:24]
    return base / name


def connect(filename, timeout=proxy.IO_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(max(.001, deadline - time.monotonic()))
        try:
            stream.connect(str(filename))
            proxy.check_peer(stream)
            return stream
        except BlockingIOError:
            # A full Unix accept queue is load, not a dead server.
            stream.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(.01)
        except BaseException:
            stream.close()
            raise


def control(filename, operation='PING'):
    with connect(proxy.control_path(filename), timeout=1) as stream:
        proxy.send_message(stream, {'protocol': proxy.PROTOCOL, 'operation': operation})
        response = proxy.receive_message(stream)
        if not response.get('ok') or response.get('protocol') != proxy.PROTOCOL:
            raise proxy.ProxyError('ProtocolMismatch')
        return response


def ensure_server(directory):
    directory = proxy.private_directory(directory)
    filename = directory / 'proxy.sock'
    try:
        control(filename)
        return filename
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    descriptor = os.open(directory / 'start.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            control(filename)
            return filename
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        for path in (filename, proxy.control_path(filename)):
            if path.exists():
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                    raise proxy.ProxyError('UnsafeSocketPath')
                path.unlink()
        # Credentials arrive in the current RPC, never the daemon's environment.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('S3_UPLOAD_', 'AWS_'))}
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        process = subprocess.Popen([sys.executable, proxy.__file__, '--socket', str(filename)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, cwd=directory, env=environment)
        _children[str(filename)] = process
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            try:
                control(filename)
                return filename
            except (OSError, proxy.ProxyError):
                time.sleep(.02)
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise proxy.ProxyError('ProxyStartupFailed')


def stop_server(directory):
    filename = Path(directory) / 'proxy.sock'
    try:
        control(filename, 'SHUTDOWN')
    except (OSError, proxy.ProxyError):
        pass
    child = _children.pop(str(filename), None)
    if child is not None:
        child.wait(timeout=10)
    else:
        deadline = time.monotonic() + 10
        while filename.exists() and time.monotonic() < deadline:
            time.sleep(.02)


class Body:
    def __init__(self, stream, length):
        self.stream = stream
        self.remaining = length

    def read(self, size=-1):
        size = self.remaining if size is None or size < 0 else min(size, self.remaining)
        if not size:
            return b''
        data = proxy.receive_exact(self.stream, size)
        self.remaining -= len(data)
        return data

    def close(self):
        self.stream.close()


class Client:
    def __init__(self, directory, config):
        self.directory = directory
        self.config = config

    def request(self, operation, arguments):
        filename = ensure_server(self.directory)
        deadline = time.monotonic() + BUSY_RETRY_TIMEOUT
        delay = .05
        while True:
            try:
                return self._request(filename, operation, arguments,
                                     max(.001, deadline - time.monotonic()))
            except proxy.ProxyError as error:
                if error.response['Error']['Code'] != 'ProxyBusy':
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                # Jitter prevents waiting BitBake tasks from retrying in lockstep.
                time.sleep(min(remaining, random.uniform(delay / 2, delay)))
                delay = min(1, delay * 2)

    def _request(self, filename, operation, arguments, timeout):
        stream = connect(filename, timeout=timeout)
        try:
            proxy.send_message(stream, {'protocol': proxy.PROTOCOL, 'operation': operation,
                                       'config': self.config, 'arguments': arguments})
            message = proxy.receive_message(stream)
            if not message.get('ok'):
                raise proxy.ProxyError(message.get('code', 'ProxyError'))
            response = message['response']
            if operation == 'GET':
                response['Body'] = Body(stream, response['ContentLength'])
            else:
                stream.close()
            return response
        except BaseException:
            stream.close()
            raise

    def get_object(self, **arguments):
        return self.request('GET', arguments)

    def head_object(self, **arguments):
        return self.request('HEAD', arguments)

    def list_objects_v2(self, **arguments):
        return self.request('LIST', arguments)
